"""Shared constants, I/O helpers, and download utilities for the installer."""
import sys, os, subprocess, json, threading, itertools, time
import urllib.request, urllib.error

# Resolve paths relative to the project root (one level up from this file)
_INSTALLER_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR     = os.path.dirname(_INSTALLER_DIR)

CONFIG_FILE  = os.path.join(SCRIPT_DIR, "alice.json")
REQ_FILE     = os.path.join(SCRIPT_DIR, "requirements.txt")
LLAMA_DIR    = os.path.join(SCRIPT_DIR, "llama-cpp")
MODELS_DIR   = os.path.join(SCRIPT_DIR, "models")
TTS_DIR      = os.path.join(SCRIPT_DIR, "models", "tts")
FORGE_DIR    = os.path.join(SCRIPT_DIR, "stable-diffusion-webui-forge")
FORGE_BAT    = os.path.join(FORGE_DIR, "webui.bat" if os.name == "nt" else "webui.sh")
CONF_DIR     = os.path.join(SCRIPT_DIR, "conf")

MIN_PYTHON = (3, 10)


# ── console helpers ───────────────────────────────────────────────────────────

def heading(n, text): print(f"\n[{n}] {text}")
def ok(msg):          print(f"     [ok] {msg}")
def info(msg):        print(f"      ... {msg}")
def warn(msg):        print(f"     [!!] {msg}", file=sys.stderr)
def die(msg):         print(f"\nERROR: {msg}", file=sys.stderr); sys.exit(1)

def run(*args, **kw):     return subprocess.run(list(args), **kw)
def run_ok(*args, **kw):  return run(*args, **kw).returncode == 0


# ── spinner ───────────────────────────────────────────────────────────────────

class Spinner:
    _CHARS = '|/-\\'

    def __init__(self, msg):
        self.msg = msg
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()
        print(f"\r{' ' * (len(self.msg) + 8)}\r", end='', flush=True)

    def _spin(self):
        for ch in itertools.cycle(self._CHARS):
            if self._stop.is_set():
                break
            print(f"\r      {ch}  {self.msg}", end='', flush=True)
            time.sleep(0.1)


# ── download / HTTP ───────────────────────────────────────────────────────────

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
    return url.split("?")[0].rstrip("/").split("/")[-1] or "model.safetensors"
