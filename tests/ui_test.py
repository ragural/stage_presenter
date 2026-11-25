"""Browser end-to-end test of Stage Presenter, driven through installed Microsoft Edge.

Run with an env that has playwright:  python tests/ui_test.py [base_url]
Uses a fake microphone that plays a WAV file, so the rehearsal flow is tested for real.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "uitest"
OUT.mkdir(parents=True, exist_ok=True)
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
TAKE = OUT / "take.wav"
APP_PY = ROOT / ".venv" / "Scripts" / "python.exe"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def make_take(script: str):
    """Synthesize a 'human' rehearsal with Piper: the script with fillers and a skipped sentence."""
    code = r'''
import sys, json, wave, numpy as np
sys.path.insert(0, r"%s")
from presenter import offline_tts
s = json.loads(sys.argv[1])
sents = offline_tts.split_sentences(s)
said = []
for i, x in enumerate(sents):
    if i == len(sents) - 2:      # skip one sentence -> should be reported as missed
        continue
    said.append(("Um, " if i %% 2 == 0 else "Uh, so, ") + x)
audio, _, sr = offline_tts.PIPER.synth(" ".join(said), "en_GB-alan-medium", 0)
n = int(len(audio) * 16000 / sr)
a = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
pad = np.zeros(8000)
a = np.concatenate([pad, a, pad])
with wave.open(sys.argv[2], "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())
print(len(a) / 16000)
''' % ROOT
    r = subprocess.run([str(APP_PY), "-c", code, json.dumps(script), str(TAKE)],
                       capture_output=True, text=True, cwd=ROOT)
    if r.returncode:
        raise RuntimeError(r.stderr[-800:])
    return float(r.stdout.strip().splitlines()[-1])


# placeholder so Edge can open the fake-mic file at launch; rewritten before recording
if not TAKE.exists():
    import wave
    with wave.open(str(TAKE), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000)

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True, args=[
        "--autoplay-policy=no-user-gesture-required",
        "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={TAKE}%noloop",
        "--proxy-bypass-list=127.0.0.1;localhost",
    ])
    ctx = browser.new_context(viewport={"width": 1600, "height": 950}, permissions=["microphone", "clipboard-read", "clipboard-write"])
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))

    # 1. load + status
    page.goto(URL)
    page.wait_for_function("document.getElementById('llmStatus').classList.contains('ok')", timeout=300_000)
    page.wait_for_function("document.getElementById('sttStatus').classList.contains('ok')", timeout=120_000)
    check("Status pills green", True, page.inner_text("#llmStatus") + " | " + page.inner_text("#sttStatus"))
    page.wait_for_function("document.getElementById('voice').options.length > 0")

    # 2. delivery + voice prefs: executive boardroom, Indian English, male
    page.select_option("#tone", "executive")
    page.select_option("#style", "boardroom")
    page.select_option("#length", "concise")
    page.fill("#audience", "Senior leadership team")
    page.select_option("#engine", "edge")
    page.select_option("#locale", "en-IN")
    page.click("#genderSeg button[data-g=Male]")
    voice = page.input_value("#voice")
    check("en-IN male Edge voice selected", "en-IN" in voice and "Prabhat" in voice, voice)

    # 3. upload
    t = time.time()
    page.set_input_files("#fileInput", str(ROOT / "samples" / "sample_deck.pptx"))
    page.wait_for_selector("#stageArea:not(.hidden)", timeout=120_000)
    page.wait_for_function("document.getElementById('slideImg').naturalWidth > 0", timeout=30_000)
    n_thumbs = page.locator(".thumb").count()
    check("Upload + PowerPoint render", n_thumbs == 3, f"{n_thumbs} slides in {time.time() - t:.1f}s")
    page.screenshot(path=str(OUT / "1_uploaded.png"))

    # 4. generate full script
    t = time.time()
    page.click("#genAll")
    page.wait_for_function("document.getElementById('genAll').textContent.includes('Generate full script') && !document.getElementById('genAll').disabled", timeout=300_000)
    dots = page.locator(".thumb .dot").count()
    tp = page.inner_text("#teleprompter")
    check("Full script generated (NPU LLM)", dots == 3 and len(tp) > 100, f"{time.time() - t:.1f}s; slide 1: {tp[:140]}…")

    # 5. copy full script
    page.click("#copyAll")
    clip = page.evaluate("navigator.clipboard.readText()")
    check("Copy full script", "Slide 3" in clip, f"{len(clip)} chars on clipboard")

    # 6. present: play, sentence highlight, captions, auto-advance
    page.click("#thumbs .thumb >> nth=0")
    page.evaluate("document.getElementById('gap').value = 0")
    page.click("#play")
    page.wait_for_selector("#teleprompter .s.now", timeout=60_000)
    time.sleep(2)
    cur = page.evaluate("document.getElementById('audio').currentTime")
    cap = page.inner_text("#caption")
    check("Narration playing with sentence highlight", cur > 0.5, f"t={cur:.1f}s caption='{cap[:60]}'")
    page.screenshot(path=str(OUT / "2_presenting.png"))
    page.wait_for_function("document.getElementById('slideCounter').textContent.startsWith('2 /')", timeout=180_000)
    check("Auto-advance to slide 2", True, page.inner_text("#slideCounter"))
    page.click("#stop")
    check("Stop", page.inner_text("#play") == "▶")

    # 7. offline voices: Kokoro and Piper
    for eng in ("kokoro", "piper"):
        page.select_option("#engine", eng)
        page.select_option("#locale", "en-GB")
        page.click("#genderSeg button[data-g=Female]")
        v = page.input_value("#voice")
        page.click("#previewVoice")
        page.wait_for_function("document.getElementById('audio').src.endsWith('.wav') && document.getElementById('audio').currentTime > 0.3", timeout=90_000)
        check(f"Offline {eng} voice plays", True, v)
    page.click("#stop")

    # 8. edit script
    page.click("#editToggle")
    page.fill("#scriptEditor", page.input_value("#scriptEditor") + " Thank you.")
    page.click("#editToggle")
    page.wait_for_function("document.getElementById('teleprompter').innerText.includes('Thank you.')", timeout=10_000)
    saved = page.request.get(f"{URL}/api/decks/" + page.evaluate("S.deck.id")).json()
    check("Edit + save script", any("Thank you." in (x["script"] or "") for x in saved["slides"]), "persisted on server")

    # 9. rehearsal with fake mic (Piper reading slide 2 with fillers and a skipped sentence)
    deck_id = page.evaluate("S.deck.id")
    script2 = page.evaluate("S.deck.slides[1].script")
    secs = make_take(script2)
    page.click("#thumbs .thumb >> nth=1")
    page.click(".tabs button[data-tab=rehearseTab]")
    page.click("#recBtn")
    page.wait_for_function("S.recording === true", timeout=10_000)
    time.sleep(secs + 1.0)
    page.click("#recBtn")
    t = time.time()
    page.wait_for_selector("#feedback .score", timeout=240_000)
    m = page.evaluate("JSON.parse(localStorage.getItem('stageHist:' + S.deck.id))['1'].at(-1)")
    fb = page.inner_text("#feedback")
    check("Rehearsal analysed (Whisper + coach)", m["wpm"] > 60, f"{time.time() - t:.1f}s; take {secs:.0f}s; {m}")
    check("Fillers detected", m["fillers"] >= 2, f"{m['fillers']} fillers")
    check("Skipped point reported", "POINTS YOU MISSED" in fb.upper(), page.inner_text("#feedback .missed") if page.locator("#feedback .missed").count() else "")
    check("AI coaching returned", "STRENGTHS" in fb.upper() and "IMPROVE" in fb.upper())
    page.locator("#feedback").screenshot(path=str(OUT / "3_feedback.png"))
    page.screenshot(path=str(OUT / "4_rehearse_full.png"))

    # 10. export endpoint
    r = page.request.get(f"{URL}/api/decks/{deck_id}/export.md")
    check("Export .md", r.ok and "## Slide 2" in r.text(), f"{len(r.text())} chars")

    check("No JS errors", not errors, "; ".join(errors)[:400])
    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
