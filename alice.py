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
from pydantic import BaseModel
import uvicorn

# ── Paths ────────────────────────────────────────────────────────────────────
ALICE_DIR   = os.path.dirname(os.path.abspath(__file__))
FORGE_DIR   = os.path.join(ALICE_DIR, "stable-diffusion-webui-forge")
FORGE_BAT   = os.path.join(FORGE_DIR, "webui.bat")
MODEL_DIR   = os.path.join(ALICE_DIR, "models")
ALICE_URL   = "http://localhost:8000"
CONFIG_FILE = os.path.join(ALICE_DIR, "alice.json")

# llama-cpp global (loaded at startup)
LLM = None

# ── Config ───────────────────────────────────────────────────────────────────
_DEFAULT_CONFIG = {
    "forge_url":    "http://localhost:7860",
    "model_path":   "",
    "ollama_model": "mistral-nemo",
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
    history.append({"role": "assistant", "content": reply})
    return reply


def extract_sd_prompt(text: str) -> str:
    try:
        raw_tags = _llm_complete(
            f"Read this conversation and extract a Stable Diffusion image prompt.\n\n"
            f"{text}\n\n"
            f"Rules:\n"
            f"- Output comma-separated tags only. No sentences. No explanation.\n"
            f"- Prioritize concrete pose/action details above all else\n"
            f"- Focus on: pose, action, expression, clothing, location/setting, lighting, mood\n"
            f"- Extract only specific details explicitly mentioned\n"
            f"- Keep it literal; avoid embellishment or inference\n"
            f"- Do not include character names or dialogue\n"
            f"Tags:"
        )
        refined = _llm_complete(
            f"Condense and normalize these Stable Diffusion tags.\n\n"
            f"{raw_tags}\n\n"
            f"Rules:\n"
            f"- Output comma-separated tags only. No sentences. No explanation.\n"
            f"- Keep 15-30 tags max; remove redundancy\n"
            f"- Ensure pose/action tags are present\n"
            f"- Do not add new facts not present in the original tags\n"
            f"Tags:"
        )
        return refined
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


class ChatRequest(BaseModel):
    message: str


class ImageRequest(BaseModel):
    extra: str = ""


def generate_image(prompt: str, extra_negative: str = ""):
    if not http_ok(f"{FORGE_URL}/sdapi/v1/sd-models"):
        print("Forge down, restarting...")
        start_forge()
    negative = (extra_negative + ", " + BASE_NEGATIVE) if extra_negative else BASE_NEGATIVE
    try:
        r = req.post(f"{FORGE_URL}/sdapi/v1/txt2img", json={
            "prompt":          prompt + ", " + ALICE_APPEARANCE + ", " + IMG_CFG["suffix"],
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

async function interrupt(reason) {
  if (imgAbort) { 
    console.log('Aborting media generation:', reason);
    imgAbort.abort(); 
    imgAbort = null; 
  }
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
  await interrupt('new media request');
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

  await interrupt('new message sent');

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
        ensure_animatediff()
        start_forge()
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
