"""Mock Anthropic Messages API + Bedrock InvokeModel + Gemini generateContent API, for testing the real SDKs offline.

python tests/mock_cloud.py [port]
  Anthropic:  ANTHROPIC_BASE_URL=http://127.0.0.1:<port>          key "sk-ant-test-123456789"
  Bedrock:    ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:<port>/bedrock   key "bedrock-api-key-test-123456789"
  Gemini:     GEMINI_BASE_URL=http://127.0.0.1:<port>/gemini      key "AIza-test-123456789"
GET /_last returns the last request each mock received (headers + body) so tests can assert the wire format.
"""
import base64
import sys

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI()
ANT_KEY, GEM_KEY = "sk-ant-test-123456789", "AIza-test-123456789"
BED_KEY = "bedrock-api-key-test-123456789"
LAST: dict = {}


@app.get("/_last")
def last():
    return LAST


# ------------------------------------------------------------------ Anthropic
def ant_ok(req: Request) -> bool:
    # API version 2026-10-21 SDKs send "Authorization: Bearer <key>"; older ones send x-api-key
    return ANT_KEY in (req.headers.get("x-api-key", ""), req.headers.get("authorization", "").removeprefix("Bearer "))


@app.post("/v1/messages")
async def messages(req: Request):
    if not ant_ok(req):
        raise HTTPException(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    body = await req.json()
    LAST["anthropic"] = {"headers": {k: v for k, v in req.headers.items() if k.startswith(("anthropic", "x-api", "authorization"))},
                         "query": dict(req.query_params), "body": body}
    user = body["messages"][-1]["content"]
    if "REFUSE" in user:
        return {"id": "msg_r", "type": "message", "role": "assistant", "model": body["model"], "content": [],
                "stop_reason": "refusal", "stop_sequence": None,
                "stop_details": {"type": "refusal", "category": "cyber", "explanation": "mock"},
                "usage": {"input_tokens": 5, "output_tokens": 0}}
    text = f"[claude:{body['model']}:{body.get('output_config', {}).get('effort')}] A hush falls; the future begins here."
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": text}],
            "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 50, "output_tokens": 20}}


@app.get("/v1/models")
async def ant_models(req: Request):
    if not ant_ok(req):
        raise HTTPException(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    data = [{"type": "model", "id": m, "display_name": m, "created_at": "2026-01-01T00:00:00Z"}
            for m in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")]
    return {"data": data, "has_more": False, "first_id": data[0]["id"], "last_id": data[-1]["id"]}


# ------------------------------------------------------------------ Amazon Bedrock (bedrock-runtime InvokeModel)
@app.post("/bedrock/model/{model}/invoke")
async def bedrock_invoke(model: str, req: Request):
    if req.headers.get("authorization", "") != f"Bearer {BED_KEY}":
        return JSONResponse({"Message": "Invalid API Key format: Must start with pre-defined prefix"}, 403)  # AWS error shape
    if not model.split(".")[0] in ("us", "eu", "apac", "global"):
        return JSONResponse({"message": f"Invocation of model ID {model} with on-demand throughput isn't supported."}, 400)
    body = await req.json()
    LAST.setdefault("bedrock", []).append({"model": model, "body": body})
    del LAST["bedrock"][:-4]
    user = body["messages"][-1]["content"]
    user = user if isinstance(user, str) else " ".join(b.get("text", "") for b in user)
    if "REFUSE" in user and model.endswith("claude-opus-5-5"):  # only the primary model declines
        return {"id": "msg_r", "type": "message", "role": "assistant", "model": model, "content": [],
                "stop_reason": "refusal", "stop_sequence": None,
                "stop_details": {"type": "refusal", "category": "cyber", "explanation": "mock"},
                "usage": {"input_tokens": 5, "output_tokens": 0}}
    text = f"[claude:{model}:{body.get('output_config', {}).get('effort')}] A hush falls; the future begins here."
    return {"id": "msg_b", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": text}],
            "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 50, "output_tokens": 20}}


# ------------------------------------------------------------------ Gemini
def gem_auth(req: Request):
    if req.headers.get("x-goog-api-key") != GEM_KEY:
        raise HTTPException(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                            "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}})


def _mime(part: dict):
    d = part.get("inlineData") or part.get("inline_data")  # SDK may send camelCase or snake_case
    return d.get("mimeType") or d.get("mime_type")


@app.post("/gemini/{ver}/models/{model}:generateContent")
async def generate(ver: str, model: str, req: Request):
    gem_auth(req)
    body = await req.json()
    parts = [p for c in body.get("contents", []) for p in c.get("parts", [])]
    audio = [p for p in parts if "inlineData" in p or "inline_data" in p]
    LAST["gemini"] = {"model": model, "version": ver,
                      "system": body.get("systemInstruction") or body.get("system_instruction"),
                      "config": body.get("generationConfig"), "n_parts": len(parts), "has_audio": bool(audio),
                      "mime": _mime(audio[0]) if audio else None}
    if audio:
        raw = (audio[0].get("inlineData") or audio[0].get("inline_data"))["data"]
        data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))  # SDK sends URL-safe base64, unpadded
        text = f"Um, so, mock Gemini heard {(len(data) - 44) / 32000:.1f} seconds of speech."
    else:
        text = f"[gemini:{model}] Tonight, we stand at the edge of something extraordinary."
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
            "modelVersion": model}


@app.get("/gemini/{ver}/models")
async def gem_models(ver: str, req: Request):
    gem_auth(req)
    return {"models": [
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
        {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/gemini-2.5-flash-preview-tts", "supportedGenerationMethods": ["generateContent"]},
    ]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 9012, log_level="warning")
