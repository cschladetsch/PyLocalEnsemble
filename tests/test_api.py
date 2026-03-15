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
