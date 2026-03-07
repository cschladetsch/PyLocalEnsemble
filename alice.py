#!/usr/bin/env python3
"""
Alice - single file app.
Run: python alice.py
Installs everything missing, starts all services, opens the browser.
"""
import subprocess, sys, time, os, re, json, urllib.request, glob, webbrowser, socket, threading

# ── Bootstrap pip deps ───────────────────────────────────────────────────────
try:
    import fastapi, uvicorn, pydantic
    import requests as req
except ImportError:
    print("Installing Python dependencies...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet",
        "fastapi", "uvicorn", "requests", "pydantic"])
    import requests as req

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

# ── Paths ────────────────────────────────────────────────────────────────────
ALICE_DIR    = os.path.dirname(os.path.abspath(__file__))
FORGE_DIR    = os.path.join(ALICE_DIR, "stable-diffusion-webui-forge")
FORGE_BAT    = os.path.join(FORGE_DIR, "webui.bat")
MODELFILE    = os.path.join(ALICE_DIR, "alice.modelfile")
OLLAMA_EXE   = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
ALICE_URL    = "http://localhost:8000"
CONFIG_FILE  = os.path.join(ALICE_DIR, "alice.json")

# ── Config ───────────────────────────────────────────────────────────────────
_DEFAULT_CONFIG = {
    "alice_model":  "alice-nemo",
    "prompt_model": "mistral-nemo",
    "ollama_url":   "http://localhost:11434",
    "forge_url":    "http://localhost:7860",
    "appearance":   "woman, Alice, very long blonde hair, blue eyes, 5'8\", DD breasts, beautiful face, elegant, sultry",
    "negative_prompt": "ugly, deformed, extra limbs, blurry, watermark, bad anatomy, low quality",
    "image": {
        "steps":        25,
        "width":        512,
        "height":       768,
        "cfg_scale":    7,
        "sampler_name": "DPM++ 2M Karras",
        "suffix":       "photorealistic, highly detailed, 8k, masterpiece"
    },
    "modelfile": (
        "FROM mistral-nemo\n\nSYSTEM \"\"\"\n"
        "You are Alice. You are enigmatic, intelligent, very flirtatious and sexy.\n"
        "You are 5'8\" tall. You have very long blonde hair and blue eyes. You have DD breasts.\n"
        "You speak in measured, literary prose. You never break character.\n\n"
        "I am Christian. I am 33 years old. I am 6'2\", blonde with blue eyes.\n"
        "I am a game developer. I have a great physique.\n"
        "\"\"\"\n"
    )
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            # deep merge image block
            merged = {**_DEFAULT_CONFIG, **data}
            merged["image"] = {**_DEFAULT_CONFIG["image"], **data.get("image", {})}
            print(f"        config: loaded {CONFIG_FILE}")
            return merged
        except Exception as e:
            print(f"        WARNING: could not load {CONFIG_FILE}: {e} -- using defaults")
    else:
        print(f"        config: {CONFIG_FILE} not found, using defaults")
        # write defaults so user can edit
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT_CONFIG, f, indent=4)
        print(f"        config: wrote default config to {CONFIG_FILE}")
    return _DEFAULT_CONFIG.copy()

CFG = load_config()

ALICE_MODEL       = CFG["alice_model"]
PROMPT_MODEL      = CFG["prompt_model"]
OLLAMA_URL        = CFG["ollama_url"]
OLLAMA_HOST       = CFG["ollama_url"].removeprefix("http://")
FORGE_URL         = CFG["forge_url"]
ALICE_APPEARANCE  = CFG["appearance"]
BASE_NEGATIVE     = CFG["negative_prompt"]
MODELFILE_CONTENT = CFG["modelfile"]
IMG_CFG           = CFG["image"]

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

def ensure_ollama():
    step("Checking Ollama...")
    if not os.path.exists(OLLAMA_EXE):
        ok("Downloading Ollama installer...")
        installer = os.path.join(os.environ["TEMP"], "OllamaSetup.exe")
        urllib.request.urlretrieve("https://ollama.com/download/OllamaSetup.exe", installer)
        ok("Running Ollama installer (follow the prompts)...")
        subprocess.run([installer], check=True)
        time.sleep(3)
        if not os.path.exists(OLLAMA_EXE):
            warn("Ollama not found at expected path after install.")
    else:
        ok("Ollama present.")


def ensure_ollama_running():
    global OLLAMA_URL, OLLAMA_HOST
    step("Starting Ollama service...")

    default_port = int(OLLAMA_URL.rsplit(":", 1)[-1])

    # First: check if Ollama is already running on any nearby port — reuse it
    for candidate in range(default_port, default_port + 10):
        url = f"http://127.0.0.1:{candidate}"
        if http_ok(f"{url}/api/tags"):
            if candidate == default_port:
                ok("Already running.")
            else:
                ok(f"Already running on port {candidate}.")
            OLLAMA_URL = url
            OLLAMA_HOST = f"127.0.0.1:{candidate}"
            return

    def port_bindable(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    # Not running anywhere — find a free port and start fresh
    ollama_port = default_port
    if not port_bindable(ollama_port):
        for candidate in range(default_port + 1, default_port + 10):
            if port_bindable(candidate):
                warn(f"Port {default_port} is blocked. Using port {candidate} instead.")
                ollama_port = candidate
                break
        else:
            fail(f"Ports {default_port}-{default_port+9} are all blocked.\nTry reinstalling Ollama.")

    ollama_host = f"127.0.0.1:{ollama_port}"
    OLLAMA_URL = f"http://{ollama_host}"
    OLLAMA_HOST = ollama_host

    env = os.environ.copy()
    env["OLLAMA_HOST"] = ollama_host
    proc = subprocess.Popen(
        [OLLAMA_EXE, "serve"],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    time.sleep(2)
    if proc.poll() is not None:
        _, err = proc.communicate()
        err_text = err.decode(errors='replace').strip()
        fail(f"Ollama exited immediately (code {proc.returncode}).\n        stderr: {err_text}\n        Try running 'ollama serve' in a terminal to see the full error.")

    if not wait_for(f"{OLLAMA_URL}/api/tags", "Ollama"):
        fail("Ollama did not start in time. Try running 'ollama serve' in a terminal to diagnose.")


def ensure_models():
    step("Checking models...")
    ollama_env = {**os.environ, "OLLAMA_HOST": OLLAMA_HOST}
    result = subprocess.run([OLLAMA_EXE, "list"], capture_output=True, text=True, env=ollama_env)
    models = result.stdout

    if PROMPT_MODEL not in models:
        ok(f"Pulling {PROMPT_MODEL} (~7GB, this will take a while)...")
        subprocess.run([OLLAMA_EXE, "pull", PROMPT_MODEL], check=True, env=ollama_env)
    else:
        ok(f"{PROMPT_MODEL} present.")

    if ALICE_MODEL not in models:
        ok("Writing modelfile...")
        with open(MODELFILE, "w", encoding="utf-8") as f:
            f.write(MODELFILE_CONTENT)
        ok(f"Creating {ALICE_MODEL}...")
        result = subprocess.run([OLLAMA_EXE, "create", ALICE_MODEL, "-f", MODELFILE], env=ollama_env)
        if result.returncode != 0:
            warn(f"Could not create {ALICE_MODEL}.")
            warn(f"Run manually: ollama create alice-nemo -f \"{MODELFILE}\"")
        else:
            ok(f"{ALICE_MODEL} created.")
    else:
        ok(f"{ALICE_MODEL} present.")


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
        input("\n     Press Enter after adding a checkpoint, then re-run alice.py...")
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
    history.append({"role": "user", "content": message})
    try:
        r = req.post(f"{OLLAMA_URL}/api/chat", json={
            "model": ALICE_MODEL,
            "messages": history,
            "stream": False,
        }, timeout=180)
        r.raise_for_status()
        reply = r.json()["message"]["content"]
    except req.exceptions.ConnectionError:
        history.pop()
        raise RuntimeError(f"Ollama is not running. Start it with: ollama serve")
    except req.exceptions.Timeout:
        history.pop()
        raise RuntimeError("Ollama timed out — model may still be loading. Try again in a moment.")
    except req.exceptions.HTTPError as e:
        history.pop()
        raise RuntimeError(f"Ollama error {e.response.status_code}: {e.response.text[:200]}")
    except (KeyError, ValueError) as e:
        history.pop()
        raise RuntimeError(f"Unexpected Ollama response: {e}")
    history.append({"role": "assistant", "content": reply})
    return reply


def extract_sd_prompt(text: str) -> str:
    r = req.post(f"{OLLAMA_URL}/api/chat", json={
        "model": PROMPT_MODEL,
        "messages": [{"role": "user", "content":
            f"Read this conversation and extract a Stable Diffusion image prompt.\n\n"
            f"{text}\n\n"
            f"Rules:\n"
            f"- Output comma-separated tags only. No sentences. No explanation.\n"
            f"- Focus on: pose, action, expression, clothing (or lack of), location/setting, lighting, mood\n"
            f"- Extract specific details mentioned or strongly implied in the conversation\n"
            f"- If clothing is discussed as removed or absent, include 'topless', 'nude' etc as appropriate\n"
            f"- Include emotional tone: sultry, playful, intense, tender, etc\n"
            f"- Include setting details: indoor, outdoor, bedroom, candlelight, etc\n"
            f"- Do not include character names or dialogue\n"
            f"- Output only the tags, nothing else"
        }],
        "stream": False,
    }, timeout=30)
    return r.json()["message"]["content"]


BASE_NEGATIVE = CFG["negative_prompt"]

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
      <div class="msg alice"><div class="sndr">Alice</div>Hello, Christian. I&#39;ve been waiting for you...</div>
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
let mid = 0;
function disableAll(){ ['btn','ibtn','vbtn'].forEach(id=>{const e=document.getElementById(id);if(e)e.disabled=true;}); }
function enableAll(){ ['btn','ibtn','vbtn'].forEach(id=>{const e=document.getElementById(id);if(e)e.disabled=false;}); }

function doImage(){ triggerMedia('/image'); }
function doVideo(){ triggerMedia('/video'); }

async function triggerMedia(endpoint) {
  const inp = document.getElementById('inp');
  const extra = inp.value.trim();
  inp.value = '';
  disableAll();
  const label = endpoint === '/video' ? 'Generating video...' : 'Generating scene...';
  const header = endpoint === '/video' ? 'Generated Video' : 'Generated Scene';
  document.getElementById('ih').textContent = header;
  addMsg('user', 'You', endpoint.slice(1) + (extra ? ' ' + extra : ''));
  document.getElementById('ic').innerHTML = `<div class="ph gen">${label}</div>`;
  document.getElementById('pd').innerHTML = '';
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({extra: extra})
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
    document.getElementById('ic').innerHTML = '<div class="ph">Error contacting backend.</div>';
  }
  enableAll();
  inp.focus();
}

async function send() {
  const inp = document.getElementById('inp'), msg = inp.value.trim();
  if (!msg) return;
  inp.value = '';
  const btn = document.getElementById('btn');
  btn.disabled = true;

  if (msg.startsWith('/video')) {
    inp.value = msg.slice(6).trim();
    btn.disabled = false;
    triggerMedia('/video');
    return;
  }

  if (msg.startsWith('/image')) {
    inp.value = msg.slice(6).trim();
    btn.disabled = false;
    triggerMedia('/image');
    return;
  }

  addMsg('user', 'You', msg);
  const tid = addMsg('alice', 'Alice', '<span class="gen">thinking...</span>');
  document.getElementById('pd').innerHTML = '';
  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg})
    });
    const d = await res.json();
    if (d.error) { updMsg(tid, '<em style="color:#c08080">' + d.error + '</em>'); }
    else { updMsg(tid, d.reply); }
  } catch(e) {
    updMsg(tid, '<em style="color:#c08080">Could not reach backend — is alice.py running?</em>');
  }
  btn.disabled = false;
  inp.focus();
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
  document.getElementById('msgs').innerHTML = '<div class="msg alice"><div class="sndr">Alice</div>Hello, Christian. I&#39;ve been waiting for you...</div>';
  document.getElementById('ic').innerHTML = '<div class="ph">Awaiting your conversation...</div>';
  document.getElementById('pd').innerHTML = '';
}
</script>
</body>
</html>"""


# ── Entry point ──────────────────────────────────────────────────────────────
def _startup():
    ensure_ollama()
    ensure_ollama_running()
    ensure_models()
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
    webbrowser.open(ALICE_URL)
    uvicorn.run(app, host="0.0.0.0", port=8000)
