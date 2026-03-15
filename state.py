"""Shared mutable runtime state — imported by all route modules."""
import re, os, time, base64
import config

# ── Per-session runtime vars (mutated by routes) ─────────────────────────────
ALICE_APPEARANCE = config.CFG["appearance"]
SYSTEM_PROMPT    = config.CFG["system_prompt"]
BASE_NEGATIVE    = config.CFG["negative_prompt"]
IMAGE_SUFFIX     = config.CFG.get("image", {}).get("suffix", "")

# Base snapshots — restored on persona switch so previous persona doesn't leak
_BASE_IMAGE_CFG  = {**config.CFG.get("image", {})}
_BASE_NEGATIVE   = config.CFG["negative_prompt"]

# Image session state
_nudity_state    = "clothed"   # floor for SD nudity; persists across turns
_character_seed  = -1          # -1 = random; set when seed is pinned
_seed_pinned     = False
last_sd_prompt   = ""          # last prompt sent to Forge; used by /reroll
last_seed        = -1          # seed used by the most recent successful generation


# ── Auto-image heuristic ──────────────────────────────────────────────────────
_AUTO_IMAGE_RE = re.compile(
    r'\b(take off|strip|undress|remove|show me|show your|spread|bend|kneel|cup|hold|touch|'
    r'finger|ride|suck|stroke|kiss|insert|stuff|fuck|naked|nude|topless|undressed|'
    r'lie down|sit on|get on|turn around|open|expose|reveal|clothes off|get off|'
    r'pull down|pull up|pull off|lift)\b',
    re.I
)

# Words that signal the user wants the character to re-clothe
_RE_CLOTHE = re.compile(
    r'\b(get dressed|put on|cover up|dress|put your clothes|clothe yourself)\b', re.I
)


def should_auto_image(user_msg: str) -> bool:
    return bool(_AUTO_IMAGE_RE.search(user_msg))


# ── Utility ───────────────────────────────────────────────────────────────────
def save_generated_image(b64_data: str) -> str:
    out_dir = os.path.join(config.ALICE_DIR, "static", "outputs")
    os.makedirs(out_dir, exist_ok=True)
    fname = f"img_{int(time.time() * 1000)}.png"
    with open(os.path.join(out_dir, fname), "wb") as f:
        f.write(base64.b64decode(b64_data))
    return f"/static/outputs/{fname}"
