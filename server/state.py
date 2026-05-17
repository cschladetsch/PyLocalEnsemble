"""Shared mutable runtime state — imported by all route modules."""
from __future__ import annotations
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
_character_seed  = -1          # -1 = random; set when seed is pinned
_seed_pinned     = False
last_sd_prompt   = ""          # last prompt sent to Forge; used by /reroll
last_appearance  = ""          # last appearance string used; used by /reroll
last_seed        = -1          # seed used by the most recent successful generation

# Active persona key — used to detect cross-session persona mismatch in history
_active_persona_key: str = ""

# Group chat state — mutated by routes/group.py
GROUP_ACTIVE   = False
GROUP_PERSONAS: dict = {}  # key → persona config dict

# Demo mode state — mutated by routes/system.py
DEMO_ACTIVE = False

# Demo mode — active user persona (name + description)
_demo_user_persona_name: str = ""
_demo_user_persona_desc: str = ""

# Pre-extracted SD prompt — populated by chat route, consumed by /image
_pre_sd_prompt:    str | None = None   # positive prompt
_pre_sd_negative:  str        = ""     # extra negative tags

# Rolling image context — sliding window of last 3 exchanges + compressed summary of all older turns.
# Each entry is (user_msg, alice_reply). On overflow, the oldest entry is folded into the summary.
_img_ctx_recent:  list  = []   # [(user_msg, alice_reply), ...]  — at most IMG_CTX_WINDOW entries
_img_ctx_summary: str   = ""   # compressed tail of all rolled-off exchanges
IMG_CTX_WINDOW        = 3      # number of full exchanges kept verbatim
IMG_CTX_SUMMARY_CHARS = 800    # max chars for the rolling summary


def update_image_context(user_msg: str, alice_reply: str) -> None:
    """Call after each chat reply to maintain the rolling context window."""
    global _img_ctx_recent, _img_ctx_summary
    if len(_img_ctx_recent) >= IMG_CTX_WINDOW:
        oldest_user, oldest_reply = _img_ctx_recent.pop(0)
        chunk = f"User: {oldest_user}\nAlice: {oldest_reply}"
        combined = (_img_ctx_summary + "\n" + chunk).strip() if _img_ctx_summary else chunk
        if len(combined) > IMG_CTX_SUMMARY_CHARS:
            combined = combined[-IMG_CTX_SUMMARY_CHARS:]
            sp = combined.find(' ')           # trim to word boundary
            if 0 < sp < 80:
                combined = combined[sp + 1:]
        _img_ctx_summary = combined
    _img_ctx_recent.append((user_msg, alice_reply))


def get_image_context() -> str:
    """Return formatted context string for SD prompt extraction."""
    parts = []
    if _img_ctx_summary:
        parts.append(f"[Earlier context]\n{_img_ctx_summary}")
    for user_msg, alice_reply in _img_ctx_recent:
        parts.append(f"User: {user_msg}\nAlice: {alice_reply}")
    return "\n\n".join(parts)


def clear_image_context() -> None:
    global _img_ctx_recent, _img_ctx_summary
    _img_ctx_recent  = []
    _img_ctx_summary = ""


def should_auto_image(user_msg: str) -> bool:
    import config
    return config.CFG.get("image", {}).get("auto_every", 0) > 0


# ── Utility ───────────────────────────────────────────────────────────────────
def image_output_dir() -> str:
    repo_static = os.path.join(config.STATIC_DIR, "outputs")
    if os.path.normpath(repo_static).startswith(os.path.normpath(config.ALICE_DIR)):
        return repo_static
    return os.path.join(config.ALICE_DIR, "static", "outputs")


def save_generated_image(b64_data: str) -> str:
    out_dir = image_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    fname = f"img_{time.time_ns()}.png"
    with open(os.path.join(out_dir, fname), "wb") as f:
        f.write(base64.b64decode(b64_data))
    return f"/static/outputs/{fname}"
