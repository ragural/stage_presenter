"""Fully offline TTS: Kokoro (82M, ONNX) and Piper (VITS, ONNX).

Text is synthesized sentence by sentence and stitched together, so we know
exact sentence timings for teleprompter highlighting.
"""
from __future__ import annotations

import logging
import re
import threading
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
BASE = Path(__file__).resolve().parent.parent
KOKORO_DIR = BASE / "models" / "kokoro"
PIPER_DIR = BASE / "models" / "piper"

SENT_RE = re.compile(r"[^.!?…]+(?:[.!?…]+[\"')\]]*|$)")


def split_sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n\s*\n", text):
        out += [s.strip() for s in SENT_RE.findall(para.replace("\n", " ")) if s.strip()]
    return out or [text.strip()]


def _write_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _stitch(pieces: list[tuple[str, np.ndarray]], sr: int, gap: float) -> tuple[np.ndarray, list[dict]]:
    silence = np.zeros(int(sr * gap), dtype=np.float32)
    chunks, timings, t = [], [], 0.0
    for text, a in pieces:
        dur = len(a) / sr
        timings.append({"start": round(t, 3), "end": round(t + dur, 3), "text": text})
        chunks += [a.astype(np.float32), silence]
        t += dur + gap
    return (np.concatenate(chunks) if chunks else silence), timings


# ------------------------------------------------------------------ Kokoro
# Kokoro v1.0 English voices. a* = American, b* = British. h* (Hindi) voices
# speaking English give an Indian-accented delivery (experimental).
KOKORO_LOCALE = {
    "en-US": ("a", "en-us"), "en-CA": ("a", "en-us"),
    "en-GB": ("b", "en-gb"), "en-IE": ("b", "en-gb"), "en-AU": ("b", "en-gb"),
    "en-NZ": ("b", "en-gb"), "en-ZA": ("b", "en-gb"), "en-SG": ("b", "en-gb"),
    "en-IN": ("h", "en-us"),
}
KOKORO_BEST = {"af_heart", "af_bella", "am_michael", "am_fenrir", "bf_emma", "bm_george", "bm_fable"}


class Kokoro:
    def __init__(self) -> None:
        self._k = None
        self.lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        return (KOKORO_DIR / "kokoro-v1.0.onnx").exists() and (KOKORO_DIR / "voices-v1.0.bin").exists()

    def engine(self):
        if self._k is None:
            from kokoro_onnx import Kokoro as K
            self._k = K(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
        return self._k

    def voices(self) -> list[dict]:
        if not self.available():
            return []
        out = []
        names = self.engine().get_voices()
        for loc, (prefix, _) in KOKORO_LOCALE.items():
            for n in names:
                if n[0] == prefix and n[1] in "fm":
                    label = n.split("_", 1)[1].title() + (" ★" if n in KOKORO_BEST else "")
                    if prefix == "h":
                        label += " (Indian, experimental)"
                    out.append({"engine": "kokoro", "name": n, "locale": loc,
                                "gender": "Female" if n[1] == "f" else "Male", "label": label})
        return out

    def synth(self, text: str, voice: str, locale: str, rate: int) -> tuple[np.ndarray, list[dict], int]:
        lang = KOKORO_LOCALE.get(locale, ("a", "en-us"))[1]
        if voice[0] == "b":
            lang = "en-gb"
        speed = max(0.5, min(2.0, 1 + rate / 100))
        with self.lock:
            k = self.engine()
            pieces, sr = [], 24000
            for s in split_sentences(text):
                a, sr = k.create(s, voice=voice, speed=speed, lang=lang)
                pieces.append((s, a))
        audio, timings = _stitch(pieces, sr, 0.32)
        return audio, timings, sr


# ------------------------------------------------------------------ Piper
PIPER_GENDER = {"lessac": "Female", "amy": "Female", "alba": "Female", "jenny_dioco": "Female", "cori": "Female",
                "ljspeech": "Female", "kristin": "Female", "hfc_female": "Female",
                "ryan": "Male", "alan": "Male", "northern_english_male": "Male", "joe": "Male",
                "john": "Male", "kusal": "Male", "hfc_male": "Male", "bryce": "Male", "norman": "Male"}
PIPER_LOCALE_MAP = {"en_US": ["en-US", "en-CA"], "en_GB": ["en-GB", "en-IE", "en-AU", "en-NZ", "en-ZA", "en-SG", "en-IN"]}


class Piper:
    def __init__(self) -> None:
        self._voices: dict = {}
        self.lock = threading.Lock()

    def files(self) -> list[Path]:
        return sorted(PIPER_DIR.glob("*.onnx")) if PIPER_DIR.exists() else []

    def voices(self) -> list[dict]:
        out = []
        for f in self.files():
            stem = f.stem  # en_US-lessac-medium
            lang, name, *_ = stem.split("-")
            for loc in PIPER_LOCALE_MAP.get(lang, []):
                out.append({"engine": "piper", "name": stem, "locale": loc,
                            "gender": PIPER_GENDER.get(name, "Female"),
                            "label": f"{name.replace('_', ' ').title()} ({lang.split('_')[1]})"})
        return out

    def synth(self, text: str, voice: str, rate: int) -> tuple[np.ndarray, list[dict], int]:
        from piper import PiperVoice, SynthesisConfig

        path = PIPER_DIR / f"{voice}.onnx"
        if not path.exists():
            raise FileNotFoundError(f"Piper voice {voice} not installed")
        with self.lock:
            if voice not in self._voices:
                self._voices[voice] = PiperVoice.load(path)
            v = self._voices[voice]
            cfg = SynthesisConfig(length_scale=1 / max(0.5, 1 + rate / 100))
            pieces, sr = [], v.config.sample_rate
            for s in split_sentences(text):
                arr = [c.audio_int16_array for c in v.synthesize(s, cfg)]
                a = (np.concatenate(arr) if arr else np.zeros(1, np.int16)).astype(np.float32) / 32768
                pieces.append((s, a))
        audio, timings = _stitch(pieces, sr, 0.3)
        return audio, timings, sr


KOKORO = Kokoro()
PIPER = Piper()


def synthesize(engine: str, text: str, voice: str, locale: str, rate: int, out: Path) -> list[dict]:
    if engine == "kokoro":
        audio, timings, sr = KOKORO.synth(text, voice, locale, rate)
    elif engine == "piper":
        audio, timings, sr = PIPER.synth(text, voice, rate)
    else:
        raise ValueError(engine)
    _write_wav(out, audio, sr)
    return timings
