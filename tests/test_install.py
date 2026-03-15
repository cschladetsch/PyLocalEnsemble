import pytest
from unittest.mock import patch


def _asset(name):
    return {"name": name, "size": 1_000_000, "browser_download_url": f"https://example.com/{name}"}


ASSETS = [
    _asset("llama-b3000-bin-win-vulkan-x64.zip"),
    _asset("llama-b3000-bin-win-avx2-x64.zip"),
    _asset("llama-b3000-bin-win-cpu-x64.zip"),
    _asset("llama-b3000-bin-macos-arm64.zip"),
    _asset("llama-b3000-bin-macos-x64.zip"),
    _asset("llama-b3000-bin-ubuntu-x64.zip"),
]


from install import _pick_llama_asset


def test_windows_prefers_vulkan():
    with patch("platform.system", return_value="Windows"):
        result = _pick_llama_asset(ASSETS)
    assert result is not None
    assert "vulkan" in result["name"]


def test_windows_falls_back_to_avx2_without_vulkan():
    assets = [a for a in ASSETS if "vulkan" not in a["name"]]
    with patch("platform.system", return_value="Windows"):
        result = _pick_llama_asset(assets)
    assert result is not None
    assert "avx2" in result["name"]


def test_windows_falls_back_to_cpu():
    assets = [a for a in ASSETS if "win" in a["name"] and "cpu" in a["name"]]
    with patch("platform.system", return_value="Windows"):
        result = _pick_llama_asset(assets)
    assert result is not None
    assert "cpu" in result["name"]


def test_macos_arm64():
    with patch("platform.system", return_value="Darwin"), \
         patch("platform.machine", return_value="arm64"):
        result = _pick_llama_asset(ASSETS)
    assert result is not None
    assert "arm64" in result["name"]


def test_macos_x64():
    with patch("platform.system", return_value="Darwin"), \
         patch("platform.machine", return_value="x86_64"):
        result = _pick_llama_asset(ASSETS)
    assert result is not None
    assert "macos" in result["name"]
    assert "arm64" not in result["name"]


def test_linux_picks_ubuntu():
    with patch("platform.system", return_value="Linux"):
        result = _pick_llama_asset(ASSETS)
    assert result is not None
    assert "ubuntu" in result["name"]


def test_unknown_platform_falls_back_to_linux_build():
    # Non-Windows, non-Darwin falls through to the Linux branch (ubuntu build)
    with patch("platform.system", return_value="FreeBSD"):
        result = _pick_llama_asset(ASSETS)
    assert result is not None
    assert "ubuntu" in result["name"]


def test_empty_asset_list_returns_none():
    with patch("platform.system", return_value="Windows"):
        assert _pick_llama_asset([]) is None
