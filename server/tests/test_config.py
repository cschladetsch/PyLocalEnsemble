import json
import pytest
from unittest.mock import patch


def test_load_config_returns_defaults_when_no_file(tmp_path):
    # Also patch ALICE_DIR so load_config() can't find alice.example.json
    with patch("config.CONFIG_FILE", str(tmp_path / "alice.json")), \
         patch("config.ALICE_DIR", str(tmp_path)):
        import config
        cfg = config.load_config()
    assert cfg["name"] == "Alice"
    assert "system_prompt" in cfg
    assert "appearance" in cfg
    assert cfg["image"]["steps"] == 25


def test_load_config_merges_user_values(tmp_path):
    cfg_file = tmp_path / "alice.json"
    cfg_file.write_text(json.dumps({"name": "Nadia", "image": {"steps": 40}}))
    with patch("config.CONFIG_FILE", str(cfg_file)):
        import config
        cfg = config.load_config()
    assert cfg["name"] == "Nadia"
    assert cfg["image"]["steps"] == 40
    assert cfg["image"]["cfg_scale"] == 7          # default preserved
    assert "system_prompt" in cfg                  # top-level default preserved


def test_load_config_handles_corrupt_file(tmp_path):
    cfg_file = tmp_path / "alice.json"
    cfg_file.write_text("NOT JSON {{")
    with patch("config.CONFIG_FILE", str(cfg_file)):
        import config
        cfg = config.load_config()
    assert cfg["name"] == "Alice"                  # falls back to defaults


def test_resolve_path_absolute_existing(tmp_path):
    import config
    f = tmp_path / "model.bin"
    f.write_bytes(b"x")
    assert config.resolve_path(str(f)) == str(f)


def test_resolve_path_relative_joins_alice_dir():
    import config
    result = config.resolve_path("models/tts")
    assert result.startswith(config.ALICE_DIR)
    assert "models" in result


def test_resolve_path_empty_returns_empty():
    import config
    assert config.resolve_path("") == ""


def test_load_personas_includes_default(tmp_path):
    with patch("config.PERSONAS_FILE", str(tmp_path / "personas.json")):
        import config
        personas = config.load_personas(config.CFG)
    assert "Alice" in personas
    assert "system_prompt" in personas["Alice"]
    assert "appearance" in personas["Alice"]


def test_load_personas_merges_file(tmp_path):
    pf = tmp_path / "personas.json"
    pf.write_text(json.dumps({
        "TestChar": {"system_prompt": "You are test.", "appearance": "test look"}
    }))
    with patch("config.PERSONAS_FILE", str(pf)):
        import config
        personas = config.load_personas(config.CFG)
    assert "Alice" in personas
    assert "TestChar" in personas
    assert personas["TestChar"]["system_prompt"] == "You are test."


def test_load_personas_handles_corrupt_file(tmp_path, capsys):
    pf = tmp_path / "personas.json"
    pf.write_text("INVALID")
    with patch("config.PERSONAS_FILE", str(pf)):
        import config
        personas = config.load_personas(config.CFG)
    assert "Alice" in personas                   # falls back gracefully
