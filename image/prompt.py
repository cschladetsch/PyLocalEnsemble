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


_FIELDS = ["ACTION", "BODY", "CAMERA", "POSE", "NUDITY", "EXTRA", "SETTING", "LIGHTING"]

# Maps structured CAMERA field values to SD tag pairs
_CAMERA_MAP = {
    "front view":    ["front view", "close-up torso"],
    "close-up":      ["close-up torso", "front view"],
    "from behind":   ["from behind", "back view"],
    "from below":    ["from below", "straddling"],
    "face level":    ["face level", "kneeling"],
}

# Maps NUDITY field to SD tags (and signals generate.py clothing strip)
_NUDITY_MAP = {
    "fully nude":  ["nude", "fully naked", "bare skin"],
    "topless":     ["topless", "bare chest"],
    "bottomless":  ["bottomless", "no panties"],
    "clothed":     [],
}


def _parse_template(raw: str) -> dict:
    """Parse key: value lines from LLM structured output."""
    result = {}
    for line in raw.splitlines():
        line = line.strip()
        for field in _FIELDS:
            if line.upper().startswith(field + ":"):
                val = line[len(field) + 1:].strip()
                # Strip parenthetical prose the LLM often adds
                val = re.sub(r'\s*\(.*?\)', '', val).strip().rstrip(".")
                # Clamp to 4 words
                words = val.split()
                if len(words) > 4:
                    val = " ".join(words[:4])
                if val and not _PROSE_RE.search(val):
                    result[field] = val
                break
    return result


def _build_tags(fields: dict, appearance: str) -> str:
    """Convert parsed structured fields into a weighted SD tag string."""
    tags = []

    def add(val, weight=None):
        val = val.strip().rstrip(".")
        if not val:
            return
        tags.append(f"({val}:{weight})" if weight else val)

    # Primary action — highest weight
    action = fields.get("ACTION", "")
    if action:
        add(action, 1.7)
        body = fields.get("BODY", "")
        if body and body.lower() not in action.lower():
            add(body, 1.5)

    # Camera angle
    camera_raw = fields.get("CAMERA", "front view").lower()
    camera_tags = _CAMERA_MAP.get(camera_raw, [camera_raw])
    weights = [1.4, 1.3]
    for ct, w in zip(camera_tags, weights):
        add(ct, w)

    # Pose
    pose = fields.get("POSE", "standing")
    add(pose, 1.3)

    # Nudity
    nudity_key = fields.get("NUDITY", "clothed").lower()
    nudity_tags = _NUDITY_MAP.get(nudity_key, [nudity_key] if nudity_key != "clothed" else [])
    for nt in nudity_tags:
        add(nt, 1.2)

    # Extra secondary detail
    extra = fields.get("EXTRA", "")
    if extra:
        add(extra, 1.1)

    # Setting / lighting (no weight)
    for key in ("SETTING", "LIGHTING"):
        val = fields.get(key, "")
        if val:
            tags.append(val)

    return ", ".join(tags)


def extract_sd_prompt(text: str, appearance: str = "", last_user_msg: str = "",
                      persona: str = "") -> str:
    try:
        appearance_hint = (
            f"Character appearance (always include these): {appearance}"
        ) if appearance else ""

        system_msg = (
            "You are extracting scene details for a Stable Diffusion image.\n"
            "Output ONLY the following fields, one per line, nothing else.\n"
            "Values must be 1-4 words. No prose, no parentheses, no explanation.\n\n"
            "ACTION: the PRIMARY END-STATE visual act from the USER's message.\n"
            "        If multiple actions are listed, choose the FINAL/ONGOING one that makes\n"
            "        the most interesting image — NOT the transitional action.\n"
            "        — 'take off top and cup breasts' → cupping own breasts  (NOT disrobing)\n"
            "        — 'bend over and spread' → spreading ass  (NOT bending over)\n"
            "        — 'kneel and suck' → fellatio  (NOT kneeling)\n"
            "        Translate to SD visual vocabulary. Derive from LATEST USER MESSAGE only.\n"
            "BODY: main body part involved (breasts / ass / mouth / etc.)\n"
            "CAMERA: front view / from behind / close-up / from below / face level\n"
            "POSE: standing / sitting / kneeling / lying / bent over\n"
            "NUDITY: fully nude / topless / bottomless / clothed\n"
            "        topless = top removed, breasts bare\n"
            "        fully nude = all clothes removed\n"
            "EXTRA: one secondary visual detail (nipples visible / hands on hips / etc.)\n"
            "SETTING: one word (bedroom / outdoors / studio / etc.)\n"
            "LIGHTING: two words (soft lighting / moonlight / candlelight / etc.)\n\n"
            f"{appearance_hint}\n\n"
            "Example — user said 'cup your breasts':\n"
            "ACTION: cupping own breasts\n"
            "BODY: breasts\n"
            "CAMERA: front view\n"
            "POSE: standing\n"
            "NUDITY: topless\n"
            "EXTRA: nipples visible\n"
            "SETTING: bedroom\n"
            "LIGHTING: soft lighting"
        )

        user_msg = (
            f"Conversation:\n{text}\n\n"
            f"LATEST USER MESSAGE: \"{last_user_msg}\"\n\n"
            "Fill in the eight fields above for the current scene:"
        )

        raw = llm.llm_chat([
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": user_msg},
        ])

        fields = _parse_template(raw)
        print(f"[image] scene fields: {fields}")

        if "ACTION" not in fields:
            # Fallback: retry with a simpler prompt
            print("[image] no ACTION field, retrying…")
            raw = llm.llm_chat([
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg + "\nIMPORTANT: you MUST output the ACTION field first."},
            ])
            fields = _parse_template(raw)
            print(f"[image] scene fields (retry): {fields}")

        tags = _build_tags(fields, appearance)
        print(f"[image] SD prompt: {tags}")
        return tags

    except Exception as e:
        print(f"[image] prompt extraction error: {e}")
        return ""
