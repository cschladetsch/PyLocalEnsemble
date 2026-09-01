"""Tests for routes/audio.py: /tts, /tts/stream, /stt endpoints."""

import json
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from io import BytesIO

from fastapi.testclient import TestClient
from alice import app
import args
import config
import tts
import stt

client = TestClient(app, raise_server_exceptions=True)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_speech_mode():
    """Ensure --no-speech is NOT set by default."""
    saved = args.ARGS.no_speech
    args.ARGS.no_speech = False
    yield
    args.ARGS.no_speech = saved


@pytest.fixture(autouse=True)
def _reset_tts():
    saved = tts.TTS
    yield
    tts.TTS = saved


# ── GET /voices ────────────────────────────────────────────────────────────────

def test_voices_returns_list():
    res = client.get("/voices")
    assert res.status_code == 200
    data = res.json()
    assert "voices" in data
    assert isinstance(data["voices"], list)
    assert len(data["voices"]) > 0


def test_voices_includes_current():
    res = client.get("/voices")
    data = res.json()
    assert "current" in data
    assert data["current"] in data["voices"]


def test_voices_current_matches_config():
    config.CFG.setdefault("tts", {})["voice"] = "af_bella"
    try:
        res = client.get("/voices")
        assert res.json()["current"] == "af_bella"
    finally:
        config.CFG["tts"]["voice"] = "af_nicole"


# ── POST /voice ────────────────────────────────────────────────────────────────

def test_set_voice_valid():
    voice = tts.VOICES[0]
    res = client.post("/voice", json={"voice": voice})
    assert res.status_code == 200
    assert res.json()["voice"] == voice


def test_set_voice_unknown_returns_400():
    res = client.post("/voice", json={"voice": "non_existent_voice_xyz"})
    assert res.status_code == 400
    assert "error" in res.json()


def test_set_voice_updates_config():
    voice = tts.VOICES[-1]
    client.post("/voice", json={"voice": voice})
    assert config.CFG["tts"]["voice"] == voice


def test_set_voice_response_shape():
    res = client.post("/voice", json={"voice": tts.VOICES[0]})
    data = res.json()
    assert data["status"] == "ok"
    assert data["voice"] == tts.VOICES[0]


# ── POST /tts (non-streaming) ─────────────────────────────────────────────────

def test_tts_no_speech_returns_none():
    """When --no-speech is set, /tts returns audio: null."""
    args.ARGS.no_speech = True
    res = client.post("/tts", json={"text": "hello"})
    assert res.status_code == 200
    assert res.json()["audio"] is None
    args.ARGS.no_speech = False


def test_tts_tts_not_ready_returns_503():
    """When TTS is not loaded, /tts returns 503."""
    tts.TTS = None
    res = client.post("/tts", json={"text": "hello"})
    assert res.status_code == 503
    assert "error" in res.json()


def test_tts_returns_audio_when_ready():
    """When TTS is loaded, /tts returns a base64 audio string."""
    sr = 24000
    fake_samples = np.zeros(sr, dtype=np.float32)
    mock_tts = MagicMock()
    mock_tts.create.return_value = (fake_samples, sr)
    tts.TTS = mock_tts

    res = client.post("/tts", json={"text": "hello world"})
    assert res.status_code == 200
    assert "audio" in res.json()
    assert isinstance(res.json()["audio"], str)
    assert mock_tts.create.called


def test_tts_uses_default_voice_from_config():
    config.CFG.setdefault("tts", {})["voice"] = "af_bella"
    sr = 24000
    mock_tts = MagicMock()
    mock_tts.create.return_value = (np.zeros(sr, dtype=np.float32), sr)
    tts.TTS = mock_tts

    try:
        res = client.post("/tts", json={"text": "hello"})
        assert res.status_code == 200
        assert res.json()["audio"] is not None
    finally:
        config.CFG["tts"]["voice"] = "af_nicole"


def test_tts_passes_explicit_voice():
    sr = 24000
    mock_tts = MagicMock()
    mock_tts.create.return_value = (np.zeros(sr, dtype=np.float32), sr)
    tts.TTS = mock_tts
    # /tts route ignores body.voice; tts_wav_b64 reads from config
    config.CFG.setdefault("tts", {})
    config.CFG["tts"]["voice"] = "af_sarah"
    res = client.post("/tts", json={"text": "hello"})
    assert res.status_code == 200
    assert mock_tts.create.called
    assert config.CFG["tts"]["voice"] == "af_sarah"


def test_tts_passes_explicit_speed():
    sr = 24000
    mock_tts = MagicMock()
    mock_tts.create.return_value = (np.zeros(sr, dtype=np.float32), sr)
    tts.TTS = mock_tts
    config.CFG.setdefault("tts", {})
    config.CFG["tts"]["speed"] = 0.85
    res = client.post("/tts", json={"text": "hello"})
    assert res.status_code == 200
    assert mock_tts.create.called
    assert config.CFG["tts"]["speed"] == 0.85


def test_tts_passes_explicit_pitch():
    sr = 24000
    mock_tts = MagicMock()
    mock_tts.create.return_value = (np.zeros(sr, dtype=np.float32), sr)
    tts.TTS = mock_tts
    config.CFG.setdefault("tts", {})
    config.CFG["tts"]["pitch"] = 0.94
    res = client.post("/tts", json={"text": "hello"})
    assert res.status_code == 200
    assert mock_tts.create.called
    assert config.CFG["tts"]["pitch"] == 0.94

def test_tts_text_is_cleaned():
    sr = 24000
    mock_tts = MagicMock()
    mock_tts.create.return_value = (np.zeros(sr, dtype=np.float32), sr)
    tts.TTS = mock_tts

    res = client.post("/tts", json={"text": "**hello** _world_"})
    assert res.status_code == 200

    call_args = mock_tts.create.call_args[0]
    assert "**" not in call_args[0]
    assert "_" not in call_args[0] or "world" in call_args[0]


def test_tts_empty_text_returns_empty_audio():
    sr = 24000
    mock_tts = MagicMock()
    mock_tts.create.return_value = (np.zeros(sr, dtype=np.float32), sr)
    tts.TTS = mock_tts

    res = client.post("/tts", json={"text": ""})
    assert res.status_code == 200


def test_tts_error_returns_500():
    mock_tts = MagicMock()
    mock_tts.create.side_effect = RuntimeError("Kokoro error")
    tts.TTS = mock_tts

    res = client.post("/tts", json={"text": "hello"})
    assert res.status_code == 500
    assert "error" in res.json()


# ── POST /tts/stream ───────────────────────────────────────────────────────────

def test_tts_stream_no_speech_returns_empty_sse():
    """Stream with --no-speech yields a single done event."""
    args.ARGS.no_speech = True
    res = client.post("/tts/stream", json={"text": "hello"})
    assert res.status_code == 200
    text = res.text
    assert "done" in text
    args.ARGS.no_speech = False


def test_tts_stream_tts_not_ready_retries_on_demand():
    """If TTS is None, the endpoint attempts to load it before yielding."""
    tts.TTS = None
    load_calls = []

    def _load_tts():
        load_calls.append(True)
        tts.TTS = MagicMock()  # synchronously set TTS so the route's follow-up check sees it
        return True

    with patch("tts.load_tts", side_effect=_load_tts):
        res = client.post("/tts/stream", json={"text": "hello"})
    assert len(load_calls) >= 1
    assert res.status_code == 200


def test_tts_stream_returns_sse_chunks():
    """When TTS is loaded, /tts/stream yields chunk events via SSE."""
    sr = 24000
    mock_tts = MagicMock()
    mock_tts.create.return_value = (np.zeros(sr, dtype=np.float32), sr)
    tts.TTS = mock_tts

    def _gen(text, voice=None, speed=None, pitch=None, effects=None):
        yield "chunk1_b64", False
        yield "chunk2_b64", True
        yield "done_b64", True  # last chunk triggers 'done' event

    with patch("tts.tts_wav_b64_stream", side_effect=_gen):
        res = client.post("/tts/stream", json={"text": "hello"})
    assert res.status_code == 200
    text = res.text
    assert "chunk" in text
    assert "done" in text
    assert "done" in text


def test_tts_stream_sse_format():
    """SSE events must have data: prefix."""
    mock_tts = MagicMock()
    mock_tts.create.return_value = (b"fake_wav", 24000)
    tts.TTS = mock_tts

    def _gen(text, voice=None, speed=None, pitch=None, effects=None):
        yield "b64chunk", True

    with patch("tts.tts_wav_b64_stream", side_effect=_gen):
        res = client.post("/tts/stream", json={"text": "hello"})
    lines = res.text.strip().splitlines()
    sse_lines = [l for l in lines if l.startswith("data:")]
    assert len(sse_lines) >= 1


def test_tts_stream_still_error_if_load_fails():
    """If TTS load fails, the endpoint returns SSE error."""
    tts.TTS = None
    with patch("tts.load_tts", return_value=False):
        res = client.post("/tts/stream", json={"text": "hello"})
    assert res.status_code == 503
    text = res.text
    assert "TTS not ready" in text
    assert "TTS not ready" in text

def test_tts_stream_cleans_text():
    """Text sent to tts_wav_b64_stream must be cleaned."""
    mock_tts = MagicMock()
    mock_tts.create.return_value = (b"fake_wav", 24000)
    tts.TTS = mock_tts
    cleaned_text = []

    def _gen(text, voice=None, speed=None, pitch=None, effects=None):
        cleaned_text.append(text)
        yield "b64", True

    with patch("tts.tts_wav_b64_stream", side_effect=_gen):
        client.post("/tts/stream", json={"text": "**bold** _italic_"})

    assert "**" not in cleaned_text[0]
    assert "_" not in cleaned_text[0] or "italic" in cleaned_text[0]


# ── POST /stt ──────────────────────────────────────────────────────────────────

def test_stt_no_audio_data_returns_400():
    """Empty body on /stt returns 400."""
    res = client.post("/stt", data=b"")
    assert res.status_code == 400
    assert "error" in res.json()


def test_stt_returns_text_when_ready():
    """Valid audio data returns transcribed text."""
    mock_whisper = MagicMock()
    mock_whisper.transcribe.return_value = ([MagicMock(text="hello world")], {})
    stt._WHISPER = mock_whisper

    with patch("stt._webm_to_wav", return_value=9999.0):
        res = client.post("/stt",
                          content=b"fake_audio_data",
                          headers={"content-type": "audio/webm"})
    assert res.status_code == 200
    assert res.json()["text"] == "hello world"


def test_stt_returns_error_on_transcription_failure():
    """Transcription exception returns 500."""
    mock_whisper = MagicMock()
    stt._WHISPER = mock_whisper

    with patch("stt._webm_to_wav", return_value=9999.0):
        mock_whisper.transcribe.side_effect = RuntimeError("Whisper crash")
        res = client.post("/stt",
                          content=b"fake",
                          headers={"content-type": "audio/webm"})
    assert res.status_code == 500
    assert "error" in res.json()


def test_stt_passes_content_type_to_transcribe():
    """Content-Type header is forwarded to stt.transcribe."""
    mock_whisper = MagicMock()
    mock_whisper.transcribe.return_value = [MagicMock(text="ok")]
    stt._WHISPER = mock_whisper

    captured_ct = []

    def _transcribe(data, content_type=""):
        captured_ct.append(content_type)
        return "ok"

    with patch.object(stt, "transcribe", side_effect=_transcribe):
        client.post("/stt",
                    content=b"audio",
                    headers={"content-type": "audio/ogg"})
    assert captured_ct[0] == "audio/ogg"
