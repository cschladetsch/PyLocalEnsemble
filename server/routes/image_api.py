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


# ── Group scene helpers ────────────────────────────────────────────────────────

# Matches user messages that are meta-instructions rather than physical actions
_META_INSTRUCTION_RE = re.compile(
    r'\b(?:show|generate|make|draw|render|create|display|get)\b.{0,40}\b(?:image|picture|photo|shot|scene|frame)\b|'
    r'\b(?:group shot|all personas|everyone together|one (?:image|frame)|'
    r"that is not|doesn't look|does not look|all of them|topless in one)\b",
    re.I,
)

# Ordered keyword → setting label; first match wins
_SETTING_HINTS = [
    (r'\bforest\b|\bwood(?:s|land)\b|\btrees?\b|\bclearing\b',      "forest clearing"),
    (r'\bvictorian\b|\bparlou?r\b|\bmanor\b|\bdrawing room\b|\bfireplace\b', "victorian parlor"),
    (r'\bdungeon\b|\bchains?\b|\bstone wall\b',                      "dungeon"),
    (r'\bsci.?fi\b|\blab(?:oratory)?\b|\bholographic\b',             "dark laboratory"),
    (r'\bbedroom\b|\bbed\b|\bsheets?\b',                             "bedroom"),
    (r'\bgarden\b|\bmeadow\b|\boutdoor',                             "outdoor garden"),
    (r'\bbeach\b|\bocean\b|\bshore\b',                               "beach"),
    (r'\btavern\b|\binn\b|\bbarroom\b',                              "tavern"),
    (r'\btemple\b|\bshrine\b|\bsacred\b|\begyptian\b',               "ancient temple"),
    (r'\bcathedral\b|\bchurch\b|\bchapel\b',                         "cathedral"),
]


def _extract_setting_hint(personas: dict) -> str:
    """Infer dominant scene setting from persona system prompts and appearance strings."""
    combined = " ".join(
        p.get("system_prompt", "") + " " + p.get("appearance", "")
        for p in personas.values()
    )
    for pattern, setting in _SETTING_HINTS:
        if re.search(pattern, combined, re.I):
            return setting
    return ""


def _synthesize_group_scene(history_entries: list, personas: dict) -> str:
    """Convert purple prose group history into a concrete physical scene description."""
    recent = [
        e for e in history_entries
        if not e.get("_internal") and e.get("content")
    ][-6:]
    if not recent:
        return ""
    persona_list = ", ".join(p.get("name", k) for k, p in personas.items())
    text = "\n".join(f"{e['sender']}: {e['content']}" for e in recent)
    try:
        desc = llm.llm_chat_deferred([
            {"role": "system", "content": (
                "Convert roleplay dialogue into a concrete physical scene description for image generation. "
                "Describe body positions, poses, who is touching whom, and the physical setting. "
                "No metaphors or purple prose. Plain visual language. 1-2 sentences only."
            )},
            {"role": "user", "content": (
                f"People present: {persona_list}\n\n"
                f"Dialogue:\n{text}\n\n"
                "Describe ONLY what is physically happening: poses, positions, contact, and setting location."
            )},
        ], label="scene synthesis").strip()
        print(f"[image] synthesized group scene: {desc!r}")
        return desc
    except Exception as e:
        print(f"[image] scene synthesis failed: {e}")
        return ""


_GROUP_SCENE_SKIP = {
    "candlelight", "moonlight", "firelight", "soft lighting", "dark atmosphere",
    "egyptian temple", "dark wood panelling", "ornate fireplace",
    "dark sci-fi lab", "holographic interface panels", "mossy forest clearing",
    "twisted ancient trees", "floating ui panels",
}


def _short_group_appearance(app_str: str, limit: int = 8) -> str:
    tags = [t.strip() for t in app_str.split(",") if t.strip()]
    identity = [t for t in tags if t.lower() not in _GROUP_SCENE_SKIP]
    chosen = identity[:limit] if identity else tags[:limit]
    return ", ".join(chosen)


def _build_group_scene_appearance(personas: dict) -> str:
    """Build explicit multi-person scene tags so SD keeps personas separate."""
    count = len(personas)
    count_tag = f"{count}girls" if count != 1 else "1girl"
    base_tags = [
        count_tag,
        "group scene",
        "separate people",
        "distinct individuals",
        "different faces",
        "different bodies",
        "full cast visible",
    ]
    persona_tags = []
    for key, persona in personas.items():
        short_app = _short_group_appearance(persona.get("appearance", ""))
        # Use '1woman' as the anchor for each slot instead of the persona name.
        # This is more SD-native and prevents 'Alice' from being treated as a token.
        persona_tags.append(f"(1woman, {short_app}:1.2)" if short_app else "1woman")
    
    return ", ".join(base_tags) + ", " + ", ".join(persona_tags)


class ImageRequest(BaseModel):
    extra: str = ""


class GenerateRequest(BaseModel):
    prompt:    str
    steps:     int   = None
    cfg_scale: float = None


def _get_relevant_personas(llm_history: list, last_user: str) -> dict:
    """Determine which personas should be in the image based on history and user request."""
    # 1. Identify all personas mentioned or speaking in recent history
    recent = llm_history[-10:]
    involved_keys = set()
    
    # Always include the current active persona if not in group mode
    if not state.GROUP_ACTIVE:
        involved_keys.add(state._active_persona_key or "Alice")

    # If in group mode, start with all personas in the group
    if state.GROUP_ACTIVE:
        involved_keys.update(state.GROUP_PERSONAS.keys())

    # Scan history for other personas who spoke
    for msg in recent:
        content = msg.get("content", "")
        # Look for [PersonaName]: pattern which is used in group history
        m = re.match(r'^\[(.*?)\].*', content)
        if m:
            name = m.group(1)
            for key, p in config.PERSONAS.items():
                if p.get("name", key).lower() == name.lower():
                    involved_keys.add(key)
                    break
    
    # 2. Check for "explicitly otherwise" (user wants only one person)
    # Examples: "just Alice", "only Morrigan", "alone", "solo"
    user_lower = last_user.lower()
    explicit_single = None
    if re.search(r'\b(just|only|alone|solo)\b', user_lower):
        for key, p in config.PERSONAS.items():
            name = p.get("name", key).lower()
            if name in user_lower:
                explicit_single = key
                break
    
    if explicit_single:
        print(f"[image] explicit single persona requested: {explicit_single}")
        return {explicit_single: config.PERSONAS[explicit_single]}

    # Return all involved personas
    return {k: config.PERSONAS[k] for k in involved_keys if k in config.PERSONAS}


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
            recent = _llm_history[-10:]
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

            # Determine relevant personas for the scene
            relevant_personas = _get_relevant_personas(_llm_history, last_user)
            _names = [p.get("name", k) for k, p in relevant_personas.items()]
            
            if len(relevant_personas) > 1:
                combined_appearance = _build_group_scene_appearance(relevant_personas)
                print(f"[image] multi-persona mode ({len(relevant_personas)}) — aggregated appearance: {combined_appearance}")
            else:
                p_key = list(relevant_personas.keys())[0] if relevant_personas else (state._active_persona_key or "Alice")
                combined_appearance = config.PERSONAS.get(p_key, {}).get("appearance", state.ALICE_APPEARANCE)

            state.last_appearance = combined_appearance

            # For group scenes: synthesize a concrete physical scene description
            # from the purple prose history, so the extractor has something to work with
            scene_desc = ""
            if state.GROUP_ACTIVE and len(relevant_personas) > 1:
                try:
                    import routes.group as _grp_mod
                    scene_desc = _synthesize_group_scene(_grp_mod._history, relevant_personas)
                except Exception as _se:
                    print(f"[image] scene synthesis error: {_se}")

            # If the user said something meta ("show all personas", "that is not a group shot"),
            # replace it with the synthesized scene so ACTION/POSE extraction is grounded
            if scene_desc and _META_INSTRUCTION_RE.search(last_user):
                effective_user = scene_desc
                print(f"[image] meta-instruction detected — using synthesized scene as action context")
            else:
                effective_user = last_user

            # Prepend scene synthesis to messages so the LLM has concrete visual context
            if scene_desc:
                messages = f"CURRENT SCENE: {scene_desc}\n\n" + messages

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
                _persona_context = state.SYSTEM_PROMPT
                if len(relevant_personas) > 1:
                    setting_hint = _extract_setting_hint(relevant_personas)
                    _persona_context += (
                        f"\n\nIMPORTANT: This is a scene with ALL of these personas: {', '.join(_names)}. "
                        "You MUST output tags that describe an interaction or pose involving EVERY persona listed. "
                        "Ensure they are distinct individuals and never merged into one person. "
                        "Do not omit any character from the scene description."
                    )
                    if setting_hint:
                        _persona_context += f"\n\nSCENE SETTING: {setting_hint} — use this for the SETTING field."

                base_prompt, new_nudity = image.extract_sd_prompt(
                    messages, appearance=combined_appearance,
                    last_user_msg=effective_user, persona=_persona_context,
                    nudity_floor=state._nudity_state,
                    interaction_priority=(len(relevant_personas) > 1),
                    names=_names,
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
    app    = state.last_appearance or state.ALICE_APPEARANCE
    def _do():
        image._gen_cancel.clear()
        img = image.generate_image(
            prompt, app, state.BASE_NEGATIVE,
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
    app  = state.last_appearance or state.ALICE_APPEARANCE
    def _regen():
        img = image.generate_image(
            body.prompt, app, state.BASE_NEGATIVE,
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
