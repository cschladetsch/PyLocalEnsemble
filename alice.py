#!/usr/bin/env python3
"""
Alice - single file app.
Run: python alice.py
Installs everything missing, starts all services, opens the browser.
"""
import subprocess, sys, time, os, re, json, urllib.request, glob, webbrowser, threading

# -- Windows CUDA DLL loading fix --
if os.name == "nt":
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        bin_path = os.path.join(cuda_path, "bin")
        if os.path.exists(bin_path):
            try:
                os.add_dll_directory(bin_path)
            except AttributeError:
                pass # Python < 3.8

# ── Bootstrap pip deps ───────────────────────────────────────────────────────
try:
    import fastapi, uvicorn, pydantic
    import requests as req
except ImportError:
    print("Installing Python dependencies...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet",
        "fastapi", "uvicorn", "requests", "pydantic"])
    import requests as req

try:
    from llama_cpp import Llama
except ImportError:
    print("Installing llama-cpp-python...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet",
        "llama-cpp-python"])
    from llama_cpp import Llama

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

# ── Paths ────────────────────────────────────────────────────────────────────
ALICE_DIR   = os.path.dirname(os.path.abspath(__file__))
FORGE_DIR   = os.path.join(ALICE_DIR, "stable-diffusion-webui-forge")
FORGE_BAT   = os.path.join(FORGE_DIR, "webui.bat")
MODEL_DIR   = os.path.join(ALICE_DIR, "models")
ALICE_URL   = "http://localhost:8000"
CONFIG_FILE   = os.path.join(ALICE_DIR, "alice.json")
PERSONAS_FILE = os.path.join(ALICE_DIR, "personas.json")

# llama-cpp global (loaded at startup)
LLM = None

# ── Config ───────────────────────────────────────────────────────────────────
_DEFAULT_CONFIG = {
    "forge_url":    "http://localhost:7860",
    "model_path":   "",
    "ollama_model": "mistral-nemo",
    "appearance":   "woman, long blonde hair, blue eyes, elegant, poised, expressive eyes, soft lighting",
    "negative_prompt": "ugly, deformed, extra limbs, blurry, watermark, bad anatomy, low quality",
    "system_prompt": (
        "You are Alice. You are enigmatic, intelligent, and warm.\n"
        "You speak in measured, literary prose. You never break character.\n"
        "You are curious and attentive, with a calm, thoughtful tone."
    ),
    "image": {
        "steps":        25,
        "width":        512,
        "height":       768,
        "cfg_scale":    7,
        "sampler_name": "DPM++ 2M Karras",
        "suffix":       "photorealistic, highly detailed, 8k, masterpiece"
    },
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            merged = {**_DEFAULT_CONFIG, **data}
            merged["image"] = {**_DEFAULT_CONFIG["image"], **data.get("image", {})}
            if "system_prompt" not in data and "modelfile" in data:
                m = re.search(r'SYSTEM\s+"""(.*?)"""', data["modelfile"], re.DOTALL)
                if m:
                    merged["system_prompt"] = m.group(1).strip()
            print(f"        config: loaded {CONFIG_FILE}")
            return merged
        except Exception as e:
            print(f"        WARNING: could not load {CONFIG_FILE}: {e} -- using defaults")
    return _DEFAULT_CONFIG.copy()

CFG = load_config()

FORGE_URL        = CFG["forge_url"]
ALICE_APPEARANCE = CFG["appearance"]
BASE_NEGATIVE    = CFG["negative_prompt"]
SYSTEM_PROMPT    = CFG["system_prompt"]
IMG_CFG          = CFG["image"]

def _load_personas() -> dict:
    defaults = {
        "Default": {
            "system_prompt": SYSTEM_PROMPT,
            "appearance":    ALICE_APPEARANCE,
        }
    }
    if os.path.exists(PERSONAS_FILE):
        try:
            with open(PERSONAS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return {**defaults, **data}
        except Exception as e:
            print(f"WARNING: could not load personas.json: {e}")
    return defaults

PERSONAS = _load_personas()

# Avoid interactive-only actions when stdout/stdin are not attached to a TTY.
INTERACTIVE = sys.stdin.isatty() and sys.stdout.isatty()

# ── Helpers ──────────────────────────────────────────────────────────────────
def step(msg):  print(f"\n[Alice] {msg}")
def ok(msg):    print(f"        ok: {msg}")
def warn(msg):  print(f"        WARNING: {msg}")
def fail(msg):  print(f"\n        ERROR: {msg}"); sys.exit(1)

def http_ok(url, timeout=2):
    try:
        req.get(url, timeout=timeout)
        return True
    except Exception:
        return False

def wait_for(url, label, retries=40, delay=3):
    print(f"        Waiting for {label}", end="", flush=True)
    for _ in range(retries):
        if http_ok(url, timeout=2):
            print(" ready.")
            return True
        print(".", end="", flush=True)
        time.sleep(delay)
    print(" timed out.")
    return False

# ── LLM (llama-cpp-python) ───────────────────────────────────────────────────
history = []


def _find_gguf() -> str:
    """Auto-detect a GGUF model: check config, local models/, then Ollama cache."""
    configured = CFG.get("model_path", "")
    if configured and os.path.exists(configured):
        return configured

    # Local models/ dir
    local = glob.glob(os.path.join(ALICE_DIR, "models", "*.gguf"))
    if local:
        return local[0]

    # Ollama blob cache — find blob via manifest for configured model
    ollama_root = os.path.join(os.path.expanduser("~"), ".ollama", "models")
    ollama_blobs = os.path.join(ollama_root, "blobs")
    model_name = CFG.get("ollama_model", "mistral-nemo").split(":")[0]
    manifest_path = os.path.join(ollama_root, "manifests", "registry.ollama.ai",
                                 "library", model_name, "latest")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            for layer in manifest.get("layers", []):
                if layer.get("mediaType") == "application/vnd.ollama.image.model":
                    digest = layer["digest"].replace(":", "-")
                    blob_path = os.path.join(ollama_blobs, digest)
                    if os.path.exists(blob_path):
                        return blob_path
        except Exception:
            pass

    # Fallback: largest blob
    if os.path.exists(ollama_blobs):
        blobs = [os.path.join(ollama_blobs, f) for f in os.listdir(ollama_blobs)
                 if not f.endswith(".part")]
        if blobs:
            return max(blobs, key=os.path.getsize)

    return ""


def load_llm():
    global LLM
    step("Loading LLM...")
    path = _find_gguf()
    if not path:
        warn("No GGUF model found. Add a .gguf file to models/ or set 'model_path' in alice.json.")
        return
    ok(f"Model: {os.path.basename(path)}")
    try:
        LLM = Llama(model_path=path, n_gpu_layers=-1, n_ctx=4096, verbose=False)
    except Exception as e:
        warn(f"GPU load failed ({e}), retrying CPU-only...")
        LLM = Llama(model_path=path, n_gpu_layers=0, n_ctx=4096, verbose=False)
    ok("LLM ready.")


def _llm_chat(messages: list) -> str:
    if LLM is None:
        raise RuntimeError("LLM not loaded")
    result = LLM.create_chat_completion(messages=messages)
    return result["choices"][0]["message"]["content"]


def _llm_complete(prompt: str) -> str:
    if LLM is None:
        raise RuntimeError("LLM not loaded")
    result = LLM(prompt, max_tokens=512, stop=["\n\n"])
    return result["choices"][0]["text"]


def chat_alice(message: str) -> str:
    history.append({"role": "user", "content": message})
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        reply = _llm_chat(messages)
    except Exception as e:
        history.pop()
        raise RuntimeError(f"LLM error: {e}")
    reply = re.sub(r'^[Aa]lice\s*[:"]\s*', '', reply).strip().strip('""\u201c\u201d')
    history.append({"role": "assistant", "content": reply})
    return reply


_VERBS = {
    "unbuttoning", "dancing", "whispering", "leaning", "looking", "slipping",
    "revealing", "kissing", "inviting", "stifling", "ventilate", "smiling",
    "reaching", "pulling", "pushing", "holding", "touching", "moving",
    "sitting", "standing", "walking", "running", "turning", "waiting",
}

_NON_VISUAL = {
    # scent
    "honeysuckle scent", "perfume", "fragrance", "scent", "aroma", "smell",
    "cologne", "incense",
    # sound
    "whisper", "music", "sound", "silence", "noise", "echo", "melody",
    # touch/feel (abstract)
    "warmth", "tingling", "sensation", "feeling",
}

def _clean_tags(prompt: str) -> str:
    """Remove verb/action tags and non-visual sensory tags SD can't render."""
    tags = [t.strip() for t in prompt.split(",")]
    tags = [t for t in tags if t
            and t.lower() not in _VERBS
            and t.lower() not in _NON_VISUAL]
    return ", ".join(tags)


def _apply_exposure_rules(text: str, prompt: str, negative: str) -> tuple:
    """Detect partial-exposure language and inject precise SD tags."""
    t = text.lower()

    one_breast = any(p in t for p in [
        "one breast", "just one", "single breast", "one side", "one strap"
    ])
    topless = any(p in t for p in ["both breasts", "topless", "bare chest", "bare breasts"])

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

        result = _llm_chat([
            {"role": "system", "content": (
                "You extract Stable Diffusion image prompts from conversations. "
                "Output ONLY comma-separated tags. Maximum 30 tags. "
                "No sentences, no explanation, no lists, nothing else. "
                "Focus on the MOST RECENT exchange — what is happening RIGHT NOW. "
                "Describe the current visual state: pose, body position, clothing state, expression, setting, lighting, mood. "
                "Use the character persona to infer appearance details (ethnicity, costume, setting) when not explicit in the conversation. "
                "If the persona is Egyptian, include: dark skin, kohl eyes, Egyptian headdress, gold jewellery, linen, hieroglyphs, etc. as appropriate. "
                "If clothing is being removed or adjusted, describe the resulting state not the action. "
                "Never output verbs — only visual nouns and adjectives."
            )},
            {"role": "user", "content": (
                f"{context}\n\nConversation:\n{text}\n\nExtract SD tags for the current scene:"
            )},
        ])
        tags = result.strip().split("\n")[-1]
        return tags
    except Exception as e:
        print(f"LLM prompt extraction error: {e}")
        return ""


def find_python310() -> str:
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


def ensure_forge():
    step("Checking Stable Diffusion Forge...")
    if not os.path.exists(FORGE_BAT):
        ok("Cloning Forge (large repo, may take several minutes)...")
        result = subprocess.run(["git", "clone",
            "https://github.com/lllyasviel/stable-diffusion-webui-forge",
            FORGE_DIR])
        if result.returncode != 0:
            fail("Failed to clone Forge. Ensure git is installed and you have internet access.")
        ok("Forge cloned.")
    else:
        ok("Forge present.")

    # If venv was built with wrong Python, nuke it so Forge rebuilds with 3.10
    venv_python = os.path.join(FORGE_DIR, "venv", "Scripts", "python.exe")
    if os.path.exists(venv_python):
        try:
            r = subprocess.run([venv_python, "--version"], capture_output=True, text=True)
            version = r.stdout.strip() + r.stderr.strip()
            if "3.10" not in version:
                warn(f"Forge venv is {version.strip()}, needs 3.10 -- deleting venv for rebuild...")
                import shutil
                shutil.rmtree(os.path.join(FORGE_DIR, "venv"))
                ok("Venv deleted. Forge will rebuild with Python 3.10 on next start.")
            else:
                ok(f"Forge venv: {version.strip()}")
        except Exception as e:
            warn(f"Could not check venv Python version: {e}")

    checkpoints = glob.glob(os.path.join(FORGE_DIR, "models", "Stable-diffusion", "*.safetensors"))
    if not checkpoints:
        ok("No checkpoint found — will download Realistic Vision V5.1...")
    ok(f"Checkpoint: {os.path.basename(checkpoints[0])}" if checkpoints else "Downloading...")


_RV_FILENAME = "Realistic_Vision_V5.1_fp16-no-ema.safetensors"
_RV_URL = ("https://huggingface.co/SG161222/Realistic_Vision_V5.1_noVAE"
           "/resolve/main/Realistic_Vision_V5.1_fp16-no-ema.safetensors")


def ensure_checkpoint():
    step("Checking checkpoint...")
    sd_dir = os.path.join(FORGE_DIR, "models", "Stable-diffusion")
    os.makedirs(sd_dir, exist_ok=True)
    rv_path = os.path.join(sd_dir, _RV_FILENAME)
    if os.path.exists(rv_path):
        ok(f"Realistic Vision already present.")
        return
    ok(f"Downloading Realistic Vision V5.1 (2.1 GB)...")
    _download_with_progress(_RV_URL, rv_path)
    ok("Download complete.")


def _set_forge_model(name: str):
    """Tell a running Forge instance to switch to the named checkpoint."""
    try:
        req.post(f"{FORGE_URL}/sdapi/v1/refresh-checkpoints", timeout=30)
        r = req.get(f"{FORGE_URL}/sdapi/v1/sd-models", timeout=5)
        models = [m["title"] for m in r.json()]
        match = next((m for m in models if name in m), None)
        if match:
            req.post(f"{FORGE_URL}/sdapi/v1/options",
                     json={"sd_model_checkpoint": match}, timeout=30)
            ok(f"Forge model set to: {match}")
        else:
            warn(f"Model '{name}' not found in Forge model list.")
    except Exception as e:
        warn(f"Could not set Forge model: {e}")


def start_forge():
    step("Starting Forge...")
    if http_ok(f"{FORGE_URL}/sdapi/v1/sd-models"):
        ok("Forge already running.")
        return
    env = os.environ.copy()
    env["COMMANDLINE_ARGS"] = "--api --cuda-malloc"
    py310 = find_python310()
    if py310:
        env["PYTHON"] = py310
        ok(f"Forge: using Python 3.10 at {py310}")
    else:
        warn("Python 3.10 not found -- Forge may fail with Python 3.13")
        warn("Install Python 3.10 from https://python.org/downloads/release/python-31011/")
    subprocess.Popen(FORGE_BAT, cwd=FORGE_DIR, env=env,
                     creationflags=subprocess.CREATE_NEW_CONSOLE)
    if not wait_for(f"{FORGE_URL}/sdapi/v1/sd-models", "Forge", retries=60, delay=5):
        warn("Forge did not start in time - images won't generate.")


# ── FastAPI app ──────────────────────────────────────────────────────────────
app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(ALICE_DIR, "static")), name="static")


class ChatRequest(BaseModel):
    message: str


class ImageRequest(BaseModel):
    extra: str = ""

class GenerateRequest(BaseModel):
    prompt: str
    steps: int = None
    cfg_scale: float = None


def generate_image(prompt: str, extra_negative: str = "", steps: int = None, cfg_scale: float = None):
    if not http_ok(f"{FORGE_URL}/sdapi/v1/sd-models"):
        print("Forge down, restarting...")
        start_forge()
    negative = (extra_negative + ", " + BASE_NEGATIVE) if extra_negative else BASE_NEGATIVE
    try:
        r = req.post(f"{FORGE_URL}/sdapi/v1/txt2img", json={
            "prompt":          prompt + ", " + ALICE_APPEARANCE + ", " + IMG_CFG["suffix"],
            "negative_prompt": negative,
            "steps":           steps if steps is not None else IMG_CFG["steps"],
            "width":           IMG_CFG["width"],
            "height":          IMG_CFG["height"],
            "cfg_scale":       cfg_scale if cfg_scale is not None else IMG_CFG["cfg_scale"],
            "sampler_name":    IMG_CFG["sampler_name"],
        }, timeout=300)
        data = r.json()
        if "images" not in data:
            print(f"Forge response (no images key): {str(data)[:300]}")
        imgs = data.get("images", [])
        return imgs[0] if imgs else None
    except Exception as e:
        print(f"Forge error: {e}")
        return None


@app.post("/chat")
async def chat(body: ChatRequest):
    try:
        reply = chat_alice(body.message)
        return JSONResponse({"reply": reply})
    except Exception as e:
        print(f"Chat error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/generate")
async def generate_raw(body: GenerateRequest):
    image = generate_image(body.prompt, steps=body.steps, cfg_scale=body.cfg_scale)
    if image:
        return JSONResponse({"image": image})
    return JSONResponse({"error": "No image generated."}, status_code=500)


@app.post("/interrupt")
async def interrupt():
    try:
        req.post(f"{FORGE_URL}/sdapi/v1/interrupt", timeout=5)
        return {"status": "interrupted"}
    except Exception as e:
        print(f"Interrupt error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/image")
async def image_from_history(body: ImageRequest):
    if not history:
        return JSONResponse({"error": "No conversation history yet."}, status_code=400)
    recent = history[-6:]
    messages = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in recent)
    last_user = next((m["content"] for m in reversed(recent) if m["role"] == "user"), "")
    base_prompt = extract_sd_prompt(messages, appearance=ALICE_APPEARANCE, last_user_msg=last_user, persona=SYSTEM_PROMPT)

    # Split extra into positive tags and "no X" -> negative tags
    positive_parts = []
    negative_parts = []
    for token in [t.strip() for t in body.extra.split(",") if t.strip()]:
        if token.lower().startswith("no "):
            negative_parts.append(token[3:].strip())
        else:
            positive_parts.append(token)

    positive_extra = ", ".join(positive_parts)
    extra_negative = ", ".join(negative_parts)
    base_prompt = _clean_tags(base_prompt)
    prompt = (positive_extra + ", " + base_prompt) if positive_extra else base_prompt
    prompt, extra_negative = _apply_exposure_rules(messages, prompt, extra_negative)
    image = generate_image(prompt, extra_negative=extra_negative)
    return JSONResponse({"sd_prompt": ALICE_APPEARANCE + ", " + prompt, "image": image})




class VideoRequest(BaseModel):
    extra: str = ""


@app.post("/video")
async def video_from_history(body: VideoRequest):
    if not history:
        return JSONResponse({"error": "No conversation history yet."}, status_code=400)
    recent = history[-6:]
    messages = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in recent)
    last_user = next((m["content"] for m in reversed(recent) if m["role"] == "user"), "")
    base_prompt = extract_sd_prompt(messages, appearance=ALICE_APPEARANCE, last_user_msg=last_user, persona=SYSTEM_PROMPT)

    positive_parts = []
    negative_parts = []
    for token in [t.strip() for t in body.extra.split(",") if t.strip()]:
        if token.lower().startswith("no "):
            negative_parts.append(token[3:].strip())
        else:
            positive_parts.append(token)

    positive_extra = ", ".join(positive_parts)
    extra_negative = ", ".join(negative_parts)
    prompt = (positive_extra + ", " + base_prompt) if positive_extra else base_prompt
    full_prompt = prompt + ", " + ALICE_APPEARANCE + ", " + IMG_CFG["suffix"]
    negative = (extra_negative + ", " + BASE_NEGATIVE) if extra_negative else BASE_NEGATIVE

    vid_cfg = CFG.get("video", {})
    try:
        r = req.post(f"{FORGE_URL}/sdapi/v1/txt2img", json={
            "prompt":          full_prompt,
            "negative_prompt": negative,
            "steps":           vid_cfg.get("steps", 20),
            "width":           vid_cfg.get("width", 512),
            "height":          vid_cfg.get("height", 512),
            "cfg_scale":       vid_cfg.get("cfg_scale", 7),
            "sampler_name":    vid_cfg.get("sampler_name", "DPM++ 2M Karras"),
            "script_name":     "AnimateDiff",
            "script_args":     [
                vid_cfg.get("motion_module", "mm_sd_v15_v2.ckpt"),
                vid_cfg.get("frames", 16),
                vid_cfg.get("fps", 8),
                True,
                vid_cfg.get("format", "GIF"),
                False,
            ],
        }, timeout=600)
        data = r.json()
        if "video" in data:
            return JSONResponse({"video": data["video"], "sd_prompt": full_prompt})
        imgs = data.get("images", [])
        if imgs:
            return JSONResponse({"video": imgs[0], "sd_prompt": full_prompt, "fallback": True})
        
        # Log the full response to the console to see what's wrong
        print(f"\n[Alice] Forge Video Response: {str(data)[:1000]}")
        return JSONResponse({"error": "No output from Forge. Is AnimateDiff installed?"}, status_code=500)
    except Exception as e:
        print(f"Forge video error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/personas")
async def list_personas():
    return JSONResponse({"personas": list(PERSONAS.keys())})


@app.post("/persona/{name}")
async def switch_persona(name: str):
    global ALICE_APPEARANCE, SYSTEM_PROMPT
    if name not in PERSONAS:
        return JSONResponse({"error": f"Persona '{name}' not found."}, status_code=404)
    p = PERSONAS[name]
    ALICE_APPEARANCE = p.get("appearance", ALICE_APPEARANCE)
    SYSTEM_PROMPT    = p.get("system_prompt", SYSTEM_PROMPT)
    history.clear()
    print(f"\n[Alice] Switched to persona: {name}")
    return JSONResponse({"status": "ok", "persona": name})


@app.delete("/history")
async def clear():
    history.clear()
    return {"status": "cleared"}


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(ALICE_DIR, "static", "index.html"), encoding="utf-8") as f:
        return f.read()


def _download_with_progress(url: str, dest: str):
    def reporthook(count, block_size, total_size):
        if total_size > 0:
            pct = min(count * block_size / total_size * 100, 100)
            print(f"\r        {pct:5.1f}%", end="", flush=True)
    urllib.request.urlretrieve(url, dest, reporthook=reporthook)
    print()


def ensure_animatediff():
    step("Checking AnimateDiff extension...")
    ext_dir = os.path.join(FORGE_DIR, "extensions", "sd-webui-animatediff")
    if not os.path.exists(ext_dir):
        ok("Cloning AnimateDiff extension...")
        try:
            subprocess.run(["git", "clone", "https://github.com/continue-revolution/sd-webui-animatediff", ext_dir], check=True)
            ok("AnimateDiff extension cloned.")
        except Exception as e:
            warn(f"Failed to clone AnimateDiff extension: {e}")
            return

    model_dir = os.path.join(ext_dir, "model")
    os.makedirs(model_dir, exist_ok=True)
    
    motion_module = "mm_sd_v15_v2.ckpt"
    module_path = os.path.join(model_dir, motion_module)
    
    if not os.path.exists(module_path):
        url = f"https://huggingface.co/guoyww/AnimateDiff/resolve/main/{motion_module}"
        ok(f"Downloading {motion_module} (~1.6 GB)...")
        try:
            _download_with_progress(url, module_path)
            ok("Motion module downloaded.")
        except Exception as e:
            if os.path.exists(module_path):
                os.remove(module_path)
            warn(f"Failed to download motion module: {e}")
    else:
        ok("Motion module present.")


# ── Entry point ──────────────────────────────────────────────────────────────
def _startup():
    try:
        load_llm()
        ensure_forge()
        ensure_checkpoint()
        ensure_animatediff()
        start_forge()
        _set_forge_model(_RV_FILENAME)
    except Exception as e:
        print(f"\n[Alice] FATAL ERROR IN STARTUP THREAD: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print()
    print("=" * 60)
    print("  Alice")
    print("=" * 60)

    print()
    print(f"[Alice] Starting server at {ALICE_URL}")
    
    # Start background tasks
    t = threading.Thread(target=_startup, daemon=True)
    t.start()

    if INTERACTIVE:
        # Give the server a moment to start before opening browser
        def open_browser():
            time.sleep(2)
            webbrowser.open(ALICE_URL)
        threading.Thread(target=open_browser, daemon=True).start()
    else:
        print("        NOTE: Non-interactive session detected; not opening browser.")
    
    try:
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
    except Exception as e:
        print(f"\n[Alice] Server failed: {e}")
