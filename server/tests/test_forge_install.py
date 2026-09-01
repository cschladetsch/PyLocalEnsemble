"""Tests for installer/forge_install.py: venv Python-version check and PATH handling."""

import os
from unittest.mock import MagicMock

from installer import forge_install as fi


def _make_forge_dir(tmp_path):
    forge_dir = tmp_path / "stable-diffusion-webui-forge"
    venv_scripts = forge_dir / "venv" / "Scripts"
    venv_scripts.mkdir(parents=True)
    (venv_scripts / "python.exe").write_bytes(b"")
    (forge_dir / "webui.bat").write_text("echo fake launcher")

    # Pretend all checkpoints are already downloaded so install_forge doesn't
    # try to hit the network.
    sd_dir = forge_dir / "models" / "Stable-diffusion"
    sd_dir.mkdir(parents=True)
    for filename, _ in fi._MODELS:
        (sd_dir / filename).write_bytes(b"x")

    return forge_dir


def test_venv_check_prepends_forge_python_dir_to_path(tmp_path, monkeypatch):
    """The venv version-check subprocess call must get forge_py's directory
    prepended to PATH, so a Windows venv python.exe can resolve its own
    pythonXY.dll instead of failing to launch at all."""
    forge_dir = _make_forge_dir(tmp_path)
    fake_python_dir = str(tmp_path / "pythoncore-3.10-64")
    fake_python = os.path.join(fake_python_dir, "python.exe")

    monkeypatch.setattr(fi, "FORGE_DIR", str(forge_dir))
    monkeypatch.setattr(fi, "FORGE_BAT", str(forge_dir / "webui.bat"))
    monkeypatch.setattr(fi, "_find_forge_python", lambda: fake_python)
    monkeypatch.setattr(fi, "install_adetailer", lambda: None)
    monkeypatch.setattr(fi, "_download", lambda *a, **k: None)
    monkeypatch.setattr(os, "name", "nt", raising=False)

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        return MagicMock(stdout="Python 3.10.11", stderr="", returncode=0)

    monkeypatch.setattr(fi.subprocess, "run", fake_run)

    fi.install_forge({})

    assert "env" in captured and captured["env"] is not None
    path_value = captured["env"]["PATH"]
    assert path_value.startswith(fake_python_dir + os.pathsep)


def test_venv_check_skips_path_prepend_when_python_not_found(tmp_path, monkeypatch):
    """If no compatible Python is found, the version-check env is left untouched
    (no crash, no bogus PATH entry)."""
    forge_dir = _make_forge_dir(tmp_path)

    monkeypatch.setattr(fi, "FORGE_DIR", str(forge_dir))
    monkeypatch.setattr(fi, "FORGE_BAT", str(forge_dir / "webui.bat"))
    monkeypatch.setattr(fi, "_find_forge_python", lambda: "")
    monkeypatch.setattr(fi, "install_adetailer", lambda: None)
    monkeypatch.setattr(fi, "_download", lambda *a, **k: None)

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        return MagicMock(stdout="Python 3.10.11", stderr="", returncode=0)

    monkeypatch.setattr(fi.subprocess, "run", fake_run)

    fi.install_forge({})

    env = captured.get("env")
    assert env is not None
    assert env.get("PATH") == os.environ.get("PATH", "")


def test_venv_with_matching_version_is_not_deleted(tmp_path, monkeypatch):
    """A correctly-versioned venv survives the check (regression guard for the
    DLL-load failure being misread as 'wrong Python version')."""
    forge_dir = _make_forge_dir(tmp_path)
    venv_dir = forge_dir / "venv"
    fake_python = os.path.join(str(tmp_path / "pythoncore-3.10-64"), "python.exe")

    monkeypatch.setattr(fi, "FORGE_DIR", str(forge_dir))
    monkeypatch.setattr(fi, "FORGE_BAT", str(forge_dir / "webui.bat"))
    monkeypatch.setattr(fi, "_find_forge_python", lambda: fake_python)
    monkeypatch.setattr(fi, "install_adetailer", lambda: None)
    monkeypatch.setattr(fi, "_download", lambda *a, **k: None)
    monkeypatch.setattr(
        fi.subprocess, "run",
        lambda cmd, **kw: MagicMock(stdout="Python 3.10.11", stderr="", returncode=0),
    )

    fi.install_forge({})

    assert venv_dir.exists()
