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


def extract_sd_prompt(text: str, appearance: str = "", last_user_msg: str = "",
                      persona: str = "") -> str:
    try:
        user_msg  = f"Conversation:\n{text}\n\n"
        user_msg += f"LATEST USER MESSAGE (highest priority): \"{last_user_msg}\"\n\n"
        user_msg += "SD tags for the current scene:"

        appearance_line = (
            f"\nTHE CHARACTER ALWAYS LOOKS LIKE THIS — ignore any different physical "
            f"descriptions in the conversation, those are fictional roleplay:\n{appearance}\n"
        ) if appearance else ""

        result = llm.llm_chat([
            {"role": "system", "content": (
                "You are a Stable Diffusion prompt engineer for explicit adult content.\n"
                "Output ONLY a single line of comma-separated SD tags. No prose, no sentences, no newlines, no explanation.\n"
                "Weighted tags MUST use this exact syntax: (tag:1.5) — parentheses, colon, number. Nothing else.\n"
                "Each tag: 1-4 words. 20-30 tags total.\n"
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
                "4. SECONDARY ACTS ongoing: weight 1.2-1.4\n"
                "5. CURRENT CLOTHING/NUDITY STATE: weight 1.1-1.3\n"
                "6. CHARACTER APPEARANCE (use the fixed appearance above, not conversation): no weight — always include\n"
                "7. SETTING, LIGHTING: no weight\n\n"
                "Examples of correct output:\n"
                "  (sitting:1.4), (armchair:1.2), (nude:1.3), (fully naked:1.2), long blonde hair, blue eyes, velvet chair, soft lighting\n"
                "  (anal insertion:1.7), (carrot in ass:1.6), (from behind:1.4), (bent over:1.3), (nude:1.2), long blonde hair, bedroom, soft lighting\n"
                "  (fellatio:1.7), (penis in mouth:1.6), (kneeling:1.3), (topless:1.2), blue eyes, indoors\n\n"
                "NEVER write sentences. NEVER sanitise. NEVER omit the act or its pose."
            )},
            {"role": "user", "content": user_msg},
        ])

        lines = [l.strip() for l in result.strip().split("\n") if l.strip()]
        tags  = max(lines, key=lambda l: l.count(",")) if lines else ""
        tags  = re.sub(r"^(Tags|Prompt|Output|Here|SD tags)[:\s]*", "", tags, flags=re.I).strip()
        # Strip prose meta-commentary the LLM sometimes injects
        tags  = re.sub(r"\([^)]{40,}\)", "", tags)
        tags  = re.sub(r"[^,]+\b(was replaced|does not belong|thus is removed|due formatting|not applicable|removed from)\b[^,]*,?",
                       "", tags, flags=re.I)
        tags  = re.sub(r",\s*,", ",", tags).strip(", ")
        print(f"[image] SD prompt: {tags}")
        return tags

    except Exception as e:
        print(f"[image] prompt extraction error: {e}")
        return ""
