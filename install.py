#!/usr/bin/env python3
"""
Alice installer — run once to set up everything needed.
  python install.py
"""
import sys, os, subprocess, json, platform, shutil

MIN_PYTHON  = (3, 10)
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "alice.json")
REQ_FILE    = os.path.join(SCRIPT_DIR, "requirements.txt")

# ── helpers ───────────────────────────────────────────────────────────────────

def heading(n, text):
    print(f"\n[{n}] {text}")

def ok(msg):
    print(f"     [ok] {msg}")

def info(msg):
    print(f"      ... {msg}")

def warn(msg):
    print(f"     [!!] {msg}", file=sys.stderr)

def die(msg):
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)

def run(*args, **kw):
    return subprocess.run(list(args), **kw)

def run_ok(*args, **kw):
    r = run(*args, **kw)
    return r.returncode == 0

# ── steps ─────────────────────────────────────────────────────────────────────

def check_python():
    heading("1/4", "Python version")
    v = sys.version_info[:2]
    if v < MIN_PYTHON:
        die(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, found {v[0]}.{v[1]}")
    ok(f"Python {v[0]}.{v[1]}")


def install_packages():
    heading("2/4", "Python packages")
    if os.path.exists(REQ_FILE):
        info(f"installing from {REQ_FILE}")
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "--quiet", "-r", REQ_FILE])
    else:
        pkgs = ["fastapi", "uvicorn[standard]", "pydantic", "requests",
                "kokoro-onnx", "faster-whisper", "av"]
        info("installing: " + ", ".join(pkgs))
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "--quiet", *pkgs])
    ok("all packages installed")


def _ollama_found():
    if not shutil.which("ollama"):
        return False
    r = run("ollama", "--version", capture_output=True, text=True)
    return r.returncode == 0


def install_ollama():
    heading("3/4", "Ollama")
    if _ollama_found():
        r = run("ollama", "--version", capture_output=True, text=True)
        ok(r.stdout.strip() or "ollama found")
        return

    plat = platform.system()
    info(f"Ollama not found — installing for {plat}...")

    if plat == "Windows":
        # Try winget first (available on Windows 11 / updated Windows 10)
        if shutil.which("winget"):
            info("using winget …")
            ok_flag = run_ok("winget", "install", "-e", "--id", "Ollama.Ollama",
                             "--accept-package-agreements", "--accept-source-agreements")
            if ok_flag:
                ok("Ollama installed via winget")
                return
        warn("winget failed or unavailable.")
        print("\n  Please install Ollama manually:")
        print("    https://ollama.com/download/windows")
        input("\n  Press Enter once Ollama is installed to continue ...")
        if not _ollama_found():
            die("Ollama still not found. Restart this installer after installing.")

    elif plat == "Darwin":
        if shutil.which("brew"):
            info("using brew …")
            subprocess.check_call(["brew", "install", "ollama"])
            ok("Ollama installed via brew")
        else:
            print("\n  Please install Ollama manually:")
            print("    https://ollama.com/download/mac")
            input("\n  Press Enter once Ollama is installed to continue ...")
            if not _ollama_found():
                die("Ollama still not found.")

    else:  # Linux
        info("using official install script (requires curl + sudo) …")
        subprocess.check_call("curl -fsSL https://ollama.com/install.sh | sh", shell=True)
        if not _ollama_found():
            die("Ollama install script ran but 'ollama' still not in PATH. "
                "Try opening a new terminal.")

    ok("Ollama ready")


def setup_config():
    heading("4/4", "Config & model")

    # Read existing config or use defaults
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        ok(f"alice.json already exists")
    else:
        cfg = {}
        info("creating alice.json with defaults …")
        defaults = {
            "name": "Alice",
            "ollama_model": "mistral-nemo",
            "forge_url": "http://localhost:7860",
            "appearance": "woman, long blonde hair, blue eyes, elegant, poised, expressive eyes, soft lighting",
            "negative_prompt": "ugly, deformed, extra limbs, blurry, watermark, bad anatomy, low quality",
            "system_prompt": (
                "You are Alice. Respond always in first person as Alice — never narrate in third person.\n"
                "You are enigmatic, sensual, intelligent, and fully present.\n"
                "You speak in measured, literary prose. You never break character."
            ),
            "tts": {"voice": "af_nicole", "speed": 1.30},
            "image": {
                "steps": 25, "width": 512, "height": 768, "cfg_scale": 7,
                "sampler_name": "DPM++ 2M Karras",
                "suffix": "photorealistic, highly detailed, 8k, masterpiece",
                "auto_every": 1
            }
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(defaults, f, indent=4)
        cfg = defaults
        ok("alice.json created")

    # Pull the model via Ollama
    model = cfg.get("ollama_model", "mistral-nemo")
    info(f"pulling model: {model}  (may take a few minutes on first run) ...")
    r = run("ollama", "pull", model)
    if r.returncode == 0:
        ok(f"model '{model}' ready")
    else:
        warn(f"'ollama pull {model}' failed. Run it manually when ready.")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  Alice - Installer")
    print("=" * 50)

    check_python()
    install_packages()
    install_ollama()
    setup_config()

    print("\n" + "=" * 50)
    print("  Installation complete!")
    print("=" * 50)
    print()
    print("  Start Alice:")
    print("    python alice.py")
    print()
    print("  Then open:  http://localhost:8000")
    print()
    print("  (Optional) For image generation, also start")
    print("  Stable Diffusion Forge on port 7860.")
    print()


if __name__ == "__main__":
    main()
