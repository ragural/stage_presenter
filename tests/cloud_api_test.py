"""Claude (Anthropic API + Amazon Bedrock) + Gemini provider test through the real SDKs, against tests/mock_cloud.py.

Needs: mock on :9012, and an app instance on :8012 started with
  STAGE_SETTINGS_FILE=data/settings_test.json ANTHROPIC_BASE_URL=http://127.0.0.1:9012
  ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:9012/bedrock
  GEMINI_BASE_URL=http://127.0.0.1:9012/gemini NO_PROXY=127.0.0.1 PRELOAD_MODELS=0 PORT=8012
"""
import json
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")
c = httpx.Client(base_url="http://127.0.0.1:8012", timeout=120, trust_env=False)
mock = httpx.Client(base_url="http://127.0.0.1:9012", timeout=10, trust_env=False)
ANT, GEM = "sk-ant-test-123456789", "AIza-test-123456789"
BED = "bedrock-api-key-test-123456789"
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


for _ in range(60):
    try:
        c.get("/api/config"); break
    except Exception:
        time.sleep(1)

# ---- Claude without a key -> clear status (start clean: no saved keys)
c.put("/api/settings", json={"llm": {"provider": "anthropic", "claude_access": "anthropic"},
                             "keys": {"anthropic": "__clear__", "gemini": "__clear__", "bedrock": "__clear__"}})
st = c.get("/api/config").json()["llm"]
check("Claude without key reports missing key", not st["enabled"] and "API key" in (st["error"] or ""), st["error"])

r = c.post("/api/settings/test-llm", json={"provider": "anthropic"})
check("Claude test without key -> points to Bedrock access", r.status_code == 502 and "Amazon Bedrock" in r.text, r.json().get("detail"))
r = c.put("/api/settings", json={"keys": {"anthropic": "ABSKQmVkcm9ja0FQSUtleS10ZXN0"}})
check("Bedrock key refused in the Anthropic field", r.status_code == 400 and "Bedrock API key" in r.text, r.json().get("detail"))

# ---- wrong key -> friendly 401 message
r = c.post("/api/settings/test-llm", json={"provider": "anthropic", "keys": {"anthropic": "sk-wrong"}})
check("Claude wrong key -> friendly error", r.status_code == 502 and "invalid API key" in r.text, r.json().get("detail"))

# ---- list models with an unsaved key
r = c.post("/api/settings/list-models", json={"section": "llm", "provider": "anthropic", "keys": {"anthropic": ANT}}).json()
check("Claude list models", "claude-opus-5-5" in r.get("models", []), r)

# ---- save Claude, effort high
c.put("/api/settings", json={"llm": {"provider": "anthropic", "claude_model": "claude-opus-5-5", "claude_effort": "high"},
                             "keys": {"anthropic": ANT}})
s = c.get("/api/settings").json()
check("Anthropic key masked", ANT not in json.dumps(s) and s["settings"]["keys"]["anthropic"]["hint"] == "••••6789",
      s["settings"]["keys"]["anthropic"])
st = c.get("/api/config").json()["llm"]
check("Status: Claude ready", st["enabled"] and st["device"] == "Claude API" and st["model"] == "claude-opus-5-5", st)

r = c.post("/api/settings/test-llm", json={}).json()
check("Claude test call", r.get("reply", "").startswith("[claude:claude-opus-5-5:high]"), r)
w = mock.get("/_last").json()["anthropic"]
b = w["body"]
check("Wire: effort in output_config", b.get("output_config") == {"effort": "high"}, b.get("output_config"))
check("Wire: refusal fallbacks='default' + beta header",
      b.get("fallbacks") == "default" and "server-side-fallback-2026-07-01" in w["headers"].get("anthropic-beta", ""),
      f"fallbacks={b.get('fallbacks')} beta={w['headers'].get('anthropic-beta')}")
check("Wire: no temperature/top_p, system prompt sent", not ({"temperature", "top_p", "top_k"} & set(b)) and b.get("system"),
      f"keys={sorted(b)}")
check("Wire: thinking block skipped, text returned", "thinking" not in r.get("reply", ""))

with open("samples/sample_deck.pptx", "rb") as f:  # a throwaway deck: never write test scripts into a real one
    deck = c.post("/api/upload", files={"file": ("sample_deck.pptx", f)}).json()["id"]
r = c.post(f"/api/decks/{deck}/script", json={"slide": 2, "tone": "inspirational", "style": "ted", "length": "concise", "locale": "en-IN"}).json()
check("Script via Claude", r.get("engine") == "llm" and "[claude:" in r.get("script", ""), str(r)[:110])
sysmsg = mock.get("/_last").json()["anthropic"]["body"]["system"]
check("Speechwriter prompt reached Claude", "speechwriter" in sysmsg, sysmsg[:60])

# ---- refusal handling (the user prompt contains REFUSE -> mock refuses)
r = c.post("/api/settings/test-llm", json={})  # baseline ok
c.put("/api/settings", json={"llm": {"claude_effort": "low"}})
from_refusal = c.post(f"/api/decks/{deck}/script", json={"slide": 1, "instructions": "REFUSE"})
check("Refusal surfaced clearly", from_refusal.status_code == 502 and "declined" in from_refusal.text, from_refusal.json().get("detail"))

# ---- Claude cannot be the speech provider
r = c.put("/api/settings", json={"stt": {"provider": "anthropic"}})
check("Claude rejected for speech", r.status_code == 400 and "audio" in r.text, r.json().get("detail", "")[:90])

# ---- Gemini as LLM
r = c.post("/api/settings/list-models", json={"section": "llm", "provider": "gemini", "keys": {"gemini": GEM}}).json()
check("Gemini list models (filtered)", r.get("models") == ["gemini-2.5-flash", "gemini-2.5-pro"], r)
r = c.post("/api/settings/test-llm", json={"provider": "gemini", "gemini_model": "gemini-2.5-pro", "keys": {"gemini": "bad"}})
check("Gemini wrong key -> friendly error", r.status_code == 502 and "Gemini" in r.text, r.json().get("detail"))
c.put("/api/settings", json={"llm": {"provider": "gemini", "gemini_model": "gemini-2.5-pro"}, "keys": {"gemini": GEM}})
r = c.post("/api/settings/test-llm", json={}).json()
check("Gemini LLM test", r.get("reply", "").startswith("[gemini:gemini-2.5-pro]") and r["device"] == "Gemini API", r)
g = mock.get("/_last").json()["gemini"]
check("Wire: Gemini system instruction + config", g["system"] is not None and g["config"].get("maxOutputTokens", 0) >= 4096, g)

# ---- Gemini as speech recognition (shared key)
c.put("/api/settings", json={"stt": {"provider": "gemini", "gemini_model": "gemini-2.5-flash", "filler_prompt": True}})
st = c.get("/api/config").json()["stt"]
check("Status: Gemini speech ready (shared key)", st["available"] and st["device"] == "Gemini API", st)
r = c.post("/api/settings/test-stt", json={}).json()
g = mock.get("/_last").json()["gemini"]
check("Gemini transcription", "mock Gemini heard" in r.get("heard", ""), r.get("heard", r))
check("Wire: audio/wav inline part", g["has_audio"] and g["mime"] == "audio/wav", g)

with open("data/uitest/take.wav", "rb") as f:
    r = c.post("/api/rehearse", files={"audio": ("t.wav", f, "audio/wav")}, data={"deck_id": deck, "slide": "2", "coach": "true"}).json()
check("Rehearse: Gemini STT + Gemini coach", r.get("stt_device") == "Gemini API" and r.get("llm_device") == "Gemini API"
      and r["metrics"]["filler_total"] >= 1, f"fillers={r['metrics']['filler_total']} transcript={r['transcript'][:50]}")

# ---- mixed: Claude coach + Gemini speech
c.put("/api/settings", json={"llm": {"provider": "anthropic"}})
with open("data/uitest/take.wav", "rb") as f:
    r = c.post("/api/rehearse", files={"audio": ("t.wav", f, "audio/wav")}, data={"deck_id": deck, "slide": "2", "coach": "true"}).json()
check("Rehearse: Gemini STT + Claude coach", r.get("stt_device") == "Gemini API" and "[claude:" in (r.get("coaching") or ""),
      (r.get("coaching") or "")[:70])

# ---- Claude through Amazon Bedrock (bedrock-runtime InvokeModel, Bedrock API key)
BM = "us.anthropic.claude-opus-5-5"
r = c.post("/api/settings/list-models", json={"section": "llm", "provider": "bedrock", "bedrock_region": "us-east-2"}).json()
check("Bedrock suggests inference-profile ids", r.get("suggested") and r["models"][0] == BM and f"global.{BM[3:]}" in r["models"], r)
r = c.post("/api/settings/list-models", json={"section": "llm", "provider": "bedrock", "bedrock_api": "mantle"}).json()
check("Mantle suggests anthropic.* ids", r.get("models", [""])[0] == "anthropic.claude-opus-5-5", r)
r = c.post("/api/settings/test-llm", json={"provider": "anthropic", "claude_access": "bedrock", "bedrock_region": "us-east-2",
                                           "keys": {"bedrock": "wrong"}})
check("Bedrock wrong key -> friendly error", r.status_code == 502 and "Claude on Bedrock: access denied - Invalid API Key" in r.text,
      r.json().get("detail"))
r = c.put("/api/settings", json={"llm": {"claude_access": "vertex"}})
check("Unknown Claude access rejected", r.status_code == 400, r.json().get("detail"))
c.put("/api/settings", json={"llm": {"provider": "anthropic", "claude_access": "bedrock", "bedrock_api": "runtime",
                                     "bedrock_region": "us-east-2", "bedrock_model": "", "claude_effort": "high"},
                             "keys": {"bedrock": BED}})
s = c.get("/api/settings").json()
check("Bedrock key masked", BED not in json.dumps(s) and s["settings"]["keys"]["bedrock"]["hint"] == "••••6789",
      s["settings"]["keys"]["bedrock"])
st = c.get("/api/config").json()["llm"]
check("Status: Claude on Bedrock ready, default model from region",
      st["enabled"] and st["device"] == "Claude · Bedrock" and st["model"] == BM, st)
r = c.post("/api/settings/test-llm", json={}).json()
check("Bedrock test call", r.get("reply", "").startswith(f"[claude:{BM}:high]") and r["device"] == "Claude · Bedrock", r)
w = mock.get("/_last").json()["bedrock"][-1]
check("Wire: model in path, effort in body, no server-side fallbacks",
      w["model"] == BM and w["body"].get("output_config") == {"effort": "high"} and "fallbacks" not in w["body"]
      and not ({"temperature", "top_p", "top_k"} & set(w["body"])), f"keys={sorted(w['body'])}")
r = c.post("/api/settings/test-llm", json={"bedrock_model": "anthropic.claude-opus-5-5"})
check("Bedrock bad model id -> AWS message shown", r.status_code == 502 and "on-demand throughput" in r.text, r.json().get("detail"))
r = c.post(f"/api/decks/{deck}/script", json={"slide": 1, "instructions": "REFUSE"}).json()
check("Bedrock refusal retried client-side on the fallback model", "[claude:us.anthropic.claude-opus-5:" in r.get("script", ""),
      str(r)[:120])
c.put("/api/settings", json={"llm": {"bedrock_region": "", "claude_access": "anthropic"}})

# ---- clear keys
c.put("/api/settings", json={"keys": {"anthropic": "__clear__", "gemini": "__clear__", "bedrock": "__clear__"}})
k = c.get("/api/settings").json()["settings"]["keys"]
check("Keys cleared", not k["anthropic"]["set"] and not k["gemini"]["set"] and not k["bedrock"]["set"], k)
c.delete(f"/api/decks/{deck}")
print(f"\n{sum(res)}/{len(res)} checks passed")
sys.exit(0 if all(res) else 1)
