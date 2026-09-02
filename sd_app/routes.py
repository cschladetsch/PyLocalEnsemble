import asyncio
import requests as req
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
import generate
from utils import http_ok

router = APIRouter()


@router.get("/sd-info")
async def sd_info():
    forge_url = config.CFG["forge_url"]
    return JSONResponse({"forge_ready": http_ok(f"{forge_url}/sdapi/v1/sd-models")})


@router.post("/sd-generate")
async def sd_generate(body: dict):
    """Direct SD generation — no chat context needed."""
    loop = asyncio.get_running_loop()
    prompt = body.get("prompt", "")
    steps  = int(body.get("steps", 25))
    width  = int(body.get("width", 512))
    height = int(body.get("height", 512))
    seed   = int(body.get("seed", -1))

    def _regen():
        generate._gen_cancel.clear()
        img = generate.generate_image(
            prompt, config.CFG["negative_prompt"],
            steps=steps, cfg_scale=7.0, width=width, height=height, seed=seed,
        )
        return generate.save_generated_image(img) if img else None

    try:
        url = await loop.run_in_executor(None, _regen)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if url:
        return JSONResponse({"url": url, "seed": generate.last_seed})
    return JSONResponse({"error": "No image generated."}, status_code=500)


@router.get("/sd-models")
async def list_sd_models():
    forge_url = config.CFG.get("forge_url", "")
    try:
        r = req.get(f"{forge_url}/sdapi/v1/sd-models", timeout=5)
        models = [{"title": m["title"], "name": m.get("model_name", m["title"])}
                  for m in r.json()]
        try:
            opts    = req.get(f"{forge_url}/sdapi/v1/options", timeout=5).json()
            current = opts.get("sd_model_checkpoint", "")
        except Exception:
            current = config.CFG.get("sd_checkpoint", "")
        return JSONResponse({"models": models, "current": current})
    except Exception as e:
        return JSONResponse({"models": [], "current": "", "error": str(e)})


class SDModelRequest(BaseModel):
    title: str


@router.post("/sd-model")
async def switch_sd_model(body: SDModelRequest):
    forge_url = config.CFG.get("forge_url", "")
    loop = asyncio.get_running_loop()
    def _switch():
        r = req.post(f"{forge_url}/sdapi/v1/options",
                     json={"sd_model_checkpoint": body.title}, timeout=120)
        if r.ok:
            config.CFG["sd_checkpoint"] = body.title
            config.save_config(config.CFG)
            from forge import _push_forge_settings
            _push_forge_settings(forge_url)
        return r.status_code
    status = await loop.run_in_executor(None, _switch)
    if status == 200:
        return JSONResponse({"status": "ok", "title": body.title})
    return JSONResponse({"error": f"Forge returned HTTP {status}"}, status_code=500)


@router.post("/interrupt")
async def interrupt():
    generate._gen_cancel.set()
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, lambda: req.post(
        f"{config.CFG['forge_url']}/sdapi/v1/interrupt", timeout=5
    ))
    return {"status": "interrupted"}


@router.get("/progress")
async def get_progress():
    forge_url = config.CFG["forge_url"]
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            lambda: req.get(f"{forge_url}/sdapi/v1/progress?skip_current_image=false", timeout=3).json()
        )
        return JSONResponse(data)
    except Exception:
        return JSONResponse({"progress": 0, "state": {}})
