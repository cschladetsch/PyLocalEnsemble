"""Step 6: Stable Diffusion Forge clone and checkpoint download."""
import os, platform, shutil, subprocess
from installer.helpers import (FORGE_DIR, FORGE_BAT, CONF_DIR,
                                heading, ok, info, warn, _download, _filename_from_response)

_RV_FILENAME = "pornmasterPro_v9VAE.safetensors"
_RV_URL      = ("https://huggingface.co/Demo112211/pornmasterPro_v9VAE.safetensors"
                "/resolve/main/pornmasterPro_v9VAE.safetensors")


def _find_forge_python() -> str:
    """Return path to a Forge-compatible Python (3.10 or 3.11), or empty string."""
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


_ADETAILER_REPO  = "https://github.com/Bing-su/adetailer"
_HAND_MODEL_URL  = "https://huggingface.co/Bingsu/adetailer/resolve/main/hand_yolov8n.pt"
_HAND_MODEL_NAME = "hand_yolov8n.pt"


def install_adetailer():
    """Clone the ADetailer extension and download the hand detection model."""
    ext_dir   = os.path.join(FORGE_DIR, "extensions", "adetailer")
    model_dir = os.path.join(FORGE_DIR, "models", "adetailer")
    os.makedirs(model_dir, exist_ok=True)

    if os.path.isdir(ext_dir):
        ok("ADetailer extension already present")
    else:
        info("cloning ADetailer extension ...")
        result = subprocess.run(["git", "clone", "--depth", "1", _ADETAILER_REPO, ext_dir])
        if result.returncode != 0:
            warn("Failed to clone ADetailer — hand repair will be unavailable.")
            return
        ok("ADetailer extension cloned")

    dest = os.path.join(model_dir, _HAND_MODEL_NAME)
    if os.path.exists(dest):
        ok(f"ADetailer hand model already present: {_HAND_MODEL_NAME}")
    else:
        info(f"downloading ADetailer hand model ({_HAND_MODEL_NAME}, ~6 MB) ...")
        try:
            _download(_HAND_MODEL_URL, dest, _HAND_MODEL_NAME)
            ok(f"ADetailer hand model ready: {_HAND_MODEL_NAME}")
        except Exception as e:
            warn(f"Failed to download hand model: {e}")
            warn("Download manually from: " + _HAND_MODEL_URL)
            warn(f"Place it in: {model_dir}")


def install_forge(cfg: dict):
    heading("6/6", "Stable Diffusion Forge")

    if not os.path.exists(FORGE_BAT):
        info("cloning Stable Diffusion Forge (large repo, may take several minutes) ...")
        result = subprocess.run(["git", "clone",
                                 "https://github.com/lllyasviel/stable-diffusion-webui-forge",
                                 FORGE_DIR])
        if result.returncode != 0:
            warn("Failed to clone Forge. Ensure git is installed and you have internet access.")
            warn("Forge is optional — chat and TTS will still work without it.")
            return
        ok("Forge cloned")
    else:
        ok("Forge already present")

    # Warn if Forge venv was built with wrong Python version
    venv_python = os.path.join(FORGE_DIR, "venv",
                               "Scripts" if os.name == "nt" else "bin",
                               "python.exe" if os.name == "nt" else "python3")
    if os.path.exists(venv_python):
        try:
            r = subprocess.run([venv_python, "--version"], capture_output=True, text=True)
            version = (r.stdout + r.stderr).strip()
            if "3.10" not in version and "3.11" not in version:
                warn(f"Forge venv is {version}, needs 3.10 or 3.11 — deleting venv for rebuild ...")
                shutil.rmtree(os.path.join(FORGE_DIR, "venv"))
                ok("Venv deleted; Forge will rebuild with a compatible Python on first run")
        except Exception as e:
            warn(f"Could not check Forge venv: {e}")

    forge_py = _find_forge_python()
    if not forge_py:
        warn("Python 3.10 or 3.11 not found — Forge requires one of these.")
        if os.name == "nt":
            warn("Install Python 3.11: https://python.org/downloads/release/python-3110/")
        elif platform.system() == "Darwin":
            warn("  brew install python@3.11")
            warn("  # or: pyenv install 3.11  (then add to PATH)")
        else:
            warn("  sudo apt install python3.11  (Debian/Ubuntu)")
            warn("  # or: pyenv install 3.11")
        warn("Image generation will be unavailable until a compatible Python is installed.")
    else:
        ok(f"Forge-compatible Python found: {forge_py}")

    # Download checkpoint if not present
    sd_dir = os.path.join(FORGE_DIR, "models", "Stable-diffusion")
    os.makedirs(sd_dir, exist_ok=True)

    custom_url = cfg.get("sd_checkpoint_url", "")
    if custom_url:
        info("resolving custom checkpoint filename ...")
        filename = cfg.get("sd_checkpoint") or _filename_from_response(custom_url)
        if not filename.endswith(".safetensors"):
            filename += ".safetensors"
        dest = os.path.join(sd_dir, filename)
        if os.path.exists(dest):
            ok(f"Checkpoint already present: {filename}")
        else:
            info(f"downloading {filename} ...")
            _download(custom_url, dest, filename)
            ok(f"checkpoint ready: {filename}")
        cfg["sd_checkpoint"] = filename
    else:
        dest = os.path.join(sd_dir, _RV_FILENAME)
        if os.path.exists(dest):
            ok(f"Checkpoint already present: {_RV_FILENAME}")
        else:
            info(f"downloading PornMaster Pro v9 VAE (~2.1 GB) ...")
            _download(_RV_URL, dest, _RV_FILENAME)
            ok(f"checkpoint ready: {_RV_FILENAME}")
        cfg.setdefault("sd_checkpoint", _RV_FILENAME)

    install_adetailer()
