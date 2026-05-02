#!/usr/bin/env python3
"""Alice — run with: python alice.py"""
import subprocess, sys, os, time, threading, webbrowser
import requests as req

from logging_setup import init_logging

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
_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SERVER_DIR)


def _tts_assets_present() -> bool:
    tts_dir = os.path.join(_SERVER_DIR, "models", "tts")
    return all(
        os.path.exists(os.path.join(tts_dir, name))
        for name in ("kokoro-v0_19.onnx", "voices.bin")
    )


def _llama_server_present() -> bool:
    base = os.path.join(_SERVER_DIR, "llama-cpp")
    return any(
        os.path.isfile(os.path.join(base, exe))
        for exe in ("llama-server.exe", "llama-server")
    )


def _forge_present() -> bool:
    return os.path.isdir(os.path.join(_SERVER_DIR, "stable-diffusion-webui-forge"))


def _needs_install() -> bool:
    if any("pytest" in arg for arg in sys.argv):
        return False
    if not (_tts_assets_present() and _llama_server_present() and _forge_present()):
        return True
    import importlib.util
    required = ["fastapi", "uvicorn", "pydantic", "requests",
                "kokoro_onnx", "faster_whisper", "av"]
    return not all(importlib.util.find_spec(pkg) is not None for pkg in required)

if _needs_install():
    _install = os.path.join(_ROOT_DIR, "install.py")
    print("\nDependencies missing — running install.py first...\n")
    result = subprocess.run([sys.executable, _install])
    if result.returncode != 0:
        print("\ninstall.py failed. Fix the errors above and try again.")
        sys.exit(1)
    print("\nInstall complete — starting Alice...\n")

_PYTHON_LOG_FILE = init_logging("python-server")

# ── Imports (after install check) ────────────────────────────────────────────
import json, logging
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import config
import llm
import state
import tts
import image

HOST = "127.0.0.1"
PORT = int(config.CFG.get("port", getattr(config, "PORT", 8000)))

# ── Runtime flags ─────────────────────────────────────────────────────────────
NO_SPEECH  = "--no-speech"   in sys.argv
NO_FORGE   = "--no-forge"    in sys.argv
TEST_MODE  = "--test"        in sys.argv
AUTO_IMAGE = "--auto-image"  in sys.argv
INTERACTIVE = sys.stdin.isatty() and sys.stdout.isatty()

if "--voices" in sys.argv:
    from tts import VOICES
    print("\nAvailable TTS voices:\n")
    for v in VOICES:
        print(f"  {v}")
    print()
    sys.exit(0)

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


_TEST_PERSONA = _resolve_persona(_PERSONA_ARG or "Alice")

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
        "plain":   {"format": "%(asctime)s %(levelname)s [%(name)s] %(message)s"},
    },
    "handlers": {
        "default": {"formatter": "default", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
        "access":  {"formatter": "access",  "class": "logging.StreamHandler", "stream": "ext://sys.stdout",
                    "filters": ["no_spam"]},
        "file":    {"formatter": "plain", "class": "logging.FileHandler", "filename": _PYTHON_LOG_FILE, "encoding": "utf-8"},
    },
    "filters": {"no_spam": {"()": _NoSpamFilter}},
    "loggers": {
        "uvicorn":        {"handlers": ["default", "file"], "level": "INFO", "propagate": False},
        "uvicorn.error":  {"handlers": ["default", "file"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["access", "file"],  "level": "INFO", "propagate": False},
    },
}

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI()
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

from routes.chat      import router as chat_router
from routes.image_api import router as image_router
from routes.persona   import router as persona_router
from routes.audio     import router as audio_router
from routes.system    import router as system_router
from routes.group     import router as group_router

app.include_router(chat_router)
app.include_router(image_router)
app.include_router(persona_router)
app.include_router(audio_router)
app.include_router(system_router)
app.include_router(group_router)

# ── Startup ───────────────────────────────────────────────────────────────────
def _ensure_llm_ready(timeout: int = 120):
    llm.load_llm()
    if not llm.wait_until_ready(timeout):
        raise RuntimeError("llama.cpp server did not become ready; check llama-server output.")
    import vram
    vram.notify_llm_ready()
    vram.log_resources("LLM ready")


def _ensure_tts_ready():
    import threading
    def _load():
        if tts.load_tts():
            print(f"[{config.NAME}] TTS ready.")
        else:
            print(f"[{config.NAME}] TTS failed to load — audio disabled.")
    threading.Thread(target=_load, daemon=True, name="tts-loader").start()
    print(f"[{config.NAME}] TTS loading in background...")


def _ensure_forge_ready():
    import vram
    vram.setup(config.CFG["forge_url"])
    if not image.start_forge():
        raise RuntimeError("Failed to launch Stable Diffusion Forge.")
    sd_checkpoint = config.CFG.get("sd_checkpoint", "pornmasterPro_v9VAE.safetensors")

    # Skip set_forge_model (slow refresh-checkpoints included) if Forge already has
    # the right checkpoint. Fall back to full set when it differs or check fails.
    _needs_model_set = True
    try:
        opts = req.get(f"{config.CFG['forge_url']}/sdapi/v1/options", timeout=5).json()
        current = opts.get("sd_model_checkpoint", "")
        if current and (sd_checkpoint in current or current in sd_checkpoint):
            _needs_model_set = False
            print(f"[forge] Checkpoint already correct: {current}")
            from image.forge import _push_forge_settings
            _push_forge_settings(config.CFG["forge_url"])
    except Exception:
        pass

    if _needs_model_set:
        if not image.set_forge_model(sd_checkpoint):
            raise RuntimeError(f"Forge could not select checkpoint '{sd_checkpoint}'.")

    if config.CFG.get("vram_swap_for_image", False):
        # vram_swap mode: evict Forge so LLM gets full VRAM. Checkpoint reloads on
        # first image request via acquire_for_image(). Only needed when model is too
        # large to coexist with Forge (>5GB model on 8GB GPU).
        vram.unload_forge()
        print("[vram] Forge evicted at startup — LLM will have full VRAM.")
    else:
        # Normal mode: LLM and Forge coexist. Warmup loads the checkpoint into VRAM
        # now so the first image request doesn't pay the reload cost.
        image.warmup_forge()


def _startup():
    import vram as _vram_mod
    _vram_mod.log_resources("startup baseline")

    _vram_swap = config.CFG.get("vram_swap_for_image", False)

    if not NO_FORGE and _vram_swap:
        # vram_swap: Forge must start before LLM so the checkpoint is evicted first,
        # freeing VRAM for the LLM. Only needed for models >5GB on 8GB GPUs.
        _ensure_forge_ready()

    _ensure_llm_ready()

    if not state._active_persona_key:
        state._active_persona_key = next(iter(config.PERSONAS), config.NAME)

    llm.load_history()

    if AUTO_IMAGE:
        config.CFG.setdefault("image", {})["auto_every"] = 1
        print(f"[{config.NAME}] Auto-image enabled (--auto-image)")

    if not NO_SPEECH:
        _ensure_tts_ready()

    if not NO_FORGE and not _vram_swap:
        _ensure_forge_ready()
    elif NO_FORGE:
        print(f"[{config.NAME}] Skipping Forge startup (--no-forge)")


def _listener_pid(host: str, port: int):
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex((host, port)) != 0:
            return None

    if os.name == "nt":
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(f":{port}") and parts[3].upper() == "LISTENING":
                try:
                    return int(parts[4])
                except ValueError:
                    return None
        return None

    result = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


def _kill_listener(host: str, port: int) -> bool:
    pid = _listener_pid(host, port)
    if not pid:
        return False

    print(f"[{config.NAME}] Port {port} is already in use by PID {pid}. Terminating it and retrying...")
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            result = subprocess.run(
                ["kill", "-TERM", str(pid)],
                capture_output=True,
                text=True,
                check=False,
            )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            print(f"[{config.NAME}] Could not terminate PID {pid}: {detail or 'unknown error'}")
            return False
    except Exception as e:
        print(f"[{config.NAME}] Could not terminate PID {pid}: {e}")
        return False

    deadline = time.time() + 5
    while time.time() < deadline:
        if _listener_pid(host, port) is None:
            return True
        time.sleep(0.2)

    print(f"[{config.NAME}] Port {port} is still busy after terminating PID {pid}.")
    return False


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
            print(f"        WSL2 detected. If localhost doesn't work, try http://{wsl_ip}:{PORT}")
        except Exception:
            pass

    _kill_listener(HOST, PORT)

    def _run_startup():
        try:
            _startup()
        except RuntimeError as exc:
            print(f"\n[{config.NAME}] Preflight failed: {exc}")

    threading.Thread(target=_run_startup, daemon=True, name="startup").start()

    if _PERSONA_ARG and not TEST_MODE:
        def _apply_persona():
            time.sleep(3)
            try:
                resolved = _resolve_persona(_PERSONA_ARG)
                req.post(f"http://{HOST}:{PORT}/persona/{resolved}", timeout=5)
                print(f"[startup] persona set to: {resolved}")
            except Exception as e:
                print(f"[startup] could not set persona: {e}")
        threading.Thread(target=_apply_persona, daemon=True).start()

    if TEST_MODE:
        def _run_test():
            base = f"http://{HOST}:{PORT}"
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
                    webbrowser.open(f"http://{HOST}:{PORT}{url}")
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
        uvicorn.run(app, host=HOST, port=PORT, log_config=_LOG_CONFIG)
    except BaseException as e:
        print(f"\n[{config.NAME}] Server failed: {type(e).__name__}: {e}")
