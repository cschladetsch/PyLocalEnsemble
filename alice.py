#!/usr/bin/env python3
"""
Alice — run with: python alice.py
Runs install.py automatically on first use if dependencies are missing.
"""
import subprocess, sys, time, os, re, json, webbrowser, threading, asyncio
import requests as req
import queue as _queue

# ── Windows CUDA DLL path fix ─────────────────────────────────────────────────
if os.name == "nt":
    _cuda_env = os.environ.get("CUDA_PATH")
    _candidates = [_cuda_env] if _cuda_env else []
    _root = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if os.path.exists(_root):
        _candidates += [os.path.join(_root, v) for v in sorted(os.listdir(_root), reverse=True)]
    for _p in _candidates:
        for _sub in ("bin", os.path.join("bin", "x64")):
            _d = os.path.join(_p, _sub)
            if os.path.exists(_d):
                try:
                    os.add_dll_directory(_d)
                except (AttributeError, OSError):
                    pass

# ── Auto-install if needed ────────────────────────────────────────────────────
def _needs_install() -> bool:
    try:
        import fastapi, uvicorn, pydantic, requests
        from kokoro_onnx import Kokoro        # noqa: F401
        import faster_whisper, av             # noqa: F401
        return False
    except ImportError:
        return True

if _needs_install():
    _install = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install.py")
    print("\nDependencies missing — running install.py first...\n")
    result = subprocess.run([sys.executable, _install])
    if result.returncode != 0:
        print("\ninstall.py failed. Fix the errors above and try again.")
        sys.exit(1)
    print("\nInstall complete — starting Alice...\n")

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

import config
import llm
import tts
import stt
import image

# ── Runtime state (mutable, owned by this layer) ──────────────────────────────
ALICE_APPEARANCE   = config.CFG["appearance"]
SYSTEM_PROMPT      = config.CFG["system_prompt"]
BASE_NEGATIVE      = config.CFG["negative_prompt"]
_auto_image_counter = 0

INTERACTIVE = sys.stdin.isatty() and sys.stdout.isatty()
NO_SPEECH   = "--no-speech" in sys.argv

# ── Logging config ────────────────────────────────────────────────────────────
import logging

class _NoSpamFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return "/progress" not in msg and "/favicon" not in msg

_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "\033[33m[%(asctime)s]\033[0m %(levelprefix)s %(message)s",
            "datefmt": "%H:%M:%S",
            "use_colors": None,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '\033[33m[%(asctime)s]\033[0m %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            "datefmt": "%H:%M:%S",
        },
    },
    "handlers": {
        "default": {"formatter": "default", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
        "access":  {"formatter": "access",  "class": "logging.StreamHandler", "stream": "ext://sys.stdout",
                    "filters": ["no_spam"]},
    },
    "filters": {"no_spam": {"()": _NoSpamFilter}},
    "loggers": {
        "uvicorn":        {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error":  {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"],  "level": "INFO", "propagate": False},
    },
}

# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(config.ALICE_DIR, "static")), name="static")


class ChatRequest(BaseModel):
    message: str

class ImageRequest(BaseModel):
    extra: str = ""

class GenerateRequest(BaseModel):
    prompt:     str
    steps:      int   = None
    cfg_scale:  float = None

class ModelSwitchRequest(BaseModel):
    path: str

class VoiceRequest(BaseModel):
    voice: str

class TtsRequest(BaseModel):
    text: str


def save_generated_image(b64_data: str) -> str:
    import base64
    out_dir = os.path.join(config.ALICE_DIR, "static", "outputs")
    os.makedirs(out_dir, exist_ok=True)
    fname = f"img_{int(time.time() * 1000)}.png"
    fpath = os.path.join(out_dir, fname)
    with open(fpath, "wb") as f:
        f.write(base64.b64decode(b64_data))
    return f"/static/outputs/{fname}"


@app.post("/chat")
async def chat(body: ChatRequest):
    global _auto_image_counter
    print(f"\n[backend] Received /chat request: {body.message[:50]}...")
    if not llm.LLM_READY:
        async def _not_ready():
            yield f"data: {json.dumps({'error': 'LLM server is still starting up — please wait a moment and try again.'})}\n\n"
        return StreamingResponse(_not_ready(), media_type="text/event-stream")

    try:
        llm.history.append({"role": "user", "content": body.message})
        sys_prompt = SYSTEM_PROMPT
        if llm.memory:
            sys_prompt += f"\n\nMemory of earlier conversation:\n{llm.memory}"
        messages = [{"role": "system", "content": sys_prompt}] + list(llm.history)
        print(f"[chat] user: {body.message!r}")

        async def generate():
            global _auto_image_counter
            q = _queue.Queue()
            collected = []

            def _run():
                try:
                    r = req.post(f"{llm.LLAMA_URL}/v1/chat/completions", json={
                        "model":            llm.llm_model(),
                        "messages":         messages,
                        "stream":           True,
                        "temperature":      0.9,
                        "top_p":            0.92,
                        "repeat_penalty":   1.25,
                        "presence_penalty": 0.8,
                        "frequency_penalty": 0.5,
                    }, stream=True, timeout=120)
                    if r.status_code != 200:
                        print(f"[chat] Error {r.status_code}: {r.text}")
                        r.raise_for_status()
                    for line in r.iter_lines():
                        if not line:
                            continue
                        if isinstance(line, bytes):
                            line = line.decode("utf-8")
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload == "[DONE]":
                            break
                        data = json.loads(payload)
                        delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            collected.append(delta)
                            q.put(delta)
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    q.put(e)
                q.put(None)

            loop = asyncio.get_running_loop()
            fut  = loop.run_in_executor(None, _run)

            while True:
                try:
                    item = q.get_nowait()
                except _queue.Empty:
                    await asyncio.sleep(0.005)
                    continue
                if item is None:
                    break
                if isinstance(item, Exception):
                    if llm.history: llm.history.pop()
                    yield f"data: {json.dumps({'error': str(item)})}\n\n"
                    return
                yield f"data: {json.dumps({'delta': item})}\n\n"

            await fut

            reply = "".join(collected)
            print(f"[chat] raw reply ({len(reply)} chars): {reply!r}")
            reply = re.sub(r'^[Aa]lice\s*[:"]\s*', '', reply).strip().strip('"""\u201c\u201d')
            reply = re.sub(
                r'\s*(Please note\b|Note that\b|I should mention\b|I\'ve aimed\b|I have aimed\b|'
                r'I want to note\b|It\'s worth noting\b|As an AI\b|I\'m an AI\b|'
                r'Here\'s a revised\b|Here is a revised\b).*',
                '', reply, flags=re.DOTALL | re.IGNORECASE
            ).strip()

            llm.history.append({"role": "assistant", "content": reply})
            await loop.run_in_executor(None, llm.compress_history)
            llm.save_history()

            _auto_image_counter += 1
            auto_every = config.CFG.get("image", {}).get("auto_every", 1)
            auto_img   = (_auto_image_counter % auto_every == 0)
            print(f"[chat] reply sent ({len(reply)} chars), auto_image={auto_img}")
            yield f"data: {json.dumps({'done': True, 'reply': reply, 'auto_image': auto_img})}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/generate")
async def generate_raw(body: GenerateRequest):
    loop = asyncio.get_running_loop()
    def _regen():
        img = image.generate_image(
            body.prompt, ALICE_APPEARANCE, BASE_NEGATIVE,
            steps=body.steps, cfg_scale=body.cfg_scale,
        )
        return save_generated_image(img) if img else None

    url = await loop.run_in_executor(None, _regen)
    if url:
        return JSONResponse({"url": url})
    return JSONResponse({"error": "No image generated."}, status_code=500)


@app.post("/interrupt")
async def interrupt():
    image._gen_cancel.set()
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, lambda: req.post(
        f"{config.CFG['forge_url']}/sdapi/v1/interrupt", timeout=5
    ))
    return {"status": "interrupted"}


@app.get("/progress")
async def get_progress():
    try:
        r = req.get(f"{config.CFG['forge_url']}/sdapi/v1/progress?skip_current_image=false", timeout=3)
        return JSONResponse(r.json())
    except Exception:
        return JSONResponse({"progress": 0, "state": {}})


@app.post("/image")
async def image_from_history(body: ImageRequest):
    print(f"\n[backend] Received /image request, extra='{body.extra}'")
    if not llm.history and not body.extra.strip():
        return JSONResponse({"error": "No conversation history yet."}, status_code=400)

    try:
        def _run():
            image._gen_cancel.clear()
            recent    = llm.history[-8:]
            last_user  = next((m["content"] for m in reversed(recent) if m["role"] == "user"), "") if recent else body.extra
            messages   = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in recent) if recent else f"User: {body.extra}"
            print("[image] extracting SD prompt via LLM...")
            base_prompt = image.extract_sd_prompt(messages, appearance=ALICE_APPEARANCE,
                                                   last_user_msg=last_user, persona=SYSTEM_PROMPT)
            if image._gen_cancel.is_set():
                print("[image] cancelled before Forge call")
                return None, None
            positive_parts, negative_parts = [], []
            for token in [t.strip() for t in body.extra.split(",") if t.strip()]:
                if token.lower().startswith("no "):
                    negative_parts.append(token[3:].strip())
                else:
                    positive_parts.append(token)
            base_prompt = image.clean_tags(base_prompt)
            positive_extra = ", ".join(positive_parts)
            extra_negative = ", ".join(negative_parts)
            prompt = (positive_extra + ", " + base_prompt) if positive_extra else base_prompt
            prompt, extra_negative = image.apply_exposure_rules(messages, prompt, extra_negative)
            img = image.generate_image(prompt, ALICE_APPEARANCE, BASE_NEGATIVE, extra_negative=extra_negative)
            url = save_generated_image(img) if img else None
            return prompt, url

        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _run)
        sd_prompt, url = result if result and result[0] is not None else (None, None)
        if url is None and sd_prompt is None:
            return JSONResponse({"error": "Cancelled or failed."}, status_code=200)

        return JSONResponse({"sd_prompt": sd_prompt, "url": url})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/models")
async def list_models():
    models  = [{"name": n, "path": p} for n, p in llm.list_models()]
    current = llm.llm_model()
    return JSONResponse({"models": models, "current": current})


@app.post("/model")
async def switch_model(body: ModelSwitchRequest):
    config.CFG["llama_model"] = body.path
    llm.clear_history()
    print(f"\n[{config.NAME}] Switched llama model to: {body.path}")
    return JSONResponse({"status": "ok", "model": body.path})


@app.get("/personas")
async def list_personas():
    return JSONResponse({"personas": list(config.PERSONAS.keys())})


@app.post("/persona/{name}")
async def switch_persona(name: str):
    global ALICE_APPEARANCE, SYSTEM_PROMPT
    if name not in config.PERSONAS:
        return JSONResponse({"error": f"Persona '{name}' not found."}, status_code=404)
    p = config.PERSONAS[name]
    ALICE_APPEARANCE = p.get("appearance", ALICE_APPEARANCE)
    SYSTEM_PROMPT    = p.get("system_prompt", SYSTEM_PROMPT)
    llm.clear_history()
    print(f"\n[{config.NAME}] Switched to persona: {name}")
    return JSONResponse({"status": "ok", "persona": name})


@app.get("/voices")
async def list_voices():
    current = config.CFG.get("tts", {}).get("voice", "af_nicole")
    return JSONResponse({"voices": tts.VOICES, "current": current})


@app.post("/voice")
async def set_voice(body: VoiceRequest):
    if body.voice not in tts.VOICES:
        return JSONResponse({"error": "Unknown voice"}, status_code=400)
    config.CFG.setdefault("tts", {})["voice"] = body.voice
    return JSONResponse({"status": "ok", "voice": body.voice})


@app.post("/stt")
async def stt_endpoint(request: Request):
    data = await request.body()
    if not data:
        return JSONResponse({"error": "No audio data"}, status_code=400)
    loop = asyncio.get_running_loop()
    try:
        text = await loop.run_in_executor(
            None, lambda: stt.transcribe(data, request.headers.get("content-type", ""))
        )
        return JSONResponse({"text": text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _tts_clean(text: str) -> str:
    """Strip markdown that TTS would read aloud."""
    text = re.sub(r'\*+([^*]+)\*+', r'\1', text)   # *action* / **bold**
    text = re.sub(r'_+([^_]+)_+',   r'\1', text)   # _italic_
    text = re.sub(r'^#{1,6}\s+',    '',    text, flags=re.MULTILINE)  # # headers
    text = re.sub(r'\s+', ' ', text).strip()
    return text


@app.post("/tts")
async def speak(body: TtsRequest):
    if NO_SPEECH:
        return JSONResponse({"audio": None})
    if tts.TTS is None:
        return JSONResponse({"error": "TTS not ready"}, status_code=503)
    try:
        audio = await asyncio.get_running_loop().run_in_executor(
            None, lambda: tts.tts_wav_b64(_tts_clean(body.text))
        )
        return JSONResponse({"audio": audio})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/history")
async def clear():
    llm.clear_history()
    return {"status": "cleared"}


@app.delete("/image/{filename}")
async def delete_image(filename: str):
    # Reject any path traversal — only bare filenames like img_1234567890.png
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "", filename)
    if safe != filename or not safe.endswith(".png"):
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    path = os.path.join(config.ALICE_DIR, "static", "outputs", safe)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    os.remove(path)
    return JSONResponse({"status": "deleted"})


@app.get("/info")
async def info():
    return JSONResponse({
        "name":        config.NAME,
        "llm_ready":   llm.LLM_READY,
        "stt_silence": config.CFG.get("stt_silence_seconds", 3),
    })


@app.get("/", response_class=HTMLResponse)
async def index():
    static_dir = os.path.join(config.ALICE_DIR, "static")
    with open(os.path.join(static_dir, "index.html"), encoding="utf-8") as f:
        html = f.read()
    # Inject mtime-based cache busters so browsers always load fresh JS/CSS
    for asset in ("app.js", "style.css"):
        path = os.path.join(static_dir, asset)
        v = int(os.path.getmtime(path)) if os.path.exists(path) else 0
        html = re.sub(rf'(/static/{re.escape(asset)})\?v=\d+', rf'\g<1>?v={v}', html)
    return html


# ── Startup ───────────────────────────────────────────────────────────────────
def _startup():
    try:
        llm.load_llm()
        llm.load_history()
        if not NO_SPEECH:
            tts.load_tts()
        image.start_forge()
        sd_checkpoint = config.CFG.get("sd_checkpoint", "epiCPhotoGasmVAE.safetensors")
        image.set_forge_model(sd_checkpoint)
    except Exception as e:
        print(f"\n[{config.NAME}] FATAL ERROR IN STARTUP THREAD: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print()
    print("=" * 60)
    print(f"  {config.NAME}")
    print("=" * 60)
    print()
    print(f"[{config.NAME}] Starting server at {config.ALICE_URL}")
    from utils import IS_WSL
    if IS_WSL:
        try:
            import socket
            wsl_ip = socket.gethostbyname(socket.gethostname())
            print(f"        WSL2 detected. If localhost doesn't work, try http://{wsl_ip}:8000")
        except Exception:
            pass

    import socket as _sock
    with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as _s:
        if _s.connect_ex(("127.0.0.1", 8000)) == 0:
            print(f"\n[{config.NAME}] Port 8000 is already in use.")
            print(f"        Alice may already be running — check {config.ALICE_URL}")
            print(f"        If not, kill the process holding port 8000 and retry.")
            sys.exit(1)

    threading.Thread(target=_startup, daemon=True).start()

    if INTERACTIVE:
        def _open():
            time.sleep(2)
            from utils import IS_WSL
            if IS_WSL:
                # webbrowser.open() silently fails in WSL — use Windows browser
                import shutil
                opener = shutil.which("wslview") or "explorer.exe"
                subprocess.Popen([opener, config.ALICE_URL],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                webbrowser.open(config.ALICE_URL)
        threading.Thread(target=_open, daemon=True).start()
    else:
        print("        NOTE: Non-interactive session detected; not opening browser.")

    try:
        uvicorn.run(app, host="127.0.0.1", port=8000, log_config=_LOG_CONFIG)
    except BaseException as e:
        print(f"\n[{config.NAME}] Server failed: {type(e).__name__}: {e}")
