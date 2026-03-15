"""Tests for tts.py utilities."""
import numpy as np
import pytest
from tts import _android_effect


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
