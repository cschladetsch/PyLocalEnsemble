"""Tests for audio utility functions: _tts_clean and _emotion_speed."""
import pytest
from routes.audio import _tts_clean
from tts import _emotion_speed

BASE = 0.85


# ── _tts_clean ────────────────────────────────────────────────────────────────

def test_tts_clean_strips_double_star_bold():
    assert _tts_clean("**hello** world") == "hello world"


def test_tts_clean_strips_single_star():
    assert _tts_clean("*emphasis*") == "emphasis"


def test_tts_clean_strips_triple_star():
    assert _tts_clean("***very bold***") == "very bold"


def test_tts_clean_strips_italic_underscore():
    assert _tts_clean("_italic_") == "italic"


def test_tts_clean_strips_double_underscore():
    assert _tts_clean("__word__") == "word"


def test_tts_clean_strips_h1():
    # \s+ collapse means \n becomes space
    assert _tts_clean("# Title\nBody") == "Title Body"


def test_tts_clean_strips_h2():
    assert _tts_clean("## Section\nText") == "Section Text"


def test_tts_clean_strips_h6():
    assert _tts_clean("###### Deep\nText") == "Deep Text"


def test_tts_clean_hash_mid_line_not_stripped():
    # Only strips # at start of line
    assert _tts_clean("color #red") == "color #red"


def test_tts_clean_collapses_whitespace():
    assert _tts_clean("hello   world") == "hello world"


def test_tts_clean_strips_leading_trailing():
    assert _tts_clean("  hello  ") == "hello"


def test_tts_clean_preserves_plain_text():
    assert _tts_clean("just plain text") == "just plain text"


def test_tts_clean_empty_string():
    assert _tts_clean("") == ""


def test_tts_clean_preserves_bracketed_content():
    # Arbitrary bracketed text is NOT stripped — only exact persona names are,
    # and that happens in _clean_reply, not here.
    text = "[Morrigan's analysis reveals ARIA-7]: data logged"
    assert _tts_clean(text) == text


def test_tts_clean_mixed_markdown():
    result = _tts_clean("## Header\n**bold** and _italic_ text")
    assert "Header" in result
    assert "bold" in result
    assert "italic" in result
    assert "**" not in result
    assert "##" not in result


def test_tts_clean_multiline_headers():
    text = "# First\n## Second\nBody"
    result = _tts_clean(text)
    assert "First" in result
    assert "Second" in result
    assert "#" not in result


# ── _emotion_speed ────────────────────────────────────────────────────────────

def test_emotion_speed_neutral_unchanged():
    assert _emotion_speed("hello there", BASE) == BASE


def test_emotion_speed_slow_on_whisper():
    # _SLOW_RE uses \b word boundaries — "whisper" matches, "whispered" does not
    assert _emotion_speed("she said whisper to me", BASE) < BASE


def test_emotion_speed_slow_on_softly():
    assert _emotion_speed("speak softly now", BASE) < BASE


def test_emotion_speed_slow_on_ellipsis():
    assert _emotion_speed("and then... silence", BASE) < BASE


def test_emotion_speed_slow_on_multiple_signals():
    result = _emotion_speed("whisper murmur softly", BASE)
    assert result < BASE


def test_emotion_speed_fast_requires_two_signals():
    # Only one fast signal — not enough
    result = _emotion_speed("she gasped once", BASE)
    assert result == BASE


def test_emotion_speed_fast_on_two_signals():
    result = _emotion_speed("gasp!! and scream", BASE)
    assert result > BASE


def test_emotion_speed_fast_on_exclamations():
    # !! alone is two matches of the exclamation alternation
    result = _emotion_speed("oh!!! oh!!!", BASE)
    assert result > BASE


def test_emotion_speed_slow_wins_when_more(BASE=BASE):
    # 5 slow signals vs 3 fast → slow wins
    result = _emotion_speed("whisper murmur softly gently... gasp!! scream", BASE)
    assert result < BASE


def test_emotion_speed_floor_at_half():
    result = _emotion_speed("whisper softly slowly", 0.3)
    assert result >= 0.5


def test_emotion_speed_ceiling():
    result = _emotion_speed("gasp!! scream desperately panting urgently", 1.3)
    assert result <= 1.4


def test_emotion_speed_tie_returns_base():
    # 2 slow (whisper, murmur), 2 fast (gasp, scream) — equal counts → base unchanged
    result = _emotion_speed("whisper murmur gasp scream", BASE)
    assert result == BASE
