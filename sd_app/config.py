import os, json, shutil

SD_APP_DIR  = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR  = os.path.join(SD_APP_DIR, "static")
CONFIG_FILE = os.path.join(SD_APP_DIR, "sd_app.json")

# Defaults to the Forge install Alice's own server/ already manages. Override
# via forge_venv_dir/forge_url in sd_app.json once this package moves to its
# own repo, where this relative path won't exist.
FORGE_DIR = os.path.join(SD_APP_DIR, "..", "server", "stable-diffusion-webui-forge")
FORGE_BAT = os.path.join(FORGE_DIR, "webui.ps1" if os.name == "nt" else "webui.sh")

_DEFAULT_CONFIG = {
    "port":            8010,
    "forge_url":       "http://localhost:7860",
    # Alice's own server — sd_app asks it to yield VRAM before generating,
    # since the two now run as independent processes that can't otherwise
    # coordinate GPU memory on a single 8GB card. Leave blank to disable
    # (sd_app will still generate, but may fail if Alice's LLM is loaded).
    "alice_url":       "http://localhost:8000",
    "forge_venv_dir":  "",
    "forge_args":      "",
    "sd_checkpoint":   "",
    "negative_prompt": "(worst quality:2), (low quality:2), lowres, (bad anatomy:1.5), (bad hands:1.8), "
                        "poorly drawn hands, poorly drawn face, mutation, deformed, blurry, bad proportions, "
                        "disfigured, out of frame, cropped, duplicate, morbid, mutilated, text, signature, watermark",
    "image": {
        "steps":           25,
        "width":           512,
        "height":          768,
        "cfg_scale":       7,
        # Matches the "quick" mode the original /sd-generate handler always used —
        # fast sampler, no hires-fix second pass.
        "sampler_name":    "DPM++ 2M Karras",
        "suffix":          "RAW photo, 8k uhd, dslr, soft lighting, high quality, photorealistic",
        "hires_fix":       False,
        "hires_scale":     1.5,
        "hires_steps":     15,
        "hires_denoising": 0.45,
        "hires_upscaler":  "Latent",
    },
}


def load_config() -> dict:
    example = os.path.join(SD_APP_DIR, "conf", "sd_app.example.json")
    if not os.path.exists(CONFIG_FILE) and os.path.exists(example):
        shutil.copy(example, CONFIG_FILE)
        print(f"        sd_app config: created {CONFIG_FILE} from example")
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            merged = {**_DEFAULT_CONFIG, **data}
            merged["image"] = {**_DEFAULT_CONFIG["image"], **data.get("image", {})}
            return merged
        except Exception as e:
            print(f"        WARNING: could not load {CONFIG_FILE}: {e} -- using defaults")
    return {**_DEFAULT_CONFIG}


def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"        WARNING: could not save config: {e}")


CFG = load_config()
