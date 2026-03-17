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

    # Build synthetic history
    _llm_history = []
    if state.GROUP_ACTIVE:
        try:
            import routes.group as _grp
            _llm_history = [
                {"role": "user" if e["role"] == "user" else "assistant",
                 "content": f"[{e['sender']}]: {e['content']}"}
                for e in _grp._history
                if not e.get("_internal")
            ]
        except ImportError:
            pass

    # Fallback to normal history if group history is empty or not in group mode
    if not _llm_history:
        _llm_history = llm.history

    if not _llm_history and not body.extra.strip():
        return JSONResponse({"error": "No conversation history yet."}, status_code=400)

    try:
        def _run():
            image._gen_cancel.clear()
            recent = _llm_history[-8:]
            last_user_full = (
                next((m["content"] for m in reversed(recent) if m["role"] == "user"), "")
                if recent else body.extra
            )
            # Reorder clauses so end-state act comes first
            _parts = [p.strip() for p in re.split(r'\b(?:and|then)\b|[;]', last_user_full, flags=re.I) if p.strip()]
            last_user = ", ".join([_parts[-1]] + _parts[:-1]) if len(_parts) > 1 else last_user_full

            # Re-clothing resets nudity state; otherwise decay if conversation went off-topic
            if state._RE_CLOTHE.search(last_user):
                state._nudity_state = "clothed"
                state._nudity_turns_since_keyword = 0
                print("[image] re-clothing detected — nudity state reset")
            else:
                state.decay_nudity_state(last_user)

            messages = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in recent) if recent else f"User: {body.extra}"

            # Aggregate all active persona appearances for the scene
            if state.GROUP_ACTIVE:
                # Build a collective appearance string: "2 girls, [Name1] is [App1], [Name2] is [App2]"
                try:
                    import routes.group as _grp
                    _names = [p.get("name", k) for k, p in _grp._personas.items()]
                    _count_str = f"{len(_names)} girls" if len(_names) > 1 else "1 girl"
                    _apps = [f"{p.get('name', k)} is {p.get('appearance', '')}"
                             for k, p in _grp._personas.items()]
                    combined_appearance = f"{_count_str}, " + ", ".join(_apps)
                    print(f"[image] group mode — aggregated appearance: {combined_appearance}")
                except Exception:
                    combined_appearance = state.ALICE_APPEARANCE
            else:
                combined_appearance = state.ALICE_APPEARANCE

            used_pre = bool(state._pre_sd_prompt and not body.extra)
            if used_pre:
                print("[image] using pre-extracted SD prompt (skipping LLM call)")
                prompt         = state._pre_sd_prompt
                extra_negative = state._pre_sd_negative
                if state._pre_sd_nudity:
                    state._nudity_state = state._pre_sd_nudity
                state._pre_sd_prompt = None
            else:
                print("[image] extracting SD prompt via LLM...")
                # If group is active, give the LLM a hint that it's a multi-character scene
                _persona_context = state.SYSTEM_PROMPT
                if state.GROUP_ACTIVE:
                    _persona_context += "\n\nIMPORTANT: This is a GROUP scene with multiple characters. " \
                                        "Ensure ACTION and EXTRA describe the interaction or collective pose."

                base_prompt, new_nudity = image.extract_sd_prompt(
                    messages, appearance=combined_appearance,
                    last_user_msg=last_user, persona=_persona_context,
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

            if not used_pre:
                base_prompt    = image.clean_tags(base_prompt)
                positive_extra = ", ".join(positive_parts)
                extra_negative = ", ".join(negative_parts)
                prompt = (positive_extra + ", " + base_prompt) if positive_extra else base_prompt
                prompt, extra_negative = image.apply_exposure_rules(messages, prompt, extra_negative)
            elif positive_parts:
                prompt = ", ".join(positive_parts) + ", " + prompt

            state.last_sd_prompt = prompt
            img = image.generate_image(
                prompt, combined_appearance, state.BASE_NEGATIVE,
                extra_negative=extra_negative,
                seed=state._character_seed,
            )
            # Auto-pin seed after first generation so the character face stays consistent
            if img and config.CFG["image"].get("auto_pin_seed", True):
                if not state._seed_pinned and state.last_seed > 0:
                    state._character_seed = state.last_seed
                    state._seed_pinned    = True
                    print(f"[seed] auto-pinned {state._character_seed} for character consistency")
            url = state.save_generated_image(img) if img else None
            return prompt, url

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _run)
        sd_prompt, url = result if result and result[0] is not None else (None, None)
        if url is None and sd_prompt is None:
            return JSONResponse({"error": "Cancelled or failed."}, status_code=200)
        return JSONResponse({
            "sd_prompt": sd_prompt,
            "url":       url,
            "seed":      state.last_seed,
            "pinned":    state._seed_pinned,
        })

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
    return JSONResponse({"seed": state.last_seed, "pinned": state._seed_pinned})


@router.post("/seed/pin")
async def pin_seed():
    state._seed_pinned    = True
    state._character_seed = state.last_seed
    print(f"[seed] pinned seed {state._character_seed}")
    return JSONResponse({"pinned": True, "seed": state._character_seed})


@router.post("/seed/unpin")
async def unpin_seed():
    state._seed_pinned    = False
    state._character_seed = -1
    print("[seed] unpinned — using random seed")
    return JSONResponse({"pinned": False})
