import re, os
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import config
import llm
import state

router = APIRouter()


class ModelSwitchRequest(BaseModel):
    path: str


@router.get("/models")
async def list_models():
    models  = [{"name": n, "path": p} for n, p in llm.list_models()]
    current = llm.llm_model()
    return JSONResponse({"models": models, "current": current})


@router.post("/model")
async def switch_model(body: ModelSwitchRequest):
    config.CFG["llama_model"] = body.path
    llm.clear_history()
    print(f"\n[{config.NAME}] Switched llama model to: {body.path}")
    return JSONResponse({"status": "ok", "model": body.path})


@router.delete("/history")
async def clear_history():
    llm.clear_history()
    return {"status": "cleared"}


@router.delete("/image/{filename}")
async def delete_image(filename: str):
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "", filename)
    if safe != filename or not safe.endswith(".png"):
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    path = os.path.join(config.ALICE_DIR, "static", "outputs", safe)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    os.remove(path)
    return JSONResponse({"status": "deleted"})


@router.get("/info")
async def info():
    return JSONResponse({
        "name":        config.NAME,
        "llm_ready":   llm.LLM_READY,
        "stt_silence": config.CFG.get("stt_silence_seconds", 3),
    })


@router.get("/", response_class=HTMLResponse)
async def index():
    static_dir = os.path.join(config.ALICE_DIR, "static")
    with open(os.path.join(static_dir, "index.html"), encoding="utf-8") as f:
        html = f.read()
    for asset in ("app.js", "style.css"):
        path = os.path.join(static_dir, asset)
        v = int(os.path.getmtime(path)) if os.path.exists(path) else 0
        html = re.sub(rf'(/static/{re.escape(asset)})\?v=\d+', rf'\g<1>?v={v}', html)
    return html
