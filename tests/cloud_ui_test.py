"""Browser test of the Claude / Gemini tabs in ⚙ Settings (app on :8012 wired to tests/mock_cloud.py)."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
OUT = Path(__file__).resolve().parent.parent / "data" / "uitest"
URL = "http://127.0.0.1:8012"
ANT, GEM = "sk-ant-test-123456789", "AIza-test-123456789"
BED = "bedrock-api-key-test-123456789"
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def wait_test(page, sec):
    page.wait_for_function(f"/^[✓✗]/.test(document.querySelector('[data-sec={sec}] .test-out').textContent)", timeout=60_000)
    return page.inner_text(f"[data-sec={sec}] .test-out")


with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True, args=["--proxy-bypass-list=127.0.0.1;localhost"])
    page = b.new_page(viewport={"width": 1500, "height": 980})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and errors.append(m.text))
    page.goto(URL)
    page.wait_for_selector("#openSettings")
    page.request.put(f"{URL}/api/settings", data={"llm": {"provider": "openvino", "claude_model": "claude-opus-5-5", "claude_effort": "medium",
                                                          "claude_access": "anthropic", "bedrock_region": "", "bedrock_model": ""},
                                                  "stt": {"provider": "openvino"},
                                                  "keys": {"anthropic": "__clear__", "gemini": "__clear__", "bedrock": "__clear__"}})
    llm, stt = "[data-sec=llm]", "[data-sec=stt]"
    page.click("#openSettings")
    page.wait_for_selector("#settingsModal:not(.hidden)")

    # STT: Claude must be disabled with an explanation
    btn = page.locator(f"{stt} .prov button[data-p=anthropic]")
    check("Claude disabled for speech", btn.is_disabled() and "audio" in (btn.get_attribute("title") or ""), btn.get_attribute("title"))

    # LLM -> Claude
    page.click(f"{llm} .prov button[data-p=anthropic]")
    check("Claude panel shown", page.locator(f"{llm} .p-anthropic").is_visible() and not page.locator(f"{llm} .p-openai").is_visible())
    check("Access defaults to Anthropic API", page.locator(f"{llm} .ca-anthropic").is_visible()
          and not page.locator(f"{llm} .ca-bedrock").is_visible())
    check("Key marked required", page.inner_text(f"{llm} [data-kh=anthropic]") == "required")
    check("Default model + effort", page.input_value(f"{llm} [data-f=claude_model]") == "claude-opus-5-5"
          and page.input_value(f"{llm} [data-f=claude_effort]") == "medium")
    page.fill(f"{llm} [data-k=anthropic]", ANT)
    page.fill(f"{llm} [data-f=claude_model]", "")
    page.click(f"{llm} .p-anthropic [data-act=list]")
    page.wait_for_function("document.querySelector('[data-sec=llm] .test-out').textContent.includes('models available')", timeout=30_000)
    check("Claude ↻ List fills model", page.input_value(f"{llm} [data-f=claude_model]") == "claude-opus-5-5")
    page.select_option(f"{llm} [data-f=claude_effort]", "low")
    page.click(f"{llm} [data-act=test]")
    out = wait_test(page, "llm")
    check("Claude ▶ Test (unsaved key)", out.startswith("✓") and "[claude:claude-opus-5-5:low]" in out, out.replace("\n", " ")[:90])
    page.screenshot(path=str(OUT / "8_settings_claude.png"))

    # Claude through Amazon Bedrock
    page.select_option(f"{llm} [data-f=claude_access]", "bedrock")
    check("Bedrock fields shown", page.locator(f"{llm} .ca-bedrock").is_visible() and not page.locator(f"{llm} .ca-anthropic").is_visible())
    page.fill(f"{llm} [data-f=bedrock_region]", "eu-west-1")
    check("Model placeholder follows the region", page.get_attribute(f"{llm} [data-f=bedrock_model]", "placeholder") == "eu.anthropic.claude-opus-5-5")
    page.fill(f"{llm} [data-f=bedrock_region]", "us-east-2")
    page.fill(f"{llm} [data-k=bedrock]", BED)
    page.click(f"{llm} .ca-bedrock [data-act=list]")
    page.wait_for_function("document.querySelector('[data-sec=llm] .test-out').textContent.includes('Common ids')", timeout=30_000)
    check("Bedrock ↻ List fills model", page.input_value(f"{llm} [data-f=bedrock_model]") == "us.anthropic.claude-opus-5-5")
    page.click(f"{llm} [data-act=test]")
    out = wait_test(page, "llm")
    check("Bedrock ▶ Test (unsaved key)", out.startswith("✓") and "Claude · Bedrock" in out
          and "[claude:us.anthropic.claude-opus-5-5:low]" in out, out.replace("\n", " ")[:110])
    page.screenshot(path=str(OUT / "8b_settings_claude_bedrock.png"))
    page.select_option(f"{llm} [data-f=claude_access]", "anthropic")

    # STT -> Gemini; key typed in the speech card mirrors to the LLM card
    page.click(f"{stt} .prov button[data-p=gemini]")
    page.fill(f"{stt} [data-k=gemini]", GEM)
    check("Shared Gemini key mirrored", page.input_value(f"{llm} [data-k=gemini]") == GEM)
    page.click(f"{stt} [data-act=test]")
    out = wait_test(page, "stt")
    check("Gemini speech ▶ Test", out.startswith("✓") and "mock Gemini heard" in out, out.replace("\n", " | ")[:110])
    page.screenshot(path=str(OUT / "9_settings_claude_gemini.png"))

    page.click("#saveSettings")
    page.wait_for_function("document.getElementById('llmStatus').textContent.includes('Claude')", timeout=10_000)
    check("Pills after save", "Gemini" in page.inner_text("#sttStatus"),
          page.inner_text("#llmStatus") + " | " + page.inner_text("#sttStatus"))

    page.click("#openSettings")
    page.wait_for_selector("#settingsModal:not(.hidden)")
    check("Keys masked on reopen", "6789" in page.inner_text(f"{llm} [data-kh=anthropic]")
          and "6789" in page.inner_text(f"{stt} [data-kh=gemini]") and page.input_value(f"{llm} [data-k=anthropic]") == "")
    check("Providers persisted", page.locator(f"{llm} .prov button.on").get_attribute("data-p") == "anthropic"
          and page.locator(f"{stt} .prov button.on").get_attribute("data-p") == "gemini")
    check("Effort persisted", page.input_value(f"{llm} [data-f=claude_effort]") == "low")

    # LLM -> Gemini works with the shared key
    page.click(f"{llm} .prov button[data-p=gemini]")
    page.click(f"{llm} [data-act=test]")
    out = wait_test(page, "llm")
    check("Gemini LLM ▶ Test via shared saved key", out.startswith("✓") and "[gemini:" in out, out.replace("\n", " ")[:90])
    page.click("#cancelSettings")
    check("No JS errors", not errors, "; ".join(errors)[:300])
    b.close()

print(f"\n{sum(res)}/{len(res)} checks passed")
sys.exit(0 if all(res) else 1)
