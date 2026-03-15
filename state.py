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

# Nudity decay — returns toward "clothed" after N consecutive non-sexual turns
_NUDITY_ORDER   = ["clothed", "topless", "bottomless", "fully nude"]
_nudity_turns_since_keyword = 0
_NUDITY_KEYWORDS_RE = re.compile(
    r'\b(nude|naked|undress|strip|topless|bottomless|disrobe|take off|remove|'
    r'bare|expose|breasts|pussy|cock|sex|fuck|cum|blowjob|suck|lick|finger|'
    r'dildo|insert|spread|bend|kneel|ride|stroke|kiss)\b', re.I
)

# Words that signal the user wants the character to re-clothe
_RE_CLOTHE = re.compile(
    r'\b(get dressed|put on|cover up|dress|put your clothes|clothe yourself)\b', re.I
)


def decay_nudity_state(user_msg: str) -> None:
    """Decay nudity floor one level toward 'clothed' after 3 non-sexual turns."""
    global _nudity_state, _nudity_turns_since_keyword
    if _NUDITY_KEYWORDS_RE.search(user_msg):
        _nudity_turns_since_keyword = 0
        return
    _nudity_turns_since_keyword += 1
    if _nudity_turns_since_keyword >= 3 and _nudity_state != "clothed":
        try:
            idx = _NUDITY_ORDER.index(_nudity_state)
        except ValueError:
            _nudity_state = "clothed"   # unknown value — reset to safe default
            _nudity_turns_since_keyword = 0
            return
        if idx > 0:
            _nudity_state = _NUDITY_ORDER[idx - 1]
            _nudity_turns_since_keyword = 0
            print(f"[state] nudity decay: → {_nudity_state!r}")


def should_auto_image(user_msg: str) -> bool:
    return True  # generate a scene image on every chat turn


# ── Utility ───────────────────────────────────────────────────────────────────
def save_generated_image(b64_data: str) -> str:
    out_dir = os.path.join(config.ALICE_DIR, "static", "outputs")
    os.makedirs(out_dir, exist_ok=True)
    fname = f"img_{int(time.time() * 1000)}.png"
    with open(os.path.join(out_dir, fname), "wb") as f:
        f.write(base64.b64decode(b64_data))
    return f"/static/outputs/{fname}"
