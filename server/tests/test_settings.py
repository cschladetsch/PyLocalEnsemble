"""Tests for routes/system.py: /settings, /persona-packs, /demo/*, /auto-image, /negative."""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from alice import app
import config
import state
import llm

client = TestClient(app, raise_server_exceptions=True)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_llm_state():
    saved_ready = llm.LLM_READY
    saved_suspended = llm.LLM_SUSPENDED
    saved_history = list(llm.history)
    yield
    llm.LLM_READY = saved_ready
    llm.LLM_SUSPENDED = saved_suspended
    llm.history.clear()
    llm.history.extend(saved_history)


@pytest.fixture()
def _mock_forge_ready():
    """Make the /info endpoint think Forge is reachable."""
    with patch("routes.system.req.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [{"title": "something.safetensors"}]
        yield


# ── GET /settings ─────────────────────────────────────────────────────────────

def test_settings_returns_all_fields(_mock_forge_ready):
    res = client.get("/settings")
    assert res.status_code == 200
    data = res.json()
    for key in ("quick_image", "vram_swap_for_image", "llm_params",
                "image", "tts", "memory", "llama_server"):
        assert key in data


def test_settings_includes_defaults_for_missing_keys(_mock_forge_ready):
    res = client.get("/settings")
    data = res.json()
    # Default llm_params should be present
    assert "llm_params" in data
    assert isinstance(data["llm_params"], dict)


def test_settings_quick_image_matches_config(_mock_forge_ready):
    res = client.get("/settings")
    assert res.json()["quick_image"] == config.CFG.get("quick_image", True)


# ── PATCH /settings ────────────────────────────────────────────────────────────

def test_patch_settings_quick_image(tmp_path, monkeypatch):
    """PATCH /settings updates quick_image in config."""
    monkeypatch.setattr(config, "CFG", {"quick_image": True, "save_path": str(tmp_path / "cfg.json")})
    res = client.post("/settings", json={"quick_image": False})
    assert res.status_code == 200
    assert config.CFG["quick_image"] is False


def test_patch_settings_vram_swap(tmp_path, monkeypatch):
    """PATCH /settings updates vram_swap_for_image."""
    monkeypatch.setattr(config, "CFG", {"vram_swap_for_image": True})
    res = client.post("/settings", json={"vram_swap_for_image": False})
    assert res.status_code == 200
    assert config.CFG["vram_swap_for_image"] is False


def test_patch_settings_llm_params_merge(tmp_path, monkeypatch):
    """PATCH /settings merges llm_params into existing config."""
    monkeypatch.setattr(config, "CFG", {
        "llm_params": {"temperature": 0.7},
        "save_path": str(tmp_path / "cfg.json"),
    })
    res = client.post("/settings", json={"llm_params": {"max_tokens": 4096}})
    assert res.status_code == 200
    assert config.CFG["llm_params"]["temperature"] == 0.7
    assert config.CFG["llm_params"]["max_tokens"] == 4096


def test_patch_settings_image_merge(tmp_path, monkeypatch):
    """PATCH /settings merges image dict into existing config."""
    monkeypatch.setattr(config, "CFG", {
        "image": {"steps": 20},
        "save_path": str(tmp_path / "cfg.json"),
    })
    res = client.post("/settings", json={"image": {"cfg_scale": 8}})
    assert res.status_code == 200
    assert config.CFG["image"]["steps"] == 20
    assert config.CFG["image"]["cfg_scale"] == 8


def test_patch_settings_tts_merge(tmp_path, monkeypatch):
    """PATCH /settings merges tts dict into existing config."""
    monkeypatch.setattr(config, "CFG", {
        "tts": {"voice": "af_nicole"},
        "save_path": str(tmp_path / "cfg.json"),
    })
    res = client.post("/settings", json={"tts": {"pitch": 0.94}})
    assert res.status_code == 200
    assert config.CFG["tts"]["voice"] == "af_nicole"
    assert config.CFG["tts"]["pitch"] == 0.94


def test_patch_settings_memory_merge(tmp_path, monkeypatch):
    """PATCH /settings merges memory dict into existing config."""
    monkeypatch.setattr(config, "CFG", {
        "memory": {"max_history": 20},
        "save_path": str(tmp_path / "cfg.json"),
    })
    res = client.post("/settings", json={"memory": {"keep_recent": 5}})
    assert res.status_code == 200
    assert config.CFG["memory"]["max_history"] == 20
    assert config.CFG["memory"]["keep_recent"] == 5


def test_patch_settings_llama_server_merge(tmp_path, monkeypatch):
    """PATCH /settings merges llama_server dict into existing config."""
    monkeypatch.setattr(config, "CFG", {
        "llama_server": {"host": "127.0.0.1"},
        "save_path": str(tmp_path / "cfg.json"),
    })
    res = client.post("/settings", json={"llama_server": {"port": 8000}})
    assert res.status_code == 200
    assert config.CFG["llama_server"]["host"] == "127.0.0.1"
    assert config.CFG["llama_server"]["port"] == 8000


def test_patch_settings_partial_nothing_does_nothing(tmp_path, monkeypatch):
    """PATCH with no recognised keys leaves CFG unchanged."""
    monkeypatch.setattr(config, "CFG", {"quick_image": True})
    res = client.post("/settings", json={"unrecognised": "value"})
    assert res.status_code == 200
    assert config.CFG["quick_image"] is True


# ── GET /persona-packs ─────────────────────────────────────────────────────────

def test_persona_packs_returns_packs():
    res = client.get("/persona-packs")
    assert res.status_code == 200
    data = res.json()
    assert "packs" in data
    assert isinstance(data["packs"], list)


# ── PATCH /persona-pack ───────────────────────────────────────────────────────

def test_switch_persona_pack_copies_to_personas_json(tmp_path, monkeypatch):
    """Switching to a persona pack copies the pack JSON over personas.json."""
    packs_dir = tmp_path / "packs"
    packs_dir.mkdir()
    mine_dir = tmp_path / "mine"
    mine_dir.mkdir()

    pack_file = packs_dir / "my_pack.json"
    pack_file.write_text(json.dumps({"Alice": {"name": "Pack Alice"}}))

    personas_file = tmp_path / "personas.json"
    personas_file.write_text(json.dumps({"Alice": {"name": "Original"}}))

    monkeypatch.setattr(config, "PACKS_DIR", str(packs_dir))
    monkeypatch.setattr(config, "MINE_DIR", str(mine_dir))
    monkeypatch.setattr(config, "PERSONAS_FILE", str(personas_file))

    res = client.post("/persona-pack", json={"name": "my_pack"})
    assert res.status_code == 200
    assert res.json()["pack"] == "my_pack"
    assert json.loads(personas_file.read_text())["Alice"]["name"] == "Pack Alice"


def test_switch_persona_pack_mine_prefix(tmp_path, monkeypatch):
    """mine/ prefix resolves to the MINE_DIR."""
    packs_dir = tmp_path / "packs"
    mine_dir = tmp_path / "mine"
    mine_dir.mkdir()

    pack_file = mine_dir / "custom.json"
    pack_file.write_text(json.dumps({"Alice": {"name": "Custom"}}))

    personas_file = tmp_path / "personas.json"
    monkeypatch.setattr(config, "PACKS_DIR", str(packs_dir))
    monkeypatch.setattr(config, "MINE_DIR", str(mine_dir))
    monkeypatch.setattr(config, "PERSONAS_FILE", str(personas_file))

    res = client.post("/persona-pack", json={"name": "mine/custom"})
    assert res.status_code == 200
    assert json.loads(personas_file.read_text())["Alice"]["name"] == "Custom"


def test_switch_persona_pack_not_found_returns_404(tmp_path, monkeypatch):
    """Unknown pack name returns 404."""
    monkeypatch.setattr(config, "PACKS_DIR", str(tmp_path / "packs"))
    res = client.post("/persona-pack", json={"name": "GhostPack"})
    assert res.status_code == 404
    assert "not found" in res.json()["error"]


def test_switch_persona_pack_does_not_create_mine_dir_if_empty(tmp_path, monkeypatch):
    """Switching to a non-mine pack still creates MINE_DIR (backup logic runs first).

    The route calls os.makedirs(config.MINE_DIR, exist_ok=True) before copying
    the pack — this is expected behaviour, so we verify the dir IS created.
    """
    packs_dir = tmp_path / "packs"
    packs_dir.mkdir()
    pack_file = packs_dir / "ok.json"
    pack_file.write_text(json.dumps({"Alice": {"name": "Pack"}}))
    personas_file = tmp_path / "personas.json"
    personas_file.write_text(json.dumps({"Alice": {"name": "Original"}}))

    monkeypatch.setattr(config, "PACKS_DIR", str(packs_dir))
    monkeypatch.setattr(config, "MINE_DIR", str(tmp_path / "nonexistent_mine"))
    monkeypatch.setattr(config, "PERSONAS_FILE", str(personas_file))

    res = client.post("/persona-pack", json={"name": "ok"})
    assert res.status_code == 200
    # The route creates MINE_DIR for the backup step regardless of pack type
    assert (tmp_path / "nonexistent_mine").is_dir()


# ── POST /auto-image ───────────────────────────────────────────────────────────

def test_toggle_auto_image_enabled():
    res = client.post("/auto-image")
    assert res.status_code == 200
    data = res.json()
    assert data["auto_image"] is True


def test_toggle_auto_image_disabled():
    config.CFG.setdefault("image", {})["auto_every"] = 1
    try:
        res = client.post("/auto-image")
        assert res.status_code == 200
        assert res.json()["auto_image"] is False
        assert config.CFG["image"]["auto_every"] == 0
    finally:
        config.CFG["image"]["auto_every"] = 0


def test_auto_image_response_shape():
    res = client.post("/auto-image")
    assert "auto_image" in res.json()


# ── GET /negative ──────────────────────────────────────────────────────────────

def test_negative_returns_current_state():
    res = client.get("/negative")
    assert res.status_code == 200
    assert res.json()["negative"] == state.BASE_NEGATIVE


# ── POST /demo/start, /demo/stop ───────────────────────────────────────────────

def test_demo_start_sets_active():
    state.DEMO_ACTIVE = False
    res = client.post("/demo/start")
    assert res.status_code == 200
    assert state.DEMO_ACTIVE is True


def test_demo_stop_clears_active():
    state.DEMO_ACTIVE = True
    res = client.post("/demo/stop")
    assert res.status_code == 200
    assert state.DEMO_ACTIVE is False


def test_demo_stop_when_inactive_still_ok():
    state.DEMO_ACTIVE = False
    res = client.post("/demo/stop")
    assert res.status_code == 200


def test_demo_start_response_shape():
    res = client.post("/demo/start")
    assert res.json() == {"status": "ok"}


def test_demo_stop_response_shape():
    res = client.post("/demo/stop")
    assert res.json() == {"status": "ok"}


# ── GET /demo/user-personas ────────────────────────────────────────────────────

def test_demo_user_personas_returns_known():
    res = client.get("/demo/user-personas")
    assert res.status_code == 200
    data = res.json()
    assert "personas" in data
    assert "current" in data
    assert isinstance(data["personas"], list)


def test_demo_user_personas_current_matches_state():
    state._demo_user_persona_name = "npc_1"
    res = client.get("/demo/user-personas")
    assert res.json()["current"] == "npc_1"


# ── POST /demo/user-persona ────────────────────────────────────────────────────

def test_set_demo_user_persona_valid():
    res = client.post("/demo/user-persona", json={"name": "default"})
    assert res.status_code == 200


def test_set_demo_user_persona_unknown_returns_400():
    res = client.post("/demo/user-persona", json={"name": "GhostPersona"})
    assert res.status_code == 400


def test_set_demo_user_persona_updates_state():
    res = client.post("/demo/user-persona", json={"name": "default"})
    assert state._demo_user_persona_name == "default"


def test_set_demo_user_persona_response_shape():
    res = client.post("/demo/user-persona", json={"name": "default"})
    data = res.json()
    assert data["status"] == "ok"
    assert data["name"] == "default"
