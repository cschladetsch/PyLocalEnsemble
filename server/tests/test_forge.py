"""Tests for image/forge.py: Forge process lifecycle and model selection."""

import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock, call

from image import forge
import config


# ── _push_forge_settings ──────────────────────────────────────────────────────

def test_push_forge_settings_posts_known_keys(tmp_path, monkeypatch):
    """Known settings keys are POSTed; unknown ones are silently skipped."""
    forge_url = "http://localhost:7860"
    with patch("image.forge.req.get") as mock_get, \
         patch("image.forge.req.post") as mock_post:

        # First GET returns the known option keys
        mock_get.return_value.json.return_value = {
            "forge_inference_memory": 0,
            "sd_checkpoints_keep_in_cpu": False,
            "samples_save": False,
            "grid_save": False,
            "save_to_dirs": False,
            "samples_format": "png",
            "unknown_option": 42,
        }
        mock_post.return_value.status_code = 200

        forge._push_forge_settings(forge_url)

    # The unknown key should have been skipped
    posted_opts = mock_post.call_args[1]["json"]
    assert "unknown_option" not in posted_opts
    assert "forge_inference_memory" in posted_opts
    assert posted_opts["forge_inference_memory"] == 1024
    assert posted_opts["samples_save"] is False
    assert posted_opts["grid_save"] is False
    assert posted_opts["save_to_dirs"] is False
    assert posted_opts["samples_format"] == "png"


def test_push_forge_settings_handles_get_failure(tmp_path, monkeypatch):
    """If GET /sdapi/v1/options fails, no POST should be attempted."""
    with patch("image.forge.req.get", side_effect=Exception("timeout")), \
         patch("image.forge.req.post") as mock_post:
        forge._push_forge_settings("http://localhost:7860")
    mock_post.assert_not_called()


def test_push_forge_settings_handles_post_failure(tmp_path, monkeypatch, capsys):
    """A non-200 POST response emits a warning."""
    with patch("image.forge.req.get") as mock_get, \
         patch("image.forge.req.post") as mock_post:
        mock_get.return_value.json.return_value = {
            "forge_inference_memory": 0,
            "samples_save": False,
        }
        mock_post.return_value.status_code = 500
        forge._push_forge_settings("http://localhost:7860")
    captured = capsys.readouterr()
    assert "HTTP 500" in captured.out or "HTTP 500" in captured.err


# ── set_forge_model ───────────────────────────────────────────────────────────

def test_set_forge_model_finds_match_and_posts(tmp_path, monkeypatch):
    """When the model name is found in Forge's list, it POSTs the selection."""
    forge_url = "http://localhost:7860"
    with patch("image.forge.req.post") as mock_post, \
         patch("image.forge.req.get") as mock_get, \
         patch("image.forge._push_forge_settings") as mock_push:

        def _fake_get(url, **kw):
            m = MagicMock()
            if "sd-models" in url:
                m.json.return_value = [
                    {"title": "Realistic_Vision.safetensors [abc123]", "model_name": "RV"},
                    {"title": "AnythingV5.safetensors [def456]", "model_name": "AV"},
                ]
            else:
                m.json.return_value = {"sd_model_checkpoint": "AnythingV5.safetensors [def456]"}
            m.status_code = 200
            return m
        mock_get.side_effect = _fake_get
        mock_post.return_value.status_code = 200
        mock_post.return_value.ok = True

        result = forge.set_forge_model("Realistic_Vision", refresh=False)
    assert result is True
    # The options POST must contain the matched title
    posted = mock_post.call_args[1]["json"]
    assert posted["sd_model_checkpoint"] == "Realistic_Vision.safetensors [abc123]"


def test_set_forge_model_not_found_warns_and_returns_false(tmp_path, monkeypatch, capsys):
    """When the model is not in Forge's list, returns False and warns."""
    with patch("image.forge.req.get") as mock_get, \
         patch("image.forge.req.post") as mock_post:
        mock_get.return_value.json.return_value = [
            {"title": "SomeOtherModel.safetensors", "model_name": "SOM"},
        ]
        mock_get.return_value.status_code = 200
        result = forge.set_forge_model("NonExistentModel", refresh=False)
    assert result is False
    captured = capsys.readouterr()
    assert "not found" in captured.out.lower() or "not found" in captured.err.lower()


def test_set_forge_model_refresh_checkpoints_first(tmp_path, monkeypatch):
    """When refresh=True, POSTs to refresh-checkpoints before selecting."""
    forge_url = "http://localhost:7860"
    with patch("image.forge.req.post") as mock_post, \
         patch("image.forge.req.get") as mock_get:

        def _fake_get(url, **kw):
            m = MagicMock()
            m.json.return_value = [{"title": "MyModel.safetensors [abc]", "model_name": "M"}]
            m.status_code = 200
            return m

        calls = []

        def _fake_post(url, **kw):
            calls.append(url)
            m = MagicMock()
            m.status_code = 200
            m.ok = True
            return m

        mock_get.side_effect = _fake_get
        mock_post.side_effect = _fake_post

        forge.set_forge_model("MyModel", refresh=True)
    assert any("refresh-checkpoints" in c for c in calls)


def test_set_forge_model_posts_full_list_when_multiple_models(tmp_path, monkeypatch, capsys):
    """With multiple Forge models, the warning must list them."""
    with patch("image.forge.req.get") as mock_get, \
         patch("image.forge.req.post") as mock_post:
        mock_get.return_value.json.return_value = [
            {"title": "Model A.safetensors", "model_name": "A"},
            {"title": "Model B.safetensors", "model_name": "B"},
        ]
        mock_get.return_value.status_code = 200
        forge.set_forge_model("Target", refresh=False)
    captured = capsys.readouterr()
    assert "Model A" in captured.out or "Model A" in captured.err
    assert "Model B" in captured.out or "Model B" in captured.err


# ── restart_forge ─────────────────────────────────────────────────────────────

def test_restart_forge_posts_server_restart(tmp_path, monkeypatch):
    """restart_forge POSTs to /sdapi/v1/server-restart."""
    with patch("image.forge.req.post") as mock_post, \
         patch("image.forge.wait_for", return_value=True):
        forge.restart_forge()
    mock_post.assert_called_once()
    url = mock_post.call_args[0][0]
    assert "server-restart" in url


def test_restart_forge_handles_connection_reset(tmp_path, monkeypatch):
    """Connection reset during restart is silently ignored."""
    with patch("image.forge.req.post", side_effect=Exception("Connection reset")), \
         patch("image.forge.wait_for", return_value=True):
        # Should not raise
        forge.restart_forge()


def test_restart_forge_warns_if_forge_does_not_come_back(tmp_path, monkeypatch, capsys):
    with patch("image.forge.req.post", side_effect=Exception("reset")), \
         patch("image.forge.wait_for", return_value=False):
        forge.restart_forge()
    captured = capsys.readouterr()
    assert "did not come back" in captured.out.lower() or "did not come back" in captured.err.lower()


# ── warmup_forge ──────────────────────────────────────────────────────────────

def test_warmup_skips_if_forge_already_warmed(tmp_path, monkeypatch):
    """If .forge_warmed exists and checkpoint is fresh, warmup is skipped (no POST)."""
    warm_file = tmp_path / ".forge_warmed"
    warm_file.write_text(str(os.path.getmtime(tmp_path) - 100))  # 100s ago
    monkeypatch.setattr(config, "FORGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SERVER_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "sd_checkpoint": "model_v2",
    })

    with patch("image.forge.req.get") as mock_get, \
         patch("image.forge.req.post") as mock_post:
        mock_get.return_value.json.return_value = {
            "sd_model_checkpoint": "model_v2.safetensors"
        }
        mock_get.return_value.status_code = 200

        # Create a safetensors file with the same name as the checkpoint
        (tmp_path / "models").mkdir(exist_ok=True)
        (tmp_path / "models" / "model_v2.safetensors").write_bytes(b"x")

        with patch("image.forge.time.time", return_value=os.path.getmtime(tmp_path) + 50):
            forge.warmup_forge()
    # Warmup skips the txt2img POST when already warmed; it still does a GET for options check
    assert mock_post.call_count == 0


def test_warmup_sends_txt2img_when_not_warmed(tmp_path, monkeypatch):
    """When not previously warmed, warmup POSTs a 1-step generation."""
    with patch("image.forge.req.post") as mock_post, \
         patch("image.forge.req.get") as mock_get, \
         patch("image.forge._record_forge_warmed") as mock_record:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {}

        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"images": ["dummy"]}
        mock_post.return_value = resp

        monkeypatch.setattr(forge, "_is_checkpoint_fresh", lambda: False)

        forge.warmup_forge()
    assert mock_post.called
    payload = mock_post.call_args[1]["json"]
    assert payload["steps"] == 1
    assert payload["width"] == 64
    assert payload["height"] == 64
    assert payload["cfg_scale"] == 1
    assert payload["seed"] == 42
    mock_record.assert_called_once()


def test_warmup_handles_forge_response_error(tmp_path, monkeypatch, capsys):
    """Non-ok Forge response prints a warning."""
    with patch("image.forge.req.post") as mock_post, \
         patch("image.forge.req.get") as mock_get, \
         patch("image.forge._is_checkpoint_fresh", return_value=False), \
         patch("image.forge._record_forge_warmed"):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {}
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 500
        mock_post.return_value = resp
        forge.warmup_forge()
    captured = capsys.readouterr()
    assert "500" in captured.out or "500" in captured.err


def test_warmup_returns_early_if_forge_not_running(tmp_path, monkeypatch, capsys):
    """If Forge isn't reachable, warmup fails gracefully (no POST)."""
    with patch("image.forge.req.get", side_effect=Exception("Connection refused")):
        # Should not raise — warmup just warns internally
        try:
            forge.warmup_forge()
        except Exception:
            pass


# ── start_forge ───────────────────────────────────────────────────────────────

def test_start_forge_returns_true_if_already_running(tmp_path, monkeypatch):
    """If Forge responds to sd-models, start_forge returns True immediately."""
    with patch("image.forge.http_ok", return_value=True):
        result = forge.start_forge()
    assert result is True


def test_start_forge_launches_when_not_running(tmp_path, monkeypatch, capsys):
    """When Forge is down, start_forge launches the launcher and waits."""
    launchers_dir = tmp_path / "stable-diffusion-webui-forge"
    launchers_dir.mkdir(parents=True)
    (launchers_dir / "webui-user.bat").write_text("echo fake launcher")

    monkeypatch.setattr(config, "FORGE_DIR", str(launchers_dir))
    monkeypatch.setattr(config, "FORGE_BAT", str(launchers_dir / "webui-user.bat"))
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "forge_args": "--api --xformers",
    })
    # Prevent _find_forge_python() from calling real subprocess.run
    monkeypatch.setattr(forge, "_find_forge_python", lambda: "")

    with patch("image.forge.http_ok", return_value=False), \
         patch("image.forge.wait_for", return_value=True), \
         patch("subprocess.Popen") as mock_popen, \
         patch("image.forge._push_forge_settings") as mock_push:
        result = forge.start_forge()
    assert result is True
    mock_popen.assert_called_once()
    mock_push.assert_called_once()


def test_start_forge_warns_when_launcher_missing(tmp_path, monkeypatch, capsys):
    """If the launcher path doesn't exist, start_forge warns and returns False."""
    monkeypatch.setattr(config, "FORGE_BAT", str(tmp_path / "missing.bat"))
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
    })
    monkeypatch.setattr(forge, "_find_forge_python", lambda: "")
    with patch("image.forge.http_ok", return_value=False), \
         patch("subprocess.Popen") as mock_popen:
        result = forge.start_forge()
    assert result is False
    mock_popen.assert_not_called()
    captured = capsys.readouterr()
    assert "not found" in captured.out.lower() or "not found" in captured.err.lower()


def test_start_forge_appends_port_to_args(tmp_path, monkeypatch):
    """--port is appended to forge_args if not already present."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "forge_args": "--api --xformers",
    })
    # Prevent _find_forge_python() and _ensure_forge_tooling() from touching
    # the real, on-disk Forge venv / calling real subprocess.run
    monkeypatch.setattr(forge, "_find_forge_python", lambda: "")
    with patch("urllib.parse.urlparse") as mock_urlparse:
        mock_urlparse.return_value.port = 7860
        with patch("image.forge.http_ok", return_value=False), \
             patch("image.forge.wait_for", return_value=True), \
             patch("image.forge._ensure_forge_tooling"), \
             patch("subprocess.Popen") as mock_popen:
            forge.start_forge()
    _, kwargs = mock_popen.call_args
    cmdline = kwargs["env"].get("COMMANDLINE_ARGS", "")
    assert "--port 7860" in cmdline or "--port=7860" in cmdline


def test_start_forge_does_not_duplicate_port(tmp_path, monkeypatch):
    """If --port is already in forge_args, it is not duplicated."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "forge_args": "--api --port 8080",
    })
    # Prevent _find_forge_python() and _ensure_forge_tooling() from touching
    # the real, on-disk Forge venv / calling real subprocess.run
    monkeypatch.setattr(forge, "_find_forge_python", lambda: "")
    with patch("urllib.parse.urlparse") as mock_urlparse:
        mock_urlparse.return_value.port = 7860
        with patch("image.forge.http_ok", return_value=False), \
             patch("image.forge.wait_for", return_value=True), \
             patch("image.forge._ensure_forge_tooling"), \
             patch("subprocess.Popen") as mock_popen:
            forge.start_forge()
    _, kwargs = mock_popen.call_args
    cmdline = kwargs["env"].get("COMMANDLINE_ARGS", "")
    assert cmdline.count("--port") == 1
    assert "8080" in cmdline


def test_start_forge_ensures_api_flag(tmp_path, monkeypatch):
    """--api is always present in the final CLI args even if missing from forge_args."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "forge_args": "--xformers",
    })
    # Prevent _find_forge_python() and _ensure_forge_tooling() from touching
    # the real, on-disk Forge venv / calling real subprocess.run
    monkeypatch.setattr(forge, "_find_forge_python", lambda: "")
    with patch("urllib.parse.urlparse") as mock_urlparse:
        mock_urlparse.return_value.port = 7860
        with patch("image.forge.http_ok", return_value=False), \
             patch("image.forge.wait_for", return_value=True), \
             patch("image.forge._ensure_forge_tooling"), \
             patch("subprocess.Popen") as mock_popen:
            forge.start_forge()
    cmd = mock_popen.call_args[0][0]
    assert "--api" in " ".join(cmd)


# ── start_forge PATH handling ─────────────────────────────────────────────────

def test_start_forge_prepends_forge_python_dir_to_path(tmp_path, monkeypatch):
    """forge_py's install directory is prepended to PATH so a venv python.exe
    can resolve its own pythonXY.dll (Windows loads it via the DLL search path,
    which includes PATH but not a venv's own Scripts dir)."""
    launchers_dir = tmp_path / "stable-diffusion-webui-forge"
    launchers_dir.mkdir(parents=True)
    (launchers_dir / "webui-user.bat").write_text("echo fake launcher")

    monkeypatch.setattr(config, "FORGE_DIR", str(launchers_dir))
    monkeypatch.setattr(config, "FORGE_BAT", str(launchers_dir / "webui-user.bat"))
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "forge_args": "--api --xformers",
    })
    fake_python_dir = str(tmp_path / "pythoncore-3.10-64")
    fake_python = os.path.join(fake_python_dir, "python.exe")
    monkeypatch.setattr(forge, "_find_forge_python", lambda: fake_python)
    monkeypatch.setattr(os, "name", "nt", raising=False)

    with patch("image.forge.http_ok", return_value=False), \
         patch("image.forge.wait_for", return_value=True), \
         patch("subprocess.Popen") as mock_popen, \
         patch("image.forge._push_forge_settings"):
        forge.start_forge()

    _, kwargs = mock_popen.call_args
    path_value = kwargs["env"]["PATH"]
    assert path_value.startswith(fake_python_dir + os.pathsep)


def test_start_forge_leaves_path_untouched_when_python_not_found(tmp_path, monkeypatch):
    """If no Forge-compatible Python is found, PATH is left as-is (no crash)."""
    launchers_dir = tmp_path / "stable-diffusion-webui-forge"
    launchers_dir.mkdir(parents=True)
    (launchers_dir / "webui-user.bat").write_text("echo fake launcher")

    monkeypatch.setattr(config, "FORGE_DIR", str(launchers_dir))
    monkeypatch.setattr(config, "FORGE_BAT", str(launchers_dir / "webui-user.bat"))
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "forge_args": "--api --xformers",
    })
    monkeypatch.setattr(forge, "_find_forge_python", lambda: "")

    with patch("image.forge.http_ok", return_value=False), \
         patch("image.forge.wait_for", return_value=True), \
         patch("subprocess.Popen") as mock_popen, \
         patch("image.forge._push_forge_settings"):
        forge.start_forge()

    _, kwargs = mock_popen.call_args
    assert kwargs["env"]["PATH"] == os.environ.get("PATH", "")


# ── set_forge_model exception safety ─────────────────────────────────────────

def test_set_forge_model_clears_warmed_on_exception(tmp_path, monkeypatch):
    """An exception during set_forge_model clears the warmed timestamp."""
    with patch("image.forge.req.get", side_effect=Exception("boom")), \
         patch("image.forge._clear_forge_warmed") as mock_clear:
        result = forge.set_forge_model("MyModel", refresh=False)
    assert result is False
    mock_clear.assert_called_once()
