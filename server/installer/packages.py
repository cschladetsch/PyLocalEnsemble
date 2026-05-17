"""Steps 1 & 2: Python version check and pip package installation."""
import sys, os, subprocess
from installer.helpers import MIN_PYTHON, REQ_FILE, Spinner, heading, ok, die


def check_python():
    heading("1/7", "Python version")
    v = sys.version_info[:2]
    if v < MIN_PYTHON:
        die(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, found {v[0]}.{v[1]}")
    ok(f"Python {v[0]}.{v[1]}")


def install_packages():
    heading("2/7", "Python packages")
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
