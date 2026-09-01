"""Forge process lifecycle: start, stop, model selection, Python detection."""
import os, platform, subprocess, time, shlex
import requests as req
import config
from utils import step, ok, warn, http_ok, wait_for

# ── Forge warmup state cache ──────────────────────────────────────────────────
# Record when the checkpoint was last warmed up so a subsequent startup can
# skip the redundant 1-step warmup if Forge is already running with the
# right checkpoint hot in VRAM.
_FORGE_WARMED_FILE = os.path.join(config.SERVER_DIR, ".forge_warmed")

def _forge_warmed_timestamp() -> float:
    try:
        with open(_FORGE_WARMED_FILE, "r", encoding="utf-8") as f:
            return float(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0.0

def _record_forge_warmed() -> None:
    try:
        with open(_FORGE_WARMED_FILE, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception:
        pass

def _clear_forge_warmed() -> None:
    try:
        os.remove(_FORGE_WARMED_FILE)
    except FileNotFoundError:
        pass


def _is_checkpoint_fresh() -> bool:
    """Return True if the configured checkpoint file hasn't been replaced since
    the last recorded warmup. Uses file mtime as a cheap staleness proxy."""
    cfg = config.CFG
    sd_checkpoint = cfg.get("sd_checkpoint", "")
    if not sd_checkpoint:
        return False
    # Walk Forge's checkpoint directory for a file whose name contains the
    # configured checkpoint string.
    forge_dir = config.FORGE_DIR
    if not os.path.isdir(forge_dir):
        return False
    for dirpath, _, files in os.walk(forge_dir):
        for fname in files:
            if fname.endswith(".safetensors") and sd_checkpoint in fname:
                mtime = os.path.getmtime(os.path.join(dirpath, fname))
                return mtime <= _forge_warmed_timestamp()
    return False


def _format_age(ts: float) -> str:
    age = time.time() - ts
    if age < 60:
        return f"{int(age)}s ago"
    if age < 3600:
        return f"{int(age // 60)}m ago"
    return f"{int(age // 3600)}h ago"

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
    """Push Alice-managed settings to Forge, skipping keys this build doesn't support."""
    desired = {
        # Forge resets forge_inference_memory to 0 whenever the checkpoint option changes.
        # 0 MB leaves no VRAM for compute, causing a silent hang during VAE decode with
        # the cudaMallocAsync backend. Pin it to 1024 so decode always has headroom.
        "forge_inference_memory":      1024,
        "sd_checkpoints_keep_in_cpu":  False,
        "samples_save":                False,     # Alice saves its own images
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
        skipped = set(desired) - settings.keys()
        if skipped:
            print(f"[forge] skipped unknown option keys: {skipped}")
    except Exception as e:
        warn(f"Could not push Forge settings: {e}")

    try:
        opts = req.get(f"{forge_url}/sdapi/v1/options", timeout=5).json()
        vae_opts = {k: v for k, v in opts.items()
                    if any(t in k.lower() for t in ("vae", "cpu", "offload", "device"))}
        if vae_opts:
            print(f"[forge] active VAE/CPU options: {vae_opts}")
    except Exception:
        pass


def set_forge_model(name: str, refresh: bool = False) -> bool:
    forge_url = config.CFG["forge_url"]
    try:
        if refresh:
            req.post(f"{forge_url}/sdapi/v1/refresh-checkpoints", timeout=90)
        r = req.get(f"{forge_url}/sdapi/v1/sd-models", timeout=5)
        models = [m["title"] for m in r.json()]
        match  = next((m for m in models if name in m), None)
        if match:
            # Push memory settings (esp. keep_in_cpu=False) BEFORE triggering the
            # checkpoint load so Forge doesn't cache the weights in CPU RAM.
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
        _clear_forge_warmed()
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


def warmup_forge() -> None:
    """Send a 1-step dummy generation to pre-load the checkpoint into VRAM."""
    forge_url = config.CFG["forge_url"]
    step("Warming up Forge (loading model into VRAM)...")

    # Skip warmup if Forge was already warmed in a previous session and the
    # checkpoint hasn't changed since. The checkpoint file mtime is a cheap
    # proxy for "has the checkpoint been replaced / updated".
    _last_warmed = _forge_warmed_timestamp()
    if _last_warmed and _is_checkpoint_fresh():
        print(f"        Forge already warmed ({_format_age(_last_warmed)}) — skipping")
        return

    # If Forge was just started by start_forge() in this session, the checkpoint
    # is already loaded in VRAM — the /sdapi/v1/sd-models check confirms it.
    # Only run warmup if Forge was already running when we got here (i.e. it
    # was started externally or by a previous session).
    try:
        opts = req.get(f"{forge_url}/sdapi/v1/options", timeout=5).json()
        current = opts.get("sd_model_checkpoint", "")
        desired = config.CFG.get("sd_checkpoint", "")
        if current and desired and (current in desired or desired in current):
            # Forge is already running with the right checkpoint hot — skip warmup.
            print(f"        Forge already running with checkpoint loaded — skipping warmup")
            return
    except Exception:
        pass

    try:
        r = req.post(f"{forge_url}/sdapi/v1/txt2img", json={
            "prompt": "test",
            "negative_prompt": "",
            "steps": 1,
            "width": 64,
            "height": 64,
            "cfg_scale": 1,
            "seed": 42,
            "override_settings": {"samples_save": False, "grid_save": False},
        }, timeout=220)
        if r.ok:
            ok("Forge warmup done — model in VRAM.")
            _record_forge_warmed()
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
        base_args = config.CFG["forge_args"]
    elif os.name == "nt":
        base_args = "--api --cuda-malloc --xformers"
    elif platform.system() == "Darwin":
        base_args = "--api --skip-torch-cuda-test"
    else:
        base_args = "--api --xformers"
    # Append --port to match forge_url so Forge binds on the expected port.
    # Do NOT set --ckpt-dir here; Forge's launch.py auto-detects the standard
    # %USERPROFILE%\.models\shared directory, and webui-user.ps1 may also set
    # it — having both causes confusion and double-passed arguments.
    import urllib.parse as _up
    _parsed = _up.urlparse(forge_url)
    _port = _parsed.port or 7860
    if "--port" not in base_args:
        base_args = f"{base_args} --port {_port}"

    env["COMMANDLINE_ARGS"] = base_args

    # The launcher passes CLI args directly to launch.py.  Ensure --api is present
    # so Forge boots in API-only mode (no Gradio UI) regardless of any
    # webui-user.ps1 override of the COMMANDLINE_ARGS env var.
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
        # dir isn't already on PATH (common for per-user python.org installs
        # registered only with the py launcher), the venv python fails to start
        # with "python3XX.dll not found", and Forge never comes up.
        if os.name == "nt":
            env["PATH"] = os.path.dirname(forge_py) + os.pathsep + env.get("PATH", "")
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
        # .ps1 files need to be launched via powershell -File
        if launcher.lower().endswith(".ps1"):
            launcher_cmd = ["powershell", "-NoProfile", "-NoExit", "-File", launcher]
        else:
            launcher_cmd = [launcher]
    else:
        launcher_cmd = [launcher]

    subprocess.Popen(launcher_cmd + cli_parts, **kw)
    if not wait_for(f"{forge_url}/sdapi/v1/sd-models", "Forge", retries=300, delay=2):
        warn("Forge did not start in time — images may be slow, but generation will still work once Forge is ready.")
        return False
    # Push keep_in_cpu=False as early as possible so the auto-loaded checkpoint
    # (and any subsequent load) doesn't duplicate model weights in CPU RAM.
    _push_forge_settings(forge_url)
    return True
