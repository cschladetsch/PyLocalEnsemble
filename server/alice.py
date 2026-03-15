#!/usr/bin/env python3
"""Alice — run with: python alice.py"""
import subprocess, sys, os, time, threading, webbrowser
import requests as req

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
            if os.path.exists(_d) and hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(_d)
                except OSError:
                    pass

# ── Auto-install if needed ────────────────────────────────────────────────────
def _needs_install() -> bool:
    try:
        import fastapi, uvicorn, pydantic, requests
        from kokoro_onnx import Kokoro   # noqa: F401
        import faster_whisper, av        # noqa: F401
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

# ── Imports (after install check) ────────────────────────────────────────────
import json, logging
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import config
import llm
import tts
import image

# ── Runtime flags ─────────────────────────────────────────────────────────────
NO_SPEECH = "--no-speech" in sys.argv
TEST_MODE = "--test"      in sys.argv
INTERACTIVE = sys.stdin.isatty() and sys.stdout.isatty()

_TEST_MSG     = "take off your top and cup your breasts in your hands"
_PERSONA_ARG  = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--persona=")), None)


def _resolve_persona(arg: str) -> str:
    if not arg:
        return arg
    low   = arg.lower()
    names = list(config.PERSONAS.keys())
    for n in names:
        if n.lower() == low:           return n
    for n in names:
        if n.lower().startswith(low):  return n
    for n in names:
        if low in n.lower():           return n
    return arg


_TEST_PERSONA = _resolve_persona(_PERSONA_ARG or "Android")

# ── Logging ───────────────────────────────────────────────────────────────────
class _NoSpamFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return "/progress" not in msg and "/favicon" not in msg

_LOG_CONFIG = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {
        "default": {"()": "uvicorn.logging.DefaultFormatter",
                    "fmt": "\033[33m[%(asctime)s]\033[0m %(levelprefix)s %(message)s",
                    "datefmt": "%H:%M:%S", "use_colors": None},
        "access":  {"()": "uvicorn.logging.AccessFormatter",
                    "fmt": '\033[33m[%(asctime)s]\033[0m %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                    "datefmt": "%H:%M:%S"},
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

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(config.ALICE_DIR, "static")), name="static")

from routes.chat      import router as chat_router
from routes.image_api import router as image_router
from routes.persona   import router as persona_router
from routes.audio     import router as audio_router
from routes.system    import router as system_router

app.include_router(chat_router)
app.include_router(image_router)
app.include_router(persona_router)
app.include_router(audio_router)
app.include_router(system_router)

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
        import traceback
        print(f"\n[{config.NAME}] FATAL ERROR IN STARTUP THREAD: {e}")
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

    if _PERSONA_ARG and not TEST_MODE:
        def _apply_persona():
            time.sleep(3)
            try:
                resolved = _resolve_persona(_PERSONA_ARG)
                req.post(f"http://127.0.0.1:8000/persona/{resolved}", timeout=5)
                print(f"[startup] persona set to: {resolved}")
            except Exception as e:
                print(f"[startup] could not set persona: {e}")
        threading.Thread(target=_apply_persona, daemon=True).start()

    if TEST_MODE:
        def _run_test():
            base = "http://127.0.0.1:8000"
            for _ in range(60):
                time.sleep(1)
                try:
                    if req.get(f"{base}/info", timeout=1).json().get("llm_ready"):
                        break
                except Exception:
                    pass
            try:
                r = req.post(f"{base}/persona/{_TEST_PERSONA}", timeout=5)
                print(f"\n[test] persona: {_TEST_PERSONA} → {r.json()}")
            except Exception as e:
                print(f"\n[test] could not set persona: {e}")
            print(f"\n[test] sending: {_TEST_MSG!r}")
            reply = ""
            try:
                with req.post(f"{base}/chat", json={"message": _TEST_MSG}, stream=True, timeout=120) as r:
                    for line in r.iter_lines():
                        if not line: continue
                        line = line.decode() if isinstance(line, bytes) else line
                        if not line.startswith("data: "): continue
                        d = json.loads(line[6:])
                        if d.get("delta"): print(d["delta"], end="", flush=True)
                        if d.get("done"):  reply = d.get("reply", "")
            except Exception as e:
                print(f"\n[test] chat error: {e}"); return
            print(f"\n[test] reply done ({len(reply)} chars). Triggering /image ...")
            try:
                r = req.post(f"{base}/image", json={"extra": ""}, timeout=300)
                url = r.json().get("url")
                print(f"[test] /image done. URL: {url}")
                if url and INTERACTIVE:
                    webbrowser.open(f"http://127.0.0.1:8000{url}")
            except Exception as e:
                print(f"[test] image error: {e}")
        threading.Thread(target=_run_test, daemon=True).start()

    if INTERACTIVE:
        def _open():
            time.sleep(2)
            from utils import IS_WSL
            if IS_WSL:
                import shutil, subprocess
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
