"""TTS dispatcher.

Engines:
  edge    - Microsoft neural voices (online, best accent coverage incl. en-IN)
  kokoro  - offline, very natural (US / UK, experimental Indian)
  piper   - offline, fast and lightweight (US / UK)
  browser - handled in the front-end (Web Speech API)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from starlette.concurrency import run_in_threadpool

from . import offline_tts

# Curated Edge defaults per locale (female, male).
CURATED = {
    "en-US": ("en-US-AvaNeural", "en-US-AndrewNeural"),
    "en-IN": ("en-IN-NeerjaExpressiveNeural", "en-IN-PrabhatNeural"),
    "en-GB": ("en-GB-SoniaNeural", "en-GB-RyanNeural"),
    "en-AU": ("en-AU-NatashaNeural", "en-AU-WilliamMultilingualNeural"),
    "en-CA": ("en-CA-ClaraNeural", "en-CA-LiamNeural"),
    "en-IE": ("en-IE-EmilyNeural", "en-IE-ConnorNeural"),
    "en-NZ": ("en-NZ-MollyNeural", "en-NZ-MitchellNeural"),
    "en-ZA": ("en-ZA-LeahNeural", "en-ZA-LukeNeural"),
    "en-SG": ("en-SG-LunaNeural", "en-SG-WayneNeural"),
}

_edge_cache: list[dict] | None = None


async def edge_voices() -> list[dict]:
    global _edge_cache
    if _edge_cache is None:
        import edge_tts

        voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=8)
        _edge_cache = sorted(
            ({"engine": "edge", "name": v["ShortName"], "locale": v["Locale"], "gender": v["Gender"],
              "label": v["ShortName"].split("-")[-1].replace("Neural", "")}
             for v in voices if v["Locale"].startswith("en-")),
            key=lambda v: (v["locale"], v["gender"], v["name"]),
        )
    return _edge_cache


async def all_voices() -> dict:
    out: dict = {"edge": [], "kokoro": [], "piper": [], "errors": {}}
    try:
        out["edge"] = await edge_voices()
    except Exception as e:  # noqa: BLE001
        out["errors"]["edge"] = f"offline or blocked ({type(e).__name__})"
    try:
        out["kokoro"] = await run_in_threadpool(offline_tts.KOKORO.voices)
    except Exception as e:  # noqa: BLE001
        out["errors"]["kokoro"] = str(e)
    out["piper"] = offline_tts.PIPER.voices()
    return out


def _pct(v: int) -> str:
    return f"{'+' if v >= 0 else ''}{int(v)}%"


def _hz(v: int) -> str:
    return f"{'+' if v >= 0 else ''}{int(v)}Hz"


async def _edge(text: str, voice: str, rate: int, pitch: int, mp3: Path) -> list[dict]:
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=_pct(rate), pitch=_hz(pitch), boundary="SentenceBoundary")
    sentences = []
    with open(mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                sentences.append({  # offsets are 100-ns units
                    "start": chunk["offset"] / 1e7,
                    "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                    "text": chunk["text"],
                })
    return sentences


async def synthesize(engine: str, text: str, voice: str, locale: str, rate: int, pitch: int,
                     cache_dir: Path) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = "mp3" if engine == "edge" else "wav"
    key = hashlib.sha256(f"{engine}|{voice}|{locale}|{rate}|{pitch}|{text}".encode()).hexdigest()[:24]
    audio = cache_dir / f"{key}.{ext}"
    meta = cache_dir / f"{key}.json"
    if audio.exists() and meta.exists():
        return {"file": audio.name, "sentences": json.loads(meta.read_text("utf-8"))}
    try:
        if engine == "edge":
            sentences = await _edge(text, voice, rate, pitch, audio)
        else:
            sentences = await run_in_threadpool(offline_tts.synthesize, engine, text, voice, locale, rate, audio)
    except Exception:
        audio.unlink(missing_ok=True)
        raise
    meta.write_text(json.dumps(sentences), "utf-8")
    return {"file": audio.name, "sentences": sentences}
