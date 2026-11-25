"""Benchmark Whisper and Kokoro across Intel devices. Usage: python bench_devices.py [whisper|kokoro]"""
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
what = sys.argv[1] if len(sys.argv) > 1 else "whisper"

if what == "whisper":
    import openvino_genai as g
    from presenter import rehearsal

    with wave.open(str(BASE / "data/smoke/kokoro.wav")) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    n = int(len(a) * 16000 / sr)
    pcm = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
    path = rehearsal.WHISPER.model_dir()
    for dev in sys.argv[2:] or ["NPU", "GPU", "CPU"]:
        t = time.time()
        p = g.WhisperPipeline(path, dev, CACHE_DIR=str(BASE / "models/cache"))
        load = time.time() - t
        for ts in (False, True):
            t = time.time()
            try:
                r = p.generate(pcm, return_timestamps=ts, initial_prompt=rehearsal.FILLER_PROMPT)
                print(f"{dev}: load {load:.1f}s | ts={ts} transcribe {time.time() - t:.2f}s | chunks={len(r.chunks or [])} | {r.texts[0][:60]}")
            except Exception as e:  # noqa: BLE001
                print(f"{dev}: ts={ts} FAILED {str(e)[:120]}")
        del p
else:
    from presenter import offline_tts

    text = "Imagine a world where your data does not have to travel far to make smart decisions. That is the power of edge AI."
    t = time.time()
    offline_tts.KOKORO.synth(text, "af_heart", "en-US", 0)
    print(f"onnxruntime CPU warm-up {time.time() - t:.2f}s")
    t = time.time()
    _, tm, _ = offline_tts.KOKORO.synth(text, "af_heart", "en-US", 0)
    print(f"onnxruntime CPU: {time.time() - t:.2f}s for {tm[-1]['end']:.1f}s audio")
