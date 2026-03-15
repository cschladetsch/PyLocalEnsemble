"""Tests for tts.py utilities."""
import numpy as np
import pytest
from tts import _android_effect, _cathedral_effect, _sentence_chunks, _crossfade


def _sine(sr=24000, freq=440, duration=0.5):
    t = np.linspace(0, duration, int(sr * duration), dtype=np.float32)
    return np.sin(2 * np.pi * freq * t)


def test_android_effect_preserves_shape():
    samples = _sine()
    result = _android_effect(samples, 24000)
    assert result.shape == samples.shape


def test_android_effect_preserves_dtype():
    samples = _sine()
    result = _android_effect(samples, 24000)
    assert result.dtype == samples.dtype


def test_android_effect_on_silence():
    silence = np.zeros(1000, dtype=np.float32)
    result = _android_effect(silence, 24000)
    assert result.shape == silence.shape
    # Silence in → silence out (ring mod of zero is zero)
    assert np.max(np.abs(result)) < 1e-5


def test_android_effect_normalises_peak():
    samples = _sine() * 0.5   # peak = 0.5
    result = _android_effect(samples, 24000)
    original_peak = np.max(np.abs(samples))
    result_peak = np.max(np.abs(result))
    assert abs(result_peak - original_peak) < 0.05


def test_android_effect_changes_signal():
    samples = _sine()
    result = _android_effect(samples, 24000)
    # The signal should be different (ring mod changes the waveform)
    assert not np.allclose(samples, result)


# ── _cathedral_effect ─────────────────────────────────────────────────────────

def test_cathedral_preserves_shape():
    samples = _sine()
    result = _cathedral_effect(samples, 24000)
    assert result.shape == samples.shape


def test_cathedral_preserves_dtype():
    samples = _sine()
    result = _cathedral_effect(samples, 24000)
    assert result.dtype == samples.dtype


def test_cathedral_on_silence():
    silence = np.zeros(1000, dtype=np.float32)
    result = _cathedral_effect(silence, 24000)
    assert result.shape == silence.shape
    assert np.max(np.abs(result)) < 1e-5


def test_cathedral_normalises_peak():
    """Output peak should stay close to input peak."""
    samples = _sine() * 0.6
    result = _cathedral_effect(samples, 24000)
    original_peak = np.max(np.abs(samples))
    result_peak   = np.max(np.abs(result))
    assert abs(result_peak - original_peak) < 0.05


def test_cathedral_changes_signal():
    """Reverb should alter the waveform."""
    samples = _sine()
    result = _cathedral_effect(samples, 24000)
    assert not np.allclose(samples, result)


# ── _sentence_chunks ──────────────────────────────────────────────────────────

def test_chunks_short_text_single_chunk():
    result = _sentence_chunks("Hello there.", max_chars=300)
    assert result == ["Hello there."]

def test_chunks_splits_on_sentence_boundary():
    text = "First sentence. Second sentence. Third sentence."
    result = _sentence_chunks(text, max_chars=30)
    assert len(result) > 1
    assert all(len(c) <= 30 for c in result)

def test_chunks_joins_short_sentences():
    text = "Hi. Yes. No. Maybe."
    result = _sentence_chunks(text, max_chars=300)
    assert len(result) == 1
    assert "Hi" in result[0] and "Maybe" in result[0]

def test_chunks_hard_splits_overlong_sentence():
    long_sentence = "a" * 400
    result = _sentence_chunks(long_sentence, max_chars=300)
    assert all(len(c) <= 300 for c in result)
    assert "".join(result) == long_sentence

def test_chunks_empty_string():
    result = _sentence_chunks("", max_chars=300)
    assert result == [""]

def test_chunks_preserves_all_content():
    text = "One. Two. Three. Four. Five."
    result = _sentence_chunks(text, max_chars=15)
    joined = " ".join(result)
    for word in ["One", "Two", "Three", "Four", "Five"]:
        assert word in joined


# ── _crossfade ─────────────────────────────────────────────────────────────────

def test_crossfade_single_part_unchanged():
    a = _sine()
    result = _crossfade([a], 24000)
    assert np.allclose(result, a)

def test_crossfade_two_parts_length():
    sr = 24000
    a = _sine(sr=sr, duration=0.3)
    b = _sine(sr=sr, duration=0.3, freq=880)
    result = _crossfade([a, b], sr, fade_ms=25)
    fade_n = int(sr * 0.025)
    assert len(result) == len(a) + len(b) - fade_n

def test_crossfade_empty_returns_empty():
    result = _crossfade([], 24000)
    assert len(result) == 0

def test_crossfade_preserves_dtype():
    sr = 24000
    a = _sine(sr=sr)
    b = _sine(sr=sr, freq=880)
    result = _crossfade([a, b], sr)
    assert result.dtype == np.float32

def test_crossfade_short_parts_no_crash():
    sr = 24000
    tiny = np.zeros(10, dtype=np.float32)
    result = _crossfade([tiny, tiny], sr, fade_ms=25)
    assert len(result) > 0
