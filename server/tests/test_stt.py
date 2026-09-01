"""Tests for stt.py: speech-to-text transcription and hallucination filtering."""

import base64
import pytest
from unittest.mock import MagicMock, patch
import stt


def _whisper_mock(transcribe_return):
    m = MagicMock()
    m.transcribe.return_value = transcribe_return
    return m


# ── silence / RMS threshold ───────────────────────────────────────────────────

def test_silence_ignored_when_rms_low():
    """Audio with RMS below 500 must return empty string."""
    with patch("stt._webm_to_wav", return_value=400.0), \
         patch("stt._WHISPER", _whisper_mock(([], {}))):
        result = stt.transcribe(b"\x00" * 1600, "audio/webm")
    assert result == ""


def test_silence_ignored_short_utterance():
    """A short utterance in the hallucination set returns empty."""
    with patch("stt._webm_to_wav", return_value=9999.0), \
         patch("stt._WHISPER", _whisper_mock(
             ([MagicMock(text="thanks")], {}))):
        result = stt.transcribe(b"dummy", "audio/webm")
    assert result == ""


def test_hallucination_filter_strips_known_phrases():
    """Known hallucination phrases are suppressed even if transcribed."""
    with patch("stt._webm_to_wav", return_value=9999.0), \
         patch("stt._WHISPER", _whisper_mock(
             ([MagicMock(text="you")], {}))):
        result = stt.transcribe(b"dummy", "audio/webm")
    assert result == ""


def test_valid_transcript_passes_through():
    """Normal transcription passes through unchanged."""
    with patch("stt._webm_to_wav", return_value=9999.0), \
         patch("stt._WHISPER", _whisper_mock(
             ([MagicMock(text="hello there")], {}))):
        result = stt.transcribe(b"dummy", "audio/webm")
    assert result == "hello there"


def test_short_non_hallucination_returns():
    """A short phrase not in the hallucination set is returned."""
    with patch("stt._webm_to_wav", return_value=9999.0), \
         patch("stt._WHISPER", _whisper_mock(
             ([MagicMock(text="hello")], {}))):
        result = stt.transcribe(b"dummy", "audio/webm")
    assert result == "hello"


# ── ensure_whisper / lazy load ────────────────────────────────────────────────

def test_ensure_whisper_loads_once():
    """second call must not re-import or re-create the model."""
    import sys
    # faster_whisper isn't installed — mock at sys.modules level so
    # the lazy import inside ensure_whisper() finds our mock.
    mock_fw = MagicMock()
    sys.modules["faster_whisper"] = mock_fw
    try:
        stt._WHISPER = None
        stt.ensure_whisper()
        stt.ensure_whisper()
        mock_fw.WhisperModel.assert_called_once()
    finally:
        sys.modules.pop("faster_whisper", None)


def test_ensure_whisper_uses_cpu_int8():
    import sys
    mock_fw = MagicMock()
    sys.modules["faster_whisper"] = mock_fw
    try:
        stt._WHISPER = None
        stt.ensure_whisper()
        mock_fw.WhisperModel.assert_called_once_with(
            "small.en", device="cpu", compute_type="int8")
    finally:
        sys.modules.pop("faster_whisper", None)


def test_transcribe_ensures_whisper_first():
    with patch.object(stt, "ensure_whisper") as mock_ensure, \
         patch("stt._webm_to_wav", return_value=9999.0), \
         patch("stt._WHISPER", _whisper_mock(([], {}))):
        stt.transcribe(b"dummy")
    mock_ensure.assert_called_once()


# ── _webm_to_wav ──────────────────────────────────────────────────────────────

def test_webm_to_wav_returns_rms():
    """av.audio is a submodule created at import time — stub the full av hierarchy."""
    import sys

    class FakeFrame:
        def __init__(self, samples):
            self.planes = [samples]

    mock_av_audio = MagicMock()
    mock_av_audio.resampler.AudioResampler.return_value = MagicMock(
        resample=MagicMock(side_effect=[
            [FakeFrame(b"\x01\x02")],
            [],
        ]))
    mock_av = MagicMock()
    mock_av.audio = mock_av_audio
    mock_av.open.return_value = MagicMock(
        streams=[MagicMock(type="audio")],
        decode=MagicMock(return_value=iter([FakeFrame(b"\x01\x02")])),
    )
    sys.modules["av"] = mock_av
    try:
        rms = stt._webm_to_wav("/tmp/fake.webm", "/tmp/fake.wav")
    finally:
        sys.modules.pop("av", None)
    assert isinstance(rms, float)


def test_webm_to_wav_raises_on_no_audio():
    import sys
    mock_av = MagicMock()
    mock_av.open.return_value = MagicMock(streams=[])
    sys.modules["av"] = mock_av
    try:
        with pytest.raises(RuntimeError, match="No audio stream"):
            stt._webm_to_wav("/tmp/fake.webm", "/tmp/fake.wav")
    finally:
        sys.modules.pop("av", None)


def test_webm_to_wav_writes_valid_wav_header(tmp_path):
    import sys
    dst = str(tmp_path / "out.wav")

    class FakeFrame:
        def __init__(self, samples):
            self.planes = [samples]

    mock_av_audio = MagicMock()
    mock_av_audio.resampler.AudioResampler.return_value = MagicMock(
        resample=MagicMock(side_effect=[
            [FakeFrame(b"\x01" * 160)],
            [],
        ]))
    mock_av = MagicMock()
    mock_av.audio = mock_av_audio
    mock_av.open.return_value = MagicMock(
        streams=[MagicMock(type="audio")],
        decode=MagicMock(return_value=iter([FakeFrame(b"\x01" * 160)])),
    )
    sys.modules["av"] = mock_av
    try:
        stt._webm_to_wav("/tmp/fake.webm", dst)
    finally:
        sys.modules.pop("av", None)
    with open(dst, "rb") as f:
        header = f.read(12)
    assert header[:4] == b"RIFF"
    assert header[8:12] == b"WAVE"


# ── content type routing ──────────────────────────────────────────────────────

def test_webm_suffix_selected_for_webm():
    with patch("stt._webm_to_wav", return_value=9999.0), \
         patch("stt._WHISPER", _whisper_mock(([], {}))):
        with patch("tempfile.NamedTemporaryFile") as mock_ntf:
            mock_ntf.return_value.name = "/tmp/test.webm"
            stt.transcribe(b"data", "audio/webm")
            assert mock_ntf.call_args[1]["suffix"] == ".webm"


def test_ogg_suffix_selected_for_ogg():
    with patch("stt._webm_to_wav", return_value=9999.0), \
         patch("stt._WHISPER", _whisper_mock(([], {}))):
        with patch("tempfile.NamedTemporaryFile") as mock_ntf:
            mock_ntf.return_value.name = "/tmp/test.ogg"
            stt.transcribe(b"data", "audio/ogg")
            assert mock_ntf.call_args[1]["suffix"] == ".ogg"


def test_wav_suffix_selected_for_wav():
    with patch("stt._webm_to_wav", return_value=9999.0), \
         patch("stt._WHISPER", _whisper_mock(([], {}))):
        with patch("tempfile.NamedTemporaryFile") as mock_ntf:
            mock_ntf.return_value.name = "/tmp/test.wav"
            stt.transcribe(b"data", "audio/wav")
            assert mock_ntf.call_args[1]["suffix"] == ".wav"


def test_default_suffix_webm():
    with patch("stt._webm_to_wav", return_value=9999.0), \
         patch("stt._WHISPER", _whisper_mock(([], {}))):
        with patch("tempfile.NamedTemporaryFile") as mock_ntf:
            mock_ntf.return_value.name = "/tmp/test.webm"
            stt.transcribe(b"data", "")
            assert mock_ntf.call_args[1]["suffix"] == ".webm"


# ── temp file cleanup ─────────────────────────────────────────────────────────

def test_temp_files_removed_after_transcribe():
    with patch("stt._webm_to_wav", return_value=9999.0), \
         patch("stt._WHISPER", _whisper_mock(([], {}))):
        with patch("tempfile.NamedTemporaryFile") as mock_ntf, \
             patch("os.unlink") as mock_unlink:
            mock_ntf.return_value.name = "/tmp/abc123.webm"
            stt.transcribe(b"data", "audio/webm")
    assert mock_unlink.call_count >= 1
