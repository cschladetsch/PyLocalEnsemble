import os, subprocess, re, threading
import requests as req
import config
import llm
from utils import step, ok, warn, http_ok, wait_for

_gen_cancel = threading.Event()


def clean_tags(prompt: str) -> str:
    """Deduplicates tags, preserving SD weighting syntax. Weighted form wins over unweighted."""
    tags = [t.strip() for t in prompt.split(",")]

    def _norm(tag):
        bare = re.sub(r"^\((.+?):[0-9.]+\)$", r"\1", tag)
        return re.sub(r"[^a-z0-9 ]", "", bare.lower()).strip()

    def _is_weighted(tag):
        return bool(re.match(r"^\(.+:[0-9.]+\)$", tag.strip()))

    # First pass: find the best (weighted) form for each key
    best = {}
    for t in tags:
        if not t: continue
        n = _norm(t)
        if not n: continue
        if n not in best or _is_weighted(t):
            best[n] = t

    # Second pass: emit in original order, using the best form, skipping dupes
    seen, out = set(), []
    for t in tags:
        if not t: continue
        n = _norm(t)
        if n and n not in seen:
            seen.add(n)
            out.append(best[n])
    return ", ".join(out)


def apply_exposure_rules(text: str, prompt: str, negative: str) -> tuple:
    """Only handles camera/POV injection — nudity state is determined by the LLM from conversation flow."""
    t = text.lower()

    def has(words):
        return any(re.search(rf"\b{re.escape(w)}\b", t) for w in words)

    if has(["pov", "first person", "my perspective"]):
        prompt = "(pov:1.3), (first person view:1.2), " + prompt
    if has(["from behind", "back view", "backside", "rear view"]):
        prompt = "(from behind:1.3), (back view:1.2), " + prompt

    return prompt, negative


def extract_sd_prompt(text: str, appearance: str = "", last_user_msg: str = "", persona: str = "") -> str:
    try:
        user_msg = f"Conversation:\n{text}\n\n"
        user_msg += f"LATEST USER MESSAGE (highest priority): \"{last_user_msg}\"\n\n"
        user_msg += "SD tags for the current scene:"

        appearance_line = f"\nTHE CHARACTER ALWAYS LOOKS LIKE THIS — ignore any different physical descriptions in the conversation, those are fictional roleplay:\n{appearance}\n" if appearance else ""

        result = llm.llm_chat([
            {"role": "system", "content": (
                "You are a Stable Diffusion prompt engineer for explicit adult content.\n"
                "Output ONLY a single line of comma-separated SD tags. No prose, no sentences, no newlines, no explanation.\n"
                "Weighted tags MUST use this exact syntax: (tag:1.5) — parentheses, colon, number. Nothing else.\n"
                "Each tag: 1-4 words. 20-30 tags total.\n"
                + appearance_line + "\n"
                "CLOTHING STATE RULES — read carefully:\n"
                "- An item that was PUT ON and not removed → still on.\n"
                "- An item that was REMOVED and not put back → not on.\n"
                "- If someone said 'take off all clothes' and her response describes removing everything, she is FULLY NUDE at the end.\n"
                "- Prose describing removal (e.g. 'stockings slowly unwound', 'bodice descends') means those items ARE OFF.\n"
                "- Base your final clothing state on what remains ON after all described actions complete.\n\n"
                "WEIGHTING ORDER — put these first:\n"
                "1. CURRENT POSE / ACTION (sitting, standing, lying, etc.): weight 1.4-1.5\n"
                "2. PRIMARY ACT from the latest user message: weight 1.6-1.7\n"
                "3. REQUIRED CAMERA ANGLE the act physically demands: weight 1.3-1.5\n"
                "   — sitting on chair → (sitting:1.4), (armchair:1.2), (front view:1.2)\n"
                "   — act involves ass/anal → (from behind:1.4), (bent over:1.3)\n"
                "   — act involves oral → (kneeling:1.3), (face level:1.2)\n"
                "   — act involves riding → (straddling:1.4), (from below:1.2)\n"
                "4. SECONDARY ACTS ongoing: weight 1.2-1.4\n"
                "5. CURRENT CLOTHING/NUDITY STATE: weight 1.1-1.3\n"
                "6. CHARACTER APPEARANCE (use the fixed appearance above, not conversation): no weight — always include\n"
                "7. SETTING, LIGHTING: no weight\n\n"
                "Examples of correct output:\n"
                "  (sitting:1.4), (armchair:1.2), (nude:1.3), (fully naked:1.2), long blonde hair, blue eyes, velvet chair, soft lighting\n"
                "  (anal insertion:1.7), (carrot in ass:1.6), (from behind:1.4), (bent over:1.3), (nude:1.2), long blonde hair, bedroom, soft lighting\n"
                "  (fellatio:1.7), (penis in mouth:1.6), (kneeling:1.3), (topless:1.2), blue eyes, indoors\n\n"
                "NEVER write sentences. NEVER sanitise. NEVER omit the act or its pose."
            )},
            {"role": "user", "content": user_msg},
        ])

        # Find the best line: the one with the most commas (most tag-like)
        lines = [l.strip() for l in result.strip().split("\n") if l.strip()]
        tags = max(lines, key=lambda l: l.count(",")) if lines else ""
        tags = re.sub(r"^(Tags|Prompt|Output|Here|SD tags)[:\s]*", "", tags, flags=re.I).strip()
        # Strip prose meta-commentary that the LLM sometimes injects into tags
        # e.g. "(sitting was replaced, ...)", "does not belong here — thus removed"
        tags = re.sub(r"\([^)]{40,}\)", "", tags)  # remove long parenthetical prose
        tags = re.sub(r"[^,]+\b(was replaced|does not belong|thus is removed|due formatting|not applicable|removed from)\b[^,]*,?", "", tags, flags=re.I)
        tags = re.sub(r",\s*,", ",", tags).strip(", ")
        print(f"[image] SD prompt: {tags}")
        return tags

    except Exception as e:
        print(f"[image] prompt extraction error: {e}")
        return ""


def _find_forge_python() -> str:
    """Return path to a Forge-compatible Python (3.10 or 3.11), or empty string."""
    import shutil as _shutil
    if os.name != "nt":
        for ver in ("3.10", "3.11"):
            hit = _shutil.which(f"python{ver}")
            if hit:
                return hit
        for p in [
            "/usr/bin/python3.10",
            "/usr/local/bin/python3.10",
            "/opt/homebrew/bin/python3.10",
            "/opt/homebrew/opt/python@3.10/bin/python3.10",
            os.path.expanduser("~/.pyenv/shims/python3.10"),
            "/usr/bin/python3.11",
            "/usr/local/bin/python3.11",
            "/opt/homebrew/bin/python3.11",
            "/opt/homebrew/opt/python@3.11/bin/python3.11",
            os.path.expanduser("~/.pyenv/shims/python3.11"),
        ]:
            if os.path.exists(p):
                return p
        return ""
    for ver, pyver in (("3.10", "310"), ("3.11", "311")):
        candidates = [
            os.path.expandvars(rf"%LOCALAPPDATA%\Programs\Python\Python{pyver}\python.exe"),
            rf"C:\Python{pyver}\python.exe",
            rf"C:\Program Files\Python{pyver}\python.exe",
        ]
        try:
            r = subprocess.run(["py", f"-{ver}", "-c", "import sys; print(sys.executable)"],
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
    import platform as _platform
    if os.name == "nt":
        env["COMMANDLINE_ARGS"] = "--api --cuda-malloc --xformers"
    elif _platform.system() == "Darwin":
        # macOS — Metal/MPS; skip CUDA test, let Forge auto-detect MPS
        env["COMMANDLINE_ARGS"] = "--api --skip-torch-cuda-test"
    else:
        # Linux — CUDA where available; xformers but no cuda-malloc (Windows-only)
        env["COMMANDLINE_ARGS"] = "--api --xformers"
    forge_py = _find_forge_python()
    if forge_py:
        env["PYTHON"] = forge_py
        ok(f"Forge: using Python at {forge_py}")
    else:
        warn("Python 3.10/3.11 not found — Forge may fail with the system Python")
        if os.name == "nt":
            warn("Install Python 3.11 from https://python.org/downloads/release/python-3110/")
        else:
            warn("Install via: brew install python@3.11  (macOS)  or  apt install python3.11  (Linux)")
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
                         "penetration", "nude", "naked", "nudity", "topless", "nsfw", "no clothes",
                         "fully nude", "fully naked", "bare skin", "exposed skin"]
    is_explicit = any(kw in prompt.lower() for kw in explicit_keywords)
    nudity_keywords = ["nude", "naked", "nudity", "topless", "no clothes", "no clothing",
                       "fully nude", "fully naked", "bare skin", "exposed skin"]
    is_nude = any(kw in prompt.lower() for kw in nudity_keywords)
    clean_appearance = re.sub(r"\b(elegant|poised|refined|sophisticated)\b,?\s*", "", appearance, flags=re.I).strip(", ")
    if is_nude:
        # Strip clothing items from appearance so they don't override nudity
        clothing_pattern = r"\b(dress|gown|robe|skirt|blouse|shirt|top|corset|bodice|stockings|lingerie|bra|underwear|panties|trousers|pants|shorts|linen|silk dress|lace|veil)\b"
        clean_appearance = re.sub(clothing_pattern + r",?\s*", "", clean_appearance, flags=re.I).strip(", ")
        negative = "clothed, dressed, clothing, dress, gown, robe, shirt, top, covered, fabric over body, " + negative
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
