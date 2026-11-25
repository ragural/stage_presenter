"""End-to-end check of the offline stack: NPU LLM -> Kokoro/Piper TTS -> NPU Whisper -> feedback."""
import io
import sys
import time
import wave
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path(__file__).parent
load_dotenv(BASE / ".env")
sys.path.insert(0, str(BASE))
from presenter import llm, offline_tts, rehearsal, script_writer  # noqa: E402

out = BASE / "data" / "smoke"
out.mkdir(parents=True, exist_ok=True)

slide = {"index": 2, "title": "Why Edge AI Matters",
         "bullets": ["Latency under 10 ms", "Data stays on device", "40% lower cloud cost"],
         "notes": "edge ai - run models locally. latency, privacy, cost. customers saw 40% lower cloud bill."}
prefs = {"tone": "inspirational", "style": "keynote", "length": "concise", "locale": "en-IN"}

print("LLM provider:", llm.provider(), "| model dir:", llm.ov_model_dir())
t = time.time()
llm.OV.load()
print(f"LLM loaded on {llm.OV.device} in {time.time() - t:.1f}s")
t = time.time()
script, engine = script_writer.write_script(slide, 5, prefs, prev_title="Introduction")
print(f"[{engine}] script in {time.time() - t:.1f}s:\n{script}\n")

for eng, voice in (("kokoro", "af_heart"), ("piper", "en_GB-alan-medium")):
    t = time.time()
    timings = offline_tts.synthesize(eng, script, voice, "en-US", 0, out / f"{eng}.wav")
    print(f"{eng}: {len(timings)} sentences, {timings[-1]['end']:.1f}s audio in {time.time() - t:.1f}s")

# Feed Kokoro's narration through Whisper as if it were a rehearsal
with wave.open(str(out / "kokoro.wav")) as w:
    sr = w.getframerate()
    a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
buf = io.BytesIO()
with wave.open(buf, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(a.tobytes())
pcm = rehearsal.read_wav(buf.getvalue())
t = time.time()
tr = rehearsal.WHISPER.transcribe(pcm)
print(f"\nWhisper on {rehearsal.WHISPER.device} in {time.time() - t:.1f}s: {tr['text']}")
m = rehearsal.analyze(tr["text"], pcm, script, None)
print("metrics:", {k: m[k] for k in ("wpm", "filler_total", "coverage", "fidelity", "score", "long_pauses")})
t = time.time()
print("\ncoach:", rehearsal.coach(tr["text"] + " um, so, uh, yeah.", script, m, prefs))
print(f"(coach {time.time() - t:.1f}s)")
