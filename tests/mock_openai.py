"""Minimal OpenAI-compatible mock (chat, models, transcription) for testing remote endpoints.

python tests/mock_openai.py [port]   -> http://127.0.0.1:<port>/v1   (requires API key "sk-test-123456789")
"""
import sys

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI()
KEY = "sk-test-123456789"


def auth(authorization: str | None):
    if authorization != f"Bearer {KEY}":
        raise HTTPException(401, {"error": {"message": "Invalid API key"}})


@app.get("/v1/models")
def models(authorization: str | None = Header(None)):
    auth(authorization)
    return {"object": "list", "data": [{"id": "mock-llm-1", "object": "model"}, {"id": "mock-whisper", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(req: Request, authorization: str | None = Header(None)):
    auth(authorization)
    body = await req.json()
    user = body["messages"][-1]["content"]
    text = f"[mock:{body['model']}] Welcome, friends, to an evening of ideas. (prompt had {len(user)} chars)"
    return {"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


@app.post("/v1/audio/transcriptions")
async def transcribe(req: Request, authorization: str | None = Header(None)):
    auth(authorization)
    form = await req.form()
    data = await form["file"].read()
    secs = max(0.0, (len(data) - 44) / 32000)
    text = f"Um, mock transcript of {secs:.1f} seconds from {form['model']}."
    return {"text": text, "language": "en", "duration": secs,
            "segments": [{"id": 0, "start": 0.0, "end": secs, "text": text}]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 9011, log_level="warning")
