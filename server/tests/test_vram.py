"""Tests for the ResourceOrchestrator in vram.py.

All tests mock HTTP calls — no Forge server required.
sample_resources() is mocked to return safe defaults so tests are not
affected by the real machine state (RAM/VRAM/GPU/CPU).
"""
import pytest
from unittest.mock import MagicMock, patch
import vram
from vram import Priority, Resources


_SAFE_RESOURCES = Resources(cpu_pct=20.0, ram_pct=50.0,
                             vram_used=2000, vram_free=6000, vram_total=8000,
                             gpu_pct=0.0)


@pytest.fixture(autouse=True)
def _reset_vram_state():
    """Restore module-level and orchestrator state after each test.

    Also mocks sample_resources() so tests are deterministic and never
    block on nvidia-smi or trigger proactive eviction based on real RAM.
    """
    saved_url      = vram._forge_url
    saved_loaded   = vram._forge_loaded
    saved_holders  = dict(vram._orch._holders)
    saved_default  = vram._orch._default
    saved_def_pri  = vram._orch._default_priority
    saved_last_res = vram._orch._last_res

    with patch("vram.sample_resources", return_value=_SAFE_RESOURCES):
        yield

    vram._forge_url              = saved_url
    vram._forge_loaded           = saved_loaded
    vram._orch._holders          = saved_holders
    vram._orch._default          = saved_default
    vram._orch._default_priority = saved_def_pri
    vram._orch._last_res         = saved_last_res


# ── setup ────────────────────────────────────────────────────────────────────

def test_setup_stores_forge_url():
    vram.setup("http://forge.local:7860")
    assert vram._forge_url == "http://forge.local:7860"


def test_setup_overwrites_previous_url():
    vram.setup("http://first:7860")
    vram.setup("http://second:7860")
    assert vram._forge_url == "http://second:7860"


# ── unload_forge ─────────────────────────────────────────────────────────────

def test_unload_forge_http200_sets_loaded_false():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    resp = MagicMock(status_code=200)
    with patch("vram.req.post", return_value=resp) as mock_post:
        result = vram.unload_forge()
    assert result is True
    assert vram._forge_loaded is False
    mock_post.assert_called_once_with(
        "http://localhost:7860/sdapi/v1/unload-checkpoint", timeout=15
    )


def test_unload_forge_http404_leaves_state_and_returns_false():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    resp = MagicMock(status_code=404)
    with patch("vram.req.post", return_value=resp):
        result = vram.unload_forge()
    assert result is False
    assert vram._forge_loaded is True  # unchanged on non-200


def test_unload_forge_http500_leaves_state_and_returns_false():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    resp = MagicMock(status_code=500)
    with patch("vram.req.post", return_value=resp):
        result = vram.unload_forge()
    assert result is False
    assert vram._forge_loaded is True


def test_unload_forge_network_error_leaves_state_and_returns_false():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    with patch("vram.req.post", side_effect=Exception("connection refused")):
        result = vram.unload_forge()
    assert result is False
    assert vram._forge_loaded is True


def test_unload_forge_already_unloaded_skips_http():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    with patch("vram.req.post") as mock_post:
        result = vram.unload_forge()
    assert result is True
    mock_post.assert_not_called()


def test_unload_forge_no_url_skips_http():
    vram._forge_url    = ""
    vram._forge_loaded = True
    with patch("vram.req.post") as mock_post:
        result = vram.unload_forge()
    assert result is True
    mock_post.assert_not_called()


# ── reload_forge ─────────────────────────────────────────────────────────────

def test_reload_forge_http200_sets_loaded_true():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    resp = MagicMock(status_code=200)
    with patch("vram.req.post", return_value=resp):
        with patch("image.forge._push_forge_settings"):
            result = vram.reload_forge()
    assert result is True
    assert vram._forge_loaded is True


def test_reload_forge_http200_calls_push_settings():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    resp = MagicMock(status_code=200)
    with patch("vram.req.post", return_value=resp):
        with patch("image.forge._push_forge_settings") as mock_push:
            vram.reload_forge()
    mock_push.assert_called_once_with("http://localhost:7860")


def test_reload_forge_http404_leaves_state_and_returns_false():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    resp = MagicMock(status_code=404)
    with patch("vram.req.post", return_value=resp):
        with patch("image.forge._push_forge_settings"):
            result = vram.reload_forge()
    assert result is False
    assert vram._forge_loaded is False  # unchanged


def test_reload_forge_network_error_returns_false():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    with patch("vram.req.post", side_effect=Exception("timeout")):
        with patch("image.forge._push_forge_settings"):
            result = vram.reload_forge()
    assert result is False
    assert vram._forge_loaded is False


def test_reload_forge_already_loaded_skips_http():
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    with patch("vram.req.post") as mock_post:
        result = vram.reload_forge()
    assert result is True
    mock_post.assert_not_called()


def test_reload_forge_no_url_skips_http():
    vram._forge_url    = ""
    vram._forge_loaded = False
    with patch("vram.req.post") as mock_post:
        result = vram.reload_forge()
    assert result is True
    mock_post.assert_not_called()


# ── acquire_for_image ─────────────────────────────────────────────────────────

def test_acquire_for_image_evicts_llm_and_loads_forge():
    """GENERATION priority must evict a BACKGROUND-priority LLM and load Forge."""
    import llm as _llm
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    # LLM is the default background holder
    vram._orch._holders["llm"] = Priority.BACKGROUND

    call_order = []

    with patch.object(_llm, "suspend_for_image", side_effect=lambda: call_order.append("suspend")):
        with patch("vram.time.sleep"):
            resp = MagicMock(status_code=200)
            with patch("vram.req.post", return_value=resp):
                with patch("image.forge._push_forge_settings"):
                    vram.acquire_for_image()

    assert "suspend" in call_order, "LLM must be suspended before image generation"
    assert vram._forge_loaded is True, "Forge checkpoint must be loaded onto GPU"


def test_acquire_for_image_sleeps_after_evicting_llm():
    """A sleep of ≥ 2 s must occur (CUDA VRAM reclaim) between LLM kill and Forge load."""
    import llm as _llm
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    vram._orch._holders["llm"] = Priority.BACKGROUND

    sleep_values = []
    resp = MagicMock(status_code=200)

    with patch.object(_llm, "suspend_for_image"):
        with patch("vram.time.sleep", side_effect=lambda s: sleep_values.append(s)):
            with patch("vram.req.post", return_value=resp):
                with patch("image.forge._push_forge_settings"):
                    vram.acquire_for_image()

    assert any(s >= 2.0 for s in sleep_values), "Must sleep ≥ 2 s for CUDA to reclaim VRAM"


def test_acquire_for_image_does_not_evict_interactive_holder():
    """GENERATION (2) must NOT evict an INTERACTIVE (1) holder."""
    import llm as _llm
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    # Pretend a chat response is already holding the GPU at INTERACTIVE priority
    vram._orch._holders["llm"] = Priority.INTERACTIVE

    resp = MagicMock(status_code=200)
    with patch.object(_llm, "suspend_for_image") as mock_suspend:
        with patch("vram.time.sleep"):
            with patch("vram.req.post", return_value=resp):
                with patch("image.forge._push_forge_settings"):
                    vram.acquire_for_image()

    mock_suspend.assert_not_called()


# ── release_from_image ────────────────────────────────────────────────────────

def test_release_from_image_keeps_forge_hot():
    """release_from_image must keep the Forge checkpoint in VRAM for fast subsequent gens."""
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    vram._orch._holders["forge"] = Priority.GENERATION

    with patch.object(vram._orch, "_reload_default_async"):
        vram.release_from_image()

    assert vram._forge_loaded is True, "Forge checkpoint must stay hot after release"


# ── Priority semantics ────────────────────────────────────────────────────────

def test_interactive_preempts_generation():
    """An INTERACTIVE acquire (e.g. urgent chat reclaiming LLM) must interrupt
    and evict an in-progress GENERATION holder (Forge mid-gen)."""
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    vram._orch._holders["forge"] = Priority.GENERATION

    interrupted = []
    unloaded    = []

    # Intercept the forge resource's interrupt/unload callbacks directly.
    vram._orch._resources["forge"]._interrupt_fn = lambda: interrupted.append(True)
    vram._orch._resources["forge"]._unload_fn    = lambda: (unloaded.append(True), True)[1]

    # Replace the LLM load callback so we don't actually start llama-server.
    vram._orch._resources["llm"]._load_fn = lambda: True

    # Acquiring "llm" at INTERACTIVE priority should interrupt + evict "forge"
    # (GENERATION=2 > INTERACTIVE=1) before loading the LLM.
    vram._orch.acquire("llm", Priority.INTERACTIVE)

    assert interrupted, "Forge interrupt must be called when a higher-priority task arrives"
    assert unloaded,    "Forge checkpoint must be evicted before the LLM reloads"


# ── idempotency ───────────────────────────────────────────────────────────────

def test_double_unload_only_calls_http_once():
    """Second unload when already unloaded must not make an HTTP request."""
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    resp = MagicMock(status_code=200)
    with patch("vram.req.post", return_value=resp) as mock_post:
        vram.unload_forge()  # first call
        vram.unload_forge()  # second call — already unloaded
    assert mock_post.call_count == 1


def test_double_reload_only_calls_http_once():
    """Second reload when already loaded must not make an HTTP request."""
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False
    resp = MagicMock(status_code=200)
    with patch("vram.req.post", return_value=resp) as mock_post:
        with patch("image.forge._push_forge_settings"):
            vram.reload_forge()  # first call
            vram.reload_forge()  # second call — already loaded
    assert mock_post.call_count == 1


# ── keep-hot / lazy-evict behaviour ──────────────────────────────────────────

def test_release_from_image_triggers_llm_reload():
    """After Forge releases, the LLM default must be scheduled for reload."""
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    vram._orch._holders.clear()
    vram._orch._holders["forge"] = Priority.GENERATION

    with patch.object(vram._orch, "_reload_default_async") as mock_reload:
        vram.release_from_image()

    mock_reload.assert_called_once()


def test_llm_load_evicts_forge_when_hot():
    """_llm_load must evict Forge before starting the LLM when checkpoint is still in VRAM."""
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True

    unloaded = []
    resp = MagicMock(status_code=200)
    with patch("vram.req.post", return_value=resp):
        # Intercept _forge_unload to track the call
        original_unload = vram._forge_unload
        def _track_unload():
            unloaded.append(True)
            return original_unload()
        with patch("vram._forge_unload", side_effect=_track_unload):
            with patch("llm.LLM_READY", False):
                with patch("llm.LLM_SUSPENDED", False):
                    with patch("llm.load_llm"):
                        vram._llm_load()

    assert unloaded, "_forge_unload must be called before LLM loads when Forge is hot"
    assert vram._forge_loaded is False


def test_llm_load_skips_forge_evict_when_already_unloaded():
    """_llm_load must NOT call _forge_unload when Forge is already unloaded."""
    vram.setup("http://localhost:7860")
    vram._forge_loaded = False

    with patch("vram._forge_unload") as mock_unload:
        with patch("llm.LLM_READY", False):
            with patch("llm.LLM_SUSPENDED", False):
                with patch("llm.load_llm"):
                    vram._llm_load()

    mock_unload.assert_not_called()


def test_acquire_for_image_skips_forge_reload_when_hot():
    """When Forge checkpoint is already in VRAM, acquire must not POST to reload-checkpoint."""
    vram.setup("http://localhost:7860")
    vram._forge_loaded = True
    vram._orch._holders["llm"] = Priority.BACKGROUND

    reload_calls = []

    def _fake_post(url, **kw):
        if "reload-checkpoint" in url:
            reload_calls.append(url)
        m = MagicMock()
        m.status_code = 200
        return m

    with patch("vram.req.post", side_effect=_fake_post):
        with patch("vram._llm_unload", return_value=True):
            vram.acquire_for_image()

    assert not reload_calls, "No reload-checkpoint call expected when Forge is already hot"
