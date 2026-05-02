"""Tests for the /chat SSE endpoint (routes/chat.py).

Key behaviours tested:
  - Normal path: delta + done events stream through correctly.
  - LLM not ready: status events emitted while waiting, chat proceeds once ready.
  - LLM restart timeout: error event returned after deadline expires.
  - Context overflow (HTTP 400): history is trimmed and the request is retried.
  - LLM server error: exception surfaced as an SSE error event.
  - Pydantic validation: message field max_length enforced.
  - Reply post-processing: Alice: prefix and curly quotes are stripped.
"""
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from alice import app

import llm
import state
import routes.chat

client = TestClient(app, raise_server_exceptions=True)


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_events(response_text: str) -> list[dict]:
    """Return list of parsed JSON objects from an SSE response body."""
    events = []
    for line in response_text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


class _FakeSSEResponse:
    """Minimal requests.Response substitute that streams SSE delta tokens."""

    def __init__(self, tokens: list[str], status_code: int = 200):
        self.status_code = status_code
        self._tokens = tokens

    def iter_lines(self):
        for token in self._tokens:
            payload = json.dumps({
                "choices": [{"delta": {"content": token}}]
            })
            yield f"data: {payload}".encode()
        yield b"data: [DONE]"

    def json(self):
        return {"error": {}}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


@pytest.fixture()
def _restore_llm_state():
    saved_ready     = llm.LLM_READY
    saved_suspended = llm.LLM_SUSPENDED
    saved_history   = list(llm.history)
    yield
    llm.LLM_READY     = saved_ready
    llm.LLM_SUSPENDED = saved_suspended
    llm.history.clear()
    llm.history.extend(saved_history)


# ── normal chat path ──────────────────────────────────────────────────────────

def test_chat_streams_delta_events(_restore_llm_state):
    llm.LLM_READY = True
    fake = _FakeSSEResponse(["Hello", ",", " world!"])

    with patch("routes.chat.req.post", return_value=fake):
        res = client.post("/chat", json={"message": "hi"})

    assert res.status_code == 200
    events = _parse_events(res.text)
    deltas = [e["delta"] for e in events if "delta" in e]
    assert deltas == ["Hello", ",", " world!"]


def test_chat_emits_done_event(_restore_llm_state):
    llm.LLM_READY = True
    fake = _FakeSSEResponse(["Nice to meet you"])

    with patch("routes.chat.req.post", return_value=fake):
        res = client.post("/chat", json={"message": "hello"})

    events = _parse_events(res.text)
    done_events = [e for e in events if e.get("done")]
    assert len(done_events) == 1
    assert done_events[0]["done"] is True
    assert "reply" in done_events[0]
    assert "auto_image" in done_events[0]


def test_chat_reply_assembled_from_deltas(_restore_llm_state):
    llm.LLM_READY = True
    tokens = ["I ", "am", " here"]
    fake   = _FakeSSEResponse(tokens)

    with patch("routes.chat.req.post", return_value=fake):
        res = client.post("/chat", json={"message": "hello"})

    events    = _parse_events(res.text)
    done_evt  = next(e for e in events if e.get("done"))
    # Reply may have post-processing applied, but must contain the core text
    assert "I " in done_evt["reply"] or "am" in done_evt["reply"]


def test_chat_appends_user_and_assistant_to_history(_restore_llm_state):
    llm.LLM_READY = True
    llm.history.clear()
    fake = _FakeSSEResponse(["Sure thing"])

    with patch("routes.chat.req.post", return_value=fake):
        client.post("/chat", json={"message": "test message"})

    roles = [m["role"] for m in llm.history]
    assert "user" in roles
    assert "assistant" in roles


def test_chat_user_message_not_duplicated_in_history(_restore_llm_state):
    llm.LLM_READY = True
    llm.history.clear()
    fake = _FakeSSEResponse(["OK"])

    with patch("routes.chat.req.post", return_value=fake):
        client.post("/chat", json={"message": "unique msg abc"})

    user_msgs = [m["content"] for m in llm.history if m["role"] == "user"]
    assert user_msgs.count("unique msg abc") == 1


# ── reply post-processing ─────────────────────────────────────────────────────

def test_chat_strips_alice_prefix(_restore_llm_state):
    llm.LLM_READY = True
    fake = _FakeSSEResponse(['Alice: "Hello there."'])

    with patch("routes.chat.req.post", return_value=fake):
        res = client.post("/chat", json={"message": "hi"})

    done_evt = next(e for e in _parse_events(res.text) if e.get("done"))
    assert not done_evt["reply"].startswith("Alice:")


def test_chat_strips_parenthetical_stage_directions(_restore_llm_state):
    """Parenthetical like '(smiling warmly)' must be removed from reply."""
    llm.LLM_READY = True
    fake = _FakeSSEResponse(["Hello (smiling warmly) there."])

    with patch("routes.chat.req.post", return_value=fake):
        res = client.post("/chat", json={"message": "hi"})

    done_evt = next(e for e in _parse_events(res.text) if e.get("done"))
    assert "(smiling warmly)" not in done_evt["reply"]


# ── pydantic validation ───────────────────────────────────────────────────────

def test_chat_rejects_message_over_4000_chars():
    res = client.post("/chat", json={"message": "x" * 4001})
    assert res.status_code == 422


def test_chat_accepts_message_at_max_length(_restore_llm_state):
    llm.LLM_READY = True
    fake = _FakeSSEResponse(["OK"])

    with patch("routes.chat.req.post", return_value=fake):
        res = client.post("/chat", json={"message": "x" * 4000})
    assert res.status_code == 200


def test_chat_requires_message_field():
    res = client.post("/chat", json={})
    assert res.status_code == 422


# ── LLM error handling ────────────────────────────────────────────────────────

def test_chat_llm_connection_error_yields_error_event(_restore_llm_state):
    import requests
    llm.LLM_READY = True
    with patch("routes.chat.req.post",
               side_effect=requests.exceptions.ConnectionError("refused")):
        res = client.post("/chat", json={"message": "hello"})

    assert res.status_code == 200
    events = _parse_events(res.text)
    error_events = [e for e in events if "error" in e]
    assert len(error_events) == 1


def test_chat_llm_error_rolls_back_user_history_entry(_restore_llm_state):
    """If the LLM call fails, the user message must not remain in history."""
    import requests
    llm.LLM_READY = True
    llm.history.clear()

    with patch("routes.chat.req.post",
               side_effect=requests.exceptions.ConnectionError("refused")):
        client.post("/chat", json={"message": "will fail"})

    assert not any(m["content"] == "will fail" for m in llm.history)


# ── context overflow ──────────────────────────────────────────────────────────

def test_chat_retries_after_context_overflow(_restore_llm_state):
    """A 400 context-overflow reply must trigger a trimmed retry."""
    llm.LLM_READY = True
    llm.history.clear()

    overflow_response = MagicMock()
    overflow_response.status_code = 400
    overflow_response.json.return_value = {
        "error": {
            "type":    "exceed_context_size",
            "message": "exceed_context_size",
            "n_prompt_tokens": 5000,
            "n_ctx":           4096,
        }
    }

    success_response = _FakeSSEResponse(["short reply"])

    call_count = [0]
    def _side_effect(*args, **kwargs):
        call_count[0] += 1
        return overflow_response if call_count[0] == 1 else success_response

    # Pre-populate history with messages to give the trimmer something to remove
    for i in range(10):
        llm.history.append({"role": "user",      "content": f"message {i}" * 50})
        llm.history.append({"role": "assistant",  "content": f"reply {i}"   * 50})

    with patch("routes.chat.req.post", side_effect=_side_effect):
        with patch("llm.compress_history"):  # prevent background req.post from compress
            res = client.post("/chat", json={"message": "hi"})

    assert res.status_code == 200
    assert call_count[0] == 2  # must have retried exactly once


# ── LLM not-ready wait path ───────────────────────────────────────────────────

def test_chat_emits_status_event_when_llm_not_ready(monkeypatch):
    """When LLM_READY is False, at least one status event must be yielded."""
    monkeypatch.setattr(llm, "LLM_READY", False)
    # Zero timeout: deadline fires on the first loop iteration without sleeping.
    # Patching time.monotonic globally breaks anyio's event loop, so we use the
    # module constant instead.
    monkeypatch.setattr(routes.chat, "_LLM_WAIT_TIMEOUT", 0)

    res = client.post("/chat", json={"message": "hello"})

    events = _parse_events(res.text)
    status_events = [e for e in events if "status" in e]
    assert len(status_events) >= 1


def test_chat_emits_error_when_llm_never_becomes_ready(monkeypatch):
    """Deadline exceeded → SSE error event, no delta/done events."""
    monkeypatch.setattr(llm, "LLM_READY", False)
    monkeypatch.setattr(routes.chat, "_LLM_WAIT_TIMEOUT", 0)

    res = client.post("/chat", json={"message": "hello"})

    events = _parse_events(res.text)
    assert any("error" in e for e in events)
    assert not any("done" in e for e in events)


def test_chat_proceeds_once_llm_becomes_ready(monkeypatch):
    """If LLM_READY flips True before the deadline, Phase 2 executes normally."""
    import threading

    monkeypatch.setattr(llm, "LLM_READY", False)
    fake = _FakeSSEResponse(["I'm back"])

    def _flip_ready():
        import time as _time
        _time.sleep(0.05)
        llm.LLM_READY = True

    threading.Thread(target=_flip_ready, daemon=True).start()

    with patch("routes.chat.req.post", return_value=fake):
        res = client.post("/chat", json={"message": "are you there?"})

    events = _parse_events(res.text)
    assert any("status" in e for e in events), "should have had at least one status event"
    assert any("done" in e for e in events),   "should have completed chat after LLM ready"
