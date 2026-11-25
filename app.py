"""Stage Presenter - a local AI presentation trainer.

Run:  .venv\\Scripts\\python app.py   (then open http://127.0.0.1:8000)
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

from presenter import llm, rehearsal, script_writer, settings, tts  # noqa: E402
from presenter.pptx_parser import parse_pptx  # noqa: E402
from presenter.slide_render import render_slides  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
DATA = BASE / "data"
DECKS = DATA / "decks"
AUDIO = DATA / "audio"
DECKS.mkdir(parents=True, exist_ok=True)
AUDIO.mkdir(parents=True, exist_ok=True)

import threading  # noqa: E402

_deck_lock = threading.Lock()  # deck.json is read-modify-written by concurrent script requests
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))
ID_RE = re.compile(r"^[a-f0-9]{32}$")

app = FastAPI(title="Stage Presenter")


def _load_local_models_async() -> None:
    """(Re)load local OpenVINO models in the background so requests don't wait on compilation."""
    import threading

    def run():
        if llm.provider() == "openvino" and llm.ov_model_dir():
            try:
                llm.OV.load()
            except Exception as e:  # noqa: BLE001
                logging.warning("LLM load failed: %s", e)
        else:
            llm.OV.unload()
        w = rehearsal.WHISPER
        if w.cfg()["provider"] == "openvino" and w.model_dir():
            try:
                w.load()
            except Exception as e:  # noqa: BLE001
                logging.warning("Whisper load failed: %s", e)
        else:
            w.unload()

    threading.Thread(target=run, daemon=True).start()


@app.on_event("startup")
def _warmup():
    if os.getenv("PRELOAD_MODELS", "1") == "1":
        _load_local_models_async()


def _deck_dir(deck_id: str) -> Path:
    if not ID_RE.match(deck_id):
        raise HTTPException(400, "Invalid deck id")
    d = DECKS / deck_id
    if not d.exists():
        raise HTTPException(404, "Deck not found")
    return d


def _load(deck_id: str) -> dict:
    return json.loads((_deck_dir(deck_id) / "deck.json").read_text("utf-8"))


def _save(deck_id: str, deck: dict) -> None:
    (_deck_dir(deck_id) / "deck.json").write_text(json.dumps(deck, indent=2), "utf-8")


# ---------------------------------------------------------------- API
@app.get("/api/config")
def config():
    from presenter.offline_tts import KOKORO, PIPER

    st = llm.status()
    return {
        "llm": st,
        "stt": rehearsal.WHISPER.status(),
        "engines": {"edge": True, "kokoro": KOKORO.available(), "piper": bool(PIPER.files())},
        "tones": list(script_writer.TONES),
        "styles": list(script_writer.STYLES),
        "lengths": list(script_writer.LENGTHS),
        "curated": tts.CURATED,
    }


# ---------------------------------------------------------------- model endpoint settings
@app.get("/api/settings")
def get_settings():
    return {
        "settings": settings.masked(),
        "devices": settings.devices(),
        "local_models": {"llm": settings.local_models("llm"), "whisper": settings.local_models("whisper")},
        "presets": {"llm": settings.LLM_PRESETS, "stt": settings.STT_PRESETS},
        "status": {"llm": llm.status(), "stt": rehearsal.WHISPER.status()},
    }


@app.put("/api/settings")
def put_settings(patch: dict):
    try:
        settings.update(patch)
    except ValueError as e:
        raise HTTPException(400, f"Invalid settings: {e}")
    _load_local_models_async()  # picks up a new local model/device; unloads if switched away
    return get_settings()


def _merged(section: str, body: dict) -> dict:
    """Saved settings for a section overlaid with unsaved values from the form (for Test buttons)."""
    all_ = settings.get()
    cur = all_[section]
    for k, v in (body or {}).items():
        if k == "api_key" and v in (None, "", settings.KEEP):
            continue
        if k in cur:
            cur[k] = "" if v == settings.CLEAR else v
    keys = dict(all_["keys"])
    for k, v in ((body or {}).get("keys") or {}).items():
        if k in keys and v not in (None, "", settings.KEEP):
            keys[k] = "" if v == settings.CLEAR else v
    cur["_keys"] = keys
    return cur


def _local_changed(section: str, form: dict) -> bool:
    """True if the form's local model/device differs from what's saved (and therefore loaded)."""
    kind = "llm" if section == "llm" else "whisper"
    saved = settings.get()[section]
    return (settings.resolve_model_dir(kind, form["ov_model"]) != settings.resolve_model_dir(kind, saved["ov_model"])
            or form["ov_device"] != saved["ov_device"] or saved["provider"] != "openvino")


@app.post("/api/settings/test-llm")
def test_llm(body: dict):
    import time as _t

    c = _merged("llm", body)
    if c["provider"] == "none":
        raise HTTPException(400, "LLM is turned off")
    if c["provider"] == "openvino" and _local_changed("llm", c):
        raise HTTPException(400, "Save first: local model or device changed (it will load in the background)")
    t0 = _t.time()
    try:
        out = llm.chat("You are a concise assistant.", "In one elegant sentence, welcome an audience to a keynote.",
                       max_tokens=60, temperature=0.3, override=c)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)[:400] if isinstance(e, RuntimeError) else f"{type(e).__name__}: {str(e)[:300]}")
    claude = "Claude · Bedrock" if c["claude_access"] == "bedrock" else "Claude API"
    device = {"openvino": llm.OV.device, "anthropic": claude, "gemini": "Gemini API"}.get(c["provider"], "remote")
    return {"ok": True, "seconds": round(_t.time() - t0, 2), "reply": script_writer._clean(out)[:300], "device": device}


@app.post("/api/settings/test-stt")
def test_stt(body: dict):
    import time as _t

    import numpy as np

    from presenter import offline_tts

    c = _merged("stt", body)
    if c["provider"] == "none":
        raise HTTPException(400, "Speech recognition is turned off")
    w = rehearsal.WHISPER
    if c["provider"] == "openvino" and _local_changed("stt", c):
        raise HTTPException(400, "Save first: local model or device changed (it will load in the background)")
    phrase = "Testing the rehearsal coach. Um, the quick brown fox jumps over the lazy dog."
    if offline_tts.PIPER.files():
        audio, _, sr = offline_tts.PIPER.synth(phrase, offline_tts.PIPER.files()[0].stem, 0)
        n = int(len(audio) * 16000 / sr)
        pcm = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    else:  # no offline voice installed: send a short tone just to check connectivity
        phrase = "(tone)"
        pcm = (0.1 * np.sin(2 * np.pi * 440 * np.arange(32000) / 16000)).astype(np.float32)
    t0 = _t.time()
    try:
        tr = w.transcribe(pcm, override=c)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)[:400] if isinstance(e, RuntimeError) else f"{type(e).__name__}: {str(e)[:300]}")
    device = {"openvino": w.device, "gemini": "Gemini API"}.get(c["provider"], "remote")
    return {"ok": True, "seconds": round(_t.time() - t0, 2), "expected": phrase, "heard": tr["text"], "device": device}


@app.post("/api/settings/list-models")
def list_remote_models(body: dict):
    section = body.get("section", "llm")
    if section not in ("llm", "stt"):
        raise HTTPException(400, "section must be llm or stt")
    c = _merged(section, body)
    provider = body.get("provider", c["provider"])
    try:
        if provider == "bedrock":  # Bedrock has no Models API: offer the usual ids for this region
            from presenter import claude_llm
            return {"models": claude_llm.bedrock_models(c["bedrock_api"], c["bedrock_region"]), "suggested": True}
        if provider == "anthropic":
            from presenter import claude_llm
            try:
                return {"models": claude_llm.list_models(c["_keys"]["anthropic"])}
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, claude_llm.friendly_error(e))
        if provider == "gemini":
            from presenter import gemini_ai
            try:
                return {"models": gemini_ai.list_models(c["_keys"]["gemini"], audio=section == "stt")}
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, gemini_ai.friendly_error(e))
        if not c["base_url"]:
            raise HTTPException(400, "Enter the endpoint URL first")
        models = sorted(m.id for m in llm.openai_client(c["base_url"], c["api_key"], timeout=15).models.list())
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}")
    return {"models": models}


@app.get("/api/voices")
async def voices():
    return await tts.all_voices()


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pptx"):
        raise HTTPException(400, "Please upload a .pptx file (save .ppt files as .pptx first).")
    deck_id = uuid.uuid4().hex
    d = DECKS / deck_id
    d.mkdir(parents=True)
    pptx_path = d / "deck.pptx"
    size = 0
    with open(pptx_path, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_MB << 20:
                f.close()
                shutil.rmtree(d, ignore_errors=True)
                raise HTTPException(413, f"File larger than {MAX_UPLOAD_MB} MB")
            f.write(chunk)
    try:
        slides, (w, h) = parse_pptx(str(pptx_path))
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, f"Could not read presentation: {e}")

    from starlette.concurrency import run_in_threadpool
    rendered = await run_in_threadpool(render_slides, pptx_path, d / "slides")
    for s in slides:
        if rendered and (d / "slides" / f"{s.index}.png").exists():
            s.image = f"/decks/{deck_id}/slides/{s.index}.png"
    deck = {
        "id": deck_id,
        "name": Path(file.filename).stem,
        "aspect": w / h if h else 16 / 9,
        "rendered": rendered,
        "slides": [s.to_dict() for s in slides],
    }
    (d / "deck.json").write_text(json.dumps(deck, indent=2), "utf-8")
    return deck


@app.get("/api/decks")
def list_decks():
    out = []
    for p in sorted(DECKS.glob("*/deck.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            dk = json.loads(p.read_text("utf-8"))
            out.append({"id": dk["id"], "name": dk["name"], "slides": len(dk["slides"])})
        except Exception:  # noqa: BLE001
            continue
    return out


@app.get("/api/decks/{deck_id}")
def get_deck(deck_id: str):
    return _load(deck_id)


@app.delete("/api/decks/{deck_id}")
def delete_deck(deck_id: str):
    shutil.rmtree(_deck_dir(deck_id), ignore_errors=True)
    return {"ok": True}


class Prefs(BaseModel):
    tone: str = "authoritative"
    style: str = "keynote"
    length: str = "standard"
    locale: str = "en-US"
    audience: str = ""
    instructions: str = Field("", max_length=2000)


class ScriptReq(Prefs):
    slide: int


@app.post("/api/decks/{deck_id}/script")
def generate_script(deck_id: str, req: ScriptReq):
    deck = _load(deck_id)
    slides = deck["slides"]
    if not 1 <= req.slide <= len(slides):
        raise HTTPException(400, "Slide out of range")
    s = slides[req.slide - 1]
    prev = slides[req.slide - 2] if req.slide > 1 else None
    try:
        text, engine = script_writer.write_script(
            s, len(slides), req.model_dump(),
            prev_title=prev["title"] if prev else None,
            prev_script=prev.get("script") if prev else None,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Script generation failed: {e}")
    with _deck_lock:  # the LLM call can take a while: re-read so scripts saved meanwhile for other slides are kept
        deck = _load(deck_id)
        deck["slides"][req.slide - 1]["script"] = text
        _save(deck_id, deck)
    return {"slide": req.slide, "script": text, "engine": engine}


class ScriptEdit(BaseModel):
    slide: int
    script: str


@app.put("/api/decks/{deck_id}/script")
def save_script(deck_id: str, req: ScriptEdit):
    with _deck_lock:
        deck = _load(deck_id)
        if not 1 <= req.slide <= len(deck["slides"]):
            raise HTTPException(400, "Slide out of range")
        deck["slides"][req.slide - 1]["script"] = req.script
        _save(deck_id, deck)
    return {"ok": True}


@app.get("/api/decks/{deck_id}/export.{fmt}")
def export_script(deck_id: str, fmt: str):
    deck = _load(deck_id)
    if fmt not in ("txt", "md"):
        raise HTTPException(400, "Use txt or md")
    lines = [f"# {deck['name']} - Speaker Script\n"] if fmt == "md" else [f"{deck['name']} - Speaker Script\n"]
    for s in deck["slides"]:
        head = f"## Slide {s['index']}: {s['title']}" if fmt == "md" else f"--- Slide {s['index']}: {s['title']} ---"
        lines += [head, "", s.get("script") or s.get("notes") or "", ""]
    return PlainTextResponse(
        "\n".join(lines),
        headers={"Content-Disposition": f'attachment; filename="{deck["name"]}_script.{fmt}"'},
    )


class TTSReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000)
    engine: str = "edge"
    voice: str
    locale: str = "en-US"
    rate: int = Field(0, ge=-50, le=50)
    pitch: int = Field(0, ge=-30, le=30)


@app.post("/api/tts")
async def speak(req: TTSReq):
    if req.engine not in ("edge", "kokoro", "piper") or not re.match(r"^[A-Za-z0-9_\-]{2,80}$", req.voice):
        raise HTTPException(400, "Invalid engine or voice")
    try:
        res = await tts.synthesize(req.engine, req.text, req.voice, req.locale, req.rate, req.pitch, AUDIO)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"{req.engine} TTS unavailable: {e}")
    return {"audio": f"/audio/{res['file']}", "sentences": res["sentences"]}


@app.get("/audio/{name}")
def audio(name: str):
    m = re.match(r"^[a-f0-9]{24}\.(mp3|wav)$", name)
    if not m:
        raise HTTPException(400)
    p = AUDIO / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/mpeg" if m.group(1) == "mp3" else "audio/wav")


@app.post("/api/rehearse")
async def rehearse(
    audio: UploadFile = File(...),
    deck_id: str = Form(""),
    slide: int = Form(0),
    script: str = Form(""),
    prefs: str = Form("{}"),
    reference_seconds: float = Form(0),
    coach: bool = Form(True),
):
    """Transcribe a rehearsal take with Whisper on the NPU and return delivery feedback."""
    from starlette.concurrency import run_in_threadpool

    data = await audio.read()
    if len(data) > 100 << 20:
        raise HTTPException(413, "Recording too long")
    try:
        pcm = rehearsal.read_wav(data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Bad audio: {e}")
    if len(pcm) < 16000:
        raise HTTPException(400, "Recording is shorter than one second")
    if not script and deck_id and slide:
        try:
            script = _load(deck_id)["slides"][slide - 1].get("script", "")
        except Exception:  # noqa: BLE001
            script = ""
    import time as _t
    t0 = _t.time()
    try:
        tr = await run_in_threadpool(rehearsal.WHISPER.transcribe, pcm)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"Speech recognition unavailable: {e}")
    stt_time = _t.time() - t0
    metrics = rehearsal.analyze(tr["text"], pcm, script, reference_seconds or None)
    feedback = None
    if coach:
        t1 = _t.time()
        feedback = await run_in_threadpool(rehearsal.coach, tr["text"], script, metrics, json.loads(prefs or "{}"))
        metrics["coach_seconds"] = round(_t.time() - t1, 1)
    metrics["stt_seconds"] = round(stt_time, 1)
    return {"transcript": tr["text"], "chunks": tr["chunks"], "metrics": metrics, "coaching": feedback,
            "stt_device": rehearsal.WHISPER.status()["device"],
            "llm_device": llm.status()["device"] if llm.enabled() else None}


@app.get("/decks/{deck_id}/slides/{n}.png")
def slide_image(deck_id: str, n: int):
    p = _deck_dir(deck_id) / "slides" / f"{n}.png"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    print(f"\n  Stage Presenter running at http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port)
