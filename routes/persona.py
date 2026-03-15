import asyncio
from fastapi import APIRouter
from fastapi.responses import JSONResponse

import config
import llm
import state
import image

router = APIRouter()


@router.get("/personas")
async def list_personas():
    return JSONResponse({"personas": list(config.PERSONAS.keys())})


@router.post("/persona/{name}")
async def switch_persona(name: str):
    if name not in config.PERSONAS:
        return JSONResponse({"error": f"Persona '{name}' not found."}, status_code=404)

    p = config.PERSONAS[name]
    config.NAME            = p.get("name", config.CFG["name"])
    state.ALICE_APPEARANCE = p.get("appearance", config.CFG["appearance"])
    state.SYSTEM_PROMPT    = p.get("system_prompt", config.CFG["system_prompt"])

    # Persona image overrides — start from base snapshot so previous suffix doesn't leak
    img_cfg = {**state._BASE_IMAGE_CFG}
    img_cfg.update(p.get("image", {}))
    config.CFG["image"] = img_cfg
    state.IMAGE_SUFFIX = img_cfg.get("suffix", "")

    # Per-persona negative prompt
    state.BASE_NEGATIVE = p.get("negative_prompt", state._BASE_NEGATIVE)

    # TTS overrides — clear previous persona's effects before applying new ones
    tts_base = {**config.CFG.get("tts", {})}
    tts_base.pop("effects", None)
    tts_base.update(p.get("tts", {}))
    config.CFG["tts"] = tts_base

    # Per-persona SD model — switch in background so persona switch returns immediately
    sd_model = p.get("sd_model", "")
    if sd_model:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, image.set_forge_model, sd_model)
        print(f"[{config.NAME}] SD model switch queued: {sd_model!r}")

    # Reset image session state (new persona = new scene)
    state._nudity_state    = "clothed"
    state._character_seed  = -1
    state._seed_pinned     = False

    print(f"\n[{config.NAME}] Switched to persona: {name}")
    return JSONResponse({"status": "ok", "persona": name, "sd_model": sd_model})
