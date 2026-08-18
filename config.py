from pathlib import Path

FORGE_URL = "http://127.0.0.1:7860"
CACHE_DIR = Path("server/pics_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

PROMPTS = [
    "A cyberpunk street in Melbourne, neon rain, hyper-detailed, 8k",
    "A cozy room with a sleeping Persian cat, cinematic lighting",
    "Abstract fractal geometry, glowing energy lines, octane render",
    "A futuristic computer laboratory with glowing server racks",
]
