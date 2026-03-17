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
    assert "Alice" in personas
    assert personas["Alice"]["font_key"] == "default"


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


def test_switch_persona_resets_decay_counter():
    import state
    state._nudity_state = "topless"
    state._nudity_turns_since_keyword = 2
    persona_name = list(config.PERSONAS.keys())[0]
    client.post(f"/persona/{persona_name}")
    assert state._nudity_turns_since_keyword == 0


# ── GET /negative ──────────────────────────────────────────────────────────────

def test_negative_returns_string():
    res = client.get("/negative")
    assert res.status_code == 200
    assert "negative" in res.json()
    assert isinstance(res.json()["negative"], str)


def test_negative_matches_state():
    import state
    res = client.get("/negative")
    assert res.json()["negative"] == state.BASE_NEGATIVE


# ── state.decay_nudity_state ───────────────────────────────────────────────────

@pytest.fixture()
def clean_nudity_state():
    """Restore nudity state globals after each decay test."""
    import state
    saved_state   = state._nudity_state
    saved_counter = state._nudity_turns_since_keyword
    yield
    state._nudity_state              = saved_state
    state._nudity_turns_since_keyword = saved_counter


def test_decay_no_change_on_sexual_keyword(clean_nudity_state):
    import state
    state._nudity_state = "topless"
    state._nudity_turns_since_keyword = 0
    state.decay_nudity_state("show me your breasts")
    assert state._nudity_state == "topless"
    assert state._nudity_turns_since_keyword == 0


def test_decay_increments_counter_on_non_sexual(clean_nudity_state):
    import state
    state._nudity_state = "topless"
    state._nudity_turns_since_keyword = 0
    state.decay_nudity_state("how are you today")
    assert state._nudity_turns_since_keyword == 1
    assert state._nudity_state == "topless"   # not yet decayed


def test_decay_fires_after_three_turns(clean_nudity_state):
    import state
    state._nudity_state = "topless"
    state._nudity_turns_since_keyword = 0
    for msg in ["hello", "nice day", "tell me a story"]:
        state.decay_nudity_state(msg)
    assert state._nudity_state == "clothed"
    assert state._nudity_turns_since_keyword == 0


def test_decay_does_not_go_below_clothed(clean_nudity_state):
    import state
    state._nudity_state = "clothed"
    state._nudity_turns_since_keyword = 5
    state.decay_nudity_state("hello")
    assert state._nudity_state == "clothed"


def test_decay_unknown_state_resets_to_clothed(clean_nudity_state):
    import state
    state._nudity_state = "semi-nude"   # invalid — not in _NUDITY_ORDER
    state._nudity_turns_since_keyword = 3
    state.decay_nudity_state("good morning")
    assert state._nudity_state == "clothed"


def test_decay_fully_nude_decays_one_step(clean_nudity_state):
    import state
    state._nudity_state = "fully nude"
    state._nudity_turns_since_keyword = 0
    for msg in ["hello", "how are you", "nice weather"]:
        state.decay_nudity_state(msg)
    assert state._nudity_state == "bottomless"


# ── GET /seed, POST /seed/pin, POST /seed/unpin ───────────────────────────────

@pytest.fixture()
def reset_seed_state():
    import state
    saved_seed   = state._character_seed
    saved_pinned = state._seed_pinned
    saved_last   = state.last_seed
    yield
    state._character_seed = saved_seed
    state._seed_pinned    = saved_pinned
    state.last_seed       = saved_last


def test_get_seed_returns_seed_and_pinned(reset_seed_state):
    import state
    state.last_seed    = 99
    state._seed_pinned = False
    res = client.get("/seed")
    assert res.status_code == 200
    data = res.json()
    assert "seed"   in data
    assert "pinned" in data


def test_pin_seed_sets_pinned(reset_seed_state):
    import state
    state.last_seed       = 42
    state._seed_pinned    = False
    state._character_seed = -1
    res = client.post("/seed/pin")
    assert res.status_code == 200
    assert res.json()["pinned"] is True
    assert res.json()["seed"]   == 42
    assert state._seed_pinned    is True
    assert state._character_seed == 42


def test_unpin_seed_clears_pinned(reset_seed_state):
    import state
    state._seed_pinned    = True
    state._character_seed = 42
    res = client.post("/seed/unpin")
    assert res.status_code == 200
    assert res.json()["pinned"] is False
    assert state._seed_pinned    is False
    assert state._character_seed == -1


def test_pin_then_unpin_roundtrip(reset_seed_state):
    import state
    state.last_seed = 7
    client.post("/seed/pin")
    assert state._seed_pinned is True
    client.post("/seed/unpin")
    assert state._seed_pinned    is False
    assert state._character_seed == -1


# ── POST /model ───────────────────────────────────────────────────────────────

@pytest.fixture()
def reset_model_state():
    import llm
    saved_det   = llm._DETECTED_MODEL
    saved_model = config.CFG.get("llama_model")
    yield
    llm._DETECTED_MODEL       = saved_det
    config.CFG["llama_model"] = saved_model


def test_switch_model_clears_detected_model(reset_model_state):
    import llm
    llm._DETECTED_MODEL = "old-model"
    res = client.post("/model", json={"path": "new-model"})
    assert res.status_code == 200
    assert llm._DETECTED_MODEL is None


def test_switch_model_updates_llama_model_config(reset_model_state):
    res = client.post("/model", json={"path": "custom-model-path"})
    assert res.status_code == 200
    assert config.CFG["llama_model"] == "custom-model-path"


def test_switch_model_clears_history(reset_model_state):
    import llm
    llm.history.append({"role": "user", "content": "test"})
    client.post("/model", json={"path": "any-model"})
    assert llm.history == []


def test_switch_model_returns_model_name(reset_model_state):
    res = client.post("/model", json={"path": "my-model"})
    assert res.json()["model"] == "my-model"
