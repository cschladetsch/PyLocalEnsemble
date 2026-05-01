"""GPU VRAM arbitrator — serialises access between llama-server and Forge.

Ownership model:
  - At startup (after warmup): Forge checkpoint is evicted from VRAM so the
    LLM can load into full VRAM.
  - Before image gen: LLM is killed, checkpoint is reloaded into VRAM.
  - After image gen: checkpoint is evicted again, LLM restarts in background.

TTS (Kokoro) and STT (Whisper) both run on CPU and are excluded from this.
"""
import time
import requests as req
from utils import step, ok, warn

_forge_url    = ""
_forge_loaded = True   # assume loaded at Forge startup; unload_forge() sets False


def setup(forge_url: str) -> None:
    global _forge_url
    _forge_url = forge_url


def unload_forge() -> bool:
    """Evict the SD checkpoint from VRAM (model stays in CPU RAM for fast reload)."""
    global _forge_loaded
    if not _forge_url or not _forge_loaded:
        return True
    try:
        r = req.post(f"{_forge_url}/sdapi/v1/unload-checkpoint", timeout=15)
        if r.status_code == 200:
            _forge_loaded = False
            print("[vram] Forge checkpoint evicted from VRAM.")
            return True
        if r.status_code == 404:
            warn("[vram] unload-checkpoint not supported by this Forge build — skipping.")
        else:
            warn(f"[vram] unload-checkpoint returned HTTP {r.status_code}.")
        return False
    except Exception as e:
        warn(f"[vram] unload-checkpoint failed: {e}")
        return False


def reload_forge() -> bool:
    """Move the SD checkpoint from CPU RAM back into VRAM."""
    global _forge_loaded
    if not _forge_url or _forge_loaded:
        return True
    try:
        step("[vram] Reloading Forge checkpoint into VRAM...")
        r = req.post(f"{_forge_url}/sdapi/v1/reload-checkpoint", timeout=60)
        if r.status_code == 200:
            _forge_loaded = True
            ok("[vram] Forge model loaded into VRAM.")
            # Re-push settings: reload-checkpoint resets Forge's runtime config
            # (including vae_in_cpu) back to whatever its saved config.json says.
            from image.forge import _push_forge_settings
            _push_forge_settings(_forge_url)
            return True
        if r.status_code == 404:
            warn("[vram] reload-checkpoint not supported by this Forge build — generation may use less VRAM.")
        else:
            warn(f"[vram] reload-checkpoint returned HTTP {r.status_code}.")
        return False
    except Exception as e:
        warn(f"[vram] reload-checkpoint failed: {e}")
        return False


def acquire_for_image() -> None:
    """Hand the GPU to Forge: kill LLM, wait for VRAM to clear, load SD model."""
    import llm as _llm
    _llm.suspend_for_image()
    time.sleep(2.0)   # give the CUDA driver time to reclaim VRAM after the kill
    reload_forge()


def release_from_image() -> None:
    """Return the GPU to LLM: evict SD model, restart llama-server in background."""
    import llm as _llm
    unload_forge()
    if _llm.LLM_SUSPENDED:
        _llm.resume_after_image()
