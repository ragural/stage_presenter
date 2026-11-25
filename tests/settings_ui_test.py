"""Browser test of the ⚙ Settings dialog. Needs the app on :8000 and tests/mock_openai.py on :9011."""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
OUT = Path(__file__).resolve().parent.parent / "data" / "uitest"
URL = "http://127.0.0.1:8000"
MOCK = "http://127.0.0.1:9011/v1"
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True, args=["--proxy-bypass-list=127.0.0.1;localhost"])
    page = b.new_page(viewport={"width": 1500, "height": 950})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.goto(URL)
    page.wait_for_function("document.getElementById('llmStatus').classList.contains('ok')", timeout=300_000)
    llm, stt = "[data-sec=llm]", "[data-sec=stt]"

    page.click("#openSettings")
    page.wait_for_selector("#settingsModal:not(.hidden)")
    page.wait_for_function("document.querySelector('[data-sec=llm] .set-status').textContent.includes('Ready')", timeout=60_000)
    devs = page.eval_on_selector(f"{llm} [data-f=ov_device]", "s => [...s.options].map(o => o.text)")
    check("Device list with full names", len(devs) == 3 and "AI Boost" in devs[0], " | ".join(devs))
    check("Installed model listed", page.input_value(f"{llm} [data-f=ov_model]") == "Qwen3-8B-int4-cw-ov")
    check("Status shown in card", "Ready" in page.inner_text(f"{llm} .set-status"), page.inner_text(f"{llm} .set-status"))
    page.screenshot(path=str(OUT / "5_settings_local.png"))

    # test local LLM from the dialog
    page.click(f"{llm} [data-act=test]")
    page.wait_for_function("/^[✓✗]/.test(document.querySelector('[data-sec=llm] .test-out').textContent)", timeout=120_000)
    out = page.inner_text(f"{llm} .test-out")
    check("Test local LLM button", out.startswith("✓"), out.replace("\n", " ")[:110])
    page.click(f"{stt} [data-act=test]")
    page.wait_for_function("/^[✓✗]/.test(document.querySelector('[data-sec=stt] .test-out').textContent)", timeout=120_000)
    out = page.inner_text(f"{stt} .test-out")
    check("Test local Whisper button", out.startswith("✓"), out.replace("\n", " | ")[:140])

    # switch LLM to an endpoint using a preset, then point it at the mock
    page.click(f"{llm} .prov button[data-p=openai]")
    page.select_option(f"{llm} [data-f=_preset]", "ollama")
    check("Preset fills URL", page.input_value(f"{llm} [data-f=base_url]") == "http://localhost:11434/v1")
    page.fill(f"{llm} [data-f=base_url]", MOCK)
    page.fill(f"{llm} [data-f=api_key]", "sk-test-123456789")
    page.fill(f"{llm} [data-f=model]", "")
    page.click(f"{llm} .p-openai [data-act=list]")
    page.wait_for_function("document.querySelector('[data-sec=llm] .test-out').textContent.includes('models available')", timeout=30_000)
    check("List models fills model", page.input_value(f"{llm} [data-f=model]") == "mock-llm-1")
    page.click(f"{llm} [data-act=test]")
    page.wait_for_function("/^[✓✗]/.test(document.querySelector('[data-sec=llm] .test-out').textContent)", timeout=30_000)
    out = page.inner_text(f"{llm} .test-out")
    check("Test endpoint (unsaved)", out.startswith("✓") and "mock" in out, out.replace("\n", " ")[:100])
    page.screenshot(path=str(OUT / "6_settings_endpoint.png"))
    page.click("#saveSettings")
    page.wait_for_selector("#settingsModal.hidden", state="attached")
    page.wait_for_function("document.getElementById('llmStatus').textContent.includes('endpoint')", timeout=10_000)
    check("Pill shows endpoint after save", True, page.inner_text("#llmStatus"))

    # reopen: key masked, values persisted
    page.click("#openSettings")
    page.wait_for_selector("#settingsModal:not(.hidden)")
    hint = page.inner_text(f"{llm} [data-kh=_section]")
    check("Key masked on reopen", "6789" in hint and page.input_value(f"{llm} [data-f=api_key]") == "", hint)
    check("Endpoint persisted", page.input_value(f"{llm} [data-f=base_url]") == MOCK)

    # space must not start the presentation while the dialog is open
    page.keyboard.press("Space")
    time.sleep(0.5)
    check("Shortcuts blocked in dialog", page.evaluate("S.state") == "idle")

    # back to local NPU, Whisper to CPU then GPU
    page.click(f"{llm} .p-openai [data-act=clearkey]")
    page.click(f"{llm} .prov button[data-p=openvino]")
    page.select_option(f"{llm} [data-f=ov_device]", "NPU")
    page.select_option(f"{stt} [data-f=ov_device]", "CPU")
    page.click("#saveSettings")
    page.wait_for_function("document.getElementById('sttStatus').textContent.includes('CPU')", timeout=120_000)
    check("Whisper moved to CPU via UI", True, page.inner_text("#sttStatus"))
    page.wait_for_function("document.getElementById('llmStatus').textContent.includes('NPU') && document.getElementById('llmStatus').classList.contains('ok')", timeout=300_000)
    check("LLM back on local NPU", True, page.inner_text("#llmStatus"))
    page.click("#openSettings")
    page.wait_for_selector("#settingsModal:not(.hidden)")
    check("Key cleared", page.inner_text(f"{llm} [data-kh=_section]") == "")
    page.select_option(f"{stt} [data-f=ov_device]", "GPU")
    page.click("#saveSettings")
    page.wait_for_function("document.getElementById('sttStatus').textContent.includes('GPU')", timeout=120_000)
    check("Whisper back on GPU", True, page.inner_text("#sttStatus"))
    page.screenshot(path=str(OUT / "7_main_after_settings.png"))
    check("No JS errors", not errors, "; ".join(errors)[:300])
    b.close()

print(f"\n{sum(res)}/{len(res)} checks passed")
sys.exit(0 if all(res) else 1)
