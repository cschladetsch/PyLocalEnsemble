import re, os, asyncio
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import requests as req

import config
import llm
import state

router = APIRouter()


class ModelSwitchRequest(BaseModel):
    path: str

class SettingsPatch(BaseModel):
    quick_image: bool | None = None


@router.get("/settings")
async def get_settings():
    return JSONResponse({"quick_image": config.CFG.get("quick_image", True)})


@router.post("/settings")
async def patch_settings(body: SettingsPatch):
    if body.quick_image is not None:
        config.CFG["quick_image"] = body.quick_image
        config.save_config(config.CFG)
    return JSONResponse({"quick_image": config.CFG.get("quick_image", True)})

class DemoPersonaRequest(BaseModel):
    name: str

class PackSwitchRequest(BaseModel):
    name: str


@router.get("/persona-packs")
async def list_persona_packs():
    return JSONResponse({"packs": config.get_persona_packs()})


@router.post("/persona-pack")
async def switch_persona_pack(body: PackSwitchRequest):
    import shutil, time
    
    # 1. Backup current personas.json to /personas/mine
    if os.path.exists(config.PERSONAS_FILE):
        os.makedirs(config.MINE_DIR, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{timestamp}.json"
        shutil.copy(config.PERSONAS_FILE, os.path.join(config.MINE_DIR, backup_name))
        print(f"[system] Backed up current personas.json to mine/{backup_name}")

    # 2. Determine source path
    if body.name.startswith("mine/"):
        real_name = body.name[5:]
        src = os.path.join(config.MINE_DIR, f"{real_name}.json")
    else:
        src = os.path.join(config.PACKS_DIR, f"{body.name}.json")
        
    if not os.path.exists(src):
        return JSONResponse({"error": f"Pack '{body.name}' not found"}, status_code=404)
    
    # 3. Overwrite personas.json and reload
    shutil.copy(src, config.PERSONAS_FILE)
    config.reload_personas()
    print(f"[system] Switched persona pack to: {body.name}")
    return JSONResponse({"status": "ok", "pack": body.name})


@router.get("/models")
async def list_models():
    models  = [{"name": n, "path": p, "size_gb": s} for n, p, s in llm.list_models()]
    current = llm.llm_model()
    return JSONResponse({"models": models, "current": current})


@router.post("/model")
async def switch_model(body: ModelSwitchRequest):
    import asyncio
    if not os.path.isfile(body.path):
        return JSONResponse({"error": f"File not found: {body.path}"}, status_code=400)
    config.CFG["model_path"] = body.path
    config.CFG["llama_model"] = os.path.basename(body.path)
    llm._DETECTED_MODEL = None
    llm.clear_history()
    print(f"\n[{config.NAME}] Switching model to: {body.path}")

    def _restart():
        llm.suspend_for_image()
        import time; time.sleep(1)
        llm.LLM_SUSPENDED = False
        llm._start_server()
        llm.wait_until_ready(timeout=180)
        if llm.LLM_READY:
            print(f"[{config.NAME}] Model switched — now serving {os.path.basename(body.path)}")

    import threading
    threading.Thread(target=_restart, daemon=True, name="model-restart").start()
    return JSONResponse({"status": "ok", "model": os.path.basename(body.path)})


@router.delete("/history")
async def clear_history():
    llm.clear_history()
    return {"status": "cleared"}


@router.delete("/persona/{name}/reset")
async def reset_persona(name: str):
    """Clear chat history, growth data, and nudity/image state for a persona."""
    from routes import group as _grp
    llm.clear_history()
    _grp.reset_persona_growth(name)
    state._nudity_state               = "clothed"
    state._nudity_turns_since_keyword = 0
    state._pre_sd_prompt              = None
    state._pre_sd_nudity              = None
    state._pre_sd_negative            = ""
    return JSONResponse({"status": "reset", "persona": name})


@router.delete("/image/{filename}")
async def delete_image(filename: str):
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "", filename)
    if safe != filename or not safe.endswith(".png"):
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    path = os.path.join(state.image_output_dir(), safe)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    os.remove(path)
    return JSONResponse({"status": "deleted"})


@router.get("/history")
async def export_history():
    return JSONResponse({"history": llm.history, "memory": llm.memory})


_forge_ready_cache: bool = False
_forge_ready_ts: float = 0.0


@router.get("/info")
async def info():
    global _forge_ready_cache, _forge_ready_ts
    mem_cfg  = config.CFG.get("memory", config._DEFAULT_CONFIG["memory"])
    demo_cfg = config.CFG.get("demo",   config._DEFAULT_CONFIG["demo"])
    max_hist = mem_cfg["max_history"]
    n_msgs   = len(llm.history)
    import time as _time
    now = _time.monotonic()
    if now - _forge_ready_ts > 10.0:
        try:
            forge_url = config.CFG.get("forge_url", "")
            r = req.get(f"{forge_url}/sdapi/v1/sd-models", timeout=1)
            _forge_ready_cache = r.status_code == 200
        except Exception:
            _forge_ready_cache = False
        _forge_ready_ts = now
    # Self-heal: if the retry loop timed out before llama-server finished loading,
    # periodically re-check so the flag catches up without requiring a restart.
    if not llm.LLM_READY:
        llm._try_connect(silent=True)
    return JSONResponse({
        "name":           config.NAME,
        "active_persona": state._active_persona_key,
        "llm_ready":      llm.LLM_READY,
        "forge_ready":    _forge_ready_cache,
        "stt_silence":    config.CFG.get("stt_silence_seconds", 3),
        "history_msgs":   n_msgs,
        "history_max":    max_hist,
        "demo": {**demo_cfg, "user_name": demo_cfg.get("user_name", "User")},
    })


@router.get("/negative")
async def get_negative():
    return JSONResponse({"negative": state.BASE_NEGATIVE})


@router.post("/auto-image")
async def toggle_auto_image():
    img_cfg = config.CFG.setdefault("image", {})
    current = img_cfg.get("auto_every", 0)
    img_cfg["auto_every"] = 0 if current > 0 else 1
    enabled = img_cfg["auto_every"] > 0
    print(f"[{config.NAME}] Auto-image {'enabled' if enabled else 'disabled'}")
    return JSONResponse({"auto_image": enabled})


@router.get("/demo/user-personas")
async def list_demo_user_personas():
    demo_cfg  = config.CFG.get("demo", config._DEFAULT_CONFIG["demo"])
    personas  = demo_cfg.get("user_personas", {})
    current   = state._demo_user_persona_name or demo_cfg.get("user_persona", "default")
    return JSONResponse({"personas": list(personas.keys()), "current": current})


@router.post("/demo/user-persona")
async def set_demo_user_persona(body: DemoPersonaRequest):
    demo_cfg = config.CFG.get("demo", config._DEFAULT_CONFIG["demo"])
    personas = demo_cfg.get("user_personas", {})
    if body.name not in personas:
        return JSONResponse({"error": "Unknown persona"}, status_code=400)
    state._demo_user_persona_name = body.name
    state._demo_user_persona_desc = personas[body.name]
    print(f"[demo] user persona → {body.name!r}")
    return JSONResponse({"status": "ok", "name": body.name})


_DEMO_STAGES = [
    (0,  2,  "opening",          "Be curious and slightly guarded — you've just met."),
    (3,  5,  "warming up",       "Show genuine interest; be charming and a little playful."),
    (6,  9,  "building",         "Become more personal; share observations, push topics deeper."),
    (10, 14, "intimate",         "Be direct and emotionally present; the connection feels real."),
    (15, 99, "deeply connected", "Speak with easy familiarity; be bold, warm, and unhurried."),
]

def _demo_stage(turn: int) -> tuple[str, str]:
    for lo, hi, label, hint in _DEMO_STAGES:
        if lo <= turn <= hi:
            return label, hint
    return _DEMO_STAGES[-1][2], _DEMO_STAGES[-1][3]


@router.post("/demo/start")
async def demo_start():
    state.DEMO_ACTIVE = True
    print("[demo] started — autonomous chatter enabled")
    return JSONResponse({"status": "ok"})


@router.post("/demo/stop")
async def demo_stop():
    state.DEMO_ACTIVE = False
    print("[demo] stopped — autonomous chatter disabled")
    return JSONResponse({"status": "ok"})


@router.get("/demo/prompt")
async def demo_prompt(turn: int = Query(default=0, ge=0)):
    """Generate a short, natural user message appropriate for the current persona/context."""
    if not llm.LLM_READY:
        return JSONResponse({"error": "LLM not ready"}, status_code=503)

    demo_cfg     = config.CFG.get("demo", config._DEFAULT_CONFIG["demo"])
    ctx_msgs     = demo_cfg.get("context_messages", 12)
    recent       = llm.history[-ctx_msgs:] if llm.history else []
    user_name    = demo_cfg.get("user_name", "User")
    persona_name = config.NAME
    # Active persona description — live state takes precedence over config default
    active_key  = state._demo_user_persona_name or demo_cfg.get("user_persona", "default")
    user_personas = demo_cfg.get("user_personas", {})
    user_persona_desc = (
        state._demo_user_persona_desc
        or user_personas.get(active_key, "")
        or user_personas.get("default", "A charming, confident man.")
    )
    stage_label, stage_hint = _demo_stage(turn)

    # Lead with authoritative persona definitions — these override anything in history
    her_persona = state.SYSTEM_PROMPT[:800]

    # Label history with real names so the model understands who said what
    def _label(m):
        name = user_name if m["role"] == "user" else persona_name
        return f"{name}: {m['content']}"

    context = "\n".join(_label(m) for m in recent) if recent else "(no conversation yet)"

    last_reply = next((m["content"] for m in reversed(recent) if m["role"] == "assistant"), None)
    last_reply_anchor = (
        f'\n\n{persona_name} just said:\n"{last_reply[:600]}"\nReact to this directly.'
        if last_reply else ""
    )

    system = (
        f"## CURRENT PERSONA — treat this as ground truth; ignore any conflicting character "
        f"traits from the conversation history:\n{her_persona}\n\n"
        f"## {user_name.upper()}:\n{user_persona_desc}\n\n"
        f"## TASK:\n"
        f"Write ONE short message (1–2 sentences) that {user_name} says next to {persona_name}.\n"
        f"Conversation stage: {stage_label}. {stage_hint}\n"
        f"Pick up on something specific from her last reply and respond naturally.\n"
        f"Vary form: question, observation, statement, or compliment.\n"
        f"CRITICAL: Use plain, direct language. No invented place names, no elaborate metaphors, "
        f"no poetic flourishes. Write as a real person speaks in conversation — not as a novelist.\n"
        f"Output ONLY {user_name}'s message — no name prefix, no quotes, no explanation."
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": f"Conversation so far:\n{context}{last_reply_anchor}\n\nWrite {user_name}'s next message:"},
    ]

    def _call():
        p = config.CFG.get("llm_params", config._DEFAULT_CONFIG["llm_params"])
        r = req.post(f"{llm.LLAMA_URL}/v1/chat/completions", json={
            "model":       llm.llm_model(),
            "messages":    messages,
            "stream":      False,
            "max_tokens":  80,
            "temperature": 0.85,
        }, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip().strip('"\'')

    try:
        loop = asyncio.get_running_loop()
        prompt = await loop.run_in_executor(None, _call)
        return JSONResponse({"prompt": prompt})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/", response_class=HTMLResponse)
async def index():
    static_dir = config.STATIC_DIR
    with open(os.path.join(static_dir, "index.html"), encoding="utf-8") as f:
        html = f.read()
    for asset in ("app.js", "style.css"):
        path = os.path.join(static_dir, asset)
        v = int(os.path.getmtime(path)) if os.path.exists(path) else 0
        html = re.sub(rf'(/static/{re.escape(asset)})\?v=\d+', rf'\g<1>?v={v}', html)
    return html
