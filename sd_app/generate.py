"""Image generation via the Forge/SD API — standalone, no chat/LLM coupling.

Ported from server/image/generate.py. Simplified relative to the original:
  - no `appearance` parameter (it was dead code there — never used in the body)
  - no VRAM arbitration with an LLM (this runs as its own process; nothing
    else is competing for GPU memory)
  - no ADetailer-not-installed auto-install-retry (Forge installation is
    Alice's installer's job, out of scope here) — surfaces a clear error instead
"""
from __future__ import annotations
import base64, itertools, json, os, threading, time
import requests as req
import config
from utils import http_ok, _c
from forge import start_forge

_gen_cancel     = threading.Event()
_upscaler_cache: str | None = None   # cached after first successful query; "" = none available
_save_image_counter = itertools.count()

last_seed = -1


def _resolve_upscaler(forge_url: str, preferred: str) -> str | None:
    """Return a valid upscaler name, or None if hires fix should be skipped."""
    global _upscaler_cache
    if _upscaler_cache is not None:
        return _upscaler_cache or None
    try:
        r     = req.get(f"{forge_url}/sdapi/v1/upscalers", timeout=5)
        names = [u.get("name") for u in r.json() if isinstance(u.get("name"), str) and u.get("name")]
        if preferred in names:
            _upscaler_cache = preferred
            return preferred
        latent = next((n for n in names if n.lower().startswith("latent")), None)
        if latent:
            print(f"[sd_app] upscaler '{preferred}' not found, using '{latent}'")
            _upscaler_cache = latent
            return latent
    except Exception as e:
        print(f"[sd_app] could not query upscalers: {e}")
    print("[sd_app] no valid upscaler found — hires fix disabled")
    _upscaler_cache = ""
    return None


def image_output_dir() -> str:
    out_dir = os.path.join(config.STATIC_DIR, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def save_generated_image(b64_data: str) -> str:
    """Write the image to disk and return a URL relative to sd_app's own root."""
    out_dir = image_output_dir()
    # time_ns() alone can collide under Windows' coarse clock resolution when
    # called in a tight loop — the counter guarantees uniqueness regardless.
    fname = f"img_{time.time_ns()}_{next(_save_image_counter)}.png"
    with open(os.path.join(out_dir, fname), "wb") as f:
        f.write(base64.b64decode(b64_data))
    return f"static/outputs/{fname}"


def generate_image(prompt: str, negative_base: str, extra_negative: str = "",
                    steps: int = None, cfg_scale: float = None,
                    seed: int = -1, width: int = None, height: int = None):
    global last_seed
    forge_url = config.CFG["forge_url"]
    img_cfg   = config.CFG["image"]
    if not http_ok(f"{forge_url}/sdapi/v1/sd-models"):
        print("[sd_app] Forge down, restarting...")
        start_forge()
        if not http_ok(f"{forge_url}/sdapi/v1/sd-models"):
            raise RuntimeError(
                f"Forge is unavailable at {forge_url}. Start Stable Diffusion Forge or update forge_url in sd_app.json."
            )

    negative = (extra_negative + ", " + negative_base) if extra_negative else negative_base

    _steps   = steps if steps is not None else img_cfg["steps"]
    _cfg     = cfg_scale if cfg_scale is not None else img_cfg["cfg_scale"]
    _width   = width if width is not None else img_cfg["width"]
    _height  = height if height is not None else img_cfg["height"]

    full_prompt = prompt + ", " + img_cfg["suffix"]
    print(f"\n[sd_app] prompt ({len(full_prompt)} chars): {full_prompt!r}")
    print(f"[sd_app] steps={_steps}, sampler={img_cfg['sampler_name']}, cfg={_cfg}, "
          f"size={_width}x{_height}, seed={seed}")

    payload = {
        "prompt":          full_prompt,
        "negative_prompt": negative,
        "steps":           _steps,
        "width":           _width,
        "height":          _height,
        "cfg_scale":       _cfg,
        "sampler_name":    img_cfg["sampler_name"],
        "seed":            seed,
    }
    if img_cfg.get("hires_fix"):
        hr_upscaler = _resolve_upscaler(forge_url, img_cfg.get("hires_upscaler", "Latent"))
        if hr_upscaler:
            payload.update({
                "enable_hr":            True,
                "hr_scale":             img_cfg.get("hires_scale",    1.5),
                "hr_second_pass_steps": img_cfg.get("hires_steps",    15),
                "denoising_strength":   img_cfg.get("hires_denoising", 0.45),
                "hr_upscaler":          hr_upscaler,
            })
    payload["override_settings"] = {
        # sd_app manages its own output — Forge doesn't need to write images to disk.
        "samples_save": False,
        "grid_save":    False,
    }

    _done = threading.Event()
    def _log_progress():
        while not _done.wait(timeout=0.5):
            try:
                pr  = req.get(f"{forge_url}/sdapi/v1/progress?skip_current_image=true", timeout=2).json()
                pct = round((pr.get("progress", 0) or 0) * 100)
                if pct > 0:
                    print(f"{_c('blue', '[forge]')}   {pct}%")
            except Exception:
                pass

    _poll_thread = threading.Thread(target=_log_progress, daemon=True)
    _poll_thread.start()
    try:
        r = req.post(f"{forge_url}/sdapi/v1/txt2img", json=payload, timeout=300)
    finally:
        _done.set()
        _poll_thread.join(timeout=0.6)

    data = r.json()
    if "images" not in data:
        err_type  = data.get("error", "")
        err_msg   = data.get("errors") or data.get("message") or ""
        forge_err = f"{err_type}: {err_msg}".strip(": ") or str(data)[:300]
        print(f"[sd_app] Forge error response: {forge_err}")
        raise RuntimeError(f"Forge: {forge_err}")

    imgs = data.get("images", [])
    if not imgs:
        print("[sd_app] Forge returned empty images list")
        raise RuntimeError("Forge returned no images")
    try:
        info = json.loads(data.get("info", "{}"))
        last_seed = info.get("seed", -1)
    except Exception:
        last_seed = -1
    print(f"[sd_app] done — got image ({len(imgs[0])} b64 chars), seed={last_seed}")
    return imgs[0]
