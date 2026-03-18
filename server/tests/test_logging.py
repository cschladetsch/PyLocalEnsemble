import logging


def test_init_logging_creates_shared_log_file(tmp_path, monkeypatch):
    import logging_setup

    monkeypatch.setattr(logging_setup, "LOG_DIR", str(tmp_path / "log"))
    path = logging_setup.init_logging("unit-test")
    logging.getLogger("alice.test").info("hello log")

    for handler in logging.getLogger().handlers:
        if hasattr(handler, "flush"):
            handler.flush()

    assert path.endswith("unit-test.log")
    assert (tmp_path / "log" / "unit-test.log").exists()
    assert "hello log" in (tmp_path / "log" / "unit-test.log").read_text(encoding="utf-8")
