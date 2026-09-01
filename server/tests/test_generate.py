"""Tests for image/generate.py: image generation orchestration."""

import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from image import generate
import config
import state


@pytest.fixture(autouse=True)
def _reset_upscaler_cache():
    """generate._upscaler_cache is a module-level cache populated on first use;
    reset it so tests don't leak a cached value into unrelated tests."""
    generate._upscaler_cache = None
    yield
    generate._upscaler_cache = None


# ── helper ────────────────────────────────────────────────────────────────────

def _forge_url_mock(return_ok=True):
    """Return a patcher that makes http_ok return return_ok."""
    return patch("image.generate.http_ok", return_value=return_ok)


# ── generate_image: Forge availability ────────────────────────────────────────

def test_generate_image_raises_when_forge_unreachable(tmp_path, monkeypatch):
    """If Forge is completely unreachable, generate_image raises RuntimeError."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras"},
    })
    with patch("image.generate.http_ok", return_value=False):
        with pytest.raises(RuntimeError, match="Forge is unavailable"):
            generate.generate_image("a test prompt", "red hair", "ugly")


def test_generate_image_restarts_forge_on_first_failure(tmp_path, monkeypatch):
    """If Forge is down but restart succeeds, generation proceeds."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras"},
    })
    calls = []

    def _http_ok(url):
        calls.append(url)
        return False if len(calls) == 1 else True

    with patch("image.generate.http_ok", side_effect=_http_ok), \
         patch("image.generate.start_forge", return_value=True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("prompt", "appearance", "negative")
    assert mock_post.called


def test_generate_image_raises_if_restart_fails(tmp_path, monkeypatch):
    """If Forge is down and restart also fails, raise RuntimeError."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras"},
    })
    with patch("image.generate.http_ok", return_value=False), \
         patch("image.generate.start_forge", return_value=False):
        with pytest.raises(RuntimeError, match="Forge is unavailable"):
            generate.generate_image("prompt", "appearance", "negative")




# ── generate_image: payload construction ──────────────────────────────────────

def test_generate_image_builds_correct_payload_quick(tmp_path, monkeypatch):
    """Verify the Forge txt2img payload for a quick generation."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("my prompt", "appearance", "negative", quick=True)

    payload = mock_post.call_args[1]["json"]
    assert payload["prompt"] == "my prompt, "
    assert payload["negative_prompt"] == "negative"
    assert payload["steps"] == 12
    assert payload["sampler_name"] == "DPM++ 2M Karras"
    assert payload["width"] == 512
    assert payload["height"] == 512
    assert payload["cfg_scale"] == 7
    assert payload["seed"] == -1


def test_generate_image_build_full_payload(tmp_path, monkeypatch):
    """Full (non-quick) generation uses steps from config and default sampler."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 30, "width": 768, "height": 768, "cfg_scale": 7.5,
                  "sampler_name": "Euler a", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("prompt", "appearance", "negative")

    payload = mock_post.call_args[1]["json"]
    assert payload["steps"] == 30
    assert payload["sampler_name"] == "Euler a"
    assert payload["width"] == 768
    assert payload["height"] == 768


def test_generate_image_custom_step_and_cfg(tmp_path, monkeypatch):
    """explicit steps/cfg_scale override config values."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n", steps=10, cfg_scale=5)

    payload = mock_post.call_args[1]["json"]
    assert payload["steps"] == 10
    assert payload["cfg_scale"] == 5


def test_generate_image_custom_seed(tmp_path, monkeypatch):
    """Explicit seed value is passed through."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n", seed=42)

    payload = mock_post.call_args[1]["json"]
    assert payload["seed"] == 42


def test_generate_image_extra_negative_appended(tmp_path, monkeypatch):
    """extra_negative is prepended to the base negative."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "base_neg", extra_negative="extra")

    payload = mock_post.call_args[1]["json"]
    assert payload["negative_prompt"] == "extra, base_neg"


def test_generate_image_no_extra_negative(tmp_path, monkeypatch):
    """Without extra_negative, negative equals base."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "base_neg")

    payload = mock_post.call_args[1]["json"]
    assert payload["negative_prompt"] == "base_neg"


# ── generate_image: override_settings ─────────────────────────────────────────

def test_generate_image_disables_samples_save(tmp_path, monkeypatch):
    """Forge should never save samples to disk — Alice manages its own output."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n")

    payload = mock_post.call_args[1]["json"]
    overrides = payload["override_settings"]
    assert overrides["samples_save"] is False
    assert overrides["grid_save"] is False


def test_generate_image_clip_skip_included(tmp_path, monkeypatch):
    """When clip_skip is configured, it appears in override_settings."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": 2,
                  "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n")

    overrides = mock_post.call_args[1]["json"]["override_settings"]
    assert overrides["CLIP_stop_at_last_layers"] == 2


def test_generate_image_clip_skip_absent_when_none(tmp_path, monkeypatch):
    """When clip_skip is None, it is not included in overrides."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n")

    overrides = mock_post.call_args[1]["json"]["override_settings"]
    assert "CLIP_stop_at_last_layers" not in overrides


def test_generate_image_quick_vae_included(tmp_path, monkeypatch):
    """When quick_vae is set and generation is quick, override applies."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": "vae-ft-mse-840000-ema-pruned.safetensors",
                  "clip_skip": None, "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n", quick=True)

    overrides = mock_post.call_args[1]["json"]["override_settings"]
    assert overrides["sd_vae"] == "vae-ft-mse-840000-ema-pruned.safetensors"


# ── generate_image: hires fix ──────────────────────────────────────────────────

def test_generate_image_hires_fix_enabled(tmp_path, monkeypatch):
    """When hires_fix is True and not quick, enable_hr is included."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": True, "hires_scale": 1.5, "hires_steps": 15,
                  "hires_denoising": 0.45, "hires_upscaler": "Latent",
                  "quick_vae": None, "clip_skip": None, "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post, \
         patch("image.generate._resolve_upscaler", return_value="Latent"):
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n", quick=False)

    payload = mock_post.call_args[1]["json"]
    assert payload["enable_hr"] is True
    assert payload["hr_scale"] == 1.5
    assert payload["hr_second_pass_steps"] == 15
    assert payload["denoising_strength"] == 0.45
    assert payload["hr_upscaler"] == "Latent"


def test_generate_image_hires_fix_disabled_when_quick(tmp_path, monkeypatch):
    """quick=True disables hires fix regardless of config."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": True, "hires_scale": 1.5, "hires_steps": 15,
                  "hires_denoising": 0.45, "hires_upscaler": "Latent",
                  "quick_vae": None, "clip_skip": None, "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n", quick=True)

    payload = mock_post.call_args[1]["json"]
    assert "enable_hr" not in payload


def test_generate_image_hires_fix_uses_custom_upscaler(tmp_path, monkeypatch):
    """When hires_upscaler is set in config, it is passed through."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": True, "hires_scale": 2.0, "hires_steps": 20,
                  "hires_denoising": 0.5, "hires_upscaler": "R-ESRGAN 4x+",
                  "quick_vae": None, "clip_skip": None, "quick_sampler": "DPM++ 2M Karras"},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post, \
         patch("image.generate._resolve_upscaler", return_value="R-ESRGAN 4x+"):
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n")

    payload = mock_post.call_args[1]["json"]
    assert payload["hr_upscaler"] == "R-ESRGAN 4x+"


# ── generate_image: ADetailer ──────────────────────────────────────────────────

def test_generate_image_adetailer_face_enabled(tmp_path, monkeypatch):
    """When adetailer_face is True, alwayson_scripts includes ADetailer with face model."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras",
                  "adetailer_face": True, "adetailer_hands": False},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n", quick=False)

    payload = mock_post.call_args[1]["json"]
    assert "alwayson_scripts" in payload
    ad = payload["alwayson_scripts"]["ADetailer"]["args"]
    assert ad[0] is True
    assert ad[1] is False  # only face, no hands
    assert ad[2]["ad_model"] == "face_yolov8n.pt"


def test_generate_image_adetailer_both_enabled(tmp_path, monkeypatch):
    """When both face and hands are enabled, two ADetailer configs are included."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras",
                  "adetailer_face": True, "adetailer_hands": True},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n", quick=False)

    ad_args = mock_post.call_args[1]["json"]["alwayson_scripts"]["ADetailer"]["args"]
    assert len(ad_args) == 4  # [True, False, face_cfg, hand_cfg]
    assert ad_args[2]["ad_model"] == "face_yolov8n.pt"
    assert ad_args[3]["ad_model"] == "hand_yolov8n.pt"


def test_generate_image_adetailer_disabled_by_quick(tmp_path, monkeypatch):
    """Quick generation skips ADetailer even if configured."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras",
                  "adetailer_face": True, "adetailer_hands": True},
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n", quick=True)

    payload = mock_post.call_args[1]["json"]
    assert "alwayson_scripts" not in payload


# ── generate_image: vram_swap ─────────────────────────────────────────────────

def test_generate_image_acquires_and_releases_vram(tmp_path, monkeypatch):
    """vram_swap=True triggers acquire_for_image / release_from_image."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras"},
   })
    acq_calls = []
    rel_calls = []

    with patch("image.generate.http_ok", return_value=True), \
         patch("image.generate._vram.acquire_for_image", side_effect=lambda: acq_calls.append(1)), \
         patch("image.generate._vram.release_from_image", side_effect=lambda: rel_calls.append(1)), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n")
    assert len(acq_calls) == 1
    assert len(rel_calls) == 1


def test_generate_image_skips_vram_swap_when_disabled(tmp_path, monkeypatch):
    """vram_swap=False skips acquire/release."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras", "adetailer_face": False, "adetailer_hands": False},
        "vram_swap_for_image": False,
    })
    with patch("image.generate.http_ok", return_value=True), \
         patch("image.generate._vram.acquire_for_image") as mock_acq, \
         patch("image.generate._vram.release_from_image") as mock_rel, \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n")
    mock_acq.assert_not_called()
    mock_rel.assert_not_called()


# ── generate_image: response parsing ──────────────────────────────────────────

def test_generate_image_returns_base64_string(tmp_path, monkeypatch):
    """The function returns the base64 image string from Forge's response."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras", "adetailer_face": False, "adetailer_hands": False},
        "vram_swap_for_image": False,
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["abc123base64"]}
        mock_post.return_value = resp

        result = generate.generate_image("p", "a", "n")
    assert result == "abc123base64"


def test_generate_image_raises_on_empty_images_list(tmp_path, monkeypatch):
    """Forge returning an empty images list must raise RuntimeError."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras", "adetailer_face": False, "adetailer_hands": False},
        "vram_swap_for_image": False,
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": []}
        mock_post.return_value = resp

        with pytest.raises(RuntimeError, match="no images"):
            generate.generate_image("p", "a", "n")


def test_generate_image_raises_on_missing_images_key(tmp_path, monkeypatch):
    """Forge response without 'images' key must raise RuntimeError."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras", "adetailer_face": False, "adetailer_hands": False},
        "vram_swap_for_image": False,
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"error": "OOM"}
        mock_post.return_value = resp

        with pytest.raises(RuntimeError, match="Forge:"):
            generate.generate_image("p", "a", "n")


# ── generate_image: seed tracking ─────────────────────────────────────────────

def test_generate_image_updates_last_seed_from_info(tmp_path, monkeypatch):
    """When Forge returns info JSON with a seed, state.last_seed is updated."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras", "adetailer_face": False, "adetailer_hands": False},
        "vram_swap_for_image": False,
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {
            "images": ["b64"],
            "info": '{"seed": 987654321}',
        }
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n")
    assert state.last_seed == 987654321


def test_generate_image_last_seed_default_on_bad_info(tmp_path, monkeypatch):
    """Malformed info JSON leaves last_seed at -1."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras", "adetailer_face": False, "adetailer_hands": False},
        "vram_swap_for_image": False,
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"], "info": "not json"}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n")
    assert state.last_seed == -1


def test_generate_image_last_seed_default_when_no_info(tmp_path, monkeypatch):
    """Forge response without info leaves last_seed at -1."""
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras", "adetailer_face": False, "adetailer_hands": False},
        "vram_swap_for_image": False,
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"images": ["b64"]}
        mock_post.return_value = resp

        generate.generate_image("p", "a", "n")
    assert state.last_seed == -1


# ── generate_image: connection error wrapping ────────────────────────────────

def test_generate_image_wraps_connection_error(tmp_path, monkeypatch):
    """requests.exceptions.ConnectionError is wrapped as RuntimeError with helpful message."""
    import requests
    monkeypatch.setattr(config, "CFG", {
        "forge_url": "http://localhost:7860",
        "image": {"steps": 20, "width": 512, "height": 512, "cfg_scale": 7,
                  "sampler_name": "DPM++ 2M Karras", "suffix": "", "quick_steps": 12,
                  "hires_fix": False, "quick_vae": None, "clip_skip": None,
                  "quick_sampler": "DPM++ 2M Karras", "adetailer_face": False, "adetailer_hands": False},
        "vram_swap_for_image": False,
    })
    with _forge_url_mock(True), \
         patch("image.generate.req.post", side_effect=requests.exceptions.ConnectionError("refused")):
        with pytest.raises(RuntimeError, match="Could not connect to Forge"):
            generate.generate_image("p", "a", "n")


# ── _resolve_upscaler ─────────────────────────────────────────────────────────

def test_resolve_upscaler_caches_result(tmp_path, monkeypatch):
    """After the first call, _resolve_upscaler returns the cached value without HTTP."""
    forge_url = "http://localhost:7860"
    with patch("image.generate.req.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"name": "Latent"},
            {"name": "R-ESRGAN 4x+"},
            {"name": "Lowres"},
        ]
        first = generate._resolve_upscaler(forge_url, "R-ESRGAN 4x+")
        second = generate._resolve_upscaler(forge_url, "R-ESRGAN 4x+")
    assert first == "R-ESRGAN 4x+"
    assert second == "R-ESRGAN 4x+"
    assert mock_get.call_count == 1


def test_resolve_upscaler_falls_back_to_latent(tmp_path, monkeypatch):
    """When preferred upscaler isn't found, falls back to first latent upscaler."""
    forge_url = "http://localhost:7860"
    with patch("image.generate.req.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"name": "R-ESRGAN 4x+"},
            {"name": "Latent"},
        ]
        result = generate._resolve_upscaler(forge_url, "Nearest")
    assert result == "Latent"


def test_resolve_upscaler_returns_none_when_no_latent(tmp_path, monkeypatch):
    """When no upscaler is found at all, returns None."""
    forge_url = "http://localhost:7860"
    with patch("image.generate.req.get") as mock_get:
        mock_get.return_value.json.return_value = []
        result = generate._resolve_upscaler(forge_url, "Nearest")
    assert result is None


def test_resolve_upscaler_returns_none_after_previous_empty_cache(tmp_path, monkeypatch):
    """If cache was set to empty string, it stays None."""
    forge_url = "http://localhost:7860"
    with patch("image.generate.req.get") as mock_get:
        mock_get.return_value.json.return_value = [{"name": "R-ESRGAN 4x+"}]
        first = generate._resolve_upscaler(forge_url, "Nonexistent")
        assert first is None
        # Cached as "" — second call must not hit network
        mock_get.reset_mock()
        second = generate._resolve_upscaler(forge_url, "Nonexistent")
        assert second is None
        mock_get.assert_not_called()


def test_resolve_upscaler_handles_get_failure(tmp_path, monkeypatch):
    """If Forge is unreachable for the upscaler query, returns None."""
    forge_url = "http://localhost:7860"
    with patch("image.generate.req.get", side_effect=Exception("timeout")):
        result = generate._resolve_upscaler(forge_url, "Nearest")
    assert result is None
