#!/usr/bin/env python3
"""
install.py - Alice installer.
Run: python install.py
Checks prerequisites, copies alice.py to <drive>:\alice\, then you just run:
    python <drive>:\alice\alice.py
"""
import os, sys, shutil, subprocess, urllib.request, tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DRIVE      = os.path.splitdrive(SCRIPT_DIR)[0] or "C:"
ALICE_DIR  = os.path.join(DRIVE + os.sep, "alice")
ALICE_SRC  = os.path.join(SCRIPT_DIR, "alice.py")
ALICE_DEST = os.path.join(ALICE_DIR, "alice.py")

def banner(msg, char="="):
    line = char * 60
    print(f"\n{line}\n  {msg}\n{line}")

def step(n, msg):
    print(f"\n[{n}] {msg}")

def ok(msg):   print(f"     ok: {msg}")
def warn(msg): print(f"     WARNING: {msg}")
def fail(msg): print(f"\n     ERROR: {msg}\n"); sys.exit(1)

# ── 1. Python version ────────────────────────────────────────────────────────
def check_python():
    step("1/4", "Python")
    v = sys.version_info
    if v < (3, 10):
        fail(f"Python 3.10+ required. You have {v.major}.{v.minor}.\n"
             "     Install from https://python.org (tick 'Add Python to PATH').")
    ok(f"Python {v.major}.{v.minor}.{v.micro}")

# ── 2. Git ───────────────────────────────────────────────────────────────────
def check_git():
    step("2/4", "Git")
    try:
        r = subprocess.run(["git", "--version"], capture_output=True, text=True)
        ok(r.stdout.strip())
    except FileNotFoundError:
        fail("Git not found.\n"
             "     Install from https://git-scm.com and re-run install.py.")

# ── 3. Create alice dir and copy alice.py ────────────────────────────────────
def setup_alice():
    step("3/4", f"Setting up {ALICE_DIR}")

    os.makedirs(ALICE_DIR, exist_ok=True)
    ok(f"Directory: {ALICE_DIR}")

    if not os.path.exists(ALICE_SRC):
        fail(f"alice.py not found alongside install.py.\n"
             f"     Expected: {ALICE_SRC}")

    if os.path.exists(ALICE_DEST):
        ok(f"alice.py already present.")
    else:
        shutil.copy2(ALICE_SRC, ALICE_DEST)
        ok(f"Copied alice.py to {ALICE_DEST}")

# ── 4. Install pip deps ──────────────────────────────────────────────────────
def install_deps():
    step("4/4", "Python dependencies")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet",
         "fastapi", "uvicorn", "requests", "pydantic"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        warn("pip install failed. alice.py will retry on first run.")
    else:
        ok("fastapi, uvicorn, requests, pydantic installed.")

# ── Done ─────────────────────────────────────────────────────────────────────
def done():
    forge_models = os.path.join(
        ALICE_DIR, "stable-diffusion-webui-forge", "models", "Stable-diffusion"
    )
    banner("Install complete", "=")
    print(f"""
  Next steps:

  1. Run Alice (it will install remaining components automatically):

       python "{ALICE_DEST}"

  2. On first run Alice will:
       - Download and install Ollama
       - Pull mistral-nemo (~7GB)
       - Clone Stable Diffusion Forge
       - Start all services
       - Open http://localhost:8000

  3. Image generation requires a .safetensors checkpoint.
     If Alice pauses asking for one, download dreamshaper_8 from:
       https://civitai.com/models/4384
     and place it in:
       {forge_models}\\
     Then re-run alice.py.
""")
    input("  Press Enter to exit...")

# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    banner("Alice Installer")
    print(f"  Install root : {ALICE_DIR}")
    print(f"  Source       : {SCRIPT_DIR}")

    check_python()
    check_git()
    setup_alice()
    install_deps()
    done()