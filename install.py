#!/usr/bin/env python3
"""
Alice installer — run once to set up everything needed.
  python install.py
"""
import sys, os, subprocess, json, platform, shutil, threading, itertools, time
import glob, zipfile, urllib.request, urllib.error

MIN_PYTHON   = (3, 10)
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE  = os.path.join(SCRIPT_DIR, "alice.json")
REQ_FILE     = os.path.join(SCRIPT_DIR, "requirements.txt")
LLAMA_DIR    = os.path.join(SCRIPT_DIR, "llama-cpp")
MODELS_DIR   = os.path.join(SCRIPT_DIR, "models")
TTS_DIR      = os.path.join(SCRIPT_DIR, "models", "tts")
FORGE_DIR    = os.path.join(SCRIPT_DIR, "stable-diffusion-webui-forge")
FORGE_BAT    = os.path.join(FORGE_DIR, "webui.bat" if os.name == "nt" else "webui.sh")

# Default NSFW model (uncensored Mistral-Nemo fine-tune, Q4_K_M ~7 GB)
_DEFAULT_MODEL_REPO = "bartowski/dolphin-2.9.4-mistral-nemo-12b-GGUF"
_DEFAULT_MODEL_QUANT = "Q4_K_M"

# TTS models
_TTS_MODEL_URL  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/kokoro-v0_19.onnx"
_TTS_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/voices.bin"

# Stable Diffusion checkpoint — PornMaster Pro v9 VAE (photorealistic, explicit, public HuggingFace)
_RV_FILENAME = "pornmasterPro_v9VAE.safetensors"
_RV_URL = "https://huggingface.co/Demo112211/pornmasterPro_v9VAE.safetensors/resolve/main/pornmasterPro_v9VAE.safetensors"

# ── helpers ───────────────────────────────────────────────────────────────────

class Spinner:
    _CHARS = '|/-\\'
    def __init__(self, msg):
        self.msg = msg
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)
    def __enter__(self):
        self._thread.start(); return self
    def __exit__(self, *_):
        self._stop.set(); self._thread.join()
        print(f"\r{' ' * (len(self.msg) + 8)}\r", end='', flush=True)
    def _spin(self):
        for ch in itertools.cycle(self._CHARS):
            if self._stop.is_set(): break
            print(f"\r      {ch}  {self.msg}", end='', flush=True)
            time.sleep(0.1)


def heading(n, text): print(f"\n[{n}] {text}")
def ok(msg):          print(f"     [ok] {msg}")
def info(msg):        print(f"      ... {msg}")
def warn(msg):        print(f"     [!!] {msg}", file=sys.stderr)
def die(msg):         print(f"\nERROR: {msg}", file=sys.stderr); sys.exit(1)

def run(*args, **kw):    return subprocess.run(list(args), **kw)
def run_ok(*args, **kw): return run(*args, **kw).returncode == 0


def _download(url: str, dest: str, label: str = ""):
    label = label or os.path.basename(dest)
    def hook(count, block, total):
        if total > 0:
            pct = min(count * block / total * 100, 100)
            mb  = min(count * block, total) / 1_048_576
            tot = total / 1_048_576
            print(f"\r      {pct:5.1f}%  {mb:.0f}/{tot:.0f} MB  {label}", end='', flush=True)
    urllib.request.urlretrieve(url, dest, reporthook=hook)
    print()


def _json_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "alice-installer/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _filename_from_response(url: str) -> str:
    """HEAD the URL and extract filename from Content-Disposition, or fall back to URL basename."""
    try:
        r = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "alice-installer/1.0"})
        with urllib.request.urlopen(r, timeout=10) as resp:
            cd = resp.headers.get("Content-Disposition", "")
            for part in cd.split(";"):
                part = part.strip()
                if part.startswith("filename="):
                    return part[9:].strip('"\'')
    except Exception:
        pass
    # fall back to last path segment before query string
    return url.split("?")[0].rstrip("/").split("/")[-1] or "model.safetensors"

# ── steps ─────────────────────────────────────────────────────────────────────

def check_python():
    heading("1/6", "Python version")
    v = sys.version_info[:2]
    if v < MIN_PYTHON:
        die(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, found {v[0]}.{v[1]}")
    ok(f"Python {v[0]}.{v[1]}")


def install_packages():
    heading("2/6", "Python packages")
    if os.path.exists(REQ_FILE):
        with Spinner("pip install -r requirements.txt"):
            subprocess.check_call([sys.executable, "-m", "pip", "install",
                                   "--quiet", "-r", REQ_FILE])
    else:
        pkgs = ["fastapi", "uvicorn[standard]", "pydantic", "requests",
                "kokoro-onnx", "faster-whisper", "av"]
        with Spinner("pip install " + " ".join(pkgs)):
            subprocess.check_call([sys.executable, "-m", "pip", "install",
                                   "--quiet", *pkgs])
    ok("all packages installed")


def _llama_server_exe():
    """Return path to llama-server if found (local install dir or PATH)."""
    local = os.path.join(LLAMA_DIR, "llama-server.exe" if os.name == "nt" else "llama-server")
    if os.path.exists(local):
        return local
    return shutil.which("llama-server") or shutil.which("llama-server.exe")


def _pick_llama_asset(assets: list) -> dict | None:
    plat = platform.system()
    if plat == "Windows":
        # Vulkan works on NVIDIA, AMD, and Intel — prefer it for universal compat
        prefs = ["vulkan", "avx2", "cpu"]
        for pref in prefs:
            for a in assets:
                n = a["name"].lower()
                if "win" in n and pref in n and "x64" in n and n.endswith(".zip"):
                    return a
    elif plat == "Darwin":
        for a in assets:
            n = a["name"].lower()
            arch = "arm64" if platform.machine() == "arm64" else "x64"
            if "macos" in n and arch in n and n.endswith(".zip"):
                return a
    else:
        for a in assets:
            n = a["name"].lower()
            if "ubuntu" in n and "x64" in n and n.endswith(".zip"):
                return a
    return None


def install_llama_server(cfg: dict):
    heading("3/6", "llama.cpp server")

    exe = _llama_server_exe()
    if exe:
        ok(f"llama-server already present: {exe}")
        cfg.setdefault("llama_server_path", exe)
        return

    info("fetching latest llama.cpp release from GitHub ...")
    try:
        release = _json_get("https://api.github.com/repos/ggerganov/llama.cpp/releases/latest")
    except Exception as e:
        warn(f"Could not reach GitHub: {e}")
        warn("Install llama-server manually: https://github.com/ggerganov/llama.cpp/releases/latest")
        return

    tag = release.get("tag_name", "?")
    assets = release.get("assets", [])
    info(f"release: {tag}")

    asset = _pick_llama_asset(assets)
    if not asset:
        warn("Could not find a suitable asset — install llama-server manually.")
        warn("https://github.com/ggerganov/llama.cpp/releases/latest")
        return

    os.makedirs(LLAMA_DIR, exist_ok=True)
    zip_path = os.path.join(LLAMA_DIR, asset["name"])
    info(f"downloading {asset['name']} ({asset['size'] // 1_048_576} MB) ...")
    _download(asset["browser_download_url"], zip_path, asset["name"])

    info("extracting ...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(LLAMA_DIR)
    os.remove(zip_path)

    exe = _llama_server_exe()
    if not exe:
        # Some releases nest files in a subdirectory — search recursively
        pattern = os.path.join(LLAMA_DIR, "**", "llama-server*")
        hits = glob.glob(pattern, recursive=True)
        exe = hits[0] if hits else None

    if exe:
        if os.name != "nt":
            os.chmod(exe, 0o755)
        cfg["llama_server_path"] = exe
        ok(f"llama-server installed: {exe}")
    else:
        warn("Extraction complete but llama-server not found — check " + LLAMA_DIR)


def _hf_gguf_url(repo_id: str, quant: str) -> tuple:
    """Return (filename, download_url) for a GGUF in a HuggingFace repo."""
    try:
        data = _json_get(f"https://huggingface.co/api/models/{repo_id}")
    except Exception as e:
        raise RuntimeError(f"HuggingFace API error: {e}")
    files = [s["rfilename"] for s in data.get("siblings", [])
             if s["rfilename"].endswith(".gguf")]
    # prefer exact quant match, then any gguf
    match = next((f for f in files if quant in f), None) or (files[0] if files else None)
    if not match:
        raise RuntimeError(f"No GGUF files found in {repo_id}")
    return match, f"https://huggingface.co/{repo_id}/resolve/main/{match}"


_SKIP_KEYWORDS = [
    "coder", "code", "math", "embed", "rerank",
    "starcoder", "tabby", "sql", "translator",
]

def _is_suitable(path: str) -> bool:
    name = os.path.basename(path).lower()
    return not any(kw in name for kw in _SKIP_KEYWORDS)


def _find_existing_ggufs() -> list:
    """Scan common locations for existing .gguf files."""
    home = os.path.expanduser("~")
    roots = [
        MODELS_DIR,
        os.path.join(home, ".cache", "lm-studio", "models"),
        os.path.join(home, "AppData", "Local", "nomic.ai", "GPT4All"),
        os.path.join(home, "models"),
    ]
    found = []
    seen = set()

    # Standard .gguf files
    for root in roots:
        if os.path.isdir(root):
            for path in glob.glob(os.path.join(root, "**", "*.gguf"), recursive=True):
                p = os.path.normpath(path)
                if p not in seen and _is_suitable(p):
                    seen.add(p)
                    found.append(p)

    return found


def setup_model(cfg: dict):
    heading("4/6", "Model")

    model_path = cfg.get("model_path", "")
    if model_path and os.path.exists(model_path):
        ok(f"model already set: {os.path.basename(model_path)}")
        return

    # Check if a GGUF already exists locally
    existing = _find_existing_ggufs()
    if existing:
        info(f"found {len(existing)} existing model(s) on disk:")
        for i, p in enumerate(existing, 1):
            print(f"    [{i}] {p}")
        print(f"    [0] Download the default NSFW model instead")
        print()
        while True:
            raw = input(f"  Select [1-{len(existing)}, or 0 to download]: ").strip()
            if raw == "0":
                break
            if raw.isdigit() and 1 <= int(raw) <= len(existing):
                cfg["model_path"] = existing[int(raw) - 1]
                ok(f"model set: {os.path.basename(cfg['model_path'])}")
                return
            print("  Invalid selection.")

    # Download default model
    info(f"resolving {_DEFAULT_MODEL_REPO} ({_DEFAULT_MODEL_QUANT}) ...")
    try:
        filename, url = _hf_gguf_url(_DEFAULT_MODEL_REPO, _DEFAULT_MODEL_QUANT)
    except Exception as e:
        warn(f"Could not resolve model: {e}")
        warn(f"Set model_path in alice.json manually.")
        return

    os.makedirs(MODELS_DIR, exist_ok=True)
    dest = os.path.join(MODELS_DIR, filename)
    if os.path.exists(dest):
        ok(f"model already downloaded: {filename}")
        cfg["model_path"] = dest
        return

    # Get file size for display
    try:
        req2 = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req2, timeout=10) as r:
            size_mb = int(r.headers.get("Content-Length", 0)) // 1_048_576
        info(f"downloading {filename} ({size_mb} MB) ...")
    except Exception:
        info(f"downloading {filename} ...")

    _download(url, dest, filename)
    cfg["model_path"] = dest
    ok(f"model downloaded: {filename}")


def install_tts_models():
    heading("5/6", "TTS models (Kokoro)")
    os.makedirs(TTS_DIR, exist_ok=True)
    model_path  = os.path.join(TTS_DIR, "kokoro-v0_19.onnx")
    voices_path = os.path.join(TTS_DIR, "voices.bin")
    # Remove stale voices.json if present
    stale = os.path.join(TTS_DIR, "voices.json")
    if os.path.exists(stale):
        os.remove(stale)
    if not os.path.exists(model_path):
        info("downloading Kokoro ONNX model (~80 MB) ...")
        _download(_TTS_MODEL_URL, model_path, "kokoro-v0_19.onnx")
    else:
        ok("Kokoro model already present")
    if not os.path.exists(voices_path):
        info("downloading Kokoro voices ...")
        _download(_TTS_VOICES_URL, voices_path, "voices.bin")
    else:
        ok("Kokoro voices already present")


def _find_forge_python() -> str:
    """Return path to a Forge-compatible Python (3.10 or 3.11), or empty string."""
    if os.name != "nt":
        # Prefer 3.10, fall back to 3.11 (Forge supports both; 3.12+ has issues)
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
    # Windows: try the launcher, then well-known install paths
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


def install_forge():
    heading("6/6", "Stable Diffusion Forge")

    # Clone if not present
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
            if "3.10" not in version:
                warn(f"Forge venv is {version}, needs 3.10 — deleting venv for rebuild ...")
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


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  Alice - Installer")
    print("=" * 50)

    check_python()
    install_packages()

    # Load or create config so steps 3 & 4 can write to it
    if not os.path.exists(CONFIG_FILE):
        example = os.path.join(SCRIPT_DIR, "alice.example.json")
        if os.path.exists(example):
            shutil.copy(example, CONFIG_FILE)
            info("created alice.json from alice.example.json")

    personas_file = os.path.join(SCRIPT_DIR, "personas.json")
    if not os.path.exists(personas_file):
        personas_example = os.path.join(SCRIPT_DIR, "personas.example.json")
        if os.path.exists(personas_example):
            shutil.copy(personas_example, personas_file)
            info("created personas.json from personas.example.json")

    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)

    install_llama_server(cfg)
    setup_model(cfg)

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)
    ok("alice.json saved")

    install_tts_models()
    install_forge()

    print("\n" + "=" * 50)
    print("  Installation complete!")
    print("=" * 50)
    print()
    print("  Start Alice:")
    print("    python alice.py")
    print()
    print("  Then open:  http://localhost:8000")
    print()


if __name__ == "__main__":
    main()
