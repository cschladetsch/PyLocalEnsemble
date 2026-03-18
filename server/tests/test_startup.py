import pytest

import alice
import config
import llm
import state


@pytest.fixture(autouse=True)
def reset_persona():
    state._active_persona_key = ""
    yield
    state._active_persona_key = ""


def _stub_config():
    config.PERSONAS = {"Alice": {}}
    config.NAME = "Alice"
    config.CFG = {
        "sd_checkpoint": "demo.safetensors",
        "forge_url": "http://localhost:7860",
    }
    alice.AUTO_IMAGE = False
    alice.NO_SPEECH = False


def test_startup_preflight_blocks_until_services_ready(monkeypatch):
    _stub_config()
    called = []

    monkeypatch.setattr(llm, "load_llm", lambda: called.append("load_llm"))
    monkeypatch.setattr(llm, "wait_until_ready", lambda timeout=120: called.append("wait_for_llm") or True)
    monkeypatch.setattr(llm, "load_history", lambda: called.append("load_history"))

    monkeypatch.setattr(alice.tts, "load_tts", lambda: called.append("load_tts") or True)
    monkeypatch.setattr(alice.image, "start_forge", lambda: called.append("start_forge") or True)
    monkeypatch.setattr(
        alice.image,
        "set_forge_model",
        lambda name: called.append(f"set_model:{name}") or True,
    )

    alice._startup()

    assert called == [
        "load_llm",
        "wait_for_llm",
        "load_history",
        "load_tts",
        "start_forge",
        "set_model:demo.safetensors",
    ]
    assert state._active_persona_key == "Alice"


def test_startup_aborts_when_forge_missing(monkeypatch):
    _stub_config()
    monkeypatch.setattr(llm, "load_llm", lambda: None)
    monkeypatch.setattr(llm, "wait_until_ready", lambda timeout=120: True)
    monkeypatch.setattr(llm, "load_history", lambda: None)
    monkeypatch.setattr(alice.tts, "load_tts", lambda: True)
    monkeypatch.setattr(alice.image, "start_forge", lambda: False)

    with pytest.raises(RuntimeError, match="Failed to launch Stable Diffusion Forge"):
        alice._startup()
