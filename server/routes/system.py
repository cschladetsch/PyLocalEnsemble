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

class DemoPersonaRequest(BaseModel):
    name: str


@router.get("/models")
async def list_models():
    models  = [{"name": n, "path": p} for n, p in llm.list_models()]
    current = llm.llm_model()
    return JSONResponse({"models": models, "current": current})


@router.post("/model")
async def switch_model(body: ModelSwitchRequest):
    config.CFG["llama_model"] = body.path
    llm._DETECTED_MODEL = None  # force re-detect on next request
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
    path = os.path.join(config.STATIC_DIR, "outputs", safe)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    os.remove(path)
    return JSONResponse({"status": "deleted"})


@router.get("/history")
async def export_history():
    return JSONResponse({"history": llm.history, "memory": llm.memory})


@router.get("/info")
async def info():
    mem_cfg  = config.CFG.get("memory", config._DEFAULT_CONFIG["memory"])
    demo_cfg = config.CFG.get("demo",   config._DEFAULT_CONFIG["demo"])
    max_hist = mem_cfg["max_history"]
    n_msgs   = len(llm.history)
    return JSONResponse({
        "name":          config.NAME,
        "llm_ready":     llm.LLM_READY,
        "stt_silence":   config.CFG.get("stt_silence_seconds", 3),
        "history_msgs":  n_msgs,
        "history_max":   max_hist,
        "demo": {**demo_cfg, "user_name": demo_cfg.get("user_name", "Christian")},
    })


@router.get("/negative")
async def get_negative():
    return JSONResponse({"negative": state.BASE_NEGATIVE})


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


@router.get("/demo/prompt")
async def demo_prompt(turn: int = Query(default=0, ge=0)):
    """Generate a short, natural user message appropriate for the current persona/context."""
    if not llm.LLM_READY:
        return JSONResponse({"error": "LLM not ready"}, status_code=503)

    recent = llm.history[-10:] if llm.history else []
    demo_cfg     = config.CFG.get("demo", config._DEFAULT_CONFIG["demo"])
    user_name    = demo_cfg.get("user_name", "Christian")
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
