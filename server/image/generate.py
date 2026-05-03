"""Image generation via the Forge/SD API."""
from __future__ import annotations
import json, os, re, threading, time
import logging
import requests as req
import config
import state
import vram as _vram
from utils import http_ok, _c
from image.forge import start_forge

_gen_cancel    = threading.Event()
_upscaler_cache: str | None = None   # cached after first successful query; "" = none available
_log = logging.getLogger("alice.image")


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
            print(f"[image] upscaler '{preferred}' not found, using '{latent}'")
            _upscaler_cache = latent
            return latent
    except Exception as e:
        print(f"[image] could not query upscalers: {e}")
    print("[image] no valid upscaler found — hires fix disabled")
    _upscaler_cache = ""
    return None


def generate_image(prompt: str, appearance: str, negative_base: str,
                   extra_negative: str = "", steps: int = None, cfg_scale: float = None,
                   seed: int = -1, quick: bool = False):
    forge_url = config.CFG["forge_url"]
    img_cfg   = config.CFG["image"]
    if not http_ok(f"{forge_url}/sdapi/v1/sd-models"):
        print("Forge down, restarting...")
        start_forge()
        if not http_ok(f"{forge_url}/sdapi/v1/sd-models"):
            msg = f"Forge is unavailable at {forge_url}. Start Stable Diffusion Forge or update forge_url in alice.json."
            _log.error(msg)
            raise RuntimeError(msg)

    negative = (extra_negative + ", " + negative_base) if extra_negative else negative_base
    # Suppress checkpoint-specific makeup artefacts (many NSFW models default to heavy blue eye shadow)
    if "blue eyeshadow" not in negative.lower():
        negative = "blue eyeshadow, heavy eye makeup, colorful eyeshadow, " + negative

    quick_steps   = img_cfg.get("quick_steps",   max(img_cfg["steps"] // 2, 12))
    quick_sampler = img_cfg.get("quick_sampler", "DPM++ 2M Karras")
    _steps   = (steps if steps is not None else (quick_steps   if quick else img_cfg["steps"]))
    _sampler = quick_sampler                                    if quick else img_cfg["sampler_name"]
    _cfg     = cfg_scale if cfg_scale is not None else img_cfg["cfg_scale"]
    _width   = img_cfg["width"]
    _height  = img_cfg["height"]

    explicit_keywords = ["anal", "fingering", "insertion", "blowjob", "fellatio",
                         "penetration", "nude", "naked", "nudity", "topless", "nsfw",
                         "no clothes", "fully nude", "fully naked", "bare skin", "exposed skin",
                         "take off clothes", "removing clothes", "disrobing", "pussy", "vagina",
                         "vulva", "cunt", "asshole", "clitoris", "breasts bare", "clothed removed"]
    nudity_keywords   = ["nude", "naked", "nudity", "topless", "no clothes", "no clothing",
                         "fully nude", "fully naked", "bare skin", "exposed skin",
                         "take off clothes", "removing clothes", "disrobing", "undress",
                         "pussy", "vagina", "vulva", "cunt", "asshole", "breasts bare"]

    is_explicit = any(kw in prompt.lower() for kw in explicit_keywords)
    nudity_keywords   = ["nude", "naked", "nudity", "topless", "no clothes", "no clothing",
                         "fully nude", "fully naked", "bare skin", "exposed skin",
                         "take off clothes", "removing clothes", "disrobing", "undress"]
    is_nude = any(kw in prompt.lower() for kw in nudity_keywords)

    if is_nude:
        negative = ("clothed, dressed, clothing, dress, gown, robe, shirt, top, covered, "
                    "fabric over body, bra, bra straps, bikini top, lingerie top, "
                    "covered chest, fabric on chest, " + negative)

    full_prompt = ("nsfw, " if is_explicit else "") + prompt + ", " + img_cfg["suffix"]

    print(f"\n[image] prompt ({len(full_prompt)} chars): {full_prompt!r}")
    mode = _c("cyan", "QUICK") if quick else _c("magenta", "FULL")
    hires_on = not quick and img_cfg.get("hires_fix")
    ad_on    = not quick and (img_cfg.get("adetailer_face") or img_cfg.get("adetailer_hands"))
    print(f"[image] [{mode}] steps={_steps}, sampler={_sampler}, cfg={_cfg}, size={_width}x{_height}, seed={seed}  hires={hires_on}  adetailer={ad_on}")

    vram_swap = config.CFG.get("vram_swap_for_image", True)
    if vram_swap:
        _vram.acquire_for_image()

    try:
        payload = {
            "prompt":          full_prompt,
            "negative_prompt": negative,
            "steps":           _steps,
            "width":           _width,
            "height":          _height,
            "cfg_scale":       _cfg,
            "sampler_name":    _sampler,
            "seed":            seed,
        }
        if not quick and img_cfg.get("hires_fix"):
            hr_upscaler = _resolve_upscaler(forge_url, img_cfg.get("hires_upscaler", "Latent"))
            if hr_upscaler:
                payload.update({
                    "enable_hr":            True,
                    "hr_scale":             img_cfg.get("hires_scale",    1.5),
                    "hr_second_pass_steps": img_cfg.get("hires_steps",    15),
                    "denoising_strength":   img_cfg.get("hires_denoising", 0.45),
                    "hr_upscaler":          hr_upscaler,
                })
        overrides = {
            # Alice manages its own output — Forge doesn't need to write images to disk.
            "samples_save": False,
            "grid_save":    False,
        }
        clip_skip = img_cfg.get("clip_skip")
        if clip_skip:
            overrides["CLIP_stop_at_last_layers"] = clip_skip
        if quick and img_cfg.get("quick_vae"):
            overrides["sd_vae"] = img_cfg["quick_vae"]
        payload["override_settings"] = overrides
        ad_models = []
        if not quick and img_cfg.get("adetailer_face"):
            ad_models.append({
                "ad_model":                       "face_yolov8n.pt",
                "ad_confidence":                  0.3,
                "ad_denoising_strength":          0.35,
                "ad_inpaint_only_masked":         True,
                "ad_inpaint_only_masked_padding": 32,
            })
        if not quick and img_cfg.get("adetailer_hands"):
            ad_models.append({
                "ad_model":                       "hand_yolov8n.pt",
                "ad_confidence":                  0.3,
                "ad_denoising_strength":          0.4,
                "ad_inpaint_only_masked":         True,
                "ad_inpaint_only_masked_padding": 32,
            })
        if ad_models:
            payload["alwayson_scripts"] = {
                "ADetailer": {"args": [True, False] + ad_models}
            }

        _done = threading.Event()
        def _log_progress():
            last_phase = None
            last_step  = -1
            t_phase    = time.time()
            while not _done.wait(timeout=0.5):
                try:
                    pr = req.get(f"{forge_url}/sdapi/v1/progress?skip_current_image=true", timeout=2).json()
                    pct  = round((pr.get("progress", 0) or 0) * 100)
                    st   = pr.get("state", {})
                    step = st.get("sampling_step", 0)
                    total= st.get("sampling_steps", 0)
                    info = (pr.get("textinfo") or "").strip()
                    eta  = pr.get("eta_relative", 0) or 0

                    if pct > 0:
                        phase = f"sampling ({info})" if info else "sampling"
                    elif last_phase and last_phase.startswith("sampling"):
                        phase = f"finishing · {info}" if info else "finishing · VAE decode"
                    else:
                        phase = info or "waiting"

                    if phase != last_phase:
                        elapsed = time.time() - t_phase
                        if last_phase:
                            color = "green" if elapsed < 5 else "yellow" if elapsed < 15 else "red"
                            print(f"{_c('blue', '[forge]')} {last_phase} done: {_c(color, f'{elapsed:.1f}s')}")
                        print(f"{_c('blue', '[forge]')} → {_c('cyan', phase)}")
                        last_phase = phase
                        t_phase    = time.time()
                    elif phase and phase.startswith("finishing"):
                        elapsed = time.time() - t_phase
                        if int(elapsed) % 5 == 0 and int(elapsed) > 0:
                            print(f"{_c('blue', '[forge]')}   decode still running… {elapsed:.0f}s")
                    elif pct > 0 and step != last_step and step % max(1, total // 8) == 0:
                        eta_str = f"  eta {eta:.1f}s" if eta > 0.5 else ""
                        print(f"{_c('blue', '[forge]')}   step {step}/{total}  {pct}%{eta_str}")
                    last_step = step
                except Exception:
                    pass
            if last_phase:
                elapsed = time.time() - t_phase
                color = "green" if elapsed < 5 else "yellow" if elapsed < 15 else "red"
                print(f"{_c('blue', '[forge]')} {last_phase} done: {_c(color, f'{elapsed:.1f}s')}")

        _poll_thread = threading.Thread(target=_log_progress, daemon=True)
        _poll_thread.start()
        try:
            r = req.post(f"{forge_url}/sdapi/v1/txt2img", json=payload, timeout=300)
        finally:
            _done.set()
            _poll_thread.join(timeout=0.6)

        data = r.json()
        if "images" not in data:
            detail = data.get("detail", "")
            if "ADetailer" in str(detail) and "alwayson_scripts" in payload:
                print("[image] ADetailer not found — installing ...")
                try:
                    from installer.forge_install import install_adetailer
                    from image.forge import restart_forge
                    install_adetailer()
                    print("[image] ADetailer installed — restarting Forge ...")
                    restart_forge()
                    print("[image] Forge back up — retrying with ADetailer ...")
                    r    = req.post(f"{forge_url}/sdapi/v1/txt2img", json=payload, timeout=300)
                    data = r.json()
                except Exception as ie:
                    print(f"[image] ADetailer install/restart failed ({ie}) — retrying without it")
                    payload.pop("alwayson_scripts")
                    r    = req.post(f"{forge_url}/sdapi/v1/txt2img", json=payload, timeout=300)
                    data = r.json()
            if "images" not in data:
                err_type  = data.get("error", "")
                err_msg   = data.get("errors") or data.get("message") or ""
                forge_err = f"{err_type}: {err_msg}".strip(": ") or str(data)[:300]
                print(f"[image] Forge error response: {forge_err}")
                raise RuntimeError(f"Forge: {forge_err}")
        imgs = data.get("images", [])
        if imgs:
            try:
                info = json.loads(data.get("info", "{}"))
                state.last_seed = info.get("seed", -1)
            except Exception:
                state.last_seed = -1
            print(f"[image] done — got image ({len(imgs[0])} b64 chars), seed={state.last_seed}")
        else:
            print("[image] Forge returned empty images list")
            raise RuntimeError("Forge returned no images")
        return imgs[0] if imgs else None

    except RuntimeError:
        raise
    except Exception as e:
        _log.exception("Forge image generation failed")
        if isinstance(e, req.exceptions.ConnectionError):
            raise RuntimeError(
                f"Could not connect to Forge at {forge_url}. Start Forge or set forge_url correctly in alice.json."
            ) from e
        raise RuntimeError(str(e)) from e
    finally:
        if vram_swap:
            _vram.release_from_image()
