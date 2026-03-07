#!/usr/bin/env python3
"""
Alice - single file app.
Run: python alice.py
Installs everything missing, starts all services, opens the browser.
"""
import subprocess, sys, time, os, re, json, urllib.request, glob, webbrowser, threading

# ── Bootstrap pip deps ───────────────────────────────────────────────────────
try:
    import fastapi, uvicorn, pydantic, llama_cpp
    import requests as req
except ImportError:
    print("Installing Python dependencies...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet",
        "fastapi", "uvicorn", "requests", "pydantic", "llama-cpp-python"])
    import requests as req

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

# ── Paths ────────────────────────────────────────────────────────────────────
ALICE_DIR   = os.path.dirname(os.path.abspath(__file__))
FORGE_DIR   = os.path.join(ALICE_DIR, "stable-diffusion-webui-forge")
FORGE_BAT   = os.path.join(FORGE_DIR, "webui.bat")
MODEL_DIR   = os.path.join(ALICE_DIR, "models")
ALICE_URL   = "http://localhost:8000"
CONFIG_FILE = os.path.join(ALICE_DIR, "alice.json")

# Default GGUF model to download (Mistral-Nemo Q4_K_M, ~7.7 GB)
MODEL_URL  = "https://huggingface.co/bartowski/Mistral-Nemo-Instruct-2407-GGUF/resolve/main/Mistral-Nemo-Instruct-2407-Q4_K_M.gguf"
MODEL_NAME = "Mistral-Nemo-Instruct-2407-Q4_K_M.gguf"

# ── Config ───────────────────────────────────────────────────────────────────
_DEFAULT_CONFIG = {
    "forge_url":    "http://localhost:7860",
    "model_path":   "",  # leave blank to auto-download
    "appearance":   "woman, Alice, long blonde hair, blue eyes, elegant, poised, expressive eyes, soft lighting",
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
            # Backward compat: extract system_prompt from old modelfile format
            if "system_prompt" not in data and "modelfile" in data:
                m = re.search(r'SYSTEM\s+"""(.*?)"""', data["modelfile"], re.DOTALL)
                if m:
                    merged["system_prompt"] = m.group(1).strip()
            print(f"        config: loaded {CONFIG_FILE}")
            return merged
        except Exception as e:
            print(f"        WARNING: could not load {CONFIG_FILE}: {e} -- using defaults")
    else:
        print(f"        config: {CONFIG_FILE} not found, using defaults")
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT_CONFIG, f, indent=4)
        print(f"        config: wrote default config to {CONFIG_FILE}")
    return _DEFAULT_CONFIG.copy()

CFG = load_config()

FORGE_URL        = CFG["forge_url"]
ALICE_APPEARANCE = CFG["appearance"]
BASE_NEGATIVE    = CFG["negative_prompt"]
SYSTEM_PROMPT    = CFG["system_prompt"]
IMG_CFG          = CFG["image"]

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

# ── Install / start steps ────────────────────────────────────────────────────

llm = None  # loaded by ensure_model() in background thread


def _download_with_progress(url: str, dest: str):
    def reporthook(count, block_size, total_size):
        if total_size > 0:
            pct = min(count * block_size / total_size * 100, 100)
            print(f"\r        {pct:5.1f}%", end="", flush=True)
    urllib.request.urlretrieve(url, dest, reporthook=reporthook)
    print()


def _valid_gguf(path: str) -> bool:
    """Return True if file exists and starts with the GGUF magic bytes."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"GGUF"
    except Exception:
        return False


def _find_ollama_blob() -> str:
    """Return path to mistral-nemo blob in Ollama's cache, or empty string."""
    manifest = os.path.join(os.path.expanduser("~"), ".ollama", "models",
        "manifests", "registry.ollama.ai", "library", "mistral-nemo", "latest")
    if not os.path.exists(manifest):
        return ""
    try:
        import json as _json
        with open(manifest, encoding="utf-8") as f:
            data = _json.load(f)
        for layer in data.get("layers", []):
            digest = layer.get("digest", "")
            # The model layer is the large one (>1 GB)
            if layer.get("size", 0) > 1_000_000_000 and digest.startswith("sha256:"):
                blob = os.path.join(os.path.expanduser("~"), ".ollama", "models",
                    "blobs", digest.replace(":", "-"))
                if _valid_gguf(blob):
                    return blob
    except Exception:
        pass
    return ""


def ensure_model():
    global llm
    step("Setting up language model...")
    os.makedirs(MODEL_DIR, exist_ok=True)

    # 1. Explicit path in config
    model_path = CFG.get("model_path", "").strip()
    if model_path and not _valid_gguf(model_path):
        warn(f"model_path '{model_path}' is not a valid GGUF — ignoring.")
        model_path = ""

    # 2. Any valid .gguf in models/
    if not model_path:
        for candidate in glob.glob(os.path.join(MODEL_DIR, "*.gguf")):
            if _valid_gguf(candidate):
                model_path = candidate
                ok(f"Found model: {os.path.basename(model_path)}")
                break
            else:
                warn(f"Removing incomplete/corrupt file: {os.path.basename(candidate)}")
                os.remove(candidate)

    # 3. Reuse Ollama's already-downloaded blob (saves re-downloading)
    if not model_path:
        blob = _find_ollama_blob()
        if blob:
            ok(f"Reusing Ollama's cached mistral-nemo model.")
            model_path = blob

    # 4. Download
    if not model_path:
        model_path = os.path.join(MODEL_DIR, MODEL_NAME)
        ok(f"Downloading {MODEL_NAME} (~7 GB) — one-time download...")
        try:
            _download_with_progress(MODEL_URL, model_path)
        except Exception as e:
            if os.path.exists(model_path):
                os.remove(model_path)
            fail(f"Download failed: {e}\nPlace a GGUF file in {MODEL_DIR} and restart.")

    ok(f"Loading {os.path.basename(model_path)} ...")
    from llama_cpp import Llama, llama_cpp as _llama_cpp
    try:
        supports_gpu = bool(_llama_cpp.llama_supports_gpu_offload())
    except Exception:
        supports_gpu = False
    llm = Llama(
        model_path=model_path,
        n_ctx=4096,
        n_gpu_layers=-1,  # use GPU if available; falls back to CPU silently
        verbose=False,
    )
    if supports_gpu:
        ok("llama.cpp GPU offload supported; LLM will use GPU if available.")
    else:
        warn("llama.cpp GPU offload not supported; LLM will run on CPU.")
    ok("Model ready.")


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
        print()
        print("  !! No .safetensors checkpoint found.")
        print(f"     Add one to: {FORGE_DIR}\\models\\Stable-diffusion\\")
        print("     Recommended: https://civitai.com/models/4384 (dreamshaper_8)")
        if INTERACTIVE:
            input("\n     Press Enter after adding a checkpoint, then re-run alice.py...")
        else:
            print("\n     Non-interactive session detected; exiting.")
        sys.exit(0)
    ok(f"Checkpoint: {os.path.basename(checkpoints[0])}")


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
history = []


class ChatRequest(BaseModel):
    message: str


class ImageRequest(BaseModel):
    extra: str = ""


def chat_alice(message: str) -> str:
    if llm is None:
        raise RuntimeError("Model is still loading — please wait a moment and try again.")
    history.append({"role": "user", "content": message})
    try:
        response = llm.create_chat_completion(
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
            max_tokens=1024,
        )
        reply = response["choices"][0]["message"]["content"]
    except Exception as e:
        history.pop()
        raise RuntimeError(f"Model error: {e}")
    history.append({"role": "assistant", "content": reply})
    return reply


def extract_sd_prompt(text: str) -> str:
    if llm is None:
        return ""
    response = llm.create_chat_completion(
        messages=[{"role": "user", "content":
            f"Read this conversation and extract a Stable Diffusion image prompt.\n\n"
            f"{text}\n\n"
            f"Rules:\n"
            f"- Output comma-separated tags only. No sentences. No explanation.\n"
            f"- Prioritize concrete pose/action details above all else\n"
            f"- If a pose is described, include explicit pose tags (e.g., 'lying on bed', 'legs raised', 'feet above head', 'arch back')\n"
            f"- Focus on: pose, action, expression, clothing, location/setting, lighting, mood\n"
            f"- Extract only specific details explicitly mentioned\n"
            f"- Keep it literal; avoid embellishment or inference\n"
            f"- Include emotional tone: playful, intense, tender, calm, etc\n"
            f"- Include setting details: indoor, outdoor, bedroom, candlelight, etc\n"
            f"- Do not include character names or dialogue\n"
            f"- Output only the tags, nothing else"
        }],
        max_tokens=200,
    )
    raw_tags = response["choices"][0]["message"]["content"]

    # Second pass: condense and normalize tags, with hard emphasis on pose.
    refine = llm.create_chat_completion(
        messages=[{"role": "user", "content":
            f"Condense and normalize these Stable Diffusion tags.\n\n"
            f"{raw_tags}\n\n"
            f"Rules:\n"
            f"- Output comma-separated tags only. No sentences. No explanation.\n"
            f"- Keep 15-30 tags max; remove redundancy\n"
            f"- Ensure pose/action tags are present and explicit if mentioned (e.g., 'lying on bed', 'legs raised', 'feet above head', 'arch back')\n"
            f"- Keep clothing, setting, lighting, and mood if present\n"
            f"- Do not add new facts not present in the original tags"
        }],
        max_tokens=160,
    )
    return refine["choices"][0]["message"]["content"]


def generate_image(prompt: str, extra_negative: str = ""):
    if not http_ok(f"{FORGE_URL}/sdapi/v1/sd-models"):
        print("Forge down, restarting...")
        start_forge()
    negative = (extra_negative + ", " + BASE_NEGATIVE) if extra_negative else BASE_NEGATIVE
    try:
        r = req.post(f"{FORGE_URL}/sdapi/v1/txt2img", json={
            "prompt":          ALICE_APPEARANCE + ", " + prompt + ", " + IMG_CFG["suffix"],
            "negative_prompt": negative,
            "steps":           IMG_CFG["steps"],
            "width":           IMG_CFG["width"],
            "height":          IMG_CFG["height"],
            "cfg_scale":       IMG_CFG["cfg_scale"],
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
    messages = "\n".join(
        f"{m['role'].capitalize()}: {m['content']}" for m in history[-12:]
    )
    base_prompt = extract_sd_prompt(messages)

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
    prompt = (positive_extra + ", " + base_prompt) if positive_extra else base_prompt
    image = generate_image(prompt, extra_negative=extra_negative)
    return JSONResponse({"sd_prompt": ALICE_APPEARANCE + ", " + prompt, "image": image})




class VideoRequest(BaseModel):
    extra: str = ""


@app.post("/video")
async def video_from_history(body: VideoRequest):
    if not history:
        return JSONResponse({"error": "No conversation history yet."}, status_code=400)
    messages = "\n".join(
        f"{m['role'].capitalize()}: {m['content']}" for m in history[-12:]
    )
    base_prompt = extract_sd_prompt(messages)

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
    full_prompt = ALICE_APPEARANCE + ", " + prompt + ", " + IMG_CFG["suffix"]
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
        return JSONResponse({"error": "No output from Forge. Is AnimateDiff installed?"}, status_code=500)
    except Exception as e:
        print(f"Forge video error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.delete("/history")
async def clear():
    history.clear()
    return {"status": "cleared"}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


# ── UI ───────────────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Alice</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;1,300&family=Montserrat:wght@300;400&display=swap" rel="stylesheet">
<style>
:root{--bg:#0d0a0e;--panel:#130f15;--border:#2a1f2e;--accent:#c084a0;--accent2:#7c4f6b;--text:#e8dde4;--muted:#7a6b74;--ba:#1e1523;--bu:#160e1c}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Montserrat',sans-serif;font-weight:300;height:100vh;display:flex;flex-direction:column;overflow:hidden}
header{padding:1rem 2rem;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;background:var(--panel)}
h1{font-family:'Cormorant Garamond',serif;font-weight:300;font-size:1.8rem;letter-spacing:.15em;color:var(--accent);font-style:italic}
.cb{background:none;border:1px solid var(--border);color:var(--muted);padding:.3rem .8rem;cursor:pointer;font-family:'Montserrat',sans-serif;font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;transition:all .2s}
.cb:hover{border-color:var(--accent2);color:var(--accent)}
.main{display:flex;flex:1;overflow:hidden}
.cp{flex:1;display:flex;flex-direction:column;border-right:1px solid var(--border);min-width:0}
.msgs{flex:1;overflow-y:auto;padding:1.5rem;display:flex;flex-direction:column;gap:1rem;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.msg{max-width:85%;padding:.9rem 1.1rem;line-height:1.6;font-size:.88rem;animation:fi .3s ease}
@keyframes fi{from{opacity:0;transform:translateY(6px)}to{opacity:1}}
.msg.alice{background:var(--ba);border-left:2px solid var(--accent);align-self:flex-start}
.msg.user{background:var(--bu);border-right:2px solid var(--accent2);align-self:flex-end;text-align:right}
.sndr{font-size:.65rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:.4rem}
.msg.alice .sndr{color:var(--accent)}
.ir{display:flex;padding:1rem 1.5rem;gap:.7rem;border-top:1px solid var(--border);background:var(--panel)}
.ir input{flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:.7rem 1rem;font-family:'Montserrat',sans-serif;font-size:.85rem;outline:none;transition:border-color .2s}
.ir input:focus{border-color:var(--accent2)}
.ir button{background:var(--accent2);border:none;color:var(--text);padding:.7rem 1.4rem;cursor:pointer;font-family:'Montserrat',sans-serif;font-size:.8rem;letter-spacing:.1em;text-transform:uppercase;transition:background .2s}
.ir button:hover{background:var(--accent)}
.ir button:disabled{opacity:.4;cursor:not-allowed}
.ip{width:420px;flex-shrink:0;display:flex;flex-direction:column;background:var(--panel)}
.ih{padding:.8rem 1.2rem;border-bottom:1px solid var(--border);font-size:.65rem;letter-spacing:.15em;text-transform:uppercase;color:var(--muted)}
.ic{flex:1;display:flex;align-items:center;justify-content:center;padding:1rem;overflow:hidden}
.ic img,.ic video{max-width:100%;max-height:100%;object-fit:contain;border:1px solid var(--border);animation:fi .5s ease}
.ph{color:var(--muted);text-align:center;font-style:italic;font-family:'Cormorant Garamond',serif;font-size:1rem}
.pd{padding:.8rem 1.2rem;border-top:1px solid var(--border);font-size:.68rem;color:var(--muted);line-height:1.5;max-height:80px;overflow-y:auto;scrollbar-width:thin}
.pd strong{color:var(--accent2)}
.gen{color:var(--accent);font-style:italic;animation:pulse 1.2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
</style>
</head>
<body>
<header><h1>Alice</h1><button class="cb" onclick="clearHistory()">Clear</button></header>
<div class="main">
  <div class="cp">
    <div class="msgs" id="msgs">
      <div class="msg alice"><div class="sndr">Alice</div>Hello. I&#39;ve been waiting for you...</div>
    </div>
    <div class="ir">
      <input type="text" id="inp" placeholder="Say something... or /image or /video" onkeydown="if(event.key==='Enter')send()">
      <button id="vbtn" onclick="doVideo()">Video</button><button id="ibtn" onclick="doImage()">Image</button><button id="btn" onclick="send()">Send</button>
    </div>
  </div>
  <div class="ip">
    <div class="ih" id="ih">Generated Scene</div>
    <div class="ic" id="ic"><div class="ph">Awaiting your conversation...</div></div>
    <div class="pd" id="pd"></div>
  </div>
</div>
<script>
let mid = 0, imgAbort = null;
function disableAll(){ ['btn','ibtn','vbtn'].forEach(id=>{const e=document.getElementById(id);if(e)e.disabled=true;}); }
function enableAll(){ ['btn','ibtn','vbtn'].forEach(id=>{const e=document.getElementById(id);if(e)e.disabled=false;}); }

async function interrupt() {
  if (imgAbort) { imgAbort.abort(); imgAbort = null; }
  await fetch('/interrupt', {method: 'POST'}).catch(() => {});
}

function doImage(){ 
  const extra = document.getElementById('inp').value.trim();
  document.getElementById('inp').value = '';
  triggerMedia('/image', extra); 
}
function doVideo(){ 
  const extra = document.getElementById('inp').value.trim();
  document.getElementById('inp').value = '';
  triggerMedia('/video', extra); 
}

async function triggerMedia(endpoint, extra = '', auto = false) {
  await interrupt();
  imgAbort = new AbortController();
  const { signal } = imgAbort;

  disableAll();
  const label = endpoint === '/video' ? 'Generating video...' : 'Generating scene...';
  const header = endpoint === '/video' ? 'Generated Video' : 'Generated Scene';
  document.getElementById('ih').textContent = header;
  
  if (extra && !auto) {
    addMsg('user', 'You', extra);
  }
  
  document.getElementById('ic').innerHTML = `<div class="ph gen">${label}</div>`;
  document.getElementById('pd').innerHTML = '';
  
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({extra: extra}),
      signal
    });
    const d = await res.json();
    if (d.error) {
      document.getElementById('ic').innerHTML = `<div class="ph">${d.error}</div>`;
    } else if (endpoint === '/video' && !d.fallback && d.video) {
      document.getElementById('ic').innerHTML = `<video autoplay loop muted src="data:video/mp4;base64,${d.video}"></video>`;
      document.getElementById('pd').innerHTML = `<strong>Prompt:</strong> ${d.sd_prompt}`;
    } else if (d.image || d.video) {
      const b64 = d.image || d.video;
      document.getElementById('ic').innerHTML = `<img src="data:image/png;base64,${b64}">`;
      if (d.fallback) document.getElementById('pd').innerHTML = `<strong>Note:</strong> AnimateDiff not installed - showing still image. <strong>Prompt:</strong> ${d.sd_prompt}`;
      else document.getElementById('pd').innerHTML = `<strong>Prompt:</strong> ${d.sd_prompt}`;
    } else {
      document.getElementById('ic').innerHTML = '<div class="ph">No output generated.</div>';
    }
  } catch(e) {
    if (e.name === 'AbortError') {
      document.getElementById('ic').innerHTML = '<div class="ph">Interrupted.</div>';
    } else {
      document.getElementById('ic').innerHTML = '<div class="ph">Error contacting backend.</div>';
    }
  }
  imgAbort = null;
  enableAll();
  document.getElementById('inp').focus();
}

async function send() {
  const inp = document.getElementById('inp'), msg = inp.value.trim();
  if (!msg) return;
  inp.value = '';
  const btn = document.getElementById('btn');
  btn.disabled = true;

  await interrupt();

  if (msg.startsWith('/video')) {
    triggerMedia('/video', msg.slice(6).trim());
    return;
  }

  if (msg.startsWith('/image')) {
    triggerMedia('/image', msg.slice(6).trim());
    return;
  }

  addMsg('user', 'You', msg);
  const tid = addMsg('alice', 'Alice', '<span class="gen">thinking...</span>');
  document.getElementById('pd').innerHTML = '';
  let success = false;
  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg})
    });
    const d = await res.json();
    if (d.error) { updMsg(tid, '<em style="color:#c08080">' + d.error + '</em>'); }
    else { 
      updMsg(tid, d.reply); 
      success = true;
    }
  } catch(e) {
    updMsg(tid, '<em style="color:#c08080">Could not reach backend — is alice.py running?</em>');
  }
  btn.disabled = false;
  inp.focus();

  if (success) {
    triggerMedia('/image', '', true);
  }
}

document.getElementById('inp').addEventListener('input', () => {
  if (imgAbort) {
    interrupt();
  }
});

function addMsg(cls, sndr, html) {
  const id = 'm' + (mid++), d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.id = id;
  d.innerHTML = `<div class="sndr">${sndr}</div>${html}`;
  const c = document.getElementById('msgs');
  c.appendChild(d);
  c.scrollTop = c.scrollHeight;
  return id;
}
function updMsg(id, t) {
  const e = document.getElementById(id);
  if (!e) return;
  e.innerHTML = e.querySelector('.sndr').outerHTML + t;
}
async function clearHistory() {
  await fetch('/history', {method: 'DELETE'});
  document.getElementById('msgs').innerHTML = '<div class="msg alice"><div class="sndr">Alice</div>Hello. I&#39;ve been waiting for you...</div>';
  document.getElementById('ic').innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  document.getElementById('pd').innerHTML = '';
}
</script>
</body>
</html>"""


# ── Entry point ──────────────────────────────────────────────────────────────
def _startup():
    ensure_model()
    ensure_forge()
    start_forge()

if __name__ == "__main__":
    print()
    print("=" * 60)
    print("  Alice")
    print("=" * 60)

    threading.Thread(target=_startup, daemon=True).start()

    print()
    print(f"[Alice] Starting at {ALICE_URL}")
    if INTERACTIVE:
        webbrowser.open(ALICE_URL)
    else:
        print("        NOTE: Non-interactive session detected; not opening browser.")
    uvicorn.run(app, host="0.0.0.0", port=8000)
