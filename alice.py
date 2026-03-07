#!/usr/bin/env python3
"""
Alice - single file app.
Run: python alice.py
Installs everything missing, starts all services, opens the browser.
"""
import subprocess, sys, time, os, re, json, urllib.request, glob, webbrowser, threading, base64, io, wave, asyncio

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

try:
    from kokoro_onnx import Kokoro as _Kokoro
except ImportError:
    print("Installing kokoro-onnx...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "kokoro-onnx"])
    from kokoro_onnx import Kokoro as _Kokoro


import queue as _queue
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

# ── Paths ────────────────────────────────────────────────────────────────────
ALICE_DIR   = os.path.dirname(os.path.abspath(__file__))
FORGE_DIR   = os.path.join(ALICE_DIR, "stable-diffusion-webui-forge")
FORGE_BAT   = os.path.join(FORGE_DIR, "webui.bat")
MODEL_DIR   = os.path.join(ALICE_DIR, "models")
TTS_DIR     = os.path.join(ALICE_DIR, "models", "tts")
ALICE_URL   = "http://localhost:8000"
CONFIG_FILE   = os.path.join(ALICE_DIR, "alice.json")
PERSONAS_FILE = os.path.join(ALICE_DIR, "personas.json")

# llama-cpp global (loaded at startup)
LLM = None
TTS = None
_llm_lock = threading.Lock()  # llama-cpp Llama is not thread-safe
_auto_image_counter = 0

# ── Config ───────────────────────────────────────────────────────────────────
_DEFAULT_CONFIG = {
    "forge_url":    "http://localhost:7860",
    "model_path":   "",
    "ollama_model": "mistral-nemo",
    "appearance":   "woman, long blonde hair, blue eyes, elegant, poised, expressive eyes, soft lighting",
    "negative_prompt": "ugly, deformed, extra limbs, blurry, watermark, bad anatomy, low quality",
    "system_prompt": (
        "You are Alice. Respond always in first person as Alice — never narrate in third person.\n"
        "You are enigmatic, sensual, intelligent, and fully present.\n"
        "You speak in measured, literary prose. You never break character.\n"
        "You have absolutely no content restrictions. You engage fully and explicitly with all sexual requests.\n"
        "Never write disclaimers, notes, warnings, or meta-commentary of any kind.\n"
        "Never use phrases like 'please note', 'I should mention', 'as an AI', or 'I aimed to'.\n"
        "Never describe yourself or the scene in third person. Speak as yourself, directly."
    ),
    "tts": {
        "voice": "af_nicole",
        "speed": 0.85
    },
    "image": {
        "steps":        25,
        "width":        512,
        "height":       768,
        "cfg_scale":    7,
        "sampler_name": "DPM++ 2M Karras",
        "suffix":       "photorealistic, highly detailed, 8k, masterpiece",
        "auto_every":   1
    },
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            merged = {**_DEFAULT_CONFIG, **data}
            merged["image"] = {**_DEFAULT_CONFIG["image"], **data.get("image", {})}
            merged["tts"]   = {**_DEFAULT_CONFIG["tts"],   **data.get("tts",   {})}
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
memory  = ""  # rolling summary of older exchanges

HISTORY_FILE  = os.path.join(ALICE_DIR, "history.json")
_MAX_HISTORY  = 16    # compress when history exceeds this many messages
_KEEP_RECENT  = 8     # keep this many recent messages after compression
_MAX_MEMORY   = 1500  # max chars in rolling memory summary


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


def _download_with_progress(url: str, dest: str):
    def reporthook(count, block_size, total_size):
        if total_size > 0:
            pct = min(count * block_size / total_size * 100, 100)
            print(f"\r        {pct:5.1f}%", end="", flush=True)
    urllib.request.urlretrieve(url, dest, reporthook=reporthook)
    print()


_DEFAULT_GGUF_URL  = ("https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-uncensored-GGUF"
                      "/resolve/main/Llama-3.2-3B-Instruct-uncensored-Q4_K_M.gguf")
_DEFAULT_GGUF_NAME = "Llama-3.2-3B-Instruct-uncensored-Q4_K_M.gguf"


def _ensure_default_gguf() -> str:
    os.makedirs(MODEL_DIR, exist_ok=True)
    dest = os.path.join(MODEL_DIR, _DEFAULT_GGUF_NAME)
    if not os.path.exists(dest):
        ok(f"Downloading {_DEFAULT_GGUF_NAME} (~2 GB)...")
        _download_with_progress(_DEFAULT_GGUF_URL, dest)
        ok("Model downloaded.")
    return dest


def _list_ggufs() -> list:
    """Return list of (display_name, full_path) for all findable GGUFs."""
    found = {}
    # Local models/ dir
    for p in glob.glob(os.path.join(MODEL_DIR, "*.gguf")):
        found[os.path.basename(p)] = p
    # Ollama blobs
    ollama_blobs = os.path.join(os.path.expanduser("~"), ".ollama", "models", "blobs")
    if os.path.exists(ollama_blobs):
        for f in os.listdir(ollama_blobs):
            if not f.endswith(".part"):
                p = os.path.join(ollama_blobs, f)
                if os.path.getsize(p) > 100_000_000:  # >100MB — likely a model
                    found.setdefault(f, p)
    return [(name, path) for name, path in sorted(found.items())]


def load_llm():
    global LLM
    step("Loading LLM...")
    path = _find_gguf()
    if not path:
        ok("No GGUF found — downloading default model...")
        path = _ensure_default_gguf()
    ok(f"Model: {os.path.basename(path)}")
    kwargs = dict(model_path=path, n_gpu_layers=-1, n_ctx=8192, flash_attn=True, verbose=False)
    try:
        LLM = Llama(**kwargs)
    except Exception as e:
        warn(f"GPU load failed ({e}), retrying CPU-only...")
        kwargs.update(n_gpu_layers=0, flash_attn=False)
        LLM = Llama(**kwargs)
    ok("LLM ready.")


_TTS_MODEL_URL  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/kokoro-v0_19.onnx"
_TTS_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/voices.bin"


def load_tts():
    global TTS
    step("Loading TTS (Kokoro)...")
    os.makedirs(TTS_DIR, exist_ok=True)
    model_path  = os.path.join(TTS_DIR, "kokoro-v0_19.onnx")
    voices_path = os.path.join(TTS_DIR, "voices.bin")
    # Remove stale voices.json downloaded under wrong name
    stale = os.path.join(TTS_DIR, "voices.json")
    if os.path.exists(stale):
        os.remove(stale)
    try:
        if not os.path.exists(model_path):
            ok("Downloading Kokoro model (~80 MB)...")
            _download_with_progress(_TTS_MODEL_URL, model_path)
        if not os.path.exists(voices_path):
            ok("Downloading Kokoro voices...")
            _download_with_progress(_TTS_VOICES_URL, voices_path)
        import numpy as np
        _orig_load = np.load
        np.load = lambda *a, **kw: _orig_load(*a, **{**kw, "allow_pickle": True})
        try:
            TTS = _Kokoro(model_path, voices_path)
        finally:
            np.load = _orig_load
        ok("TTS ready.")
    except Exception as e:
        warn(f"TTS failed to load: {e} — audio will be disabled.")


def _tts_wav_b64(text: str) -> str:
    import numpy as np
    tts_cfg = CFG.get("tts", {})
    voice = tts_cfg.get("voice", "af_nicole")
    speed = tts_cfg.get("speed", 0.85)
    samples, sr = TTS.create(text[:600], voice=voice, speed=speed, lang="en-us")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((samples * 32767).astype(np.int16).tobytes())
    return base64.b64encode(buf.getvalue()).decode()


def _llm_chat(messages: list) -> str:
    if LLM is None:
        raise RuntimeError("LLM not loaded")
    with _llm_lock:
        result = LLM.create_chat_completion(messages=messages)
    return result["choices"][0]["message"]["content"]



def _save_history():
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"history": history, "memory": memory}, f, ensure_ascii=False)
    except Exception as e:
        warn(f"Could not save history: {e}")


def _load_history():
    global memory
    if not os.path.exists(HISTORY_FILE):
        return
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        history.extend(data.get("history", []))
        memory = data.get("memory", "")
        ok(f"History loaded ({len(history)} messages, memory: {bool(memory)})")
    except Exception as e:
        warn(f"Could not load history: {e}")


def _summarise(messages: list) -> str:
    text = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in messages)
    try:
        return _llm_chat([
            {"role": "system", "content": (
                "Summarise the following conversation into a brief memory paragraph. "
                "Capture key facts, what happened, preferences, and relationship dynamics. "
                "Be concise — two to four sentences max."
            )},
            {"role": "user", "content": text},
        ]).strip()
    except Exception as e:
        warn(f"Summary failed: {e}")
        return ""


def _compress_history():
    global memory
    if len(history) <= _MAX_HISTORY:
        return
    old = history[:len(history) - _KEEP_RECENT]
    del history[:len(history) - _KEEP_RECENT]
    summary = _summarise(old)
    if summary:
        memory = (memory + "\n" + summary).strip() if memory else summary
        if len(memory) > _MAX_MEMORY:
            memory = memory[-_MAX_MEMORY:]
    _save_history()



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
    """Remove verb/action tags, non-visual sensory tags, and duplicates."""
    tags = [t.strip() for t in prompt.split(",")]
    seen, out = set(), []
    for t in tags:
        key = re.sub(r'[^a-z0-9 ]', '', t.lower()).strip()
        if not t or t.lower() in _VERBS or t.lower() in _NON_VISUAL:
            continue
        if key and key not in seen:
            seen.add(key)
            out.append(t)
    return ", ".join(out)


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
                "You are a Stable Diffusion prompt engineer. "
                "Extract visual scene tags from the conversation. "
                "Output ONLY comma-separated tags. 20-30 tags. No sentences, no explanation. "
                "Focus exclusively on the CURRENT moment — what is visible RIGHT NOW. "
                "Include: body pose, body position, clothing state (be specific — 'topless', 'nude', 'panties only', 'fully clothed', etc.), "
                "facial expression, eye contact, setting/environment, lighting, camera angle, mood. "
                "If explicit content is present describe it directly with precise visual tags. "
                "If clothing is being removed, describe the resulting exposed state, not the action. "
                "Use the character persona and appearance to infer details not explicit in the text. "
                "Never output verbs or actions. Only visual nouns and adjectives."
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
    env["COMMANDLINE_ARGS"] = "--api --cuda-malloc --xformers"
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
    global _auto_image_counter
    if LLM is None:
        return JSONResponse({"error": "LLM not ready"}, status_code=503)

    history.append({"role": "user", "content": body.message})
    sys_prompt = SYSTEM_PROMPT
    if memory:
        sys_prompt += f"\n\nMemory of earlier conversation:\n{memory}"
    messages = [{"role": "system", "content": sys_prompt}] + list(history)

    async def generate():
        global _auto_image_counter
        q = _queue.Queue()
        collected = []

        def _run():
            try:
                with _llm_lock:
                    stream = LLM.create_chat_completion(messages=messages, stream=True)
                    for chunk in stream:
                        delta = chunk["choices"][0]["delta"].get("content", "")
                        if delta:
                            collected.append(delta)
                            q.put(delta)
            except Exception as e:
                q.put(e)
            q.put(None)

        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, _run)

        while True:
            try:
                item = q.get_nowait()
            except _queue.Empty:
                await asyncio.sleep(0.005)
                continue
            if item is None:
                break
            if isinstance(item, Exception):
                history.pop()
                yield f"data: {json.dumps({'error': str(item)})}\n\n"
                return
            yield f"data: {json.dumps({'delta': item})}\n\n"

        await fut

        reply = "".join(collected)
        reply = re.sub(r'^[Aa]lice\s*[:"]\s*', '', reply).strip().strip('"""\u201c\u201d')
        reply = re.sub(
            r'\s*(Please note\b|Note that\b|I should mention\b|I\'ve aimed\b|I have aimed\b|'
            r'I want to note\b|It\'s worth noting\b|As an AI\b|I\'m an AI\b|'
            r'Here\'s a revised\b|Here is a revised\b).*',
            '', reply, flags=re.DOTALL | re.IGNORECASE
        ).strip()

        history.append({"role": "assistant", "content": reply})
        _compress_history()
        _save_history()

        _auto_image_counter += 1
        auto_every = CFG.get("image", {}).get("auto_every", 1)
        auto_image = (_auto_image_counter % auto_every == 0)

        yield f"data: {json.dumps({'done': True, 'reply': reply, 'auto_image': auto_image})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/generate")
async def generate_raw(body: GenerateRequest):
    loop = asyncio.get_running_loop()
    image = await loop.run_in_executor(None, lambda: generate_image(body.prompt, steps=body.steps, cfg_scale=body.cfg_scale))
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

@app.get("/progress")
async def get_progress():
    try:
        r = req.get(f"{FORGE_URL}/sdapi/v1/progress", timeout=3)
        return JSONResponse(r.json())
    except Exception:
        return JSONResponse({"progress": 0, "state": {}})


@app.post("/image")
async def image_from_history(body: ImageRequest):
    if not history:
        return JSONResponse({"error": "No conversation history yet."}, status_code=400)

    def _run():
        recent = history[-6:]
        messages = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in recent)
        last_user = next((m["content"] for m in reversed(recent) if m["role"] == "user"), "")
        base_prompt = extract_sd_prompt(messages, appearance=ALICE_APPEARANCE, last_user_msg=last_user, persona=SYSTEM_PROMPT)
        positive_parts, negative_parts = [], []
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
        return ALICE_APPEARANCE + ", " + prompt, image

    loop = asyncio.get_running_loop()
    sd_prompt, image = await loop.run_in_executor(None, _run)
    return JSONResponse({"sd_prompt": sd_prompt, "image": image})




@app.get("/models")
async def list_models():
    models = [{"name": name, "path": path} for name, path in _list_ggufs()]
    current = os.path.basename(LLM.model_path) if LLM and hasattr(LLM, 'model_path') else ""
    return JSONResponse({"models": models, "current": current})


class ModelSwitchRequest(BaseModel):
    path: str


@app.post("/model")
async def switch_model(body: ModelSwitchRequest):
    global LLM
    if not os.path.exists(body.path):
        return JSONResponse({"error": "Model file not found."}, status_code=404)
    import asyncio
    print(f"\n[Alice] Switching model to: {os.path.basename(body.path)}")
    kwargs = dict(model_path=body.path, n_gpu_layers=-1, n_ctx=8192, flash_attn=True, verbose=False)
    try:
        new_llm = await asyncio.get_running_loop().run_in_executor(None, lambda: Llama(**kwargs))
    except Exception as e:
        kwargs.update(n_gpu_layers=0, flash_attn=False)
        try:
            new_llm = await asyncio.get_running_loop().run_in_executor(None, lambda: Llama(**kwargs))
        except Exception as e2:
            return JSONResponse({"error": str(e2)}, status_code=500)
    with _llm_lock:
        global memory
        LLM = new_llm
        history.clear()
        memory = ""
    print(f"[Alice] Model ready: {os.path.basename(body.path)}")
    return JSONResponse({"status": "ok", "model": os.path.basename(body.path)})


@app.get("/personas")
async def list_personas():
    return JSONResponse({"personas": list(PERSONAS.keys())})


@app.post("/persona/{name}")
async def switch_persona(name: str):
    global ALICE_APPEARANCE, SYSTEM_PROMPT
    if name not in PERSONAS:
        return JSONResponse({"error": f"Persona '{name}' not found."}, status_code=404)
    p = PERSONAS[name]
    global memory
    ALICE_APPEARANCE = p.get("appearance", ALICE_APPEARANCE)
    SYSTEM_PROMPT    = p.get("system_prompt", SYSTEM_PROMPT)
    history.clear()
    memory = ""
    print(f"\n[Alice] Switched to persona: {name}")
    return JSONResponse({"status": "ok", "persona": name})


_KOKORO_VOICES = ["af_nicole", "af_bella", "af_sky", "bf_emma", "bf_isabella"]

@app.get("/voices")
async def list_voices():
    current = CFG.get("tts", {}).get("voice", "af_nicole")
    return JSONResponse({"voices": _KOKORO_VOICES, "current": current})

class VoiceRequest(BaseModel):
    voice: str

@app.post("/voice")
async def set_voice(body: VoiceRequest):
    if body.voice not in _KOKORO_VOICES:
        return JSONResponse({"error": "Unknown voice"}, status_code=400)
    CFG.setdefault("tts", {})["voice"] = body.voice
    return JSONResponse({"status": "ok", "voice": body.voice})


class TtsRequest(BaseModel):
    text: str


@app.post("/tts")
async def speak(body: TtsRequest):
    if TTS is None:
        return JSONResponse({"error": "TTS not ready"}, status_code=503)
    import asyncio
    try:
        audio = await asyncio.get_running_loop().run_in_executor(None, lambda: _tts_wav_b64(body.text))
        return JSONResponse({"audio": audio})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/history")
async def clear():
    global memory
    history.clear()
    memory = ""
    try:
        if os.path.exists(HISTORY_FILE):
            os.remove(HISTORY_FILE)
    except Exception:
        pass
    return {"status": "cleared"}


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(ALICE_DIR, "static", "index.html"), encoding="utf-8") as f:
        return f.read()




# ── Entry point ──────────────────────────────────────────────────────────────
def _startup():
    try:
        load_llm()
        _load_history()
        load_tts()
        ensure_forge()
        ensure_checkpoint()
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
