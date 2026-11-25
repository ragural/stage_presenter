"""Rehearsal feedback: Whisper (OpenVINO) transcription + delivery analytics + LLM coaching."""
from __future__ import annotations

import difflib
import gc
import io
import logging
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np

from . import llm, settings

log = logging.getLogger(__name__)

# Whisper tends to "clean up" disfluencies. A disfluent prompt nudges it to keep them.
FILLER_PROMPT = "Umm, so, uh, I mean, like, you know, basically we, uh, hmm... Okay."

FILLERS = ["um", "umm", "uh", "uhh", "er", "erm", "ah", "hmm", "mm", "like", "you know", "basically",
           "actually", "literally", "i mean", "sort of", "kind of", "so yeah", "okay so", "right so"]
STOP = set("""a an the and or but if then so of to in on at by for with from as is are was were be been being it its
this that these those we you they he she i our your their my me us them not no yes do does did have has had will
would can could should may might must there here what which who whom whose when where why how all any each few more
most other some such only own same than too very just also into over under again further once about against between
through during before after above below up down out off let lets now well""".split())


class _Whisper:
    """Speech-to-text: local OpenVINO WhisperPipeline or a remote OpenAI-compatible endpoint."""

    def __init__(self) -> None:
        self.pipe = None
        self.device = None
        self.model = None
        self.key = None
        self.lock = threading.RLock()
        self.error: str | None = None
        self.loading = False
        self.prompt_ok = True  # NPU static shapes can't fit an initial prompt

    @staticmethod
    def cfg() -> dict:
        return settings.get()["stt"]

    def model_dir(self) -> str:
        return settings.resolve_model_dir("whisper", self.cfg()["ov_model"])

    def wanted(self) -> tuple[str, str]:
        return self.model_dir(), self.cfg()["ov_device"]

    def unload(self) -> None:
        with self.lock:
            self.pipe, self.device, self.model, self.key = None, None, None, None
            self.prompt_ok = True
            gc.collect()

    def status(self) -> dict:
        c = self.cfg()
        if c["provider"] == "openai":
            ok = bool(c["base_url"] and c["model"])
            return {"provider": "openai", "available": ok, "device": "remote" if ok else None, "model": c["model"],
                    "loaded": ok, "loading": False, "error": None if ok else "Endpoint URL and model are required"}
        if c["provider"] == "none":
            return {"provider": "none", "available": False, "device": None, "model": None,
                    "loaded": False, "loading": False, "error": None}
        if c["provider"] == "gemini":
            from . import gemini_ai
            ok = gemini_ai.has_credentials(settings.get()["keys"]["gemini"])
            return {"provider": "gemini", "available": ok, "device": "Gemini API" if ok else None,
                    "model": c["gemini_model"] or gemini_ai.DEFAULT_MODEL, "loaded": ok, "loading": False,
                    "error": None if ok else "Add a Gemini API key in Settings (or set GEMINI_API_KEY)"}
        path = self.model_dir()
        return {"provider": "openvino", "available": bool(path), "device": self.device,
                "model": self.model or (Path(path).name if path else None),
                "loaded": self.pipe is not None and self.key == self.wanted(),
                "loading": self.loading, "error": self.error}

    def load(self):
        want = self.wanted()
        if self.pipe is not None and self.key == want:
            return self.pipe
        with self.lock:
            if self.pipe is not None and self.key == want:
                return self.pipe
            self.unload()
            import openvino_genai as ov_genai

            path, pref = want
            if not path:
                self.error = "No Whisper model installed. Run: python download_models.py --only whisper"
                raise RuntimeError(self.error)
            errors = []
            cache = Path(__file__).resolve().parent.parent / "models" / "cache"
            self.loading, self.error = True, None
            try:
                for dev in llm.device_chain(pref):
                    try:
                        t0 = time.time()
                        self.pipe = ov_genai.WhisperPipeline(path, dev, CACHE_DIR=str(cache))
                        self.device, self.model, self.key = dev, Path(path).name, want
                        log.info("Whisper %s ready on %s in %.1fs", self.model, dev, time.time() - t0)
                        if dev != pref:
                            self.error = f"{pref} unavailable, fell back to {dev}"
                        return self.pipe
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{dev}: {e}")
                        log.warning("Whisper on %s failed: %s", dev, e)
                self.error = "; ".join(errors)
                raise RuntimeError("Could not load Whisper - " + self.error)
            finally:
                self.loading = False

    def transcribe(self, audio: np.ndarray, override: dict | None = None) -> dict:
        c = {**self.cfg(), **(override or {})}
        if c["provider"] == "openai":
            return self._remote(audio, c)
        if c["provider"] == "gemini":
            return self._gemini(audio, c)
        if c["provider"] == "none":
            raise RuntimeError("Speech recognition is turned off in Settings")
        with self.lock:
            pipe = self.load()
            english_only = ".en" in (self.model or "")
            kw: dict = {"return_timestamps": True}
            if self.prompt_ok and c.get("filler_prompt", True):
                kw["initial_prompt"] = FILLER_PROMPT
            if not english_only:
                kw.update(language="<|en|>", task="transcribe")
            try:
                res = pipe.generate(audio, **kw)
            except Exception as e:  # noqa: BLE001
                if "initial_prompt" not in kw:
                    raise
                # NPU: static decoder shapes reject the filler prompt -> run without it from now on
                log.warning("Whisper on %s rejected the filler prompt (%s); disabling it", self.device, str(e)[:80])
                self.prompt_ok = False
                kw.pop("initial_prompt")
                res = pipe.generate(audio, **kw)
        chunks = [{"start": float(c_.start_ts), "end": float(c_.end_ts), "text": c_.text.strip()}
                  for c_ in (getattr(res, "chunks", None) or [])]
        return {"text": res.texts[0].strip(), "chunks": chunks}

    @staticmethod
    def _wav(audio: np.ndarray) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
        return buf.getvalue()

    def _gemini(self, audio: np.ndarray, c: dict) -> dict:
        from . import gemini_ai

        keys = c.get("_keys") or settings.get()["keys"]
        try:
            text = gemini_ai.transcribe(self._wav(audio), api_key=keys["gemini"], model=c["gemini_model"],
                                        keep_fillers=c.get("filler_prompt", True))
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(gemini_ai.friendly_error(e)) from e
        return {"text": text, "chunks": []}

    @staticmethod
    def _remote(audio: np.ndarray, c: dict) -> dict:
        if not c["base_url"] or not c["model"]:
            raise RuntimeError("Speech endpoint URL and model are required")
        wav = _Whisper._wav(audio)
        client = llm.openai_client(c["base_url"], c["api_key"], timeout=300)
        kw = {"model": c["model"], "file": ("take.wav", wav, "audio/wav"), "language": "en"}
        if c.get("filler_prompt", True):
            kw["prompt"] = FILLER_PROMPT
        try:
            res = client.audio.transcriptions.create(response_format="verbose_json", **kw)
        except Exception as e:  # noqa: BLE001  (some servers only support plain json)
            log.info("verbose_json not supported (%s); retrying with json", str(e)[:80])
            res = client.audio.transcriptions.create(**kw)
        segs = getattr(res, "segments", None) or []
        chunks = [{"start": float(getattr(s, "start", 0) if not isinstance(s, dict) else s.get("start", 0)),
                   "end": float(getattr(s, "end", 0) if not isinstance(s, dict) else s.get("end", 0)),
                   "text": (getattr(s, "text", "") if not isinstance(s, dict) else s.get("text", "")).strip()}
                  for s in segs]
        return {"text": (getattr(res, "text", "") or "").strip(), "chunks": chunks}


WHISPER = _Whisper()


def read_wav(data: bytes) -> np.ndarray:
    """Read 16-bit PCM WAV → float32 mono 16 kHz."""
    with wave.open(io.BytesIO(data)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError("Expected 16-bit PCM WAV")
    a = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:  # linear resample (the browser already sends 16 kHz)
        n = int(len(a) * 16000 / sr)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
    return a


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _count_fillers(text: str) -> dict:
    t = " " + " ".join(_words(text)) + " "
    out = {}
    for f in FILLERS:
        n = len(re.findall(rf"(?<= ){re.escape(f)}(?= )", t))
        if f == "like":  # only count "like" not followed by typical comparison objects
            n = len(re.findall(r"(?<= )like(?= (um|uh|you|i|we|so|the|it's|totally|really|basically) )", t))
        if n:
            out[f] = n
    return out


def _speech_energy_pauses(audio: np.ndarray, sr: int = 16000, min_pause: float = 1.2) -> list[dict]:
    """Detect long silences from audio energy (works regardless of Whisper timestamp support)."""
    win = int(sr * 0.05)
    if len(audio) < win * 4:
        return []
    frames = audio[: len(audio) // win * win].reshape(-1, win)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    thresh = max(0.008, np.percentile(rms, 30) * 1.6)
    silent = rms < thresh
    pauses, start = [], None
    # ignore leading/trailing silence
    voiced = np.where(~silent)[0]
    if not len(voiced):
        return []
    lo, hi = voiced[0], voiced[-1]
    for i in range(lo, hi + 1):
        if silent[i] and start is None:
            start = i
        elif not silent[i] and start is not None:
            dur = (i - start) * 0.05
            if dur >= min_pause:
                pauses.append({"at": round(start * 0.05, 1), "duration": round(dur, 1)})
            start = None
    return pauses


def analyze(transcript: str, audio: np.ndarray, script: str, reference_seconds: float | None) -> dict:
    duration = len(audio) / 16000
    words = _words(transcript)
    n = len(words)
    speaking_min = max(duration, 1) / 60
    wpm = round(n / speaking_min) if n else 0
    fillers = _count_fillers(transcript)
    filler_total = sum(fillers.values())
    pauses = _speech_energy_pauses(audio)
    repeats = len(re.findall(r"\b(\w+)\s+\1\b", transcript.lower()))

    coverage, missed, fidelity = None, [], None
    if script.strip():
        from .offline_tts import split_sentences

        spoken = set(words)
        key = [w for w in _words(script) if w not in STOP and len(w) > 3]
        coverage = round(100 * sum(w in spoken for w in key) / max(1, len(key)))
        for s in split_sentences(script):
            k = [w for w in _words(s) if w not in STOP and len(w) > 3]
            if len(k) >= 3 and sum(w in spoken for w in k) / len(k) < 0.4:
                missed.append(s)
        fidelity = round(100 * difflib.SequenceMatcher(None, _words(script), words, autojunk=False).ratio())

    # Composite score (heuristic, 0-100)
    pace_pen = 0 if 120 <= wpm <= 165 else min(25, abs(wpm - 142) / 2)
    filler_pen = min(25, filler_total / speaking_min * 4)
    pause_pen = min(10, max(0, len([p for p in pauses if p["duration"] > 3]) * 3))
    cov_pen = 0 if coverage is None else max(0, (85 - coverage) * 0.5)
    score = max(0, round(100 - pace_pen - filler_pen - pause_pen - cov_pen - min(5, repeats)))

    return {
        "duration": round(duration, 1),
        "reference_duration": round(reference_seconds, 1) if reference_seconds else None,
        "words": n,
        "wpm": wpm,
        "fillers": fillers,
        "filler_total": filler_total,
        "fillers_per_min": round(filler_total / speaking_min, 1),
        "long_pauses": pauses,
        "repeats": repeats,
        "coverage": coverage,
        "fidelity": fidelity,
        "missed": missed[:6],
        "vocab_richness": round(100 * len(set(words)) / max(1, n)),
        "score": score,
    }


COACH_SYSTEM = """You are an elite executive presentation coach (think TED speaker coach).
You give candid, specific, encouraging feedback on a rehearsal, grounded in the transcript and metrics provided.
Write in plain text with short sections titled exactly: STRENGTHS, IMPROVE, POLISHED LINE.
- STRENGTHS: 2-3 bullets starting with "- ".
- IMPROVE: 3-4 bullets starting with "- ", each actionable (pace, pauses, fillers, word choice, structure, missed points, confidence).
- POLISHED LINE: take the weakest sentence the speaker actually said, quote it, then give an eloquent, mature rewrite.
Be concise (under 220 words). Never invent things the speaker did not say."""


def coach(transcript: str, script: str, metrics: dict, prefs: dict) -> str | None:
    if not llm.enabled() or not transcript.strip():
        return None
    m = {k: metrics[k] for k in ("duration", "reference_duration", "wpm", "fillers", "fillers_per_min",
                                  "long_pauses", "coverage", "fidelity", "missed")}
    user = (
        f"Intended tone: {prefs.get('tone')}; format: {prefs.get('style')}; audience: {prefs.get('audience') or 'general'}.\n"
        f"Metrics: {m}\n(Healthy stage pace is 120-165 words per minute; 130-150 is ideal.)\n\n"
        f"REFERENCE SCRIPT:\n{script[:2500] or '(none)'}\n\n"
        f"WHAT THE SPEAKER ACTUALLY SAID (Whisper transcript):\n{transcript[:2500]}"
    )
    try:
        txt = llm.chat(COACH_SYSTEM, user, max_tokens=500, temperature=0.4)
        txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S)
        return re.sub(r"[*#`]+", "", txt).strip()
    except Exception as e:  # noqa: BLE001
        log.warning("Coaching failed: %s", e)
        return f"(AI coaching unavailable: {e})"
