"""Image generation via the Forge/SD API."""
import json, os, re, threading
import requests as req
import config
from utils import http_ok
from image.forge import start_forge

_gen_cancel = threading.Event()
_last_seed  = -1   # seed used by the most recent successful generation


def generate_image(prompt: str, appearance: str, negative_base: str,
                   extra_negative: str = "", steps: int = None, cfg_scale: float = None,
                   seed: int = -1):
    global _last_seed
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
        negative = "clothed, dressed, clothing, dress, gown, robe, shirt, top, covered, fabric over body, " + negative

    used_appearance = clean_appearance if is_explicit else appearance
    full_prompt = ("nsfw, " if is_explicit else "") + prompt + ", " + used_appearance + ", " + img_cfg["suffix"]

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
        clip_skip = img_cfg.get("clip_skip")
        if clip_skip:
            payload["override_settings"] = {"CLIP_stop_at_last_layers": clip_skip}
        r    = req.post(f"{forge_url}/sdapi/v1/txt2img", json=payload, timeout=300)
        data = r.json()
        if "images" not in data:
            print(f"[image] Forge response (no images key): {str(data)[:300]}")
        imgs = data.get("images", [])
        if imgs:
            try:
                info = json.loads(data.get("info", "{}"))
                _last_seed = info.get("seed", -1)
            except Exception:
                _last_seed = -1
            print(f"[image] done — got image ({len(imgs[0])} b64 chars), seed={_last_seed}")
        else:
            print("[image] Forge returned no images")
        return imgs[0] if imgs else None
    except Exception as e:
        print(f"[image] Forge error: {e}")
        return None
