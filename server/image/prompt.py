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
    r'\b(I|me|my\b|thee|thou|thy|down spine|tease shivers|'
    r'escaping|streaming through|weaving together)\b', re.I
)
# Matches verb phrases that indicate narrative prose rather than SD tags
_VERB_PHRASE_RE = re.compile(r'\b\w+(?:ed|ened|ened)\s+\w+\b', re.I)

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


_FIELDS = ["ACTION", "BODY", "CAMERA", "POSE", "NUDITY", "PROP", "EXTRA", "SETTING", "LIGHTING"]

# Nudity states ordered from least to most undressed; used for floor logic
_NUDITY_ORDER = ["clothed", "topless", "bottomless", "fully nude"]

# Normalise LLM nudity variants to canonical keys before map/floor lookup
_NUDITY_NORM = {
    "full nudity":        "fully nude",
    "full nude":          "fully nude",
    "completely nude":    "fully nude",
    "completely naked":   "fully nude",
    "naked":              "fully nude",
    "nude":               "fully nude",
    "no clothes":         "fully nude",
    "no clothing":        "fully nude",
    "bare":               "topless",
    "topless nude":       "topless",
    "half nude":          "topless",
    "bare breasts":       "topless",
    "exposed breasts":    "topless",
}

# Reject SETTING/LIGHTING values that are LLM meta-commentary rather than scene tags
_META_RE = re.compile(
    r'\b(not specified|no specification|unspecified|unknown|unclear|assumed|'
    r'n/?a|none given|not mentioned|not stated|not provided|not applicable)\b', re.I
)

# Pattern → (action_tags_list, body_tag, camera_hint, nudity_hint)
_ACTION_PATTERNS = [
    (r'\bcup(?:ping)?\b.{0,30}\bbreast',
        ["cupping breasts", "hands on breasts", "holding breasts", "breast grab"],
        "breasts", "face level", "topless"),
    (r'\bhold(?:ing)?\b.{0,30}\bbreast',
        ["holding breasts", "cupping breasts", "hands on breasts", "breast grab"],
        "breasts", "face level", "topless"),
    (r'\bsqueez\w*\b.{0,30}\bbreast',
        ["squeezing breasts", "hands on breasts", "breast grab"],
        "breasts", "face level", "topless"),
    (r'\btouch\w*\b.{0,30}\bbreast',
        ["hands on breasts", "cupping breasts"],
        "breasts", "face level", "topless"),
    (r'\bfinger\w*\b.{0,20}\b(?:mouth|lips|suck|lick|tongue)\b|\b(?:mouth|lips|suck|lick|tongue)\b.{0,20}\bfinger\w*\b',
        ["one finger in mouth", "index finger", "lips parted", "looking at viewer"],
        "mouth", "face level", None),
    (r'\bsuck\b|\bblowjob\b|\bfellatio\b',
        ["fellatio", "penis in mouth", "oral sex"],
        "mouth", "face level", "fully nude"),
    (r'\banal\b|\bbutt plug\b',
        ["anal insertion", "ass penetration"],
        "ass", "from behind", "fully nude"),
    (r'\bfinger\w*\b',
        ["fingering", "fingers in pussy"],
        "pussy", "from below", "fully nude"),
    (r'\bride\b|\briding\b|\bsit on\b',
        ["riding", "cowgirl position", "straddling"],
        "pussy", "from below", "fully nude"),
    (r'\bbend\w*\s+over\b',
        ["bent over", "doggy style position"],
        "ass", "from behind", None),
    (r'\bspread\w*\s+(?:(?:her|your|my)\s+)?legs?\b',
        ["spreading legs", "legs spread wide"],
        "pussy", "from below", "fully nude"),
    (r'\bkneel\b|\bkneeling\b',
        ["kneeling"],
        "body", "front view", None),
    (r'\bstroke\b|\bstroking\b',
        ["stroking penis", "handjob"],
        "cock", "front view", "fully nude"),
    (r'\bkiss\w*\b',
        ["kissing", "lips touching"],
        "lips", "face level", None),
    (r'\b(?:take off|remove|strip|undress)\b.{0,20}\b(?:clothes|clothing|outfit|dress|top|shirt|bra|panties|underwear|stockings|gown)\b',
        ["disrobing", "removing clothes", "nude", "naked"],
        "body", "front view", "fully nude"),
    (r'\bstrip\b|\bstripping\b|\bundress\b',
        ["disrobing", "removing clothes", "nude", "naked"],
        "body", "front view", "fully nude"),
]


def _detect_action(msg: str):
    """Return (actions_list, body, camera, nudity) from pattern match, or None if no match."""
    for entry in _ACTION_PATTERNS:
        pattern, actions, body, camera, nudity = entry[0], entry[1], entry[2], entry[3], entry[4]
        exclude = entry[5] if len(entry) > 5 else None
        if re.search(pattern, msg, re.I):
            if exclude and re.search(exclude, msg, re.I):
                continue
            return actions, body, camera, nudity
    return None

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


# Wearable accessories — pattern-matched from the user message and injected directly,
# bypassing the LLM (which only handles PROP for held/inserted objects).
_ACCESSORY_RE = [
    (r'\bglasses?\b|\bspectacles?\b|\breading glasses\b', "wearing glasses"),
    (r'\bsunglasses?\b',                                   "wearing sunglasses"),
    (r'\bhat\b|\bcap\b|\bberet\b',                         "wearing hat"),
    (r'\bchoker\b|\bcollar\b',                             "choker necklace"),
    (r'\bstockings?\b|\bthigh.?highs?\b',                  "thigh-high stockings"),
    (r'\bheels?\b|\bstiletto\w*\b',                        "high heels"),
    (r'\bmask\b',                                          "wearing mask"),
]


def _detect_accessories(msg: str) -> list:
    """Return SD accessory tags for any wearables explicitly mentioned in user message."""
    return [tag for pattern, tag in _ACCESSORY_RE if re.search(pattern, msg, re.I)]


_STOP_WORDS = re.compile(r'\b(with|the|a|an|of|in|on|at|and|or|but|from|into|by)\s*$', re.I)

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
                # Reject if LLM expressed uncertainty ("kneeling or bent over")
                if re.search(r'\bor\b', val, re.I):
                    break
                # Clamp to 4 words, then strip dangling stop words
                words = val.split()
                if len(words) > 4:
                    val = " ".join(words[:4])
                val = _STOP_WORDS.sub('', val).strip()
                # Reject if contains non-tag characters (apostrophes, quotes, etc.)
                if re.search(r"[^a-zA-Z0-9 \-]", val):
                    print(f"[image] dropped tag with special chars: {val!r}")
                    break
                # EXTRA: reject verb-phrase narrative ("grip tightened further")
                if field == "EXTRA" and _VERB_PHRASE_RE.search(val):
                    print(f"[image] dropped narrative EXTRA: {val!r}")
                    break
                # SETTING/LIGHTING: reject LLM meta-commentary placeholders
                if field in ("SETTING", "LIGHTING") and _META_RE.search(val):
                    print(f"[image] dropped meta-commentary {field}: {val!r}")
                    break
                # NUDITY: normalise LLM variants to canonical keys
                if field == "NUDITY":
                    val = _NUDITY_NORM.get(val.lower(), val)
                if val and not _PROSE_RE.search(val):
                    result[field] = val
                break
    return result


_CLOTHING_RE = re.compile(
    r"\b(dress|gown|robe|skirt|blouse|shirt|top|corset|bodice|stockings|lingerie|bra|underwear|panties|trousers|pants|shorts|linen|silk dress|lace|veil|outfit|clothes|clothing)\b",
    re.I
)

def _strip_distractions(text: str) -> str:
    """Remove non-visual personality words that confuse SD 1.5."""
    return re.sub(r"\b(elegant|poised|refined|sophisticated|mystical|ethereal|divine|regal|divine|sensual|timeless)\b,?\s*", "", text, flags=re.I).strip(", ")

def _strip_clothing(text: str) -> str:
    """Remove clothing words from an appearance string."""
    return _CLOTHING_RE.sub("", text).replace(", ,", ",").strip(", ")


def _build_tags(fields: dict, appearance: str, interaction_priority: bool = False) -> str:
    """Convert parsed structured fields into a weighted SD tag string."""
    tags = []
    
    def add(val, weight=None):
        val = val.strip().rstrip(".,")
        if not val or val.lower() == "none":
            return
        # Clean dangling conjunctions and common typos (kneeling, knelng, etc.)
        val = re.sub(r'\s+\b(and|with|of|the|then)\b$', '', val, flags=re.I)
        val = re.sub(r'\bkneal\w*\b', 'kneeling', val, flags=re.I)
        val = val.replace("knelng", "kneeling").replace("kneelng", "kneeling")
        tags.append(f"({val}:{weight})" if weight else val)

    # 1. Determine if we are undressed
    nudity_key = fields.get("NUDITY", "clothed").lower()
    is_nude = nudity_key in ("fully nude", "topless", "bottomless")
    
    # 2. Clean appearance string
    clean_app = _strip_distractions(appearance)
    if is_nude:
        clean_app = _strip_clothing(clean_app)

    # 3. Handle Pose Conflicts
    # If ACTION contains a specific pose, it must override the POSE field
    action_val = fields.get("ACTION", "")
    action_text = " ".join(action_val) if isinstance(action_val, list) else str(action_val)
    pose = fields.get("POSE", "standing")
    
    # If we are kneeling/sitting in action, we cannot be standing in pose
    if any(p in action_text.lower() for p in ("kneel", "sit", "lie", "bent over", "spread")):
        pose = pose.replace("standing", "").strip(", ")
    
    # Primary action
    action_list = action_val if isinstance(action_val, list) else ([action_val] if action_val else [])
    action_weights = [1.7, 1.6, 1.5, 1.4]
    for act, w in zip(action_list, action_weights):
        add(act, w)
    
    body = fields.get("BODY", "")
    if body and not any(body.lower() in a.lower() for a in action_list):
        add(body, 1.3)

    # Camera angle
    camera_raw = fields.get("CAMERA", "front view").lower()
    camera_tags = _CAMERA_MAP.get(camera_raw, [camera_raw])
    weights = [1.4, 1.3]
    for ct, w in zip(camera_tags, weights):
        add(ct, w)

    # Pose (now conflict-cleaned)
    if pose:
        add(pose, 1.3)

    # Nudity
    nudity_tags = _NUDITY_MAP.get(nudity_key, [nudity_key] if nudity_key != "clothed" else [])
    for nt in nudity_tags:
        add(nt, 1.2)

    # Prop
    prop = fields.get("PROP", "").strip().lower()
    if prop and prop != "none":
        add(prop, 1.4)

    # Accessories
    for acc in fields.get("ACCESSORIES", []):
        add(acc, 1.3)

    # Extra secondary detail
    extra = fields.get("EXTRA", "")
    if extra:
        add(extra, 1.1)

    # Setting / lighting
    for key in ("SETTING", "LIGHTING"):
        val = fields.get(key, "")
        if val:
            tags.append(val)

    # Assemble final prompt. 
    joined_tags = ", ".join(tags)
    if clean_app:
        if interaction_priority:
            # Use a regex to split by commas that are NOT inside parentheses
            app_parts = [p.strip() for p in re.split(r',(?![^()]*\))', clean_app) if p.strip()]
            count_tags = []
            # Extract first few tags if they look like count/group tags
            while app_parts and (re.search(r"\d+girls?|group scene", app_parts[0], re.I) or len(count_tags) < 2):
                count_tags.append(app_parts.pop(0))
            
            remaining_app = ", ".join(app_parts)
            return f"{', '.join(count_tags)}, {joined_tags}, {remaining_app}"
        else:
            return f"{clean_app}, {joined_tags}"
    return joined_tags


def extract_sd_prompt(text: str, appearance: str = "", last_user_msg: str = "",
                      persona: str = "", nudity_floor: str = "clothed",
                      interaction_priority: bool = False,
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
            "PROP: any held or inserted object from USER message (banana / dildo / none)\n"
            "      Use 'none' if no object. 1-4 words.\n"
            "EXTRA: one secondary visual detail (nipples visible / hands on hips / etc.)\n"
            "SETTING: one word (bedroom / outdoors / forest / etc.)\n"
            "LIGHTING: two words (soft lighting / moonlight / candlelight / etc.)\n\n"
            "IMPORTANT for GROUP scenes: You MUST describe the physical positioning and "
            "appearance of EACH persona separately. Ensure they are distinct individuals. "
            "For example: 'Alice is [pose1], Morrigan is [pose2]'.\n\n"
            f"{persona}\n\n"
            f"{appearance_hint}\n\n"
            "Example — user said 'stuff a banana in your pussy':\n"
            "ACTION: vaginal insertion\n"
            "BODY: pussy\n"
            "CAMERA: from below\n"
            "POSE: kneeling\n"
            "NUDITY: fully nude\n"
            "PROP: banana\n"
            "EXTRA: legs spread\n"
            "SETTING: bedroom\n"
            "LIGHTING: soft lighting\n\n"
            "Example — user said 'cup your breasts':\n"
            "ACTION: cupping own breasts\n"
            "BODY: breasts\n"
            "CAMERA: front view\n"
            "POSE: standing\n"
            "NUDITY: topless\n"
            "PROP: none\n"
            "EXTRA: nipples visible\n"
            "SETTING: bedroom\n"
            "LIGHTING: soft lighting"
        )

        floor_hint = (
            f"\nCURRENT NUDITY FLOOR: {nudity_floor} — she is at least this undressed already; "
            f"NUDITY must be '{nudity_floor}' or more exposed."
            if nudity_floor != "clothed" else ""
        )
        user_msg = (
            f"Conversation:\n{text}\n\n"
            f"LATEST USER MESSAGE: \"{last_user_msg}\"\n"
            f"{floor_hint}\n"
            "Fill in the nine fields above for the current scene:"
        )

        raw = llm.llm_chat([
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": user_msg},
        ])

        fields = _parse_template(raw)

        # Detect wearable accessories from user message — LLM only handles held props
        accessories = _detect_accessories(last_user_msg)
        if accessories:
            fields["ACCESSORIES"] = accessories
            print(f"[image] accessories detected: {accessories}")

        # Override ACTION/BODY/CAMERA/NUDITY with pattern match from user command
        detected = _detect_action(last_user_msg)
        if detected:
            action, body, camera, nudity = detected
            fields["ACTION"] = action
            fields.setdefault("BODY",   body)
            fields.setdefault("CAMERA", camera)
            if nudity:
                fields["NUDITY"] = nudity
            print(f"[image] action detected from user msg: {action!r}, nudity={nudity}")
        elif "ACTION" not in fields:
            print("[image] no ACTION field, retrying…")
            raw = llm.llm_chat([
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg + "\nIMPORTANT: you MUST output the ACTION field first."},
            ])
            fields = _parse_template(raw)

        # Pose cleanup: if they are kneeling or sitting, they are not standing.
        pose = fields.get("POSE", "").lower()
        if any(p in pose for p in ("kneeling", "sitting", "lying", "bent over")):
            fields["POSE"] = pose.replace("standing", "").strip(", ")

        # Apply nudity floor — never go less undressed than the current session state
        nudity_key = fields.get("NUDITY", "clothed").lower()
        # ... nudity floor logic ...
        try:
            floor_idx   = _NUDITY_ORDER.index(nudity_floor.lower())
            current_idx = _NUDITY_ORDER.index(nudity_key)
            if current_idx < floor_idx:
                print(f"[image] nudity floor: {nudity_key!r} → {nudity_floor!r}")
                fields["NUDITY"] = nudity_floor
                nudity_key = nudity_floor
        except ValueError:
            pass

        # In group mode, names are noise. Remove "[Name], " from appearance blocks.
        if interaction_priority and names:
            # Build a dynamic regex from the list of names
            name_pattern = "|".join(re.escape(n) for n in names)
            # 1. Remove names at the start of parenthetical blocks: "(Alice, blonde..." -> "(blonde..."
            appearance = re.sub(rf'\(\s*(?:{name_pattern}),\s*', '(', appearance, flags=re.I)
            # 2. Remove standalone names followed by commas: "Alice, blonde..." -> "blonde..."
            appearance = re.sub(rf'\b(?:{name_pattern})\b,\s*', '', appearance, flags=re.I)

        print(f"[image] scene fields: {fields}")

        tags = _build_tags(fields, appearance, interaction_priority=interaction_priority)
        print(f"[image] SD prompt: {tags}")
        return tags, fields.get("NUDITY", "clothed")

    except Exception as e:
        print(f"[image] prompt extraction error: {e}")
        return "", nudity_floor
