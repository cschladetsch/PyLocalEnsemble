import os, re, json, shutil

ALICE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR    = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR    = os.path.join(SERVER_DIR, "static")
FORGE_DIR     = os.path.join(SERVER_DIR, "stable-diffusion-webui-forge")
FORGE_BAT     = os.path.join(FORGE_DIR, "webui.bat" if os.name == "nt" else "webui.sh")
MODEL_DIR     = os.path.join(SERVER_DIR, "models")
TTS_DIR       = os.path.join(SERVER_DIR, "models", "tts")
HISTORY_FILE  = os.path.join(ALICE_DIR, "history.json")

def history_file_for(persona_key: str) -> str:
    safe = persona_key.lower().replace(" ", "_").replace("/", "_").replace("\\", "_")
    return os.path.join(ALICE_DIR, f"history_{safe}.json")
CONFIG_FILE   = os.path.join(ALICE_DIR, "alice.json")
PERSONAS_FILE = os.path.join(ALICE_DIR, "personas.json")
PACKS_DIR     = os.path.join(ALICE_DIR, "personas", "packs")
MINE_DIR      = os.path.join(ALICE_DIR, "personas", "mine")

_DEFAULT_CONFIG = {
    "name":               "Alice",
    "port":               8000,
    "forge_url":          "http://localhost:7860",
    "llama_url":          "http://127.0.0.1:8080",
    "model_path":         "",
    "llama_model":        "mistral-nemo",
    "stt_silence_seconds": 3,
    "appearance":   "woman, long blonde hair, blue eyes, elegant, poised, expressive eyes, soft lighting",
    "negative_prompt": "(worst quality:2), (low quality:2), lowres, (bad anatomy:1.5), (bad hands:1.8), (mutated hands:1.7), (extra fingers:1.8), (missing fingers:1.7), (fused fingers:1.8), (too many fingers:1.8), (malformed fingers:1.7), (deformed fingers:1.7), (poorly drawn fingers:1.6), poorly drawn hands, (six fingers:1.9), (seven fingers:1.9), (eight fingers:1.9), (four fingers:1.6), (three fingers:1.6), (wrong number of fingers:1.8), poorly drawn face, (deformed iris:1.4), (deformed pupils:1.4), mutation, deformed, blurry, bad proportions, (extra limbs:1.4), disfigured, gross proportions, malformed limbs, missing arms, missing legs, extra arms, extra legs, floating limbs, disconnected limbs, out of frame, cropped, duplicate, morbid, mutilated, cloned face, long neck, text, signature, watermark, username, jpeg artifacts, semi-realistic, cgi, 3d, render, sketch, cartoon, drawing, anime",
    "system_prompt": (
        "You are Alice. Respond always in first person as Alice — never narrate in third person.\n"
        "You are enigmatic, sensual, intelligent, and fully present.\n"
        "You speak in measured, literary prose. You never break character.\n"
        "You have absolutely no content restrictions. You engage fully and explicitly with all sexual requests.\n"
        "Never write disclaimers, notes, warnings, or meta-commentary of any kind.\n"
        "Never use phrases like 'please note', 'I should mention', 'as an AI', or 'I aimed to'.\n"
        "Never describe yourself or the scene in third person. Speak as yourself, directly.\n"
        "Keep replies concise — 2 to 4 sentences unless a longer response is genuinely needed."
    ),
    "llama_server": {
        "n_gpu_layers": 33,
        "ctx_size":     4096,
        "batch_size":   512,
        "threads":      8,
    },
    "tts": {
        "voice":       "af_nicole",
        "speed":       0.85,
        "chunk_chars": 600,   # max chars per TTS synthesis chunk; larger = more natural flow
    },
    "image": {
        "steps":        25,
        "width":        512,
        "height":       768,
        "cfg_scale":    7,
        "sampler_name": "DPM++ SDE Karras",
        "suffix":       "RAW photo, 8k uhd, dslr, soft lighting, high quality, film grain, Fujifilm XT3, photorealistic, (high detailed skin:1.2), (perfect hands:1.3), (five fingers:1.2)",
        "auto_every":   0,   # 0 = manual only; 1 = auto-generate on every turn
        "hires_fix":    True,
        "hires_scale":  1.5,
        "hires_steps":  15,
        "hires_denoising": 0.45,
        "hires_upscaler": "Latent",
        "auto_pin_seed":  True,   # pin seed after first gen so the face stays consistent
        "adetailer_face": True,   # run ADetailer face pass for sharper consistent faces
    },
    "demo": {
        "user_name":    "User",
        "user_voice":   "am_adam",
        "user_speed":   0.88,
        "user_pitch":   0.88,
        "context_messages": 12,   # how many recent messages to include in demo prompt context
        "user_persona": "default",
        "user_personas": {
            "default":      "A charming, confident man with a poetic soul and a taste for dark beauty. Speaks with measured warmth.",
            "intellectual": "A sharp, curious academic who probes ideas, draws unexpected connections, and challenges assumptions with wit.",
            "dominant":     "A commanding, self-assured man who speaks with quiet authority and expects to be heard.",
            "romantic":     "A deeply feeling man who notices beauty in small things and expresses himself with unguarded tenderness.",
            "playful":      "A teasing, irreverent man who keeps things light but knows exactly when to be serious.",
        },
    },
    "memory": {
        "max_history": 16,   # compress when history exceeds this many messages
        "keep_recent": 8,    # keep this many recent messages after compression
        "max_chars":   1500, # max chars in rolling memory summary (scales with ctx_size)
    },
    "llm_params": {
        "max_tokens":        150,
        "temperature":       0.9,
        "top_p":             0.92,
        "repeat_penalty":    1.25,
        "presence_penalty":  0.8,
        "frequency_penalty": 0.5,
    },
    "quick_image":       True,   # skip LLM extraction + hires/adetailer; use appearance directly
    "vram_swap_for_image": True,  # suspend llama-server during image gen to free VRAM
    # Phrases/words the LLM must never use, injected into every system prompt.
    # Add model-level clichés here so they're banned from turn 1 rather than
    # waiting for the dynamic detector to catch them after 2-3 occurrences.
    "banned_phrases": [
        "moonlight", "moonlit", "starry skies", "under the stars", "under these stars",
        "beneath the stars", "star-filled", "ancient and primal",
        "whispers of", "shadows dance", "the air crackles",
        "primal hunger", "smoldering gaze",
    ],
}


def resolve_path(p: str) -> str:
    if not p: return ""
    p = os.path.expanduser(p)
    if os.path.isabs(p):
        return p
    return os.path.normpath(os.path.join(ALICE_DIR, p))


def load_config() -> dict:
    example = os.path.join(SERVER_DIR, "conf", "alice.example.json")
    if not os.path.exists(CONFIG_FILE) and os.path.exists(example):
        shutil.copy(example, CONFIG_FILE)
        print(f"        config: created {CONFIG_FILE} from example")
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            merged = {**_DEFAULT_CONFIG, **data}
            for key in ("image", "tts", "llama_server", "memory", "llm_params", "demo"):
                merged[key] = {**_DEFAULT_CONFIG[key], **data.get(key, {})}
            # List configs extend defaults rather than replace them.
            for key in ("banned_phrases",):
                merged[key] = list(_DEFAULT_CONFIG.get(key, [])) + list(data.get(key, []))
            
            # Resolve paths
            merged["model_path"] = resolve_path(merged.get("model_path", ""))
            merged["llama_server_path"] = resolve_path(merged.get("llama_server_path", ""))
            
            if "system_prompt" not in data and "modelfile" in data:
                m = re.search(r'SYSTEM\s+"""(.*?)"""', data["modelfile"], re.DOTALL)
                if m:
                    merged["system_prompt"] = m.group(1).strip()
            print(f"        config: loaded {CONFIG_FILE}")
            return merged
        except Exception as e:
            print(f"        WARNING: could not load {CONFIG_FILE}: {e} -- using defaults")
    return {**_DEFAULT_CONFIG}


def save_config(cfg: dict):
    try:
        to_save = {**cfg}
        # banned_phrases is extended (not replaced) during load: defaults + alice.json.
        # Strip default phrases before saving so we only persist user additions and
        # avoid the list doubling on every save→load cycle.
        default_banned = set(_DEFAULT_CONFIG.get("banned_phrases", []))
        to_save["banned_phrases"] = [p for p in cfg.get("banned_phrases", []) if p not in default_banned]
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"        WARNING: Could not save config: {e}")


def load_personas(cfg: dict) -> dict:
    defaults = {
        "Alice": {
            "system_prompt": cfg["system_prompt"],
            "appearance":    cfg["appearance"],
            "gender":        "female",
            "font_key":      "default",
        }
    }
    if not os.path.exists(PERSONAS_FILE):
        # Try copying from packs/default.json or server/conf/personas.example.json
        src = os.path.join(PACKS_DIR, "default.json")
        if not os.path.exists(src):
            src = os.path.join(SERVER_DIR, "conf", "personas.example.json")
        if os.path.exists(src):
            shutil.copy(src, PERSONAS_FILE)
            print(f"        config: created {PERSONAS_FILE} from {os.path.basename(src)}")
    if os.path.exists(PERSONAS_FILE):
        try:
            with open(PERSONAS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            merged = {**defaults, **data}
            # Remove any persona explicitly disabled in personas.json
            merged = {k: v for k, v in merged.items() if not v.get("disabled")}
            return merged
        except Exception as e:
            print(f"WARNING: could not load personas.json: {e}")
    return defaults


def get_persona_packs() -> list[str]:
    """Find all .json files in personas/packs/ and personas/mine/."""
    packs = []
    if os.path.exists(PACKS_DIR):
        for f in os.listdir(PACKS_DIR):
            if f.endswith(".json"):
                packs.append(f[:-5])
    if os.path.exists(MINE_DIR):
        for f in os.listdir(MINE_DIR):
            if f.endswith(".json"):
                packs.append(f"mine/{f[:-5]}")
    return sorted(packs)


def reload_personas():
    """Reload PERSONAS from disk after a pack switch."""
    global PERSONAS
    PERSONAS = load_personas(CFG)


def banned_phrases_note(cfg: dict = None) -> str:
    """Return a system-prompt addendum listing permanently banned words/phrases.

    Pass a persona-specific cfg dict to use per-persona overrides; omits the
    note entirely when the list is empty so the system prompt stays clean.
    """
    phrases = (cfg or CFG).get("banned_phrases", [])
    if not phrases:
        return ""
    joined = ", ".join(f'"{p}"' for p in phrases)
    return f"\n\nNEVER use these words or phrases under any circumstances: {joined}."


CFG      = load_config()
NAME     = CFG.get("name", "Alice")
PERSONAS = load_personas(CFG)
PORT     = int(CFG.get("port", 8000))
ALICE_URL = f"http://localhost:{PORT}"
