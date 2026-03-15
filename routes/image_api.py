import re, asyncio
import requests as req
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
import llm
import image
import state

router = APIRouter()


class ImageRequest(BaseModel):
    extra: str = ""


class GenerateRequest(BaseModel):
    prompt:    str
    steps:     int   = None
    cfg_scale: float = None


@router.post("/image")
async def image_from_history(body: ImageRequest):
    print(f"\n[backend] Received /image request, extra='{body.extra}'")
    if not llm.history and not body.extra.strip():
        return JSONResponse({"error": "No conversation history yet."}, status_code=400)

    try:
        def _run():
            image._gen_cancel.clear()
            recent = llm.history[-8:]
            last_user_full = (
                next((m["content"] for m in reversed(recent) if m["role"] == "user"), "")
                if recent else body.extra
            )
            # Reorder clauses so end-state act comes first
            _parts = [p.strip() for p in re.split(r'\b(?:and|then)\b|[;]', last_user_full, flags=re.I) if p.strip()]
            last_user = ", ".join([_parts[-1]] + _parts[:-1]) if len(_parts) > 1 else last_user_full

            # Re-clothing resets nudity state
            if state._RE_CLOTHE.search(last_user):
                state._nudity_state = "clothed"
                print("[image] re-clothing detected — nudity state reset")

            messages = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in recent) if recent else f"User: {body.extra}"
            print("[image] extracting SD prompt via LLM...")
            base_prompt, new_nudity = image.extract_sd_prompt(
                messages, appearance=state.ALICE_APPEARANCE,
                last_user_msg=last_user, persona=state.SYSTEM_PROMPT,
                nudity_floor=state._nudity_state,
            )
            state._nudity_state = new_nudity

            if image._gen_cancel.is_set():
                print("[image] cancelled before Forge call")
                return None, None

            positive_parts, negative_parts = [], []
            for token in [t.strip() for t in body.extra.split(",") if t.strip()]:
                if token.lower().startswith("no "):
                    negative_parts.append(token[3:].strip())
                else:
                    positive_parts.append(token)

            base_prompt    = image.clean_tags(base_prompt)
            positive_extra = ", ".join(positive_parts)
            extra_negative = ", ".join(negative_parts)
            prompt = (positive_extra + ", " + base_prompt) if positive_extra else base_prompt
            prompt, extra_negative = image.apply_exposure_rules(messages, prompt, extra_negative)

            state.last_sd_prompt = prompt
            img = image.generate_image(
                prompt, state.ALICE_APPEARANCE, state.BASE_NEGATIVE,
                extra_negative=extra_negative,
                seed=state._character_seed,
            )
            url = state.save_generated_image(img) if img else None
            return prompt, url

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _run)
        sd_prompt, url = result if result and result[0] is not None else (None, None)
        if url is None and sd_prompt is None:
            return JSONResponse({"error": "Cancelled or failed."}, status_code=200)
        return JSONResponse({"sd_prompt": sd_prompt, "url": url})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/reroll")
async def reroll():
    """Re-generate with the same last prompt but a new random seed."""
    if not state.last_sd_prompt:
        return JSONResponse({"error": "No prompt to re-roll."}, status_code=400)
    loop = asyncio.get_running_loop()
    prompt = state.last_sd_prompt
    def _do():
        image._gen_cancel.clear()
        img = image.generate_image(
            prompt, state.ALICE_APPEARANCE, state.BASE_NEGATIVE,
            seed=-1,
        )
        return state.save_generated_image(img) if img else None
    url = await loop.run_in_executor(None, _do)
    if url:
        return JSONResponse({"url": url, "sd_prompt": prompt})
    return JSONResponse({"error": "Generation failed."}, status_code=500)


@router.post("/generate")
async def generate_raw(body: GenerateRequest):
    loop = asyncio.get_running_loop()
    def _regen():
        img = image.generate_image(
            body.prompt, state.ALICE_APPEARANCE, state.BASE_NEGATIVE,
            steps=body.steps, cfg_scale=body.cfg_scale,
        )
        return state.save_generated_image(img) if img else None

    url = await loop.run_in_executor(None, _regen)
    if url:
        return JSONResponse({"url": url})
    return JSONResponse({"error": "No image generated."}, status_code=500)


@router.post("/interrupt")
async def interrupt():
    image._gen_cancel.set()
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, lambda: req.post(
        f"{config.CFG['forge_url']}/sdapi/v1/interrupt", timeout=5
    ))
    return {"status": "interrupted"}


@router.get("/progress")
async def get_progress():
    try:
        r = req.get(f"{config.CFG['forge_url']}/sdapi/v1/progress?skip_current_image=false", timeout=3)
        return JSONResponse(r.json())
    except Exception:
        return JSONResponse({"progress": 0, "state": {}})


@router.get("/seed")
async def get_seed():
    return JSONResponse({"seed": image._last_seed, "pinned": state._seed_pinned})


@router.post("/seed/pin")
async def pin_seed():
    state._seed_pinned   = True
    state._character_seed = image._last_seed
    print(f"[seed] pinned seed {state._character_seed}")
    return JSONResponse({"pinned": True, "seed": state._character_seed})


@router.post("/seed/unpin")
async def unpin_seed():
    state._seed_pinned    = False
    state._character_seed = -1
    print("[seed] unpinned — using random seed")
    return JSONResponse({"pinned": False})
