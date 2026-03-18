"""Forge process lifecycle: start, stop, model selection, Python detection."""
import os, platform, subprocess
import requests as req
import config
from utils import step, ok, warn, http_ok, wait_for

# _find_forge_python is defined here (mirrors installer/forge_install.py)
def _find_forge_python() -> str:
    """Return path to a Forge-compatible Python (3.10 or 3.11), or empty string."""
    import shutil
    if os.name != "nt":
        for ver in ("3.10", "3.11"):
            hit = shutil.which(f"python{ver}")
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
        match  = next((m for m in models if name in m), None)
        if match:
            req.post(f"{forge_url}/sdapi/v1/options",
                     json={"sd_model_checkpoint": match}, timeout=30)
            ok(f"Forge model set to: {match}")
        else:
            warn(f"Model '{name}' not found in Forge model list.")
    except Exception as e:
        warn(f"Could not set Forge model: {e}")


def restart_forge():
    """Ask Forge to restart via its API (picks up newly installed extensions)."""
    forge_url = config.CFG["forge_url"]
    try:
        req.post(f"{forge_url}/sdapi/v1/server-restart", timeout=10)
    except Exception:
        pass  # connection reset is expected on restart
    if not wait_for(f"{forge_url}/sdapi/v1/sd-models", "Forge (restart)", retries=60, delay=5):
        warn("Forge did not come back after restart.")


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
    if "forge_args" in config.CFG:
        env["COMMANDLINE_ARGS"] = config.CFG["forge_args"]
    elif os.name == "nt":
        env["COMMANDLINE_ARGS"] = "--api --cuda-malloc --xformers"
    elif platform.system() == "Darwin":
        env["COMMANDLINE_ARGS"] = "--api --skip-torch-cuda-test"
    else:
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

    forge_venv_dir = config.CFG.get("forge_venv_dir", "").strip()
    if forge_venv_dir:
        env["VENV_DIR"] = forge_venv_dir
        ok(f"Forge: using venv at {forge_venv_dir}")

    kw = {"cwd": config.FORGE_DIR, "env": env}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE

    subprocess.Popen(launcher, **kw)
    if not wait_for(f"{forge_url}/sdapi/v1/sd-models", "Forge", retries=60, delay=5):
        warn("Forge did not start in time - images won't generate.")
