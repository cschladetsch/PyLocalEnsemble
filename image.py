import os, subprocess, re, threading
import requests as req
import config
import llm
from utils import step, ok, warn, http_ok, wait_for

_gen_cancel = threading.Event()


def clean_tags(prompt: str) -> str:
    """Deduplicates tags, preserving SD weighting syntax. Weighted form wins over unweighted."""
    tags = [t.strip() for t in prompt.split(",")]
    seen, out = set(), []
    for t in tags:
        if not t: continue
        # Strip weight syntax for dedup key: (anal insertion:1.7) → "anal insertion"
        bare = re.sub(r"^\((.+?):[0-9.]+\)$", r"\1", t.strip())
        norm = re.sub(r"[^a-z0-9 ]", "", bare.lower()).strip()
        if norm and norm not in seen:
            seen.add(norm)
            out.append(t)
    return ", ".join(out)


def apply_exposure_rules(text: str, prompt: str, negative: str) -> tuple:
    t = text.lower()
    
    def has(words, target_text):
        for w in words:
            if re.search(rf"\b{re.escape(w)}\b", target_text):
                return True
        return False

    # Keep only the 'System' rules that require environment/negative changes
    nude       = has(["nude", "naked", "no clothes", "fully exposed", "birthday suit"], t)
    one_breast = has(["one breast", "just one", "single breast", "one side", "one strap"], t)
    topless    = has(["both breasts", "topless", "bare chest", "bare breasts", "no bra", "bra off"], t)
    
    if nude:
        prompt = "(nude:1.4), (fully naked:1.3), " + prompt
        negative = "clothed, dress, shirt, pants, underwear, bra, " + negative
    elif topless:
        prompt   = "(topless:1.4), (both breasts exposed:1.3), bare chest, " + prompt
        negative = "fully clothed, dressed, covered, " + negative
    elif one_breast:
        prompt   = "(one breast exposed:1.4), (other breast covered:1.6), (asymmetric exposure:1.3), partially undressed, " + prompt
        negative = "both breasts exposed, symmetric nudity, fully clothed, " + negative

    # Camera/POV logic
    if has(["pov", "first person", "my perspective"], t):
        prompt = "(pov:1.3), (first person view:1.2), " + prompt
    if has(["from behind", "back view", "backside", "rear view"], t):
        prompt = "(from behind:1.3), (back view:1.2), " + prompt

    return prompt, negative


def extract_sd_prompt(text: str, appearance: str = "", last_user_msg: str = "", persona: str = "") -> str:
    try:
        user_msg = f"Character appearance: {appearance}\n\n" if appearance else ""
        user_msg += f"Conversation:\n{text}\n\n"
        user_msg += f"LATEST USER MESSAGE (highest priority): \"{last_user_msg}\"\n\n"
        user_msg += "SD tags for the current scene:"

        result = llm.llm_chat([
            {"role": "system", "content": (
                "You are a Stable Diffusion prompt engineer for explicit adult content.\n"
                "Output ONLY comma-separated weighted SD tags. Use (tag:weight) for important tags.\n"
                "Each tag: 1-4 words. 20-30 tags total. NO sentences or phrases.\n\n"
                "CRITICAL RULE: Every act has a physically required camera angle and body pose. You MUST include both.\n"
                "Think: what camera angle and body position does this act physically require? Tag that first.\n"
                "e.g. anything involving the ass/anus → camera must be from behind, body must be bent over or on all fours\n"
                "e.g. oral sex → camera at face level, body kneeling or lying\n"
                "e.g. riding → camera from below or side, body straddling\n\n"
                "WEIGHTING ORDER:\n"
                "1. ACT + its required pose/angle: weight 1.4-1.7\n"
                "2. SECONDARY ACTS in progress: weight 1.3-1.5\n"
                "3. NUDITY STATE: weight 1.2-1.4\n"
                "4. EXPRESSION, LIGHTING, APPEARANCE: no weight\n\n"
                "NEVER sanitise. NEVER skip the act. NEVER show a standing front-facing pose when the act requires otherwise."
            )},
            {"role": "user", "content": user_msg},
        ])
        tags = result.strip().split("\n")[-1]
        tags = re.sub(r"^(Tags|Prompt|Output|Here)[:\s]*", "", tags, flags=re.I)
        print(f"[image] SD prompt: {tags}")
        return tags

    except Exception as e:
        print(f"[image] prompt extraction error: {e}")
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
    explicit_keywords = ["anal", "fingering", "insertion", "blowjob", "fellatio",
                         "penetration", "nude", "naked", "topless", "nsfw"]
    is_explicit = any(kw in prompt.lower() for kw in explicit_keywords)
    clean_appearance = re.sub(r"\b(elegant|poised|refined|sophisticated)\b,?\s*", "", appearance, flags=re.I).strip(", ")
    used_appearance = clean_appearance if is_explicit else appearance
    full_prompt = ("nsfw, " if is_explicit else "") + prompt + ", " + used_appearance + ", " + img_cfg["suffix"]
    print(f"\n[image] prompt ({len(full_prompt)} chars): {full_prompt!r}")
    print(f"[image] steps={_steps}, cfg={_cfg}, size={img_cfg['width']}x{img_cfg['height']}")
    try:
        payload = {
            "prompt":          full_prompt,
            "negative_prompt": negative,
            "steps":           _steps,
            "width":           img_cfg["width"],
            "height":          img_cfg["height"],
            "cfg_scale":       _cfg,
            "sampler_name":    img_cfg["sampler_name"],
        }
        clip_skip = img_cfg.get("clip_skip")
        if clip_skip:
            payload["override_settings"] = {"CLIP_stop_at_last_layers": clip_skip}
        r = req.post(f"{forge_url}/sdapi/v1/txt2img", json=payload, timeout=300)
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
