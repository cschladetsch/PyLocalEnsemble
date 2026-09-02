"""Forge process lifecycle: start, model selection, Python detection.

Ported from server/image/forge.py, trimmed of the warmup-cache machinery
(an Alice chat-latency optimization not needed for a manual console) and
rewired onto sd_app's own config/utils instead of Alice's.
"""
import os, platform, subprocess, shlex
import requests as req
import config
from utils import step, ok, warn, http_ok, wait_for


# _find_forge_python mirrors server/image/forge.py and server/installer/forge_install.py
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


def _python_from_venv_dir(venv_dir: str) -> str:
    if not venv_dir:
        return ""
    runner = "Scripts" if os.name == "nt" else "bin"
    exe = "python.exe" if os.name == "nt" else "python"
    return os.path.join(venv_dir, runner, exe)


def _ensure_forge_tooling(python_exe: str, env: dict) -> None:
    if not python_exe or not os.path.exists(python_exe):
        return
    try:
        subprocess.run([python_exe, "-m", "pip", "install", "--upgrade", "--quiet", "pip", "setuptools<81", "wheel"],
                       check=True, env=env, capture_output=True)
    except subprocess.CalledProcessError as exc:
        warn(f"Forge tooling upgrade failed: {exc}")


def _push_forge_settings(forge_url: str) -> None:
    """Push sd_app-managed settings to Forge, skipping keys this build doesn't support."""
    desired = {
        # Forge resets forge_inference_memory to 0 whenever the checkpoint option changes.
        # 0 MB leaves no VRAM for compute, causing a silent hang during VAE decode with
        # the cudaMallocAsync backend. Pin it to 1024 so decode always has headroom.
        "forge_inference_memory":      1024,
        "sd_checkpoints_keep_in_cpu":  False,
        "samples_save":                False,     # sd_app saves its own images
        "grid_save":                   False,
        "save_to_dirs":                False,
        "samples_format":              "png",
    }
    try:
        known = set(req.get(f"{forge_url}/sdapi/v1/options", timeout=10).json().keys())
        settings = {k: v for k, v in desired.items() if k in known}
        if settings:
            r = req.post(f"{forge_url}/sdapi/v1/options", json=settings, timeout=30)
            if r.status_code != 200:
                warn(f"Forge settings push returned HTTP {r.status_code}")
    except Exception as e:
        warn(f"Could not push Forge settings: {e}")


def set_forge_model(name: str, refresh: bool = False) -> bool:
    forge_url = config.CFG["forge_url"]
    try:
        if refresh:
            req.post(f"{forge_url}/sdapi/v1/refresh-checkpoints", timeout=90)
        r = req.get(f"{forge_url}/sdapi/v1/sd-models", timeout=5)
        models = [m["title"] for m in r.json()]
        match  = next((m for m in models if name in m), None)
        if match:
            _push_forge_settings(forge_url)
            req.post(f"{forge_url}/sdapi/v1/options",
                     json={"sd_model_checkpoint": match}, timeout=30)
            ok(f"Forge model set to: {match}")
            return True
        else:
            warn(f"Model '{name}' not found in Forge model list.")
            warn(f"Available models: {', '.join(models) if models else '(none)'}")
            return False
    except Exception as e:
        warn(f"Could not set Forge model: {e}")
        return False


def restart_forge():
    """Ask Forge to restart via its API (picks up newly installed extensions)."""
    forge_url = config.CFG["forge_url"]
    try:
        req.post(f"{forge_url}/sdapi/v1/server-restart", timeout=100)
    except Exception:
        pass  # connection reset is expected on restart
    if not wait_for(f"{forge_url}/sdapi/v1/sd-models", "Forge (restart)", retries=30, delay=3):
        warn("Forge did not come back after restart.")


def start_forge() -> bool:
    forge_url = config.CFG["forge_url"]
    step("Starting Forge...")
    if http_ok(f"{forge_url}/sdapi/v1/sd-models"):
        ok("Forge already running.")
        return True
    launcher = config.FORGE_BAT
    if not os.path.exists(launcher):
        warn(f"Forge not found at {config.FORGE_DIR} — install it via Alice's installer first")
        return False
    env = os.environ.copy()
    if config.CFG.get("forge_args"):
        base_args = config.CFG["forge_args"]
    elif os.name == "nt":
        base_args = "--api --cuda-malloc --xformers"
    elif platform.system() == "Darwin":
        base_args = "--api --skip-torch-cuda-test"
    else:
        base_args = "--api --xformers"

    import urllib.parse as _up
    _parsed = _up.urlparse(forge_url)
    _port = _parsed.port or 7860
    if "--port" not in base_args:
        base_args = f"{base_args} --port {_port}"

    env["COMMANDLINE_ARGS"] = base_args

    cli_parts = shlex.split(base_args)
    if "--api" not in cli_parts:
        cli_parts.append("--api")

    default_venv_python = _python_from_venv_dir(os.path.join(config.FORGE_DIR, "venv"))
    forge_py = _find_forge_python()
    python_for_upgrade = default_venv_python
    if forge_py:
        env["PYTHON"] = forge_py
        ok(f"Forge: using Python at {forge_py}")
        python_for_upgrade = forge_py
        # On Windows, a venv's python.exe doesn't bundle its own pythonXY.dll —
        # it resolves it via the OS DLL search path (PATH). If forge_py's install
        # dir isn't already on PATH, the venv python fails to start.
        if os.name == "nt":
            env["PATH"] = os.path.dirname(forge_py) + os.pathsep + env.get("PATH", "")
    else:
        warn("Python 3.10/3.11 not found — Forge may fail with the system Python")

    forge_venv_dir = config.CFG.get("forge_venv_dir", "").strip()
    if forge_venv_dir:
        env["VENV_DIR"] = forge_venv_dir
        ok(f"Forge: using venv at {forge_venv_dir}")
        if not forge_py:
            venv_python = _python_from_venv_dir(forge_venv_dir)
            if venv_python:
                python_for_upgrade = venv_python

    _ensure_forge_tooling(python_for_upgrade, env)
    env.setdefault("PIP_NO_BUILD_ISOLATION", "1")

    kw = {"cwd": config.FORGE_DIR, "env": env}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        if launcher.lower().endswith(".ps1"):
            launcher_cmd = ["powershell", "-NoProfile", "-NoExit", "-File", launcher]
        else:
            launcher_cmd = [launcher]
    else:
        launcher_cmd = [launcher]

    subprocess.Popen(launcher_cmd + cli_parts, **kw)
    if not wait_for(f"{forge_url}/sdapi/v1/sd-models", "Forge", retries=300, delay=2):
        warn("Forge did not start in time — generation will still work once Forge is ready.")
        return False
    _push_forge_settings(forge_url)
    return True
