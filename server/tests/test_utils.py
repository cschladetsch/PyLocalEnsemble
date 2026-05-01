"""Tests for utils.py helper functions."""
import pytest
from unittest.mock import patch, MagicMock
import utils


# ── _c (colour formatting) ────────────────────────────────────────────────────

def test_c_known_color_wraps_text():
    result = utils._c("red", "hello")
    assert "hello" in result
    assert "\033[" in result        # ANSI escape present
    assert result.endswith("\033[0m")  # reset at the end


def test_c_unknown_color_returns_text_with_reset():
    result = utils._c("nonexistent", "hello")
    assert "hello" in result
    assert result.endswith("\033[0m")


def test_c_green_different_from_red():
    assert utils._c("green", "x") != utils._c("red", "x")


def test_c_empty_text():
    result = utils._c("blue", "")
    assert "\033[0m" in result  # reset still present even for empty text


# ── http_ok ───────────────────────────────────────────────────────────────────

def test_http_ok_returns_true_on_success():
    with patch("utils.req.get", return_value=MagicMock()):
        assert utils.http_ok("http://localhost:8080") is True


def test_http_ok_returns_false_on_connection_error():
    import requests
    with patch("utils.req.get", side_effect=requests.exceptions.ConnectionError()):
        assert utils.http_ok("http://nowhere") is False


def test_http_ok_returns_false_on_timeout():
    import requests
    with patch("utils.req.get", side_effect=requests.exceptions.Timeout()):
        assert utils.http_ok("http://nowhere") is False


def test_http_ok_returns_false_on_any_exception():
    with patch("utils.req.get", side_effect=Exception("unexpected")):
        assert utils.http_ok("http://nowhere") is False


def test_http_ok_passes_timeout_to_get():
    mock_get = MagicMock()
    with patch("utils.req.get", mock_get):
        utils.http_ok("http://localhost:8080", timeout=5)
    mock_get.assert_called_once_with("http://localhost:8080", timeout=5)


# ── wait_for ──────────────────────────────────────────────────────────────────

def test_wait_for_returns_true_on_first_success():
    with patch("utils.http_ok", return_value=True):
        with patch("utils.time.sleep"):
            result = utils.wait_for("http://localhost:8080", "Test", retries=5, delay=0)
    assert result is True


def test_wait_for_returns_false_after_exhausting_retries():
    with patch("utils.http_ok", return_value=False):
        with patch("utils.time.sleep"):
            result = utils.wait_for("http://localhost:8080", "Test", retries=3, delay=0)
    assert result is False


def test_wait_for_retries_correct_number_of_times():
    call_count = [0]
    def _counting_http_ok(url, timeout=2):
        call_count[0] += 1
        return False
    with patch("utils.http_ok", side_effect=_counting_http_ok):
        with patch("utils.time.sleep"):
            utils.wait_for("http://localhost", "X", retries=4, delay=0)
    assert call_count[0] == 4


def test_wait_for_stops_early_on_success():
    responses = [False, False, True, False]
    idx = [0]
    def _mock(url, timeout=2):
        val = responses[idx[0]]
        idx[0] += 1
        return val
    with patch("utils.http_ok", side_effect=_mock):
        with patch("utils.time.sleep"):
            result = utils.wait_for("http://localhost", "X", retries=10, delay=0)
    assert result is True
    assert idx[0] == 3  # stopped after third call returned True


# ── is_wsl ────────────────────────────────────────────────────────────────────

def test_is_wsl_returns_false_when_proc_version_missing():
    with patch("builtins.open", side_effect=OSError("no such file")):
        assert utils.is_wsl() is False


def test_is_wsl_returns_true_when_microsoft_in_proc_version():
    from unittest.mock import mock_open
    m = mock_open(read_data="Linux version 5.15-microsoft-standard-WSL2")
    with patch("builtins.open", m):
        assert utils.is_wsl() is True


def test_is_wsl_returns_false_when_not_wsl():
    from unittest.mock import mock_open
    m = mock_open(read_data="Linux version 5.15.0-generic #1 SMP Ubuntu")
    with patch("builtins.open", m):
        assert utils.is_wsl() is False
