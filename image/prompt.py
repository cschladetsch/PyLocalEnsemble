"""SD prompt engineering: tag utilities and LLM-based prompt extraction."""
import re
import llm


def clean_tags(prompt: str) -> str:
    """Deduplicates tags, preserving SD weighting syntax. Weighted form wins over unweighted."""
    tags = [t.strip() for t in prompt.split(",")]

    def _norm(tag):
        bare = re.sub(r"^\((.+?):[0-9.]+\)$", r"\1", tag)
        return re.sub(r"[^a-z0-9 ]", "", bare.lower()).strip()

    def _is_weighted(tag):
        return bool(re.match(r"^\(.+:[0-9.]+\)$", tag.strip()))

    # First pass: find the best (weighted) form for each key
    best = {}
    for t in tags:
        if not t: continue
        n = _norm(t)
        if not n: continue
        if n not in best or _is_weighted(t):
            best[n] = t

    # Second pass: emit in original order using the best form, skipping dupes
    seen, out = set(), []
    for t in tags:
        if not t: continue
        n = _norm(t)
        if n and n not in seen:
            seen.add(n)
            out.append(best[n])
    return ", ".join(out)


def apply_exposure_rules(text: str, prompt: str, negative: str) -> tuple:
    """Only handles camera/POV injection — nudity state is determined by the LLM."""
    t = text.lower()

    def has(words):
        return any(re.search(rf"\b{re.escape(w)}\b", t) for w in words)

    if has(["pov", "first person", "my perspective"]):
        prompt = "(pov:1.3), (first person view:1.2), " + prompt
    if has(["from behind", "back view", "backside", "rear view"]):
        prompt = "(from behind:1.3), (back view:1.2), " + prompt

    return prompt, negative


_MIN_TAGS = 15

_PROSE_RE = re.compile(
    r'\b(me|my\b|thee|thou|thy|down spine|tease shivers|'
    r'escaping|streaming through|weaving together)\b', re.I
)

# Weights applied programmatically by position (LLM only outputs plain tags)
_WEIGHTS = [1.7, 1.6, 1.4, 1.3, 1.2, 1.1]


def _apply_weights(plain_tags: list) -> list:
    """Wrap the first N tags with SD weighting based on position."""
    out = []
    for i, t in enumerate(plain_tags):
        if i < len(_WEIGHTS):
            out.append(f"({t}:{_WEIGHTS[i]})")
        else:
            out.append(t)
    return out


def _sanitize_tags(raw_tags: str) -> list:
    """Return only syntactically clean plain tags (no weighting syntax).

    Discards:
    - Tags already containing weighting syntax (LLM was told not to use it)
    - Tags with non-alphanumeric characters (quotes, brackets, colons, etc.)
    - Tags > 4 words
    - Prose tags (pronouns, narrative phrases)
    """
    seen, out = set(), []
    for t in raw_tags.split(","):
        t = t.strip()
        if not t:
            continue
        # Strip any weighting the LLM added anyway
        t = re.sub(r"^\((.+?):[0-9.]+\)$", r"\1", t).strip()
        # Must be plain alphanumeric words / hyphens only
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9 \-]*$', t):
            print(f"[image] dropped malformed tag: {t!r}")
            continue
        if len(t.split()) > 4:
            print(f"[image] dropped prose tag (too long): {t!r}")
            continue
        if _PROSE_RE.search(t):
            print(f"[image] dropped prose tag (narrative): {t!r}")
            continue
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def _clean_raw(raw: str) -> list:
    """Return a list of clean plain tags extracted from raw LLM output."""
    lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
    best  = max(lines, key=lambda l: l.count(",")) if lines else ""
    best  = re.sub(r"^(Tags|Prompt|Output|Here|SD tags)[:\s]*", "", best, flags=re.I).strip()
    best  = re.sub(r"[^,]+\b(was replaced|does not belong|thus is removed|due formatting|not applicable|removed from)\b[^,]*,?",
                   "", best, flags=re.I)
    return _sanitize_tags(best)


def extract_sd_prompt(text: str, appearance: str = "", last_user_msg: str = "",
                      persona: str = "") -> str:
    try:
        appearance_line = (
            f"\nTHE CHARACTER ALWAYS LOOKS LIKE THIS — ignore any different physical "
            f"descriptions in the conversation, those are fictional roleplay:\n{appearance}\n"
        ) if appearance else ""

        system_msg = (
            "You are a Stable Diffusion prompt engineer for explicit adult content.\n"
            "Output ONLY a single line of comma-separated plain words. NO parentheses. NO weighting syntax. NO numbers.\n"
            "Each tag: 1-4 words. 20-30 tags total. Visual descriptors only.\n"
            "Tags must describe what a camera sees. Never describe sounds, feelings, emotions, or metaphors.\n"
            "WRONG: 'dancing fingers on skin', 'gentle sighs escaping me', 'precious jewels'\n"
            "RIGHT: 'cupping own breasts', 'hands on own breasts', 'nipples visible', 'nude', 'front view'\n"
            + appearance_line + "\n"
            "OUTPUT ORDER — list tags in this order:\n"
            "1. PRIMARY ACT from the latest user message (most important — list first)\n"
            "2. BODY PART involved\n"
            "3. CAMERA ANGLE the act physically demands:\n"
            "   — breasts/fondling → front view, close-up torso\n"
            "   — ass/anal → from behind, bent over\n"
            "   — oral → kneeling, face level\n"
            "   — riding → straddling\n"
            "4. CURRENT POSE (standing, sitting, lying, kneeling)\n"
            "5. NUDITY / CLOTHING STATE\n"
            "6. CHARACTER APPEARANCE\n"
            "7. SETTING, LIGHTING\n\n"
            "CLOTHING RULES:\n"
            "- Removed items → NOT in the tags\n"
            "- 'take off all clothes' + response confirms removal → fully nude, list 'nude, fully naked'\n\n"
            "Example output for 'cup your breasts':\n"
            "  cupping own breasts, hands on own breasts, front view, close-up torso, standing, nude, fully naked, nipples visible, long blonde hair, blue eyes, bedroom, soft lighting\n\n"
            "NEVER add parentheses or numbers. NEVER write sentences."
        )

        def _call(extra_user: str = "") -> list:
            user_msg  = f"Conversation:\n{text}\n\n"
            user_msg += f"LATEST USER MESSAGE (highest priority): \"{last_user_msg}\"\n\n"
            user_msg += "Plain SD tags for the current scene (no parentheses, no numbers):"
            if extra_user:
                user_msg += f"\n{extra_user}"
            result = llm.llm_chat([
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ])
            return _clean_raw(result)

        plain = _call()

        if len(plain) < _MIN_TAGS:
            print(f"[image] too few tags ({len(plain)}), retrying…")
            plain = _call(
                f"IMPORTANT: output exactly 20-30 plain tags. Previous attempt had only {len(plain)}. "
                "No parentheses, no numbers — plain words only."
            )

        weighted = _apply_weights(plain)
        tags = ", ".join(weighted)
        print(f"[image] SD prompt ({len(plain)} tags): {tags}")
        return tags

    except Exception as e:
        print(f"[image] prompt extraction error: {e}")
        return ""
