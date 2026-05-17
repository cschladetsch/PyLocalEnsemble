"""SD prompt engineering: tag utilities and LLM-based prompt extraction."""
import re
import llm


def clean_tags(prompt: str) -> str:
    """Deduplicates tags, preserving SD weighting syntax. Weighted form wins over unweighted."""
    tags = [t.strip() for t in re.split(r',(?![^()]*\))', prompt)]

    def _norm(tag):
        bare = re.sub(r"^\((.+?):[0-9.]+\)$", r"\1", tag)
        return re.sub(r"[^a-z0-9 ]", "", bare.lower()).strip()

    def _is_weighted(tag):
        return bool(re.match(r"^\(.+:[0-9.]+\)$", tag.strip()))

    best = {}
    for t in tags:
        if not t: continue
        n = _norm(t)
        if not n: continue
        if n not in best or _is_weighted(t):
            best[n] = t

    seen, out = set(), []
    for t in tags:
        if not t: continue
        n = _norm(t)
        if n and n not in seen:
            seen.add(n)
            out.append(best[n])
    return ", ".join(out)


def apply_exposure_rules(text: str, prompt: str, negative: str) -> tuple:
    """Handles camera/POV injection based on conversation context."""
    t = text.lower()

    def has(words):
        return any(re.search(rf"\b{re.escape(w)}\b", t) for w in words)

    if has(["pov", "first person", "my perspective"]):
        prompt = "(pov:1.3), (first person view:1.2), " + prompt
    if has(["from behind", "back view", "backside", "rear view"]):
        prompt = "(from behind:1.3), (back view:1.2), " + prompt

    if re.search(r'\b(eyes?\s+closed|shut\s+(her\s+)?eyes?|close\s+(her\s+)?eyes?)\b', t):
        prompt = "(closed eyes:1.4), " + prompt
    if re.search(r'\b(mouth\s+open|open\s+(her\s+)?mouth|tongue\s+out)\b', t):
        prompt = "(open mouth:1.3), " + prompt
    if re.search(r'\b(on\s+(her\s+)?knees|kneel(s|ing)?)\b', t):
        if "kneeling" not in prompt:
            prompt = "(kneeling:1.3), " + prompt

    return prompt, negative


_PROSE_RE = re.compile(
    r'\b(I|me|my\b|thee|thou|thy|down spine|tease shivers|'
    r'escaping|streaming through|weaving together)\b', re.I
)
_VERB_PHRASE_RE = re.compile(r'\b\w+(?:ed|ened|ened)\s+\w+\b', re.I)

_WEIGHTS = [1.7, 1.6, 1.4, 1.3, 1.2, 1.1]


def _apply_weights(plain_tags: list) -> list:
    out = []
    for i, t in enumerate(plain_tags):
        if i < len(_WEIGHTS):
            out.append(f"({t}:{_WEIGHTS[i]})")
        else:
            out.append(t)
    return out


def _sanitize_tags(raw_tags: str) -> list:
    seen, out = set(), []
    for t in raw_tags.split(","):
        t = t.strip()
        if not t:
            continue
        t = re.sub(r"^\((.+?):[0-9.]+\)$", r"\1", t).strip()
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
    lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
    best  = max(lines, key=lambda l: l.count(",")) if lines else ""
    best  = re.sub(r"^(Tags|Prompt|Output|Here|SD tags)[:\s]*", "", best, flags=re.I).strip()
    best  = re.sub(r"[^,]+\b(was replaced|does not belong|thus is removed|due formatting|not applicable|removed from)\b[^,]*,?",
                   "", best, flags=re.I)
    return _sanitize_tags(best)


_FIELDS = ["ACTION", "BODY", "CAMERA", "POSE", "PROP", "EXTRA", "SETTING", "LIGHTING"]

_META_RE = re.compile(
    r'\b(not specified|no specification|unspecified|unknown|unclear|assumed|'
    r'n/?a|none given|not mentioned|not stated|not provided|not applicable)\b', re.I
)

_ACTION_PATTERNS = [
    (r'\bkiss\w*\b',
        ["kissing", "lips touching"],
        "lips", "face level"),
    (r'\bkneel\b|\bkneeling\b',
        ["kneeling"],
        "body", "front view"),
    (r'\bbend\w*\s+over\b',
        ["bent over"],
        "body", "from behind"),
    (r'\bfrom behind\b',
        ["from behind"],
        "body", "from behind"),
]


def _detect_action(msg: str):
    """Return (actions_list, body, camera) from pattern match, or None if no match."""
    for entry in _ACTION_PATTERNS:
        pattern, actions, body, camera = entry[0], entry[1], entry[2], entry[3]
        if re.search(pattern, msg, re.I):
            return actions, body, camera
    return None


_CAMERA_MAP = {
    "front view":    ["front view", "close-up torso"],
    "close-up":      ["close-up torso", "front view"],
    "from behind":   ["from behind", "back view"],
    "from below":    ["from below"],
    "face level":    ["face level"],
}

_ACCESSORY_RE = [
    (r'\bglasses?\b|\bspectacles?\b|\breading glasses\b', "wearing glasses"),
    (r'\bsunglasses?\b',                                   "wearing sunglasses"),
    (r'\bhat\b|\bcap\b|\bberet\b',                         "wearing hat"),
    (r'\bchoker\b|\bcollar\b',                             "choker necklace"),
    (r'\bscarf\b',                                         "wearing scarf"),
    (r'\bgloves?\b',                                       "wearing gloves"),
    (r'\bmask\b',                                          "wearing mask"),
]


def _detect_accessories(msg: str) -> list:
    return [tag for pattern, tag in _ACCESSORY_RE if re.search(pattern, msg, re.I)]


_STOP_WORDS = re.compile(r'\b(with|the|a|an|of|in|on|at|and|or|but|from|into|by)\s*$', re.I)


def _parse_template(raw: str) -> dict:
    result = {}
    for line in raw.splitlines():
        line = line.strip()
        for field in _FIELDS:
            if line.upper().startswith(field + ":"):
                val = line[len(field) + 1:].strip()
                val = re.sub(r'\s*\(.*?\)', '', val).strip().rstrip(".")
                if re.search(r'\bor\b', val, re.I):
                    break
                words = val.split()
                if len(words) > 4:
                    val = " ".join(words[:4])
                val = _STOP_WORDS.sub('', val).strip()
                if re.search(r"[^a-zA-Z0-9 \-]", val):
                    print(f"[image] dropped tag with special chars: {val!r}")
                    break
                if field == "EXTRA" and _VERB_PHRASE_RE.search(val):
                    print(f"[image] dropped narrative EXTRA: {val!r}")
                    break
                if field in ("SETTING", "LIGHTING") and _META_RE.search(val):
                    print(f"[image] dropped meta-commentary {field}: {val!r}")
                    break
                if val and not _PROSE_RE.search(val):
                    result[field] = val
                break
    return result


def _strip_distractions(text: str) -> str:
    return re.sub(r"\b(elegant|poised|refined|sophisticated|mystical|ethereal|divine|regal|sensual|timeless)\b,?\s*", "", text, flags=re.I).strip(", ")


def _build_tags(fields: dict, appearance: str, interaction_priority: bool = False) -> str:
    tags = []

    def add(val, weight=None):
        val = val.strip().rstrip(".,")
        if not val or val.lower() == "none":
            return
        val = re.sub(r'\s+\b(and|with|of|the|then)\b$', '', val, flags=re.I)
        val = re.sub(r'\bkneal\w*\b', 'kneeling', val, flags=re.I)
        val = val.replace("knelng", "kneeling").replace("kneelng", "kneeling")
        tags.append(f"({val}:{weight})" if weight else val)

    clean_app = _strip_distractions(appearance)

    action_val = fields.get("ACTION", "")
    action_text = " ".join(action_val) if isinstance(action_val, list) else str(action_val)
    pose = fields.get("POSE", "standing")

    if any(p in action_text.lower() for p in ("kneel", "sit", "lie", "bent over")):
        pose = pose.replace("standing", "").strip(", ")

    action_list = action_val if isinstance(action_val, list) else ([action_val] if action_val else [])
    action_weights = [1.7, 1.6, 1.5, 1.4]
    for act, w in zip(action_list, action_weights):
        add(act, w)

    body = fields.get("BODY", "")
    if body and not any(body.lower() in a.lower() for a in action_list):
        add(body, 1.3)

    camera_raw = fields.get("CAMERA", "front view").lower()
    camera_tags = _CAMERA_MAP.get(camera_raw, [camera_raw])
    weights = [1.4, 1.3]
    for ct, w in zip(camera_tags, weights):
        add(ct, w)

    if pose:
        add(pose, 1.3)

    prop = fields.get("PROP", "").strip().lower()
    if prop and prop != "none":
        add(prop, 1.4)

    for acc in fields.get("ACCESSORIES", []):
        add(acc, 1.3)

    extra = fields.get("EXTRA", "")
    if extra:
        add(extra, 1.1)

    for key in ("SETTING", "LIGHTING"):
        val = fields.get(key, "")
        if val:
            tags.append(val)

    joined_tags = ", ".join(tags)
    if clean_app:
        if interaction_priority:
            app_parts = [p.strip() for p in re.split(r',(?![^()]*\))', clean_app) if p.strip()]
            count_tags = []
            while app_parts and (re.search(r"\d+girls?|group scene", app_parts[0], re.I) or len(count_tags) < 2):
                count_tags.append(app_parts.pop(0))
            remaining_app = ", ".join(app_parts)
            return f"{', '.join(count_tags)}, {joined_tags}, {remaining_app}"
        else:
            return f"{clean_app}, {joined_tags}"
    return joined_tags


def extract_sd_prompt(text: str, appearance: str = "", last_user_msg: str = "",
                      persona: str = "", interaction_priority: bool = False,
                      names: list[str] = None) -> tuple:
    try:
        appearance_hint = (
            f"Character appearance (always include these): {appearance}"
        ) if appearance else ""

        system_msg = (
            "You are extracting scene details for a Stable Diffusion image.\n"
            "Output ONLY the following fields, one per line, nothing else.\n"
            "Values must be 1-4 words. No prose, no parentheses, no explanation.\n\n"
            "ACTION: the PRIMARY END-STATE visual act from the USER's message.\n"
            "        Translate to SD visual vocabulary. Derive from LATEST USER MESSAGE only.\n"
            "BODY: main body part involved (hands / face / torso / etc.)\n"
            "CAMERA: front view / from behind / close-up / from below / face level\n"
            "POSE: standing / sitting / kneeling / lying / bent over\n"
            "PROP: any held object from USER message (book / sword / cup / none)\n"
            "      Use 'none' if no object. 1-4 words.\n"
            "EXTRA: one secondary visual detail (hands on hips / looking away / etc.)\n"
            "SETTING: one word (bedroom / outdoors / forest / library / etc.)\n"
            "LIGHTING: two words (soft lighting / moonlight / candlelight / etc.)\n\n"
            "IMPORTANT for GROUP scenes: describe the physical positioning and "
            "appearance of EACH persona separately as distinct individuals.\n\n"
            f"{persona}\n\n"
            f"{appearance_hint}\n\n"
            "Example — user said 'sit by the window and read':\n"
            "ACTION: reading book\n"
            "BODY: hands\n"
            "CAMERA: front view\n"
            "POSE: sitting\n"
            "PROP: book\n"
            "EXTRA: looking down\n"
            "SETTING: indoors\n"
            "LIGHTING: natural light\n\n"
            "Example — user said 'walk through the forest':\n"
            "ACTION: walking\n"
            "BODY: body\n"
            "CAMERA: front view\n"
            "POSE: standing\n"
            "PROP: none\n"
            "EXTRA: trees in background\n"
            "SETTING: forest\n"
            "LIGHTING: dappled light"
        )

        user_msg = (
            f"Conversation:\n{text}\n\n"
            f"LATEST USER MESSAGE: \"{last_user_msg}\"\n"
            "Fill in the eight fields above for the current scene:"
        )

        raw = llm.llm_chat_deferred([
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": user_msg},
        ], label="SD extraction")

        fields = _parse_template(raw)

        accessories = _detect_accessories(last_user_msg)
        if accessories:
            fields["ACCESSORIES"] = accessories
            print(f"[image] accessories detected: {accessories}")

        detected = _detect_action(last_user_msg)
        if detected:
            action, body, camera = detected
            fields["ACTION"] = action
            fields.setdefault("BODY",   body)
            fields.setdefault("CAMERA", camera)
            print(f"[image] action detected from user msg: {action!r}")
        elif "ACTION" not in fields:
            print("[image] no ACTION field, retrying…")
            raw = llm.llm_chat_deferred([
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg + "\nIMPORTANT: you MUST output the ACTION field first."},
            ], label="SD extraction retry")
            fields = _parse_template(raw)

        pose = fields.get("POSE", "").lower()
        if any(p in pose for p in ("kneeling", "sitting", "lying", "bent over")):
            fields["POSE"] = pose.replace("standing", "").strip(", ")

        if interaction_priority and names:
            name_pattern = "|".join(re.escape(n) for n in names)
            appearance = re.sub(rf'\(\s*(?:{name_pattern}),\s*', '(', appearance, flags=re.I)
            appearance = re.sub(rf'\b(?:{name_pattern})\b,\s*', '', appearance, flags=re.I)

        print(f"[image] scene fields: {fields}")

        tags = _build_tags(fields, appearance, interaction_priority=interaction_priority)
        print(f"[image] SD prompt: {tags}")
        return tags, "clothed"

    except Exception as e:
        print(f"[image] prompt extraction error: {e}")
        return "", "clothed"
