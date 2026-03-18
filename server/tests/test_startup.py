import alice
import config
import llm
import state


def test_startup_continues_when_optional_subsystems_fail(monkeypatch, capsys):
    calls = []

    monkeypatch.setattr(state, "_active_persona_key", "")
    monkeypatch.setattr(config, "PERSONAS", {"Alice": {}})
    monkeypatch.setattr(config, "NAME", "Alice")
    monkeypatch.setattr(config, "CFG", {"sd_checkpoint": "demo.safetensors"})
    monkeypatch.setattr(alice, "AUTO_IMAGE", False)
    monkeypatch.setattr(alice, "NO_SPEECH", False)

    monkeypatch.setattr(llm, "load_llm", lambda: calls.append("llm"))

    def fail_history():
        calls.append("history")
        raise RuntimeError("history down")

    monkeypatch.setattr(llm, "load_history", fail_history)

    def fail_tts():
        calls.append("tts")
        raise RuntimeError("tts down")

    monkeypatch.setattr(alice.tts, "load_tts", fail_tts)

    monkeypatch.setattr(alice.image, "start_forge", lambda: calls.append("forge") or True)
    monkeypatch.setattr(
        alice.image,
        "set_forge_model",
        lambda name: calls.append(f"model:{name}"),
    )

    alice._startup()

    out = capsys.readouterr().out
    assert calls == ["llm", "history", "tts", "forge", "model:demo.safetensors"]
    assert state._active_persona_key == "Alice"
    assert "Startup warning (history): history down" in out
    assert "Startup warning (TTS): tts down" in out
    assert "FATAL ERROR IN STARTUP THREAD" not in out
