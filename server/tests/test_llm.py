"""Tests for llm.py: history operations and memory compression."""
import pytest
from unittest.mock import patch
import llm
import config


@pytest.fixture(autouse=True)
def reset_llm():
    """Restore llm globals after each test."""
    saved_hist = list(llm.history)
    saved_mem  = llm.memory
    saved_det  = llm._DETECTED_MODEL
    yield
    llm.history.clear()
    llm.history.extend(saved_hist)
    llm.memory          = saved_mem
    llm._DETECTED_MODEL = saved_det


def _fill(n: int):
    llm.history.clear()
    for i in range(n):
        llm.history.append({"role": "user", "content": str(i)})


# ── clear_history ──────────────────────────────────────────────────────────────

def test_clear_history_empties_history():
    llm.history.append({"role": "user", "content": "hi"})
    llm.clear_history()
    assert llm.history == []


def test_clear_history_clears_memory():
    llm.memory = "some prior context"
    llm.clear_history()
    assert llm.memory == ""


def test_clear_history_deletes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_FILE", str(tmp_path / "h.json"))
    (tmp_path / "h.json").write_text("{}")
    llm.clear_history()
    assert not (tmp_path / "h.json").exists()


def test_clear_history_tolerates_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_FILE", str(tmp_path / "nope.json"))
    llm.clear_history()  # must not raise


# ── save_history / load_history ────────────────────────────────────────────────

def test_save_load_roundtrip(tmp_path, monkeypatch):
    import state
    monkeypatch.setattr(config, "HISTORY_FILE", str(tmp_path / "h.json"))
    monkeypatch.setattr(state, "_active_persona_key", "Alice")
    llm.history.clear()
    llm.history.append({"role": "user", "content": "hello"})
    llm.memory = "memory text"
    llm.save_history()

    llm.history.clear()
    llm.memory = ""
    llm.load_history()

    assert any(m["content"] == "hello" for m in llm.history)
    assert llm.memory == "memory text"


def test_load_history_missing_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_FILE", str(tmp_path / "nope.json"))
    llm.history.clear()
    llm.load_history()
    assert llm.history == []


def test_load_history_corrupt_file_is_noop(tmp_path, monkeypatch):
    f = tmp_path / "bad.json"
    f.write_text("not json {{{")
    monkeypatch.setattr(config, "HISTORY_FILE", str(f))
    llm.history.clear()
    llm.load_history()  # must not raise
    assert llm.history == []


def test_save_history_persists_multiple_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_FILE", str(tmp_path / "h.json"))
    llm.history.clear()
    llm.history.extend([
        {"role": "user",      "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ])
    llm.save_history()

    import json
    data = json.loads((tmp_path / "h.json").read_text())
    assert len(data["history"]) == 2
    assert data["history"][1]["content"] == "hi there"


# ── compress_history ───────────────────────────────────────────────────────────

def test_compress_below_limit_does_nothing():
    mem_cfg  = config.CFG.get("memory", config._DEFAULT_CONFIG["memory"])
    max_hist = mem_cfg["max_history"]
    _fill(max_hist - 1)
    original = list(llm.history)
    with patch.object(llm, "_summarise", return_value="s") as mock:
        llm.compress_history()
    mock.assert_not_called()
    assert list(llm.history) == original


def test_compress_trims_old_messages():
    mem_cfg  = config.CFG.get("memory", config._DEFAULT_CONFIG["memory"])
    max_hist = mem_cfg["max_history"]
    keep     = mem_cfg["keep_recent"]
    _fill(max_hist + 4)
    with patch.object(llm, "_summarise", return_value="summary"), \
         patch.object(llm, "save_history"):
        llm.compress_history()
    assert len(llm.history) == keep


def test_compress_keeps_most_recent_messages():
    mem_cfg  = config.CFG.get("memory", config._DEFAULT_CONFIG["memory"])
    max_hist = mem_cfg["max_history"]
    keep     = mem_cfg["keep_recent"]
    _fill(max_hist + 4)
    last_contents = [m["content"] for m in llm.history[-keep:]]
    with patch.object(llm, "_summarise", return_value="summary"), \
         patch.object(llm, "save_history"):
        llm.compress_history()
    retained = [m["content"] for m in llm.history]
    assert retained == last_contents


def test_compress_accumulates_memory():
    mem_cfg  = config.CFG.get("memory", config._DEFAULT_CONFIG["memory"])
    max_hist = mem_cfg["max_history"]
    llm.memory = "old memory"
    _fill(max_hist + 4)
    with patch.object(llm, "_summarise", return_value="new summary"), \
         patch.object(llm, "save_history"):
        llm.compress_history()
    assert "old memory" in llm.memory
    assert "new summary" in llm.memory


def test_compress_empty_summary_leaves_memory_unchanged():
    mem_cfg  = config.CFG.get("memory", config._DEFAULT_CONFIG["memory"])
    max_hist = mem_cfg["max_history"]
    llm.memory = "existing"
    _fill(max_hist + 4)
    with patch.object(llm, "_summarise", return_value=""), \
         patch.object(llm, "save_history"):
        llm.compress_history()
    assert llm.memory == "existing"


def test_compress_memory_trimmed_at_sentence_boundary(monkeypatch):
    """Overflow trim starts after the first '. ' in the tail window."""
    monkeypatch.setitem(config.CFG, "memory", {"max_history": 4, "keep_recent": 2, "max_chars": 60})
    llm.memory = ""
    _fill(6)
    summary = "First sentence. Second sentence is much longer and should survive."
    with patch.object(llm, "_summarise", return_value=summary), \
         patch.object(llm, "save_history"):
        llm.compress_history()
    assert len(llm.memory) <= 60
    assert not llm.memory.startswith("First")
    assert "Second" in llm.memory


def test_compress_memory_fallback_when_no_period(monkeypatch):
    """No '. ' in the trimmed window → use raw tail."""
    monkeypatch.setitem(config.CFG, "memory", {"max_history": 4, "keep_recent": 2, "max_chars": 20})
    llm.memory = ""
    _fill(6)
    with patch.object(llm, "_summarise", return_value="X" * 100), \
         patch.object(llm, "save_history"):
        llm.compress_history()
    assert len(llm.memory) <= 20


# ── list_models — size_gb ─────────────────────────────────────────────────────

def test_list_models_returns_size_gb(tmp_path, monkeypatch):
    """list_models must return (name, path, size_gb) triples."""
    fake = tmp_path / "test_model.gguf"
    fake.write_bytes(b"x" * 4_400_000_000)
    monkeypatch.setitem(config.CFG, "model_path", str(fake))
    monkeypatch.setitem(config.CFG, "model_dir", "")
    result = llm.list_models()
    assert len(result) > 0
    names, paths, sizes = zip(*result)
    assert "test_model.gguf" in names
    idx = names.index("test_model.gguf")
    assert sizes[idx] == pytest.approx(4.4, abs=0.2)


def test_list_models_triple_format(tmp_path, monkeypatch):
    """Each element returned by list_models must be a 3-tuple (name, path, size_gb)."""
    fake = tmp_path / "x.gguf"
    fake.write_bytes(b"x" * 1_000_000_000)
    monkeypatch.setitem(config.CFG, "model_path", str(fake))
    monkeypatch.setitem(config.CFG, "model_dir", "")
    for entry in llm.list_models():
        assert len(entry) == 3
        name, path, size = entry
        assert isinstance(name, str)
        assert isinstance(path, str)
        assert isinstance(size, float)


def test_list_models_size_zero_on_missing_file(tmp_path, monkeypatch):
    """If stat fails, size_gb should default to 0.0 rather than raising."""
    fake = tmp_path / "ghost.gguf"
    fake.write_bytes(b"x" * 100)
    monkeypatch.setitem(config.CFG, "model_path", str(fake))
    monkeypatch.setitem(config.CFG, "model_dir", "")
    # Remove the file after list_models scans the directory but before stat
    # — simulate by patching os.path.getsize to raise
    with patch("os.path.getsize", side_effect=OSError("permission denied")):
        result = llm.list_models()
    sizes = [s for _, _, s in result]
    assert all(s == 0.0 for s in sizes), "OSError in getsize must yield size_gb=0.0"
