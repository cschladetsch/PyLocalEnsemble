"""Tests for vram.py VRAM state machine.

All tests mock HTTP calls — no Forge server required.
"""
import pytest
from unittest.mock import MagicMock, patch, call
import vram


@pytest.fixture(autouse=True)
def _reset_vram_state():
    """Restore module-level state after each test."""
    saved_url    = vram._forge_url
    saved_loaded = vram._forge_loaded
    yield
    vram._forge_url    = saved_url
    vram._forge_loaded = saved_loaded


# ── setup ────────────────────────────────────────────────────────────────────

def test_setup_stores_forge_url():
    vram.setup("http://forge.local:7860")
    assert vram._forge_url == "http://forge.local:7860"


def test_setup_overwrites_previous_url():
    vram.setup("http://first:7860")
    vram.setup("http://second:7860")
    assert vram._forge_url == "http://second:7860"


# ── unload_forge ─────────────────────────────────────────────────────────────

def test_unload_forge_http200_sets_loaded_false():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    resp = MagicMock(status_code=200)
    with patch("vram.req.post", return_value=resp) as mock_post:
        result = vram.unload_forge()
    assert result is True
    assert vram._forge_loaded is False
    mock_post.assert_called_once_with(
        "http://localhost:7860/sdapi/v1/unload-checkpoint", timeout=15
    )


def test_unload_forge_http404_leaves_state_and_returns_false():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    resp = MagicMock(status_code=404)
    with patch("vram.req.post", return_value=resp):
        result = vram.unload_forge()
    assert result is False
    assert vram._forge_loaded is True  # unchanged on non-200


def test_unload_forge_http500_leaves_state_and_returns_false():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    resp = MagicMock(status_code=500)
    with patch("vram.req.post", return_value=resp):
        result = vram.unload_forge()
    assert result is False
    assert vram._forge_loaded is True


def test_unload_forge_network_error_leaves_state_and_returns_false():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    with patch("vram.req.post", side_effect=Exception("connection refused")):
        result = vram.unload_forge()
    assert result is False
    assert vram._forge_loaded is True


def test_unload_forge_already_unloaded_skips_http():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    with patch("vram.req.post") as mock_post:
        result = vram.unload_forge()
    assert result is True
    mock_post.assert_not_called()


def test_unload_forge_no_url_skips_http():
    vram._forge_url    = ""
    vram._forge_loaded = True
    with patch("vram.req.post") as mock_post:
        result = vram.unload_forge()
    assert result is True
    mock_post.assert_not_called()


# ── reload_forge ─────────────────────────────────────────────────────────────

def test_reload_forge_http200_sets_loaded_true():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    resp = MagicMock(status_code=200)
    with patch("vram.req.post", return_value=resp):
        with patch("image.forge._push_forge_settings"):
            result = vram.reload_forge()
    assert result is True
    assert vram._forge_loaded is True


def test_reload_forge_http200_calls_push_settings():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    resp = MagicMock(status_code=200)
    with patch("vram.req.post", return_value=resp):
        with patch("image.forge._push_forge_settings") as mock_push:
            vram.reload_forge()
    mock_push.assert_called_once_with("http://localhost:7860")


def test_reload_forge_http404_leaves_state_and_returns_false():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    resp = MagicMock(status_code=404)
    with patch("vram.req.post", return_value=resp):
        with patch("image.forge._push_forge_settings"):
            result = vram.reload_forge()
    assert result is False
    assert vram._forge_loaded is False  # unchanged


def test_reload_forge_network_error_returns_false():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    with patch("vram.req.post", side_effect=Exception("timeout")):
        with patch("image.forge._push_forge_settings"):
            result = vram.reload_forge()
    assert result is False
    assert vram._forge_loaded is False


def test_reload_forge_already_loaded_skips_http():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    with patch("vram.req.post") as mock_post:
        result = vram.reload_forge()
    assert result is True
    mock_post.assert_not_called()


def test_reload_forge_no_url_skips_http():
    vram._forge_url    = ""
    vram._forge_loaded = False
    with patch("vram.req.post") as mock_post:
        result = vram.reload_forge()
    assert result is True
    mock_post.assert_not_called()


# ── acquire_for_image / release_from_image ───────────────────────────────────

def test_acquire_for_image_suspends_llm_then_reloads():
    """acquire_for_image must call suspend_for_image before reload_forge."""
    call_order = []
    import llm as _llm

    def fake_suspend(): call_order.append("suspend")
    def fake_reload(): call_order.append("reload"); return True

    with patch.object(_llm, "suspend_for_image", side_effect=fake_suspend):
        with patch("vram.reload_forge", side_effect=fake_reload):
            with patch("vram.time.sleep"):
                vram.acquire_for_image()

    assert call_order == ["suspend", "reload"]


def test_acquire_for_image_sleeps_before_reload():
    """acquire_for_image must sleep to let CUDA reclaim VRAM after the kill."""
    import llm as _llm
    sleep_calls = []

    with patch.object(_llm, "suspend_for_image"):
        with patch("vram.reload_forge"):
            with patch("vram.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                vram.acquire_for_image()

    assert sleep_calls and sleep_calls[0] == pytest.approx(2.0)


def test_release_from_image_unloads_then_resumes_llm():
    """release_from_image must unload Forge, then restart the LLM if suspended."""
    import llm as _llm
    call_order = []

    def fake_unload(): call_order.append("unload"); return True
    def fake_resume(): call_order.append("resume")

    with patch("vram.unload_forge", side_effect=fake_unload):
        with patch.object(_llm, "LLM_SUSPENDED", True):
            with patch.object(_llm, "resume_after_image", side_effect=fake_resume):
                vram.release_from_image()

    assert call_order == ["unload", "resume"]


def test_release_from_image_skips_resume_when_not_suspended():
    import llm as _llm
    with patch("vram.unload_forge"):
        with patch.object(_llm, "LLM_SUSPENDED", False):
            with patch.object(_llm, "resume_after_image") as mock_resume:
                vram.release_from_image()
    mock_resume.assert_not_called()


# ── idempotency ───────────────────────────────────────────────────────────────

def test_double_unload_only_calls_http_once():
    """Second unload when already unloaded must not make an HTTP request."""
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    resp = MagicMock(status_code=200)
    with patch("vram.req.post", return_value=resp) as mock_post:
        vram.unload_forge()  # first call
        vram.unload_forge()  # second call — already unloaded
    assert mock_post.call_count == 1


def test_double_reload_only_calls_http_once():
    """Second reload when already loaded must not make an HTTP request."""
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    resp = MagicMock(status_code=200)
    with patch("vram.req.post", return_value=resp) as mock_post:
        with patch("image.forge._push_forge_settings"):
            vram.reload_forge()  # first call
            vram.reload_forge()  # second call — already loaded
    assert mock_post.call_count == 1
