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
    """Push Alice-managed settings to Forge so users never need to touch Forge's UI."""
    settings = {
        # VAE compute device — the setting that actually controls whether the
        # decode step runs on CPU or GPU.  sd_vae_cpu_offload is Forge's own
        # option; vae_in_cpu is the upstream SD-WebUI equivalent.  Push both so
        # whichever one this Forge build respects gets set.
        "sd_vae_cpu_offload": False,
        "vae_in_cpu":         False,
        "vae_in_fp32":        False,   # no slow fp32 upcast
        "samples_save":       False,   # Alice saves its own images
        "grid_save":          False,
        "save_to_dirs":       False,
        "samples_format":     "png",
    }
    try:
        r = req.post(f"{forge_url}/sdapi/v1/options", json=settings, timeout=10)
        if r.status_code != 200:
            warn(f"Forge settings push returned HTTP {r.status_code}")
    except Exception as e:
        warn(f"Could not push Forge settings: {e}")

    # Log the VAE-related options that are actually active in Forge, so we
    # can diagnose decode-on-CPU problems without guessing at setting names.
    try:
        opts = req.get(f"{forge_url}/sdapi/v1/options", timeout=5).json()
        vae_opts = {k: v for k, v in opts.items()
                    if any(t in k.lower() for t in ("vae", "cpu", "offload", "device"))}
        if vae_opts:
            print(f"[forge] active VAE/CPU options: {vae_opts}")
    except Exception:
        pass


def set_forge_model(name: str) -> bool:
    forge_url = config.CFG["forge_url"]
    try:
        req.post(f"{forge_url}/sdapi/v1/refresh-checkpoints", timeout=30)
        r = req.get(f"{forge_url}/sdapi/v1/sd-models", timeout=5)
        models = [m["title"] for m in r.json()]
        match  = next((m for m in models if name in m), None)
        if match:
            req.post(f"{forge_url}/sdapi/v1/options",
                     json={"sd_model_checkpoint": match}, timeout=30)
            _push_forge_settings(forge_url)
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
        req.post(f"{forge_url}/sdapi/v1/server-restart", timeout=10)
    except Exception:
        pass  # connection reset is expected on restart
    if not wait_for(f"{forge_url}/sdapi/v1/sd-models", "Forge (restart)", retries=30, delay=10):
        warn("Forge did not come back after restart.")


def warmup_forge() -> None:
    """Send a 1-step dummy generation to force the checkpoint into VRAM."""
    forge_url = config.CFG["forge_url"]
    step("Warming up Forge (loading model into VRAM)...")
    try:
        r = req.post(f"{forge_url}/sdapi/v1/txt2img", json={
            "prompt": "test",
            "negative_prompt": "",
            "steps": 1,
            "width": 128,
            "height": 128,
            "cfg_scale": 1,
            "seed": 42,
            "override_settings": {"samples_save": False, "grid_save": False},
        }, timeout=300)
        if r.ok:
            ok("Forge warmup done — model in VRAM.")
        else:
            warn(f"Forge warmup returned {r.status_code}")
    except Exception as e:
        warn(f"Forge warmup failed: {e}")


def start_forge() -> bool:
    forge_url = config.CFG["forge_url"]
    step("Starting Forge...")
    if http_ok(f"{forge_url}/sdapi/v1/sd-models"):
        ok("Forge already running.")
        return True
    launcher = config.FORGE_BAT
    if not os.path.exists(launcher):
        warn(f"Forge not found at {config.FORGE_DIR} — run install.py")
        return False
    env = os.environ.copy()
    if "forge_args" in config.CFG:
        env["COMMANDLINE_ARGS"] = config.CFG["forge_args"]
    elif os.name == "nt":
        env["COMMANDLINE_ARGS"] = "--api --cuda-malloc --xformers"
    elif platform.system() == "Darwin":
        env["COMMANDLINE_ARGS"] = "--api --skip-torch-cuda-test"
    else:
        env["COMMANDLINE_ARGS"] = "--api --xformers"

    default_venv_python = _python_from_venv_dir(os.path.join(config.FORGE_DIR, "venv"))
    forge_py = _find_forge_python()
    python_for_upgrade = default_venv_python
    if forge_py:
        env["PYTHON"] = forge_py
        ok(f"Forge: using Python at {forge_py}")
        python_for_upgrade = forge_py
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
        if not forge_py:
            venv_python = _python_from_venv_dir(forge_venv_dir)
            if venv_python:
                python_for_upgrade = venv_python

    _ensure_forge_tooling(python_for_upgrade, env)

    env.setdefault("PIP_NO_BUILD_ISOLATION", "1")

    kw = {"cwd": config.FORGE_DIR, "env": env}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE

    subprocess.Popen(launcher, **kw)
    if not wait_for(f"{forge_url}/sdapi/v1/sd-models", "Forge", retries=300, delay=4):
        warn("Forge did not start in time - images won't generate.")
        return False
    return True
