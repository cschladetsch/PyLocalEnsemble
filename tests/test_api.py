import pytest
import config
from fastapi.testclient import TestClient
from alice import app

client = TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def output_file(tmp_path, monkeypatch):
    """Create a fake PNG in a temp outputs dir and patch ALICE_DIR."""
    out_dir = tmp_path / "static" / "outputs"
    out_dir.mkdir(parents=True)
    fname = "img_1234567890123.png"
    (out_dir / fname).write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(config, "ALICE_DIR", str(tmp_path))
    return out_dir / fname


# ── DELETE /image/{filename} ──────────────────────────────────────────────────

def test_delete_image_removes_file(output_file):
    res = client.delete(f"/image/{output_file.name}")
    assert res.status_code == 200
    assert res.json() == {"status": "deleted"}
    assert not output_file.exists()


def test_delete_image_not_found(tmp_path, monkeypatch):
    (tmp_path / "static" / "outputs").mkdir(parents=True)
    monkeypatch.setattr(config, "ALICE_DIR", str(tmp_path))
    res = client.delete("/image/img_9999999999999.png")
    assert res.status_code == 404


def test_delete_image_rejects_path_traversal():
    # FastAPI may normalise ../ before our handler sees it (→ 404) or we catch it (→ 400)
    res = client.delete("/image/../../etc/passwd.png")
    assert res.status_code in (400, 404)


def test_delete_image_rejects_non_png():
    res = client.delete("/image/img_1234567890123.jpg")
    assert res.status_code == 400


def test_delete_image_rejects_slashes_in_name():
    # %2F is decoded to / by FastAPI's router before reaching the handler
    res = client.delete("/image/subdir%2Fimg.png")
    assert res.status_code in (400, 404)


# ── GET /info ─────────────────────────────────────────────────────────────────

def test_info_returns_expected_fields():
    res = client.get("/info")
    assert res.status_code == 200
    data = res.json()
    assert "name" in data
    assert "llm_ready" in data
    assert "stt_silence" in data


# ── GET /personas ─────────────────────────────────────────────────────────────

def test_personas_returns_list():
    res = client.get("/personas")
    assert res.status_code == 200
    data = res.json()
    assert "personas" in data
    assert isinstance(data["personas"], list)
    assert len(data["personas"]) > 0
    # Each entry is a dict with name and font_key
    for p in data["personas"]:
        assert "name" in p
        assert "font_key" in p


def test_personas_default_has_font_key():
    res = client.get("/personas")
    personas = {p["name"]: p for p in res.json()["personas"]}
    assert "Default" in personas
    assert personas["Default"]["font_key"] == "default"


def test_personas_font_key_derived_from_name():
    res = client.get("/personas")
    personas = {p["name"]: p for p in res.json()["personas"]}
    if "Victorian Lady" in personas:
        assert personas["Victorian Lady"]["font_key"] == "victorian-lady"
    if "Forest Witch" in personas:
        assert personas["Forest Witch"]["font_key"] == "forest-witch"


# ── DELETE /history ───────────────────────────────────────────────────────────

def test_delete_history_clears():
    import llm
    llm.history.append({"role": "user", "content": "test"})
    res = client.delete("/history")
    assert res.status_code == 200
    assert llm.history == []


# ── GET /voices ───────────────────────────────────────────────────────────────

def test_voices_returns_list():
    res = client.get("/voices")
    assert res.status_code == 200
    data = res.json()
    assert "voices" in data
    assert isinstance(data["voices"], list)


def test_voices_includes_current():
    res = client.get("/voices")
    assert "current" in res.json()


# ── POST /voice ───────────────────────────────────────────────────────────────

def test_set_voice_valid():
    import tts as tts_mod
    voice = tts_mod.VOICES[0]
    res = client.post("/voice", json={"voice": voice})
    assert res.status_code == 200
    assert res.json()["voice"] == voice


def test_set_voice_updates_config():
    import tts as tts_mod
    import config
    voice = tts_mod.VOICES[-1]
    client.post("/voice", json={"voice": voice})
    assert config.CFG["tts"]["voice"] == voice


def test_set_voice_unknown_returns_400():
    res = client.post("/voice", json={"voice": "non_existent_voice"})
    assert res.status_code == 400


# ── POST /persona/{name} ──────────────────────────────────────────────────────

def test_switch_persona_valid():
    persona_name = list(config.PERSONAS.keys())[0]
    res = client.post(f"/persona/{persona_name}")
    assert res.status_code == 200
    assert res.json()["persona"] == persona_name


def test_switch_persona_updates_appearance():
    import state
    persona_name = list(config.PERSONAS.keys())[0]
    expected = config.PERSONAS[persona_name].get("appearance", "")
    client.post(f"/persona/{persona_name}")
    assert state.ALICE_APPEARANCE == expected


def test_switch_persona_updates_system_prompt():
    import state
    persona_name = list(config.PERSONAS.keys())[0]
    expected = config.PERSONAS[persona_name].get("system_prompt", "")
    client.post(f"/persona/{persona_name}")
    assert state.SYSTEM_PROMPT == expected


def test_switch_persona_preserves_history():
    import llm
    llm.history.append({"role": "user", "content": "test"})
    persona_name = list(config.PERSONAS.keys())[0]
    client.post(f"/persona/{persona_name}")
    assert any(m["content"] == "test" for m in llm.history)


def test_switch_persona_unknown_returns_404():
    res = client.post("/persona/NonExistentPersona99")
    assert res.status_code == 404


def test_switch_persona_applies_tts_effects():
    """Android persona should set effects=android in config.CFG['tts']."""
    if "Android" not in config.PERSONAS:
        pytest.skip("Android persona not present")
    client.post("/persona/Android")
    assert config.CFG["tts"].get("effects") == "android"


def test_switch_persona_clears_effects_on_non_android():
    """Switching away from Android should clear the effects key."""
    if "Android" not in config.PERSONAS:
        pytest.skip("Android persona not present")
    client.post("/persona/Android")
    non_android = next(n for n in config.PERSONAS if n != "Android")
    client.post(f"/persona/{non_android}")
    assert config.CFG["tts"].get("effects", "") == ""


def test_switch_persona_updates_config_name():
    """Switching to a named persona updates config.NAME."""
    named = next((n for n, p in config.PERSONAS.items() if "name" in p), None)
    if named is None:
        pytest.skip("No persona with a name field")
    client.post(f"/persona/{named}")
    assert config.NAME == config.PERSONAS[named]["name"]


def test_switch_persona_response_includes_sd_model():
    persona_name = list(config.PERSONAS.keys())[0]
    res = client.post(f"/persona/{persona_name}")
    assert "sd_model" in res.json()


def test_switch_persona_resets_nudity_state():
    import state
    state._nudity_state = "fully nude"
    persona_name = list(config.PERSONAS.keys())[0]
    client.post(f"/persona/{persona_name}")
    assert state._nudity_state == "clothed"


def test_switch_persona_resets_seed():
    import state
    state._character_seed = 12345
    state._seed_pinned    = True
    persona_name = list(config.PERSONAS.keys())[0]
    client.post(f"/persona/{persona_name}")
    assert state._character_seed == -1
    assert state._seed_pinned is False


def test_info_returns_history_fields():
    res = client.get("/info")
    data = res.json()
    assert "history_msgs" in data
    assert "history_max" in data
    assert isinstance(data["history_msgs"], int)
    assert isinstance(data["history_max"], int)


def test_export_history_structure():
    res = client.get("/history")
    assert res.status_code == 200
    data = res.json()
    assert "history" in data
    assert "memory" in data
    assert isinstance(data["history"], list)


def test_export_history_reflects_messages():
    import llm
    llm.clear_history()
    llm.history.append({"role": "user", "content": "hello"})
    res = client.get("/history")
    data = res.json()
    assert any(m["content"] == "hello" for m in data["history"])
    llm.clear_history()


def test_info_name_matches_config():
    res = client.get("/info")
    assert res.json()["name"] == config.NAME


def test_personas_list_includes_known_personas():
    res = client.get("/personas")
    names = [p["name"] for p in res.json()["personas"]]
    for key in config.PERSONAS:
        assert key in names
