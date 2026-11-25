"""API test for model-endpoint settings. Needs the app on :8000 and tests/mock_openai.py on :9011."""
import json
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")
U = "http://127.0.0.1:8000"
MOCK = "http://127.0.0.1:9011/v1"
c = httpx.Client(base_url=U, timeout=400, trust_env=False)
res = []


def check(name, ok, detail=""):
    res.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def wait_ready(sec, device=None, timeout=400):
    t = time.time()
    while time.time() - t < timeout:
        st = c.get("/api/settings").json()["status"][sec]
        if st.get("loaded") and (device is None or st.get("device") == device):
            return st, time.time() - t
        if st.get("error") and not st.get("loading"):
            return st, time.time() - t
        time.sleep(2)
    return st, time.time() - t


orig = c.get("/api/settings").json()
check("GET settings", orig["settings"]["llm"]["provider"] == "openvino",
      f"devices={[d['id'] for d in orig['devices']]} llm={orig['local_models']['llm']} whisper={orig['local_models']['whisper']}")
wait_ready("llm")

# --- local LLM device switch NPU -> GPU -> NPU
c.put("/api/settings", json={"llm": {"provider": "openvino", "ov_device": "GPU"}})
st, dt = wait_ready("llm", "GPU")
check("LLM switched to GPU", st["device"] == "GPU" and st["loaded"], f"{dt:.0f}s {st.get('error') or ''}")
r = c.post("/api/settings/test-llm", json={}).json()
check("LLM test on GPU", r.get("ok") and r["device"] == "GPU", f"{r.get('seconds')}s: {r.get('reply', r)}")
c.put("/api/settings", json={"llm": {"ov_device": "NPU"}})
st, dt = wait_ready("llm", "NPU")
check("LLM back on NPU", st["device"] == "NPU" and st["loaded"], f"{dt:.0f}s")
r = c.post("/api/settings/test-llm", json={}).json()
check("LLM test on NPU", r.get("ok") and r["device"] == "NPU", f"{r.get('seconds')}s: {r.get('reply', r)}")

# --- local Whisper device switch GPU -> CPU -> GPU
c.put("/api/settings", json={"stt": {"provider": "openvino", "ov_device": "CPU"}})
st, dt = wait_ready("stt", "CPU")
r = c.post("/api/settings/test-stt", json={}).json()
check("Whisper on CPU test", r.get("ok") and r["device"] == "CPU", f"{r.get('seconds')}s heard: {r.get('heard', r)}")
c.put("/api/settings", json={"stt": {"ov_device": "GPU"}})
st, dt = wait_ready("stt", "GPU")
check("Whisper back on GPU", st["device"] == "GPU", f"{dt:.0f}s")

# --- remote endpoints via mock
r = c.post("/api/settings/list-models", json={"section": "llm", "base_url": MOCK, "api_key": "wrong"})
check("Bad API key rejected", r.status_code == 502 and "401" in r.text, r.text[:90])
r = c.post("/api/settings/list-models", json={"section": "llm", "base_url": MOCK, "api_key": "sk-test-123456789"}).json()
check("List remote models", "mock-llm-1" in r.get("models", []), str(r))
r = c.post("/api/settings/test-llm", json={"provider": "openai", "base_url": MOCK, "api_key": "sk-test-123456789", "model": "mock-llm-1"}).json()
check("Test unsaved remote LLM", "[mock:mock-llm-1]" in r.get("reply", ""), str(r)[:120])
c.put("/api/settings", json={
    "llm": {"provider": "openai", "base_url": MOCK, "api_key": "sk-test-123456789", "model": "mock-llm-1"},
    "stt": {"provider": "openai", "base_url": MOCK, "api_key": "sk-test-123456789", "model": "mock-whisper"}})
s = c.get("/api/settings").json()
raw = json.dumps(s)
check("API key masked in GET", "sk-test-123456789" not in raw and s["settings"]["llm"]["api_key_hint"] == "••••6789", s["settings"]["llm"]["api_key_hint"])
cfg = c.get("/api/config").json()
check("Status shows endpoints", cfg["llm"]["device"] == "remote" and cfg["stt"]["device"] == "remote", f"{cfg['llm']['model']} / {cfg['stt']['model']}")
time.sleep(1)
check("Local LLM unloaded after switch", c.get("/api/settings").json()["status"]["llm"]["provider"] == "openai")

with open("samples/sample_deck.pptx", "rb") as f:  # a throwaway deck: never write test scripts into a real one
    deck = c.post("/api/upload", files={"file": ("sample_deck.pptx", f)}).json()["id"]
r = c.post(f"/api/decks/{deck}/script", json={"slide": 1, "tone": "executive", "style": "keynote", "length": "concise", "locale": "en-US"}).json()
check("Script via remote endpoint", r.get("script", "").startswith("[mock:mock-llm-1]"), str(r.get("script", r))[:80])
r = c.post("/api/settings/test-stt", json={}).json()
check("Remote transcription", "mock transcript" in r.get("heard", ""), r.get("heard", str(r))[:80])
with open("data/uitest/take.wav", "rb") as f:
    r = c.post("/api/rehearse", files={"audio": ("t.wav", f, "audio/wav")}, data={"deck_id": deck, "slide": "2", "coach": "true"}).json()
check("Rehearse via remote STT + LLM", r.get("stt_device") == "remote" and "[mock" in (r.get("coaching") or ""), f"{r.get('transcript', r)}"[:90])

# keep key when blank, clear when asked
c.put("/api/settings", json={"llm": {"api_key": ""}})
check("Blank key keeps saved key", c.get("/api/settings").json()["settings"]["llm"]["api_key_set"])
c.put("/api/settings", json={"llm": {"api_key": "__clear__"}})
check("Clear key", not c.get("/api/settings").json()["settings"]["llm"]["api_key_set"])

# validation
r = c.put("/api/settings", json={"llm": {"extra_body": "{not json"}})
check("Bad JSON rejected", r.status_code == 400, r.text[:80])
r = c.put("/api/settings", json={"stt": {"provider": "bogus"}})
check("Bad provider rejected", r.status_code == 400, r.text[:80])

# --- off
c.put("/api/settings", json={"llm": {"provider": "none"}, "stt": {"provider": "none"}})
cfg = c.get("/api/config").json()
check("Providers off", not cfg["llm"]["enabled"] and not cfg["stt"]["available"])
r = c.post(f"/api/decks/{deck}/script", json={"slide": 1}).json()
check("Off -> local polish fallback", r.get("engine") == "local", str(r.get("script", r))[:60])

# --- restore local defaults
c.put("/api/settings", json={"llm": {"provider": "openvino", "ov_device": "NPU", "base_url": "", "model": ""},
                             "stt": {"provider": "openvino", "ov_device": "GPU", "base_url": "", "model": "whisper-1", "api_key": "__clear__"}})
st1, _ = wait_ready("llm", "NPU")
st2, _ = wait_ready("stt", "GPU")
check("Restored NPU LLM + GPU Whisper", st1["loaded"] and st2["loaded"], f"{st1['device']} / {st2['device']}")
c.delete(f"/api/decks/{deck}")
print(f"\n{sum(res)}/{len(res)} checks passed")
sys.exit(0 if all(res) else 1)
