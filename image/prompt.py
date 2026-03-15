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


_MIN_TAGS   = 15
_ACTION_MIN = 1.4


def _count_tags(tags: str) -> int:
    return len([t for t in tags.split(",") if t.strip()])


def _ensure_action_weighted(tags: str) -> str:
    """Guarantee the first tag (primary action) has weight >= _ACTION_MIN."""
    parts = [t.strip() for t in tags.split(",")]
    if not parts:
        return tags
    first = parts[0]
    m = re.match(r"^\((.+?):([0-9.]+)\)$", first)
    if m:
        if float(m.group(2)) < _ACTION_MIN:
            parts[0] = f"({m.group(1)}:{_ACTION_MIN})"
    else:
        parts[0] = f"({first}:{_ACTION_MIN})"
    return ", ".join(parts)


_PROSE_RE = re.compile(
    r'\b(me|my\b|thee|thou|thy|down spine|tease shivers|'
    r'escaping|streaming through|weaving together)\b', re.I
)


def _strip_prose_tags(tags: str) -> str:
    """Remove tags that are narrative prose rather than SD vocabulary.

    Valid SD tags are 1-4 words and describe visuals, not feelings or actions
    written as sentences. Strips anything that:
      - is more than 4 words (after removing weighting syntax)
      - contains first/second-person pronouns or stock narrative phrases
    """
    out = []
    for t in tags.split(","):
        t = t.strip()
        if not t:
            continue
        bare = re.sub(r"^\((.+?):[0-9.]+\)$", r"\1", t).strip()
        if len(bare.split()) > 4:
            print(f"[image] dropped prose tag (too long): {t!r}")
            continue
        if _PROSE_RE.search(bare):
            print(f"[image] dropped prose tag (pronoun/narrative): {t!r}")
            continue
        out.append(t)
    return ", ".join(out)


def _clean_raw(raw: str) -> str:
    lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
    tags  = max(lines, key=lambda l: l.count(",")) if lines else ""
    tags  = re.sub(r"^(Tags|Prompt|Output|Here|SD tags)[:\s]*", "", tags, flags=re.I).strip()
    tags  = re.sub(r"\([^)]{40,}\)", "", tags)
    tags  = re.sub(r"[^,]+\b(was replaced|does not belong|thus is removed|due formatting|not applicable|removed from)\b[^,]*,?",
                   "", tags, flags=re.I)
    tags  = re.sub(r",\s*,", ",", tags).strip(", ")
    tags  = _strip_prose_tags(tags)
    return tags


def extract_sd_prompt(text: str, appearance: str = "", last_user_msg: str = "",
                      persona: str = "") -> str:
    try:
        appearance_line = (
            f"\nTHE CHARACTER ALWAYS LOOKS LIKE THIS — ignore any different physical "
            f"descriptions in the conversation, those are fictional roleplay:\n{appearance}\n"
        ) if appearance else ""

        system_msg = (
            "You are a Stable Diffusion prompt engineer for explicit adult content.\n"
            "Output ONLY a single line of comma-separated SD tags. No prose, no sentences, no newlines, no explanation.\n"
            "Weighted tags MUST use this exact syntax: (tag:1.5) — parentheses, colon, number. Nothing else.\n"
            "Each tag: 1-4 words maximum. 20-30 tags total.\n"
            "Tags describe VISUALS ONLY. Never describe sounds, feelings, emotions, or metaphors.\n"
            "WRONG (narrative, these will be discarded):\n"
            "  'dancing fingers on skin', 'gentle sighs escaping me', 'morning dew-kissed nipples show',\n"
            "  'pert nipple tips tease shivers down spine', 'precious jewels plucked from garden'\n"
            "RIGHT (visual SD vocabulary):\n"
            "  'cupping own breasts', 'hands on own breasts', 'nipples visible', 'nipples erect',\n"
            "  'standing', 'front view', 'nude', 'long blonde hair'\n"
            + appearance_line + "\n"
            "CLOTHING STATE RULES — read carefully:\n"
            "- An item that was PUT ON and not removed → still on.\n"
            "- An item that was REMOVED and not put back → not on.\n"
            "- If someone said 'take off all clothes' and her response describes removing everything, she is FULLY NUDE at the end.\n"
            "- Prose describing removal (e.g. 'stockings slowly unwound', 'bodice descends') means those items ARE OFF.\n"
            "- Base your final clothing state on what remains ON after all described actions complete.\n\n"
            "WEIGHTING ORDER — put these first:\n"
            "1. CURRENT POSE / ACTION (sitting, standing, lying, etc.): weight 1.4-1.5\n"
            "2. PRIMARY ACT from the latest user message: weight 1.6-1.7\n"
            "3. REQUIRED CAMERA ANGLE the act physically demands: weight 1.3-1.5\n"
            "   — sitting on chair → (sitting:1.4), (armchair:1.2), (front view:1.2)\n"
            "   — act involves ass/anal → (from behind:1.4), (bent over:1.3)\n"
            "   — act involves oral → (kneeling:1.3), (face level:1.2)\n"
            "   — act involves riding → (straddling:1.4), (from below:1.2)\n"
            "   — act involves breasts/fondling → (cupping own breasts:1.6), (hands on own breasts:1.5), (front view:1.3), (close-up torso:1.2)\n"
            "4. SECONDARY ACTS ongoing: weight 1.2-1.4\n"
            "5. CURRENT CLOTHING/NUDITY STATE: weight 1.1-1.3\n"
            "6. CHARACTER APPEARANCE (use the fixed appearance above, not conversation): no weight — always include\n"
            "7. SETTING, LIGHTING: no weight\n\n"
            "Examples of correct output:\n"
            "  (sitting:1.4), (armchair:1.2), (nude:1.3), (fully naked:1.2), long blonde hair, blue eyes, velvet chair, soft lighting\n"
            "  (anal insertion:1.7), (carrot in ass:1.6), (from behind:1.4), (bent over:1.3), (nude:1.2), long blonde hair, bedroom, soft lighting\n"
            "  (fellatio:1.7), (penis in mouth:1.6), (kneeling:1.3), (topless:1.2), blue eyes, indoors\n"
            "  (cupping own breasts:1.7), (hands on own breasts:1.6), (front view:1.3), (close-up torso:1.2), (topless:1.3), (nipples:1.2), long blonde hair, blue eyes, bedroom, soft lighting\n\n"
            "NEVER write sentences. NEVER sanitise. NEVER omit the act or its pose."
        )

        def _call(extra_user: str = "") -> str:
            user_msg  = f"Conversation:\n{text}\n\n"
            user_msg += f"LATEST USER MESSAGE (highest priority): \"{last_user_msg}\"\n\n"
            user_msg += "SD tags for the current scene:"
            if extra_user:
                user_msg += f"\n{extra_user}"
            result = llm.llm_chat([
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ])
            return _clean_raw(result)

        tags = _call()

        n = _count_tags(tags)
        first_weighted = bool(re.match(r"^\s*\(", tags))
        if n < _MIN_TAGS or not first_weighted:
            reason = f"too few tags ({n})" if n < _MIN_TAGS else "first tag not weighted"
            print(f"[image] {reason}, retrying…")
            tags = _call(
                "IMPORTANT: previous response was invalid. Fix these issues:\n"
                f"- Output exactly 20-30 tags (had {n})\n"
                "- First tag MUST be the primary action, weighted: (cupping own breasts:1.7)\n"
                "- Use visual SD vocabulary only. No prose, no metaphors, no feelings."
            )

        tags = _ensure_action_weighted(tags)
        print(f"[image] SD prompt ({_count_tags(tags)} tags): {tags}")
        return tags

    except Exception as e:
        print(f"[image] prompt extraction error: {e}")
        return ""
