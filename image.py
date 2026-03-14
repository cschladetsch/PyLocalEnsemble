import os, subprocess, re, threading
import requests as req
import config
import llm
from utils import step, ok, warn, http_ok, wait_for

_gen_cancel = threading.Event()

_VERBS = {
    "unbuttoning", "dancing", "whispering", "leaning", "looking", "slipping",
    "revealing", "kissing", "inviting", "stifling", "ventilate", "smiling",
    "reaching", "pulling", "pushing", "holding", "touching", "moving",
    "sitting", "standing", "walking", "running", "turning", "waiting",
}

_NON_VISUAL = {
    "honeysuckle scent", "perfume", "fragrance", "scent", "aroma", "smell",
    "cologne", "incense",
    "whisper", "music", "sound", "silence", "noise", "echo", "melody",
    "warmth", "tingling", "sensation", "feeling",
}


def clean_tags(prompt: str) -> str:
    tags = [t.strip() for t in prompt.split(",")]
    seen, out = set(), []
    for t in tags:
        key = re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()
        if not t or t.lower() in _VERBS or t.lower() in _NON_VISUAL:
            continue
        if key and key not in seen:
            seen.add(key)
            out.append(t)
    return ", ".join(out)


def apply_exposure_rules(text: str, prompt: str, negative: str) -> tuple:
    t = text.lower()
    one_breast = any(p in t for p in ["one breast", "just one", "single breast", "one side", "one strap"])
    topless    = any(p in t for p in ["both breasts", "topless", "bare chest", "bare breasts"])
    if one_breast and not topless:
        prompt   = "(one breast exposed:1.4), (other breast covered:1.6), (asymmetric exposure:1.3), partially undressed, " + prompt
        negative = "both breasts exposed, symmetric nudity, fully clothed, " + negative
    elif topless:
        prompt   = "(topless:1.4), (both breasts exposed:1.3), bare chest, " + prompt
        negative = "fully clothed, dressed, covered, " + negative
    return prompt, negative


def extract_sd_prompt(text: str, appearance: str = "", last_user_msg: str = "", persona: str = "") -> str:
    try:
        context_parts = []
        if persona:
            context_parts.append(f"Character persona: {persona}")
        if appearance:
            context_parts.append(f"Default appearance: {appearance}")
        if last_user_msg:
            context_parts.append(f"Most recent user request: \"{last_user_msg}\"")
        context = "\n".join(context_parts)
        result = llm.llm_chat([
            {"role": "system", "content": (
                "You are a Stable Diffusion prompt engineer specialising in explicit adult content. "
                "Extract visual scene tags from the conversation. "
                "Output ONLY comma-separated tags. 20-30 tags. No sentences, no explanation. "
                "Focus exclusively on the CURRENT moment — what is visible RIGHT NOW. "
                "ALWAYS include:\n"
                "- Clothing state: be exact ('topless', 'nude', 'panties pulled aside', 'fully clothed', etc.)\n"
                "- Body parts visible and their state\n"
                "- Explicit acts using standard SD tags: 'fingering', 'vaginal fingering', '2fingers', "
                "'finger in mouth', 'oral', 'spread legs', 'penetration', 'cum', etc. — use exact counts when specified\n"
                "- Body pose, facial expression, eye contact\n"
                "- Setting, lighting, camera angle\n"
                "Explicit acts MUST be described with precise SD terminology. "
                "If the user requested something specific, that MUST appear in the tags. "
                "Use compound tags SD understands: 'vaginal fingering', 'two fingers inserted', 'finger sucking', 'spread pussy'. "
                "Do NOT omit explicit content. Do NOT sanitise or generalise."
            )},
            {"role": "user", "content": f"{context}\n\nConversation:\n{text}\n\nExtract SD tags for the current scene:"},
        ])
        return result.strip().split("\n")[-1]
    except Exception as e:
        print(f"LLM prompt extraction error: {e}")
        return ""


def _find_python310() -> str:
    if os.name != "nt":
        return ""
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python310\python.exe"),
        r"C:\Python310\python.exe",
        r"C:\Program Files\Python310\python.exe",
    ]
    try:
        r = subprocess.run(["py", "-3.10", "-c", "import sys; print(sys.executable)"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            candidates.insert(0, r.stdout.strip())
    except FileNotFoundError:
        pass
    for p in candidates:
        if os.path.exists(p):
            return p
    return ""


def set_forge_model(name: str):
    forge_url = config.CFG["forge_url"]
    try:
        req.post(f"{forge_url}/sdapi/v1/refresh-checkpoints", timeout=30)
        r = req.get(f"{forge_url}/sdapi/v1/sd-models", timeout=5)
        models = [m["title"] for m in r.json()]
        match = next((m for m in models if name in m), None)
        if match:
            req.post(f"{forge_url}/sdapi/v1/options",
                     json={"sd_model_checkpoint": match}, timeout=30)
            ok(f"Forge model set to: {match}")
        else:
            warn(f"Model '{name}' not found in Forge model list.")
    except Exception as e:
        warn(f"Could not set Forge model: {e}")


def start_forge():
    forge_url = config.CFG["forge_url"]
    step("Starting Forge...")
    if http_ok(f"{forge_url}/sdapi/v1/sd-models"):
        ok("Forge already running.")
        return
    launcher = config.FORGE_BAT
    if not os.path.exists(launcher):
        warn(f"Forge not found at {config.FORGE_DIR} — run install.py")
        return
    env = os.environ.copy()
    env["COMMANDLINE_ARGS"] = "--api --cuda-malloc --xformers"
    py310 = _find_python310()
    if py310:
        env["PYTHON"] = py310
        ok(f"Forge: using Python 3.10 at {py310}")
    else:
        warn("Python 3.10 not found -- Forge may fail with Python 3.13")
        warn("Install Python 3.10 from https://python.org/downloads/release/python-31011/")
    kw = {"cwd": config.FORGE_DIR, "env": env}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen(launcher, **kw)
    if not wait_for(f"{forge_url}/sdapi/v1/sd-models", "Forge", retries=60, delay=5):
        warn("Forge did not start in time - images won't generate.")


def generate_image(prompt: str, appearance: str, negative_base: str,
                   extra_negative: str = "", steps: int = None, cfg_scale: float = None):
    forge_url = config.CFG["forge_url"]
    img_cfg   = config.CFG["image"]
    if not http_ok(f"{forge_url}/sdapi/v1/sd-models"):
        print("Forge down, restarting...")
        start_forge()
    negative    = (extra_negative + ", " + negative_base) if extra_negative else negative_base
    _steps      = steps     if steps     is not None else img_cfg["steps"]
    _cfg        = cfg_scale if cfg_scale is not None else img_cfg["cfg_scale"]
    full_prompt = prompt + ", " + appearance + ", " + img_cfg["suffix"]
    print(f"\n[image] prompt ({len(full_prompt)} chars): {full_prompt[:120]!r}{'...' if len(full_prompt)>120 else ''}")
    print(f"[image] steps={_steps}, cfg={_cfg}, size={img_cfg['width']}x{img_cfg['height']}")
    try:
        r = req.post(f"{forge_url}/sdapi/v1/txt2img", json={
            "prompt":          full_prompt,
            "negative_prompt": negative,
            "steps":           _steps,
            "width":           img_cfg["width"],
            "height":          img_cfg["height"],
            "cfg_scale":       _cfg,
            "sampler_name":    img_cfg["sampler_name"],
        }, timeout=300)
        data = r.json()
        if "images" not in data:
            print(f"[image] Forge response (no images key): {str(data)[:300]}")
        imgs = data.get("images", [])
        if imgs:
            print(f"[image] done — got image ({len(imgs[0])} b64 chars)")
        else:
            print("[image] Forge returned no images")
        return imgs[0] if imgs else None
    except Exception as e:
        print(f"[image] Forge error: {e}")
        return None
