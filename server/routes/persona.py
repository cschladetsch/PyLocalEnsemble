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
    personas = []
    for name, p in config.PERSONAS.items():
        # font_key: explicit in config, or derive from persona name
        font_key = p.get("font_key", name.lower().replace(" ", "-"))
        personas.append({"name": name, "font_key": font_key})
    return JSONResponse({"personas": personas})


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

    # Per-persona negative appends to base so anatomy terms are never lost
    persona_neg = p.get("negative_prompt", "")
    state.BASE_NEGATIVE = (state._BASE_NEGATIVE + ", " + persona_neg) if persona_neg else state._BASE_NEGATIVE

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
    state._nudity_state              = "clothed"
    state._nudity_turns_since_keyword = 0
    state._character_seed            = -1
    state._seed_pinned               = False

    # Clear history so the previous persona's lore cannot bleed into the new one
    state._active_persona_key = name
    llm.clear_history()

    print(f"\n[{config.NAME}] Switched to persona: {name}")
    return JSONResponse({"status": "ok", "persona": name, "sd_model": sd_model})
