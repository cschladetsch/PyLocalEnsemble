"""Step 4: LLM model selection and download."""
import glob, os, sys, urllib.request
from installer.helpers import MODELS_DIR, heading, ok, info, warn, _download, _json_get

_DEFAULT_MODEL_REPO  = "bartowski/dolphin-2.9.4-mistral-nemo-12b-GGUF"
_DEFAULT_MODEL_QUANT = "Q4_K_M"

_SKIP_KEYWORDS = [
    "coder", "code", "math", "embed", "rerank",
    "starcoder", "tabby", "sql", "translator",
]


def _is_suitable(path: str) -> bool:
    return not any(kw in os.path.basename(path).lower() for kw in _SKIP_KEYWORDS)


def _find_existing_ggufs() -> list:
    """Scan common locations for existing .gguf files."""
    home  = os.path.expanduser("~")
    roots = [
        MODELS_DIR,
        os.path.join(home, ".cache", "lm-studio", "models"),
        os.path.join(home, "AppData", "Local", "nomic.ai", "GPT4All"),
        os.path.join(home, "models"),
    ]
    found, seen = [], set()
    for root in roots:
        if os.path.isdir(root):
            for path in glob.glob(os.path.join(root, "**", "*.gguf"), recursive=True):
                p = os.path.normpath(path)
                if p not in seen and _is_suitable(p):
                    seen.add(p)
                    found.append(p)
    return found


def _hf_gguf_url(repo_id: str, quant: str) -> tuple:
    """Return (filename, download_url) for a GGUF in a HuggingFace repo."""
    try:
        data = _json_get(f"https://huggingface.co/api/models/{repo_id}")
    except Exception as e:
        raise RuntimeError(f"HuggingFace API error: {e}")
    files = [s["rfilename"] for s in data.get("siblings", [])
             if s["rfilename"].endswith(".gguf")]
    match = next((f for f in files if quant in f), None) or (files[0] if files else None)
    if not match:
        raise RuntimeError(f"No GGUF files found in {repo_id}")
    return match, f"https://huggingface.co/{repo_id}/resolve/main/{match}"


INTERACTIVE = sys.stdin.isatty()


def setup_model(cfg: dict):
    heading("4/6", "Model")

    model_path = cfg.get("model_path", "")
    if model_path and os.path.exists(model_path):
        ok(f"model already set: {os.path.basename(model_path)}")
        return

    existing = _find_existing_ggufs()
    if existing:
        info(f"found {len(existing)} existing model(s) on disk:")
        for i, p in enumerate(existing, 1):
            print(f"    [{i}] {p}")
        print(f"    [0] Download the default NSFW model instead")
        print()
        cfg["model_path"] = existing[0]
        ok(f"model set: {os.path.basename(cfg['model_path'])}")
        return

    info(f"resolving {_DEFAULT_MODEL_REPO} ({_DEFAULT_MODEL_QUANT}) ...")
    try:
        filename, url = _hf_gguf_url(_DEFAULT_MODEL_REPO, _DEFAULT_MODEL_QUANT)
    except Exception as e:
        warn(f"Could not resolve model: {e}")
        warn("Set model_path in alice.json manually.")
        return

    os.makedirs(MODELS_DIR, exist_ok=True)
    dest = os.path.join(MODELS_DIR, filename)
    if os.path.exists(dest):
        ok(f"model already downloaded: {filename}")
        cfg["model_path"] = dest
        return

    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as r:
            size_mb = int(r.headers.get("Content-Length", 0)) // 1_048_576
        info(f"downloading {filename} ({size_mb} MB) ...")
    except Exception:
        info(f"downloading {filename} ...")

    _download(url, dest, filename)
    cfg["model_path"] = dest
    ok(f"model downloaded: {filename}")
