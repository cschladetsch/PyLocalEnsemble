"""Step 3: llama.cpp server download and installation."""
import os, platform, glob, zipfile, shutil
from installer.helpers import LLAMA_DIR, heading, ok, info, warn, _download, _json_get


def _llama_server_exe() -> str:
    """Return path to llama-server if found (local install dir or PATH)."""
    local = os.path.join(LLAMA_DIR, "llama-server.exe" if os.name == "nt" else "llama-server")
    if os.path.exists(local):
        return local
    return shutil.which("llama-server") or shutil.which("llama-server.exe")


def _pick_llama_asset(assets: list) -> dict | None:
    plat = platform.system()
    if plat == "Windows":
        # Vulkan works on NVIDIA, AMD, and Intel — prefer it for universal compat
        for pref in ("vulkan", "avx2", "cpu"):
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

    tag    = release.get("tag_name", "?")
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
        hits = glob.glob(os.path.join(LLAMA_DIR, "**", "llama-server*"), recursive=True)
        exe  = hits[0] if hits else None

    if exe:
        if os.name != "nt":
            os.chmod(exe, 0o755)
        cfg["llama_server_path"] = exe
        ok(f"llama-server installed: {exe}")
    else:
        warn("Extraction complete but llama-server not found — check " + LLAMA_DIR)
