"""Google Gemini backend (official `google-genai` SDK): language model and speech-to-text.

Gemini accepts audio input, so it can transcribe rehearsal takes as well as write scripts.
"""
from __future__ import annotations

import logging
import os

from google import genai
from google.genai import errors, types

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"

TRANSCRIBE_PROMPT = (
    "Transcribe this English speech recording verbatim. Keep every filler word and disfluency exactly "
    "as spoken (um, uh, er, ah, like, you know, so, repeated words, false starts). Do not correct grammar, "
    "do not summarise, do not add speaker labels, timestamps or commentary. Output only the transcript text."
)


def has_credentials(api_key: str) -> bool:
    return bool(api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))


def _client(api_key: str, timeout_s: float = 180.0) -> genai.Client:
    # Blank key -> the SDK reads GEMINI_API_KEY / GOOGLE_API_KEY. HTTPS_PROXY is honoured.
    opts = types.HttpOptions(timeout=int(timeout_s * 1000))  # milliseconds
    if os.getenv("GEMINI_BASE_URL"):  # optional: corporate API gateway / test server
        opts.base_url = os.getenv("GEMINI_BASE_URL")
    return genai.Client(api_key=api_key or None, http_options=opts)


def chat(system: str, user: str, *, api_key: str, model: str, max_tokens: int, temperature: float) -> str:
    # Keep the Client alive for the whole call: when a temporary Client is garbage-collected
    # it closes its HTTP connection ("client has been closed").
    with _client(api_key) as client:
        resp = client.models.generate_content(
            model=model or DEFAULT_MODEL,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=max(max_tokens, 4096),  # thinking models spend part of this budget thinking
            ),
        )
    text = (resp.text or "").strip()
    if not text:
        reason = resp.candidates[0].finish_reason if resp.candidates else "no candidates"
        raise RuntimeError(f"Gemini returned no text (finish reason: {reason})")
    return text


def transcribe(wav_bytes: bytes, *, api_key: str, model: str, keep_fillers: bool = True) -> str:
    prompt = TRANSCRIBE_PROMPT if keep_fillers else TRANSCRIBE_PROMPT.replace(
        "Keep every filler word and disfluency exactly as spoken (um, uh, er, ah, like, you know, so, repeated words, false starts). ", "")
    with _client(api_key, timeout_s=300) as client:
        resp = client.models.generate_content(
            model=model or DEFAULT_MODEL,
            contents=[prompt, types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")],
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=8192),
        )
    return (resp.text or "").strip()


def list_models(api_key: str, audio: bool = False) -> list[str]:
    out = []
    with _client(api_key, timeout_s=20) as client:
        models = list(client.models.list())  # the pager fetches lazily: drain it while the client is open
    for m in models:
        name = (m.name or "").removeprefix("models/")
        actions = m.supported_actions or []
        if "generateContent" in actions and name.startswith("gemini") and "embedding" not in name \
                and not any(x in name for x in ("-tts", "image", "live")):
            out.append(name)
    return sorted(out)


def friendly_error(e: Exception) -> str:
    if isinstance(e, errors.ClientError):
        code = getattr(e, "code", None)
        if code in (401, 403) or "API_KEY_INVALID" in str(e):
            return "Gemini: invalid API key or no access"
        if code == 404:
            return "Gemini: model not found - use ↻ List to pick one"
        if code == 429:
            return "Gemini: quota / rate limit reached - wait and retry"
        return f"Gemini: request error {code} - {getattr(e, 'message', e)}"
    if isinstance(e, errors.ServerError):
        return f"Gemini: server error {getattr(e, 'code', '')} - retry later"
    msg = str(e)
    if "ConnectError" in type(e).__name__ or "connect" in msg.lower():
        return "Gemini: cannot reach generativelanguage.googleapis.com (check network / HTTPS_PROXY)"
    return f"Gemini: {msg[:300]}"
