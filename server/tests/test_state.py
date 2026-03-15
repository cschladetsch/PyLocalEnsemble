"""Tests for state.py: image saving, RE_CLOTHE, and nudity keyword patterns."""
import base64
import pytest
import config
import state


# ── save_generated_image ──────────────────────────────────────────────────────

def test_save_generated_image_returns_url(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ALICE_DIR", str(tmp_path))
    url = state.save_generated_image(base64.b64encode(b"data").decode())
    assert url.startswith("/static/outputs/img_")
    assert url.endswith(".png")


def test_save_generated_image_file_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ALICE_DIR", str(tmp_path))
    b64 = base64.b64encode(b"fake png data").decode()
    url = state.save_generated_image(b64)
    fname = url.split("/")[-1]
    assert (tmp_path / "static" / "outputs" / fname).exists()


def test_save_generated_image_decodes_bytes_correctly(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ALICE_DIR", str(tmp_path))
    raw = b"\x89PNG\r\n\x1a\n"
    url = state.save_generated_image(base64.b64encode(raw).decode())
    fname = url.split("/")[-1]
    assert (tmp_path / "static" / "outputs" / fname).read_bytes() == raw


def test_save_generated_image_creates_output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ALICE_DIR", str(tmp_path))
    out_dir = tmp_path / "static" / "outputs"
    assert not out_dir.exists()
    state.save_generated_image(base64.b64encode(b"x").decode())
    assert out_dir.is_dir()


def test_save_generated_image_unique_names(tmp_path, monkeypatch):
    """Two rapid calls must produce different filenames."""
    monkeypatch.setattr(config, "ALICE_DIR", str(tmp_path))
    b64 = base64.b64encode(b"x").decode()
    urls = {state.save_generated_image(b64) for _ in range(5)}
    assert len(urls) == 5


# ── should_auto_image ─────────────────────────────────────────────────────────

def test_should_auto_image_always_true():
    assert state.should_auto_image("hello") is True
    assert state.should_auto_image("") is True
    assert state.should_auto_image("show me your breasts") is True


# ── _RE_CLOTHE ────────────────────────────────────────────────────────────────

def test_re_clothe_matches_get_dressed():
    assert state._RE_CLOTHE.search("get dressed now")


def test_re_clothe_matches_put_on():
    assert state._RE_CLOTHE.search("put on your clothes")


def test_re_clothe_matches_cover_up():
    assert state._RE_CLOTHE.search("cover up please")


def test_re_clothe_matches_dress():
    assert state._RE_CLOTHE.search("dress yourself")


def test_re_clothe_no_match_on_undress():
    assert not state._RE_CLOTHE.search("take off your top")


def test_re_clothe_no_match_neutral():
    assert not state._RE_CLOTHE.search("hello, how are you")


# ── _NUDITY_KEYWORDS_RE ───────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "go nude", "get naked", "take off", "strip for me",
    "expose yourself", "show your breasts", "fuck me",
    "blowjob please", "lick it", "dildo",
])
def test_nudity_keywords_match(msg):
    assert state._NUDITY_KEYWORDS_RE.search(msg), f"Expected match for: {msg!r}"


@pytest.mark.parametrize("msg", [
    "tell me a story",
    "what is the weather like",
    "let us go for a walk",
])
def test_nudity_keywords_no_match(msg):
    assert not state._NUDITY_KEYWORDS_RE.search(msg), f"Unexpected match for: {msg!r}"
