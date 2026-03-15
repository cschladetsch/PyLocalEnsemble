"""Image generation via the Forge/SD API."""
import json, os, re, threading
import requests as req
import config
import state
from utils import http_ok
from image.forge import start_forge

_gen_cancel    = threading.Event()
_upscaler_cache: str | None = None   # cached after first successful query; "" = none available


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
                   seed: int = -1):
    forge_url = config.CFG["forge_url"]
    img_cfg   = config.CFG["image"]
    if not http_ok(f"{forge_url}/sdapi/v1/sd-models"):
        print("Forge down, restarting...")
        start_forge()

    negative = (extra_negative + ", " + negative_base) if extra_negative else negative_base
    _steps   = steps     if steps     is not None else img_cfg["steps"]
    _cfg     = cfg_scale if cfg_scale is not None else img_cfg["cfg_scale"]

    explicit_keywords = ["anal", "fingering", "insertion", "blowjob", "fellatio",
                         "penetration", "nude", "naked", "nudity", "topless", "nsfw",
                         "no clothes", "fully nude", "fully naked", "bare skin", "exposed skin"]
    nudity_keywords   = ["nude", "naked", "nudity", "topless", "no clothes", "no clothing",
                         "fully nude", "fully naked", "bare skin", "exposed skin"]

    is_explicit = any(kw in prompt.lower() for kw in explicit_keywords)
    is_nude     = any(kw in prompt.lower() for kw in nudity_keywords)

    clean_appearance = re.sub(r"\b(elegant|poised|refined|sophisticated)\b,?\s*",
                              "", appearance, flags=re.I).strip(", ")
    if is_nude:
        clothing_pattern = (r"\b(dress|gown|robe|skirt|blouse|shirt|top|corset|bodice|"
                            r"stockings|lingerie|bra|underwear|panties|trousers|pants|"
                            r"shorts|linen|silk dress|lace|veil)\b")
        clean_appearance = re.sub(clothing_pattern + r",?\s*", "", clean_appearance,
                                  flags=re.I).strip(", ")
        negative = ("clothed, dressed, clothing, dress, gown, robe, shirt, top, covered, "
                    "fabric over body, bra, bra straps, bikini top, lingerie top, "
                    "covered chest, fabric on chest, " + negative)

    used_appearance = clean_appearance if is_explicit else appearance
    # Appearance first — SD attention weights earlier tokens more heavily, so character
    # features (face, hair, eyes) must lead the prompt to stay consistent across turns.
    full_prompt = ("nsfw, " if is_explicit else "") + used_appearance + ", " + prompt + ", " + img_cfg["suffix"]

    print(f"\n[image] prompt ({len(full_prompt)} chars): {full_prompt!r}")
    print(f"[image] steps={_steps}, cfg={_cfg}, size={img_cfg['width']}x{img_cfg['height']}, seed={seed}")

    try:
        payload = {
            "prompt":          full_prompt,
            "negative_prompt": negative,
            "steps":           _steps,
            "width":           img_cfg["width"],
            "height":          img_cfg["height"],
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
        clip_skip = img_cfg.get("clip_skip")
        if clip_skip:
            payload["override_settings"] = {"CLIP_stop_at_last_layers": clip_skip}
        ad_models = []
        if img_cfg.get("adetailer_face"):
            ad_models.append({
                "ad_model":                       "face_yolov8n.pt",
                "ad_confidence":                  0.3,
                "ad_denoising_strength":          0.35,
                "ad_inpaint_only_masked":         True,
                "ad_inpaint_only_masked_padding": 32,
            })
        if img_cfg.get("adetailer_hands"):
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
        r    = req.post(f"{forge_url}/sdapi/v1/txt2img", json=payload, timeout=300)
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
        print(f"[image] Forge error: {e}")
        raise RuntimeError(str(e))
