"""Tests for group chat routes and internal helpers."""
import json
import pytest
import config
import llm
import routes.group as grp
from fastapi.testclient import TestClient
from alice import app

client = TestClient(app, raise_server_exceptions=True)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_group_state():
    """Isolate every test: wipe module-level group state before and after."""
    def _clear():
        grp._active        = False
        grp._personas      = {}
        grp._history       = []
        grp._pair_histories = {}
        if grp._chatter_task and not grp._chatter_task.done():
            grp._chatter_task.cancel()
        grp._chatter_task = None
    _clear()
    yield
    _clear()


@pytest.fixture()
def two_keys():
    """Return the first two persona keys from config and set them as active group."""
    keys = list(config.PERSONAS.keys())[:2]
    grp._active   = True
    grp._personas = {k: config.PERSONAS[k] for k in keys}
    return keys


@pytest.fixture()
def llm_ready(monkeypatch):
    """Patch LLM_READY so chat routes don't return 'not ready'."""
    monkeypatch.setattr(llm, "LLM_READY", True)


class _FakeStream:
    """Minimal fake of a streaming llama-server response.

    Lines must have the ``data: `` prefix the route's SSE parser requires.
    """
    status_code = 200

    def iter_lines(self):
        for word in ["Hello", " there", "!"]:
            yield f'data: {json.dumps({"choices": [{"delta": {"content": word}}]})}'.encode()
        yield b"data: [DONE]"

    def raise_for_status(self):
        pass


@pytest.fixture()
def mock_llm(monkeypatch, llm_ready):
    """Patch requests.post inside routes.group to return _FakeStream."""
    monkeypatch.setattr(grp, "req", type("R", (), {"post": staticmethod(lambda *a, **kw: _FakeStream())})())


def _parse_sse(response) -> list[dict]:
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


# ── POST /group/start ──────────────────────────────────────────────────────────

def test_start_returns_ok():
    keys = list(config.PERSONAS.keys())[:2]
    res = client.post("/group/start", json={"personas": keys})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_start_echoes_accepted_keys():
    keys = list(config.PERSONAS.keys())[:2]
    res = client.post("/group/start", json={"personas": keys})
    assert set(res.json()["personas"]) == set(keys)


def test_start_ignores_unknown_personas():
    key = list(config.PERSONAS.keys())[0]
    res = client.post("/group/start", json={"personas": [key, "Ghost9999"]})
    assert key in res.json()["personas"]
    assert "Ghost9999" not in res.json()["personas"]


def test_start_empty_list_accepted():
    res = client.post("/group/start", json={"personas": []})
    assert res.status_code == 200
    assert res.json()["personas"] == []


def test_start_sets_active_flag():
    client.post("/group/start", json={"personas": list(config.PERSONAS.keys())[:1]})
    assert grp._active is True


def test_start_populates_personas_dict():
    keys = list(config.PERSONAS.keys())[:2]
    client.post("/group/start", json={"personas": keys})
    assert set(grp._personas.keys()) == set(keys)


def test_start_clears_previous_history():
    grp._history.append({"role": "user", "sender": "User", "content": "old", "to": "all"})
    client.post("/group/start", json={"personas": list(config.PERSONAS.keys())[:1]})
    assert grp._history == []


def test_start_all_personas_accepted():
    keys = list(config.PERSONAS.keys())
    res = client.post("/group/start", json={"personas": keys})
    assert set(res.json()["personas"]) == set(keys)


def test_start_updates_state_module():
    import state
    keys = list(config.PERSONAS.keys())[:2]
    client.post("/group/start", json={"personas": keys})
    assert state.GROUP_ACTIVE is True
    assert set(state.GROUP_PERSONAS.keys()) == set(keys)


# ── POST /group/stop ───────────────────────────────────────────────────────────

def test_stop_returns_ok(two_keys):
    res = client.post("/group/stop")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_stop_clears_active_flag(two_keys):
    client.post("/group/stop")
    assert grp._active is False


def test_stop_clears_personas_dict(two_keys):
    client.post("/group/stop")
    assert grp._personas == {}


def test_stop_when_not_active_still_ok():
    res = client.post("/group/stop")
    assert res.status_code == 200


def test_stop_updates_state_module(two_keys):
    import state
    client.post("/group/stop")
    assert state.GROUP_ACTIVE is False
    assert state.GROUP_PERSONAS == {}


def test_stop_clears_pair_histories(two_keys):
    # Populate a pair history in memory, then stop — pair_histories should be cleared
    pk = grp._pair_key(two_keys[0], two_keys[1])
    grp._pair_histories[pk] = [{"role": "persona", "sender": "X", "content": "hi", "to": "Y"}]
    client.post("/group/stop")
    assert grp._pair_histories == {}


def test_start_populates_pair_histories(two_keys):
    # After a start, pair_histories should have an entry for each pair of personas
    keys = list(config.PERSONAS.keys())[:2]
    client.post("/group/start", json={"personas": keys})
    pk = grp._pair_key(keys[0], keys[1])
    assert pk in grp._pair_histories


# ── GET /group/status ──────────────────────────────────────────────────────────

def test_status_shape():
    res = client.get("/group/status")
    assert res.status_code == 200
    data = res.json()
    assert "active" in data
    assert "personas" in data
    assert isinstance(data["personas"], list)


def test_status_inactive_before_start():
    res = client.get("/group/status")
    assert res.json()["active"] is False
    assert res.json()["personas"] == []


def test_status_active_after_start(two_keys):
    res = client.get("/group/status")
    assert res.json()["active"] is True


def test_status_persona_required_fields(two_keys):
    for p in client.get("/group/status").json()["personas"]:
        assert "key"      in p
        assert "name"     in p
        assert "tts"      in p
        assert "font_key" in p


def test_status_persona_keys_match_started(two_keys):
    returned = {p["key"] for p in client.get("/group/status").json()["personas"]}
    assert returned == set(two_keys)


def test_status_inactive_after_stop(two_keys):
    client.post("/group/stop")
    res = client.get("/group/status")
    assert res.json()["active"] is False
    assert res.json()["personas"] == []


def test_status_tts_is_dict(two_keys):
    for p in client.get("/group/status").json()["personas"]:
        assert isinstance(p["tts"], dict)


# ── _clean_reply ───────────────────────────────────────────────────────────────

def test_clean_reply_strips_bracketed_name_prefix():
    assert grp._clean_reply("[Alice]: hello", "Alice") == "hello"


def test_clean_reply_strips_bracketed_name_with_spaces():
    assert grp._clean_reply("[Forest Witch]: greetings", "Forest Witch") == "greetings"


def test_clean_reply_strips_name_colon_prefix():
    assert grp._clean_reply("Alice: hello", "Alice") == "hello"


def test_clean_reply_strips_name_quote_prefix():
    assert grp._clean_reply('Alice: "hi"', "Alice") == '"hi"'


def test_clean_reply_no_prefix_unchanged():
    assert grp._clean_reply("just talking", "Alice") == "just talking"


def test_clean_reply_empty_string():
    assert grp._clean_reply("", "Alice") == ""


def test_clean_reply_strips_please_note():
    result = grp._clean_reply("Hi! Please note that I am an AI...", "Alice")
    assert "Please note" not in result
    assert result.startswith("Hi!")


def test_clean_reply_strips_as_an_ai():
    result = grp._clean_reply("Sure! As an AI, I must say...", "Alice")
    assert "As an AI" not in result


def test_clean_reply_strips_im_an_ai():
    result = grp._clean_reply("Hello! I'm an AI so...", "Alice")
    assert "I'm an AI" not in result


def test_clean_reply_different_persona_name():
    assert grp._clean_reply("Morrigan: greetings", "Morrigan") == "greetings"


def test_clean_reply_unrelated_persona_name_not_stripped():
    # "Alice: " prefix should not be stripped when persona is "Morrigan"
    result = grp._clean_reply("Alice: hello", "Morrigan")
    assert result == "Alice: hello"


# ── _build_response_messages ───────────────────────────────────────────────────

@pytest.fixture()
def two_personas_loaded(two_keys):
    """two_keys already loaded into grp._personas."""
    return two_keys


def test_build_response_starts_with_system(two_personas_loaded):
    msgs = grp._build_response_messages(two_personas_loaded[0])
    assert msgs[0]["role"] == "system"


def test_build_response_system_contains_persona_prompt(two_personas_loaded):
    key = two_personas_loaded[0]
    expected = config.PERSONAS[key].get("system_prompt", "")
    msgs = grp._build_response_messages(key)
    assert expected in msgs[0]["content"]


def test_build_response_system_mentions_other_persona(two_personas_loaded):
    key1, key2 = two_personas_loaded
    other_name = config.PERSONAS[key2].get("name", key2)
    msgs = grp._build_response_messages(key1)
    assert other_name in msgs[0]["content"]


def test_build_response_second_message_is_user(two_personas_loaded):
    msgs = grp._build_response_messages(two_personas_loaded[0])
    assert msgs[1]["role"] == "user"


def test_build_response_ends_with_user(two_personas_loaded):
    msgs = grp._build_response_messages(two_personas_loaded[0])
    assert msgs[-1]["role"] == "user"


def test_build_response_own_message_becomes_assistant(two_personas_loaded):
    key = two_personas_loaded[0]
    name = config.PERSONAS[key].get("name", key)
    grp._history.append({"role": "persona", "sender": name, "persona": key,
                          "content": "I said this.", "to": "all"})
    msgs = grp._build_response_messages(key)
    assert any(m["role"] == "assistant" and "I said this." in m["content"] for m in msgs)


def test_build_response_other_persona_becomes_user(two_personas_loaded):
    key1, key2 = two_personas_loaded
    name2 = config.PERSONAS[key2].get("name", key2)
    grp._history.append({"role": "persona", "sender": name2, "persona": key2,
                          "content": "Other said this.", "to": "all"})
    msgs = grp._build_response_messages(key1)
    assert any(m["role"] == "user" and "Other said this." in m["content"] for m in msgs)


def test_build_response_user_message_included(two_personas_loaded):
    grp._history.append({"role": "user", "sender": "User",
                          "content": "Hello group!", "to": "all"})
    msgs = grp._build_response_messages(two_personas_loaded[0])
    assert any(m["role"] == "user" and "Hello group!" in m["content"] for m in msgs)


def test_build_response_directed_to_other_excluded(two_personas_loaded):
    """Message addressed specifically to persona2 should not appear in persona1's view."""
    key1, key2 = two_personas_loaded
    name2 = config.PERSONAS[key2].get("name", key2)
    grp._history.append({"role": "user", "sender": "User",
                          "content": "Only for you.", "to": name2})
    msgs = grp._build_response_messages(key1)
    all_text = " ".join(m["content"] for m in msgs)
    assert "Only for you." not in all_text


def test_build_response_no_consecutive_same_role(two_personas_loaded):
    key = two_personas_loaded[0]
    name = config.PERSONAS[key].get("name", key)
    grp._history += [
        {"role": "user",    "sender": "User", "content": "msg1", "to": "all"},
        {"role": "user",    "sender": "User", "content": "msg2", "to": "all"},
        {"role": "persona", "sender": name,   "content": "reply", "to": "all", "persona": key},
    ]
    msgs = grp._build_response_messages(key)
    non_system = [m for m in msgs if m["role"] != "system"]
    for i in range(len(non_system) - 1):
        assert non_system[i]["role"] != non_system[i + 1]["role"], (
            f"Consecutive {non_system[i]['role']!r} at positions {i}, {i+1}"
        )


def test_build_response_skips_internal_entries(two_personas_loaded):
    grp._history.append({"role": "user", "sender": "sys", "content": "INTERNAL",
                          "to": "all", "_internal": True})
    msgs = grp._build_response_messages(two_personas_loaded[0])
    all_text = " ".join(m["content"] for m in msgs)
    assert "INTERNAL" not in all_text


def test_build_response_caps_history_length(two_personas_loaded):
    """History is capped at 20 entries; messages list stays bounded."""
    key = two_personas_loaded[0]
    for i in range(30):
        grp._history.append({"role": "user", "sender": "User",
                               "content": f"msg{i}", "to": "all"})
    msgs = grp._build_response_messages(key)
    # +1 system, +1 trailing user = at most 22 messages (20 history + system + continuation)
    assert len(msgs) <= 23


# ── _build_chatter_messages ────────────────────────────────────────────────────

def test_build_chatter_starts_with_system(two_personas_loaded):
    msgs = grp._build_chatter_messages(two_personas_loaded[0], "all")
    assert msgs[0]["role"] == "system"


def test_build_chatter_system_has_persona_prompt(two_personas_loaded):
    key = two_personas_loaded[0]
    expected = config.PERSONAS[key].get("system_prompt", "")
    msgs = grp._build_chatter_messages(key, "all")
    assert expected in msgs[0]["content"]


def test_build_chatter_all_target_directive(two_personas_loaded):
    msgs = grp._build_chatter_messages(two_personas_loaded[0], "all")
    assert msgs[-1]["role"] == "user"
    assert "group" in msgs[-1]["content"].lower()


def test_build_chatter_all_target_is_sexy(two_personas_loaded):
    msgs = grp._build_chatter_messages(two_personas_loaded[0], "all")
    assert "sexy" in msgs[-1]["content"].lower() or "flirtatious" in msgs[-1]["content"].lower()


def test_build_chatter_specific_target_names_persona(two_personas_loaded):
    key1, key2 = two_personas_loaded
    target_name = config.PERSONAS[key2].get("name", key2)
    msgs = grp._build_chatter_messages(key1, key2)
    assert target_name in msgs[-1]["content"]


def test_build_chatter_second_message_is_user(two_personas_loaded):
    msgs = grp._build_chatter_messages(two_personas_loaded[0], "all")
    assert msgs[1]["role"] == "user"


def test_build_chatter_ends_with_user(two_personas_loaded):
    msgs = grp._build_chatter_messages(two_personas_loaded[0], "all")
    assert msgs[-1]["role"] == "user"


def test_build_chatter_specific_target_uses_pair_history(two_personas_loaded):
    """Specific-target chatter draws from the pair history, not flat _history."""
    key1, key2 = two_personas_loaded
    name1 = config.PERSONAS[key1].get("name", key1)
    # Add something to flat history only (should NOT appear in pair context)
    grp._history.append({"role": "persona", "sender": name1, "persona": key1,
                          "content": "flat_only_phrase", "to": "all"})
    # Add something to the pair history (SHOULD appear)
    pk = grp._pair_key(key1, key2)
    grp._pair_histories[pk] = [{"role": "persona", "sender": name1, "persona": key1,
                                  "content": "pair_specific_phrase", "to": key2}]
    msgs = grp._build_chatter_messages(key1, key2)
    all_text = " ".join(m["content"] for m in msgs)
    assert "pair_specific_phrase" in all_text
    assert "flat_only_phrase" not in all_text


def test_record_pair_history_fans_out_group_persona_message(two_personas_loaded):
    key1, key2 = two_personas_loaded
    pk = grp._pair_key(key1, key2)
    grp._pair_histories[pk] = []
    entry = {
        "role": "persona",
        "sender": config.PERSONAS[key1].get("name", key1),
        "persona": key1,
        "content": "shared with everyone",
        "to": "all",
    }
    grp._record_pair_history(entry)
    assert grp._pair_histories[pk][-1]["content"] == "shared with everyone"


def test_build_chatter_all_target_uses_flat_history(two_personas_loaded):
    """'all' target chatter uses the flat _history, not pair history."""
    key1, key2 = two_personas_loaded
    name1 = config.PERSONAS[key1].get("name", key1)
    grp._history.append({"role": "persona", "sender": name1, "persona": key1,
                          "content": "flat_group_message", "to": "all"})
    msgs = grp._build_chatter_messages(key1, "all")
    all_text = " ".join(m["content"] for m in msgs)
    assert "flat_group_message" in all_text


# ── POST /group/chat ───────────────────────────────────────────────────────────

def test_chat_not_active_returns_error_event():
    res = client.post("/group/chat", json={"message": "hi", "to": "all"})
    events = _parse_sse(res)
    assert any("error" in e for e in events)


def test_group_message_not_active_returns_400():
    res = client.post("/group/message", json={"message": "hi", "to": "all"})
    assert res.status_code == 400


def test_group_message_records_user_message(two_keys):
    res = client.post("/group/message", json={"message": "free-form note", "to": "all"})
    assert res.status_code == 200
    assert grp._history[-1]["role"] == "user"
    assert grp._history[-1]["content"] == "free-form note"
    assert grp._history[-1]["to"] == "all"


def test_group_message_updates_all_pair_histories(two_keys):
    pk = grp._pair_key(two_keys[0], two_keys[1])
    grp._pair_histories[pk] = []
    client.post("/group/message", json={"message": "room context", "to": "all"})
    assert grp._pair_histories[pk][-1]["content"] == "room context"


def test_chat_llm_not_ready_returns_error_event(two_keys, monkeypatch):
    monkeypatch.setattr(llm, "LLM_READY", False)
    res = client.post("/group/chat", json={"message": "hi", "to": "all"})
    events = _parse_sse(res)
    assert any("error" in e for e in events)


def test_chat_records_user_message(two_keys, mock_llm):
    client.post("/group/chat", json={"message": "test content", "to": "all"})
    assert any(e.get("content") == "test content" for e in grp._history)


def test_chat_user_message_has_correct_to(two_keys, mock_llm):
    client.post("/group/chat", json={"message": "hi", "to": "all"})
    user_entries = [e for e in grp._history if e["role"] == "user"]
    assert user_entries[0]["to"] == "all"


def test_chat_records_persona_replies(two_keys, mock_llm):
    client.post("/group/chat", json={"message": "hi", "to": "all"})
    persona_entries = [e for e in grp._history if e["role"] == "persona"]
    assert len(persona_entries) == len(two_keys)


def test_chat_streams_typing_event(two_keys, mock_llm):
    res = client.post("/group/chat", json={"message": "hi", "to": "all"})
    assert any(e.get("typing") for e in _parse_sse(res))


def test_chat_streams_done_event(two_keys, mock_llm):
    res = client.post("/group/chat", json={"message": "hi", "to": "all"})
    assert any(e.get("done") for e in _parse_sse(res))


def test_chat_streams_all_done_event(two_keys, mock_llm):
    res = client.post("/group/chat", json={"message": "hi", "to": "all"})
    assert any(e.get("all_done") for e in _parse_sse(res))


def test_chat_streams_delta_events(two_keys, mock_llm):
    res = client.post("/group/chat", json={"message": "hi", "to": "all"})
    assert any("delta" in e for e in _parse_sse(res))


def test_chat_directed_one_persona_responds(two_keys, mock_llm):
    key = two_keys[0]
    res = client.post("/group/chat", json={"message": "hi", "to": key})
    done_events = [e for e in _parse_sse(res) if e.get("done")]
    assert len(done_events) == 1
    assert done_events[0]["persona"] == key


def test_chat_directed_by_name_one_persona_responds(two_keys, mock_llm):
    key = two_keys[0]
    name = config.PERSONAS[key].get("name", key)
    res = client.post("/group/chat", json={"message": "hi", "to": name})
    done_events = [e for e in _parse_sse(res) if e.get("done")]
    assert len(done_events) == 1


def test_chat_all_done_events_include_tts(two_keys, mock_llm):
    res = client.post("/group/chat", json={"message": "hi", "to": "all"})
    done_events = [e for e in _parse_sse(res) if e.get("done")]
    assert all("tts" in e for e in done_events)


def test_chat_done_event_has_reply_field(two_keys, mock_llm):
    key = two_keys[0]
    res = client.post("/group/chat", json={"message": "hi", "to": key})
    done_events = [e for e in _parse_sse(res) if e.get("done")]
    assert "reply" in done_events[0]
    assert done_events[0]["reply"]  # non-empty


def test_chat_done_event_has_sender_and_persona(two_keys, mock_llm):
    res = client.post("/group/chat", json={"message": "hi", "to": "all"})
    done_events = [e for e in _parse_sse(res) if e.get("done")]
    for ev in done_events:
        assert "sender" in ev
        assert "persona" in ev


def test_chat_strips_persona_prefix_from_reply(two_keys, mock_llm, monkeypatch):
    key = two_keys[0]
    name = config.PERSONAS[key].get("name", key)

    class _Prefixed:
        status_code = 200
        def iter_lines(self):
            yield f'data: {json.dumps({"choices": [{"delta": {"content": f"{name}: Hi!"}}]})}'.encode()
            yield b"data: [DONE]"
        def raise_for_status(self): pass

    monkeypatch.setattr(grp, "req",
        type("R", (), {"post": staticmethod(lambda *a, **kw: _Prefixed())})())
    res = client.post("/group/chat", json={"message": "hello", "to": key})
    done = [e for e in _parse_sse(res) if e.get("done")]
    assert done[0]["reply"] == "Hi!"


def test_chat_typing_event_has_sender_and_persona(two_keys, mock_llm):
    res = client.post("/group/chat", json={"message": "hi", "to": "all"})
    typing_events = [e for e in _parse_sse(res) if e.get("typing")]
    for ev in typing_events:
        assert "sender" in ev
        assert "persona" in ev


def test_chat_number_of_typing_events_matches_respondents(two_keys, mock_llm):
    res = client.post("/group/chat", json={"message": "hi", "to": "all"})
    typing_events = [e for e in _parse_sse(res) if e.get("typing")]
    assert len(typing_events) == len(two_keys)


def test_chat_history_order_user_then_personas(two_keys, mock_llm):
    client.post("/group/chat", json={"message": "hi", "to": "all"})
    assert grp._history[0]["role"] == "user"
    for entry in grp._history[1:]:
        assert entry["role"] == "persona"


def test_chat_llm_error_streams_error_event(two_keys, monkeypatch, llm_ready):
    class _ErrorStream:
        status_code = 500
        def iter_lines(self): return iter([])
        def raise_for_status(self): raise Exception("server error")

    monkeypatch.setattr(grp, "req",
        type("R", (), {"post": staticmethod(lambda *a, **kw: _ErrorStream())})())
    key = two_keys[0]
    res = client.post("/group/chat", json={"message": "hi", "to": key})
    events = _parse_sse(res)
    assert any("error" in e for e in events)


# ── GET /group/events ──────────────────────────────────────────────────────────
# The events endpoint is an infinite SSE keepalive stream.  We verify it is
# registered on the app rather than consuming it (which would hang the runner).

def test_group_events_route_registered():
    """Route /group/events must appear in the app's route table."""
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/group/events" in paths
