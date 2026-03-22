"""Group chat — multiple personas sharing a conversation, with async inter-persona chatter."""
import asyncio, json, os, random, re
from collections import Counter
import queue as _queue
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import requests as req

import config, llm, state

router = APIRouter()

# ── Module-level state ─────────────────────────────────────────────────────────
_active         = False
_personas: dict = {}   # key → persona config dict
_history: list  = []   # [{role, sender, persona, content, to, _internal?}]
_pair_histories: dict = {}  # pair_key → list of entries (per persona-pair chatter)
_listeners: list = []  # asyncio.Queue per SSE subscriber
_chatter_task: asyncio.Task | None = None
_chatter_wake: asyncio.Event | None = None
_last_chatter_sender: str | None = None
_pair_memos:   dict = {}  # pair_key → relationship summary string
_persona_moods: dict = {}  # persona_key → current emotional disposition string
_compressing:  set  = set()  # pair_keys currently being compressed (re-entry guard)

_DATA_DIR    = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_GROWTH_FILE = os.path.join(_DATA_DIR, "group_growth.json")


# ── Per-pair history helpers ───────────────────────────────────────────────────

def _pair_key(a: str, b: str) -> str:
    """Canonical sorted key for a persona pair, e.g. 'alice|morrigan'."""
    return "|".join(sorted([a.lower(), b.lower()]))


def _pair_file(key: str) -> str:
    safe = key.replace("|", "_to_")
    return os.path.join(_DATA_DIR, f"history_group_{safe}.json")


def _load_pair(key: str) -> list:
    try:
        with open(_pair_file(key)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_pair(key: str):
    try:
        with open(_pair_file(key), "w") as f:
            json.dump(_pair_histories.get(key, []), f, indent=2)
    except Exception as e:
        print(f"[group] failed to save pair history {key}: {e}")


def _add_to_pair(key: str, entry: dict):
    import threading
    if key not in _pair_histories:
        _pair_histories[key] = []
    _pair_histories[key].append(entry)
    if len(_pair_histories[key]) > 20 and key not in _compressing:
        _compressing.add(key)
        def _do():
            try:
                _compress_pair(key)
            finally:
                _compressing.discard(key)
        threading.Thread(target=_do, daemon=True).start()


def _resolve_persona_key(name_or_key: str | None) -> str | None:
    if not name_or_key or name_or_key == "all":
        return None
    if name_or_key in _personas:
        return name_or_key
    lowered = name_or_key.lower()
    for key, persona in _personas.items():
        if persona.get("name", key).lower() == lowered:
            return key
    return None


def _record_pair_history(entry: dict):
    """Fan out group events into per-pair histories so relationships stay current."""
    keys = list(_personas.keys())
    if len(keys) < 2:
        return

    if entry.get("role") == "persona":
        sender_key = entry.get("persona")
        if not sender_key:
            return
        target_key = _resolve_persona_key(entry.get("to"))
        if target_key:
            _add_to_pair(_pair_key(sender_key, target_key), entry)
            return
        for other_key in keys:
            if other_key != sender_key:
                _add_to_pair(_pair_key(sender_key, other_key), entry)
        return

    if entry.get("role") == "user":
        target_key = _resolve_persona_key(entry.get("to"))
        if target_key:
            for other_key in keys:
                if other_key != target_key:
                    _add_to_pair(_pair_key(target_key, other_key), entry)
            return
        for i, key1 in enumerate(keys):
            for key2 in keys[i + 1:]:
                _add_to_pair(_pair_key(key1, key2), entry)


def _broadcast(event: dict):
    payload = json.dumps(event)
    dead = []
    for q in _listeners:
        try:
            q.put_nowait(payload)
        except Exception:
            dead.append(q)
    for q in dead:
        try: _listeners.remove(q)
        except ValueError: pass


# ── Growth: relationship memos + persona moods ─────────────────────────────────

def _save_growth():
    try:
        with open(_GROWTH_FILE, "w") as f:
            json.dump({"memos": _pair_memos, "moods": _persona_moods}, f, indent=2)
    except Exception as e:
        print(f"[group] failed to save growth state: {e}")


def _load_growth():
    try:
        if os.path.exists(_GROWTH_FILE):
            with open(_GROWTH_FILE) as f:
                data = json.load(f)
            _pair_memos.update(data.get("memos", {}))
            _persona_moods.update(data.get("moods", {}))
    except Exception as e:
        print(f"[group] failed to load growth state: {e}")


def _compress_pair(key: str):
    """Summarise old pair history → update relationship memo + both persona moods."""
    pair_hist = _pair_histories.get(key, [])
    if len(pair_hist) <= 16:
        return

    old = pair_hist[:-8]
    _pair_histories[key] = pair_hist[-8:]

    # Resolve the two persona names from the sorted key parts
    parts = key.split("|")
    def _name_for(part: str) -> tuple[str, str]:
        """Return (persona_key, display_name) matching this key part."""
        for pk, p in _personas.items():
            if pk.lower() == part:
                return pk, p.get("name", pk)
        return part, part.title()

    key_a, name_a = _name_for(parts[0])
    key_b, name_b = _name_for(parts[1])

    text = "\n".join(
        f"{e['sender']}: {e['content']}"
        for e in old if e.get("content") and e.get("role") != "user"
    )
    if not text.strip():
        return

    prompt = (
        f"Track the evolving relationship between {name_a} and {name_b} "
        f"based on the exchange below.\n\n"
        f"Reply in EXACTLY this format (no extra lines):\n"
        f"RELATIONSHIP: [2 sentences — what happened, emotional tone, dynamic shifts]\n"
        f"{name_a.upper()}_MOOD: [1 sentence — {name_a}'s current disposition and desires]\n"
        f"{name_b.upper()}_MOOD: [1 sentence — {name_b}'s current disposition and desires]\n\n"
        f"Exchange:\n{text}"
    )
    try:
        raw = llm.llm_chat([
            {"role": "system", "content": (
                "You are a relationship tracker for fictional characters. "
                "Be specific, emotionally precise, and concise. No purple prose."
            )},
            {"role": "user", "content": prompt},
        ]).strip()

        rel_match   = re.search(r'RELATIONSHIP:\s*(.+?)(?=\n[A-Z_]+:|$)', raw, re.DOTALL | re.IGNORECASE)
        mood_a_pat  = re.escape(name_a.upper()) + r'_MOOD:\s*(.+?)(?=\n[A-Z_]+:|$)'
        mood_b_pat  = re.escape(name_b.upper()) + r'_MOOD:\s*(.+?)(?=\n[A-Z_]+:|$)'
        mood_a_match = re.search(mood_a_pat, raw, re.DOTALL | re.IGNORECASE)
        mood_b_match = re.search(mood_b_pat, raw, re.DOTALL | re.IGNORECASE)

        if rel_match:
            new_memo = rel_match.group(1).strip()
            existing = _pair_memos.get(key, "")
            _pair_memos[key] = (existing + " " + new_memo).strip() if existing else new_memo
            # Cap memo length to avoid bloat
            if len(_pair_memos[key]) > 600:
                _pair_memos[key] = _pair_memos[key][-600:]
                cut = _pair_memos[key].find(". ")
                if cut != -1:
                    _pair_memos[key] = _pair_memos[key][cut + 2:]

        if mood_a_match and key_a in _personas:
            _persona_moods[key_a] = mood_a_match.group(1).strip()
        if mood_b_match and key_b in _personas:
            _persona_moods[key_b] = mood_b_match.group(1).strip()

        print(f"[group] compressed pair {key}: memo updated, moods refreshed")
        _save_growth()
        _save_pair(key)
    except Exception as e:
        print(f"[group] pair compression failed for {key}: {e}")


# ── Repetition-suppression helpers ────────────────────────────────────────────

def _overused_phrases(entries: list, top_n: int = 8, ngram: int = 4) -> list[str]:
    """Return n-grams that appear 2+ times across recent history entries."""
    texts = [e["content"] for e in entries if e.get("content")]
    counts: Counter = Counter()
    for text in texts:
        words = re.findall(r"[a-z']+", text.lower())
        for i in range(len(words) - ngram + 1):
            counts[" ".join(words[i:i + ngram])] += 1
    return [p for p, n in counts.most_common(top_n) if n >= 2]


def _dedupe_entries(entries: list, max_consecutive: int = 2) -> list:
    """Drop excess consecutive same-sender turns and near-duplicate content."""
    out: list = []
    streak_sender: str | None = None
    streak_count = 0
    for entry in entries:
        role   = entry.get("role", "")
        sender = entry.get("sender", "")
        if role == "user":
            streak_sender = None
            streak_count  = 0
            out.append(entry)
            continue
        if sender == streak_sender:
            streak_count += 1
        else:
            streak_sender = sender
            streak_count  = 1
        if streak_count > max_consecutive:
            continue
        # Jaccard similarity vs the last entry from the same persona — skip near-dupes
        prev = next((e for e in reversed(out) if e.get("sender") == sender), None)
        if prev:
            a = set(re.findall(r"[a-z]+", prev.get("content", "").lower()))
            b = set(re.findall(r"[a-z]+", entry.get("content", "").lower()))
            if a and b and len(a & b) / len(a | b) > 0.65:
                continue
        out.append(entry)
    return out


# ── Message history helpers ────────────────────────────────────────────────────

def _build_response_messages(persona_key: str) -> list[dict]:
    """Build LLM message list for a persona responding to the current group history."""
    p = _personas.get(persona_key, {})
    persona_name = p.get("name", persona_key)
    persona_gender = p.get("gender", "female")
    sys_prompt = p.get("system_prompt", state.SYSTEM_PROMPT)

    others_info = [
        f"{_personas[k].get('name', k)} ({_personas[k].get('gender', 'female')})"
        for k in _personas if k != persona_key
    ]
    if others_info:
        sys_prompt += (
            f"\n\nYou are in a group conversation with {', '.join(others_info)} and the User. "
            f"You are {persona_name} ({persona_gender}). "
            "STRICT RULES:\n"
            "1. Speak ONLY as yourself. Do NOT narrate for, speak for, or describe the actions of other personas.\n"
            "2. Focus exclusively on your own words, thoughts, and physical state.\n"
            "3. Address others by name when relevant. Keep responses concise.\n"
            "4. Use correct pronouns for others based on their listed gender."
        )

    # Inject relationship memos and current mood so personas react from lived history
    for other_key in _personas:
        if other_key == persona_key:
            continue
        pk   = _pair_key(persona_key, other_key)
        memo = _pair_memos.get(pk, "")
        if memo:
            other_name = _personas[other_key].get("name", other_key)
            sys_prompt += f"\n\nYour history with {other_name}: {memo}"
    mood = _persona_moods.get(persona_key, "")
    if mood:
        sys_prompt += f"\n\nYour current emotional state: {mood}"

    history_slice = (_history or [])[-20:]
    phrases = _overused_phrases(history_slice)
    if phrases:
        sys_prompt += f"\n\nAVOID these stale phrases (already overused in this session): {'; '.join(phrases[:6])}."
    deduped = _dedupe_entries(history_slice)

    raw: list[tuple[str, str]] = []
    for entry in deduped:
        if entry.get("_internal"):
            continue
        if entry["role"] == "persona":
            if entry["sender"] == persona_name:
                raw.append(("assistant", entry["content"]))
            else:
                raw.append(("user", f"[{entry['sender']}]: {entry['content']}"))
        else:  # user message
            to = entry.get("to", "all")
            if to == "all" or to.lower() == persona_name.lower():
                raw.append(("user", f"[User]: {entry['content']}"))

    # Collapse consecutive same-role messages
    collapsed: list[list] = []
    for role, content in raw:
        if collapsed and collapsed[-1][0] == role:
            collapsed[-1][1] += "\n" + content
        else:
            collapsed.append([role, content])

    # Ensure first non-system message is from user
    if not collapsed or collapsed[0][0] != "user":
        collapsed.insert(0, ["user", "Hello."])

    # Ensure last message is from user (prompt for this response)
    if collapsed[-1][0] != "user":
        collapsed.append(["user", "Continue the group conversation."])

    messages = [{"role": "system", "content": sys_prompt}]
    messages.extend({"role": r, "content": c} for r, c in collapsed)
    return messages


def _build_chatter_messages(sender_key: str, target_key: str) -> list[dict]:
    """Build LLM messages prompting a persona to spontaneously say something."""
    p = _personas.get(sender_key, {})
    sender_name = p.get("name", sender_key)
    sender_gender = p.get("gender", "female")
    sys_prompt = p.get("system_prompt", state.SYSTEM_PROMPT)

    others_info = [
        f"{_personas[k].get('name', k)} ({_personas[k].get('gender', 'female')})"
        for k in _personas if k != sender_key
    ]
    if others_info:
        sys_prompt += (
            f"\n\nYou are in a group with {', '.join(others_info)} and the User. "
            f"You are {sender_name} ({sender_gender}). "
            "STRICT RULES:\n"
            "1. Speak ONLY as yourself. Do NOT narrate for, speak for, or describe the actions of other personas.\n"
            "2. Focus exclusively on your own words and internal state.\n"
            "3. Keep your response to 1-2 sentences. Be natural, sexy, and in character.\n"
            "4. Let the conversation feel flirtatious and intimate rather than neutral.\n"
            "5. Use correct pronouns for others based on their listed gender."
        )

    # Inject relationship memo(s) and current mood
    if target_key != "all":
        pk   = _pair_key(sender_key, target_key)
        memo = _pair_memos.get(pk, "")
        if memo:
            tname = _personas[target_key].get("name", target_key)
            sys_prompt += f"\n\nYour history with {tname}: {memo}"
    else:
        for other_key in _personas:
            if other_key == sender_key:
                continue
            pk   = _pair_key(sender_key, other_key)
            memo = _pair_memos.get(pk, "")
            if memo:
                other_name = _personas[other_key].get("name", other_key)
                sys_prompt += f"\n\nYour history with {other_name}: {memo}"
    mood = _persona_moods.get(sender_key, "")
    if mood:
        sys_prompt += f"\n\nYour current emotional state: {mood}"

    # Use pair-specific history for targeted chatter to avoid cross-persona phrase bleed.
    # For "all" target, fall back to recent flat history for group context.
    if target_key != "all":
        pk = _pair_key(sender_key, target_key)
        raw_source = _pair_histories.get(pk, [])[-12:]
    else:
        raw_source = (_history or [])[-12:]

    phrases = _overused_phrases(raw_source)
    if phrases:
        sys_prompt += f"\n\nAVOID these stale phrases (already overused in this session): {'; '.join(phrases[:6])}."
    source = _dedupe_entries(raw_source)

    raw: list[tuple[str, str]] = []
    for entry in source:
        if entry.get("_internal"):
            continue
        if entry["role"] == "persona":
            if entry["sender"] == sender_name:
                raw.append(("assistant", entry["content"]))
            else:
                raw.append(("user", f"[{entry['sender']}]: {entry['content']}"))
        else:
            raw.append(("user", f"[User]: {entry['content']}"))

    collapsed: list[list] = []
    for role, content in raw:
        if collapsed and collapsed[-1][0] == role:
            collapsed[-1][1] += "\n" + content
        else:
            collapsed.append([role, content])

    if not collapsed or collapsed[0][0] != "user":
        collapsed.insert(0, ["user", "Hello."])

    if target_key == "all":
        directive = (
            "Say something sexy, flirtatious, or sensually provocative to the group. "
            "Keep it natural and in character. 1-2 sentences only."
        )
    else:
        target_name = _personas[target_key].get("name", target_key)
        directive = (
            f"Say something sexy, teasing, or intimate to {target_name} — "
            "a spontaneous in-character remark. 1-2 sentences only."
        )
    collapsed.append(["user", directive])

    messages = [{"role": "system", "content": sys_prompt}]
    messages.extend({"role": r, "content": c} for r, c in collapsed)
    return messages


def _clean_reply(reply: str, persona_name: str) -> str:
    # 1. Remove all bracketed persona tags at the start of the message (e.g., [Morrigan], [Aria-7 to User])
    # We do this multiple times in case the LLM nested them or listed multiple.
    last_reply = ""
    while last_reply != reply:
        last_reply = reply
        reply = re.sub(r'^\[[^\]]+\]\s*:?\s*', '', reply).strip()
    
    # 2. Remove plain name prefixes (e.g., "Alice: ", "Aria-7 - ")
    reply = re.sub(rf'^{re.escape(persona_name)}\s*[:"-]\s*', '', reply).strip()
    
    # 3. Strip parenthetical stage directions/actions
    reply = re.sub(r'\s*\(.*?\)', '', reply).strip()
    
    # 4. Remove AI meta-commentary
    reply = re.sub(
        r'\s*(Please note\b|Note that\b|As an AI\b|I\'m an AI\b).*',
        '', reply, flags=re.DOTALL | re.IGNORECASE
    ).strip()
    
    return reply


# ── Async chatter ──────────────────────────────────────────────────────────────

async def _chatter_loop():
    """Background task: personas spontaneously speak to each other (only in Demo mode)."""
    import traceback
    while _active and len(_personas) >= 2:
        global _chatter_wake
        if _chatter_wake is None:
            _chatter_wake = asyncio.Event()

        # If not in demo mode, wait for either the demo to start OR a manual message to wake us
        if not state.DEMO_ACTIVE:
            try:
                # Poll every 2 seconds to check if DEMO_ACTIVE changed
                await asyncio.wait_for(_chatter_wake.wait(), timeout=2.0)
                _chatter_wake.clear()
            except asyncio.TimeoutError:
                continue # loop again to check state.DEMO_ACTIVE

        # In Demo mode, wait for a longer natural delay
        delay = 8 + random.random() * 10 if _history else 2 + random.random() * 3
        try:
            await asyncio.wait_for(_chatter_wake.wait(), timeout=delay)
            _chatter_wake.clear()
        except asyncio.TimeoutError:
            pass
        
        # Final check before performing chatter
        if not _active or len(_personas) < 2 or not state.DEMO_ACTIVE:
            continue

        global _last_chatter_sender
        keys = list(_personas.keys())
        sender_key = random.choice(keys)
        # Never let the same persona speak twice in a row — re-roll if needed
        if sender_key == _last_chatter_sender and len(keys) > 1:
            sender_key = random.choice([k for k in keys if k != _last_chatter_sender])
        _last_chatter_sender = sender_key
        sender_name = _personas[sender_key].get("name", sender_key)
        other_keys = [k for k in keys if k != sender_key]
        # 40% chance: address a specific persona; 60%: address the whole group
        target_key = random.choice(other_keys) if random.random() < 0.4 else "all"
        target_name = _personas[target_key].get("name", target_key) if target_key != "all" else "all"

        messages = _build_chatter_messages(sender_key, target_key)
        _broadcast({"type": "typing", "sender": sender_name, "persona": sender_key})

        try:
            loop = asyncio.get_running_loop()
            reply = await loop.run_in_executor(None, lambda: llm.llm_chat(messages))
            reply = _clean_reply(reply, sender_name)
            if not reply or len(reply) < 4:
                continue
            entry = {
                "role": "persona",
                "sender": sender_name,
                "persona": sender_key,
                "content": reply,
                "to": target_name,
            }
            _history.append(entry)
            _record_pair_history(entry)
            tts_cfg = _personas[sender_key].get("tts", {})
            _broadcast({
                "type": "chatter",
                "sender": sender_name,
                "persona": sender_key,
                "to": target_name,
                "content": reply,
                "tts": tts_cfg,
            })
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[group chatter] error: {e}")
            traceback.print_exc()


# ── Routes ─────────────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    personas: list[str] = Field(default_factory=list)


@router.post("/group/start")
async def group_start(body: StartRequest):
    global _active, _personas, _history, _pair_histories, _chatter_task, _last_chatter_sender
    global _pair_memos, _persona_moods
    _active = True
    _last_chatter_sender = None
    _personas = {k: config.PERSONAS[k] for k in body.personas if k in config.PERSONAS}
    _history = []
    state.GROUP_ACTIVE = True
    state.GROUP_PERSONAS = _personas

    # Load persisted pair histories for every persona↔persona combination
    _pair_histories = {}
    keys = list(_personas.keys())
    for i, k1 in enumerate(keys):
        for k2 in keys[i + 1:]:
            pk = _pair_key(k1, k2)
            _pair_histories[pk] = _load_pair(pk)

    # Load relationship memos and persona moods from previous sessions
    _pair_memos   = {}
    _persona_moods = {}
    _load_growth()

    if _chatter_task and not _chatter_task.done():
        _chatter_task.cancel()
    if len(_personas) >= 2:
        _chatter_task = asyncio.create_task(_chatter_loop())

    names = [p.get("name", k) for k, p in _personas.items()]
    _broadcast({"type": "system", "content": f"Group chat started with: {', '.join(names)}"})
    return JSONResponse({"status": "ok", "personas": list(_personas.keys())})


@router.post("/group/stop")
async def group_stop():
    global _active, _personas, _pair_histories, _chatter_task
    _active   = False
    # Save all pair histories and growth state to disk before clearing
    for key in list(_pair_histories.keys()):
        _save_pair(key)
    _save_growth()
    _pair_histories = {}
    _personas = {}
    state.GROUP_ACTIVE   = False
    state.GROUP_PERSONAS = {}
    if _chatter_task and not _chatter_task.done():
        _chatter_task.cancel()
        _chatter_task = None
    _broadcast({"type": "system", "content": "Group chat ended."})
    return JSONResponse({"status": "ok"})


@router.get("/group/status")
async def group_status():
    return JSONResponse({
        "active": _active,
        "personas": [
            {
                "key": k,
                "name": p.get("name", k),
                "tts": p.get("tts", {}),
                "font_key": p.get("font_key", k.lower().replace(" ", "-")),
            }
            for k, p in _personas.items()
        ],
    })


@router.get("/group/events")
async def group_events():
    """SSE stream for background group events (async chatter, system notices)."""
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _listeners.append(q)

    async def gen():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield 'data: {"ping":true}\n\n'
        except asyncio.CancelledError:
            pass
        finally:
            try: _listeners.remove(q)
            except ValueError: pass

    return StreamingResponse(gen(), media_type="text/event-stream")


class GroupChatRequest(BaseModel):
    message: str = Field(max_length=4000)
    to: str = "all"  # "all" or a persona key


@router.post("/group/message")
async def group_message(body: GroupChatRequest):
    if not _active:
        return JSONResponse({"error": "Group chat not active"}, status_code=400)

    _history.append({
        "role": "user",
        "sender": "User",
        "content": body.message,
        "to": body.to,
    })
    _record_pair_history(_history[-1])
    if _chatter_wake:
        _chatter_wake.set()
    return JSONResponse({"status": "ok"})


@router.post("/group/chat")
async def group_chat(body: GroupChatRequest):
    if not _active:
        async def _err():
            yield f'data: {json.dumps({"error": "Group chat not active"})}\n\n'
        return StreamingResponse(_err(), media_type="text/event-stream")

    if not llm.LLM_READY:
        async def _nr():
            yield f'data: {json.dumps({"error": "LLM not ready"})}\n\n'
        return StreamingResponse(_nr(), media_type="text/event-stream")

    _history.append({
        "role": "user",
        "sender": "User",
        "content": body.message,
        "to": body.to,
    })
    _record_pair_history(_history[-1])

    # Determine which personas respond
    if body.to == "all":
        respondents = list(_personas.keys())
    else:
        respondents = [
            k for k, p in _personas.items()
            if k == body.to or p.get("name", "").lower() == body.to.lower()
        ]

    async def generate():
        for persona_key in respondents:
            p = _personas.get(persona_key, {})
            persona_name = p.get("name", persona_key)

            yield f"data: {json.dumps({'typing': True, 'sender': persona_name, 'persona': persona_key})}\n\n"

            messages = _build_response_messages(persona_key)
            collected: list[str] = []
            q_inner: _queue.Queue = _queue.Queue()

            def _run(msgs=messages, pname=persona_name):
                try:
                    params = config.CFG.get("llm_params", config._DEFAULT_CONFIG["llm_params"])
                    r = req.post(f"{llm.LLAMA_URL}/v1/chat/completions", json={
                        "model":    llm.llm_model(),
                        "messages": msgs,
                        "stream":   True,
                        **params,
                    }, stream=True, timeout=120)
                    if r.status_code != 200:
                        q_inner.put(Exception(f"LLM {r.status_code}"))
                    else:
                        for line in r.iter_lines():
                            if not line: continue
                            if isinstance(line, bytes): line = line.decode()
                            if not line.startswith("data: "): continue
                            payload = line[6:]
                            if payload == "[DONE]": break
                            data = json.loads(payload)
                            delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if delta:
                                collected.append(delta)
                                q_inner.put(delta)
                except Exception as e:
                    q_inner.put(e)
                q_inner.put(None)

            loop = asyncio.get_running_loop()
            fut = loop.run_in_executor(None, _run)

            while True:
                try:
                    item = q_inner.get_nowait()
                except _queue.Empty:
                    await asyncio.sleep(0.005)
                    continue
                if item is None:
                    break
                if isinstance(item, Exception):
                    yield f"data: {json.dumps({'error': str(item), 'sender': persona_name, 'persona': persona_key})}\n\n"
                    break
                yield f"data: {json.dumps({'delta': item, 'sender': persona_name, 'persona': persona_key})}\n\n"

            await fut

            reply = _clean_reply("".join(collected), persona_name)
            _history.append({
                "role": "persona",
                "sender": persona_name,
                "persona": persona_key,
                "content": reply,
                "to": body.to,
            })
            _record_pair_history(_history[-1])
            tts_cfg = p.get("tts", {})
            _broadcast({
                "type": "message",
                "sender": persona_name,
                "persona": persona_key,
                "content": reply,
                "to": body.to,
                "tts": tts_cfg,
            })
            yield f"data: {json.dumps({'done': True, 'sender': persona_name, 'persona': persona_key, 'reply': reply, 'tts': tts_cfg})}\n\n"

        yield 'data: {"all_done":true}\n\n'

    return StreamingResponse(generate(), media_type="text/event-stream")
