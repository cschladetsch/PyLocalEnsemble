"""Text-to-video via Forge — a frame-chain img2img loop stitched with ffmpeg.

Forge's plain /sdapi/v1 surface has no native video endpoint (that needs
extensions like AnimateDiff/Deforum, which aren't assumed to be installed).
Instead this generates a short animated clip the same way "cinemagraph"
tooling does it without extra extensions:

  1. txt2img renders frame 0 from the description.
  2. Each following frame is an img2img pass seeded from the previous frame,
     at a low denoising strength, so motion drifts smoothly instead of
     flickering between unrelated images.
  3. ffmpeg muxes the frame sequence into an mp4 at the requested fps.

Uses generate.py's existing Forge plumbing (VRAM yield/reload, negative
prompt, suffix) so a video request behaves like any other generation.
"""
from __future__ import annotations
import base64, os, shutil, subprocess, tempfile, threading, time
import requests as req
import config
import forge
from forge import start_forge
from utils import http_ok

_gen_cancel = threading.Event()


def video_output_dir() -> str:
    out_dir = os.path.join(config.STATIC_DIR, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _txt2img(forge_url, payload) -> str:
    r = req.post(f"{forge_url}/sdapi/v1/txt2img", json=payload, timeout=300)
    data = r.json()
    if "images" not in data or not data["images"]:
        raise RuntimeError(f"Forge txt2img error: {data.get('error') or data}"[:300])
    return data["images"][0]


def _img2img(forge_url, payload) -> str:
    r = req.post(f"{forge_url}/sdapi/v1/img2img", json=payload, timeout=300)
    data = r.json()
    if "images" not in data or not data["images"]:
        raise RuntimeError(f"Forge img2img error: {data.get('error') or data}"[:300])
    return data["images"][0]


def generate_video(
    prompt: str,
    negative_base: str,
    frames: int = None,
    fps: int = None,
    steps: int = None,
    cfg_scale: float = None,
    width: int = None,
    height: int = None,
    seed: int = -1,
    denoise: float = None,
    on_progress=None,
):
    """Describe a video in `prompt`; returns a URL to the stitched mp4.

    `on_progress(frame_index, total_frames)` is called after each frame, if given.
    """
    forge_url = config.CFG["forge_url"]
    img_cfg   = config.CFG["image"]
    vid_cfg   = config.CFG["video"]

    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found on PATH — install ffmpeg to stitch frames into a video.")

    frames   = frames   if frames   is not None else vid_cfg["frames"]
    fps      = fps      if fps      is not None else vid_cfg["fps"]
    steps    = steps    if steps    is not None else vid_cfg["steps"]
    cfg_scale= cfg_scale if cfg_scale is not None else img_cfg["cfg_scale"]
    width    = width    if width    is not None else img_cfg["width"]
    height   = height   if height   is not None else img_cfg["height"]
    denoise  = denoise  if denoise  is not None else vid_cfg["denoise"]
    frames   = max(2, min(int(frames), vid_cfg["max_frames"]))

    if not http_ok(f"{forge_url}/sdapi/v1/sd-models"):
        print("[sd_app] Forge down, restarting...")
        start_forge()
        if not http_ok(f"{forge_url}/sdapi/v1/sd-models"):
            raise RuntimeError(
                f"Forge is unavailable at {forge_url}. Start Stable Diffusion Forge or update forge_url in sd_app.json."
            )

    from generate import _yield_alice_llm, _resume_alice_llm
    _yield_alice_llm()
    forge.reload_checkpoint()

    tmp_dir = tempfile.mkdtemp(prefix="sd_video_")
    try:
        full_prompt = prompt + ", " + img_cfg["suffix"]
        negative = negative_base
        base_payload = {
            "prompt":          full_prompt,
            "negative_prompt": negative,
            "steps":           steps,
            "width":           width,
            "height":          height,
            "cfg_scale":       cfg_scale,
            "sampler_name":    img_cfg["sampler_name"],
            "seed":            seed,
            "override_settings": {"samples_save": False, "grid_save": False},
        }

        print(f"\n[sd_app] video prompt ({len(full_prompt)} chars): {full_prompt!r}")
        print(f"[sd_app] frames={frames}, fps={fps}, steps={steps}, size={width}x{height}, denoise={denoise}")

        _gen_cancel.clear()
        last_b64 = _txt2img(forge_url, base_payload)
        _save_frame(tmp_dir, 0, last_b64)
        if on_progress:
            on_progress(1, frames)

        for i in range(1, frames):
            if _gen_cancel.is_set():
                raise RuntimeError("Video generation cancelled.")
            img2img_payload = {
                "init_images":     [last_b64],
                "prompt":          full_prompt,
                "negative_prompt": negative,
                "steps":           steps,
                "width":           width,
                "height":          height,
                "cfg_scale":       cfg_scale,
                "sampler_name":    img_cfg["sampler_name"],
                "denoising_strength": denoise,
                "seed":            -1,
                "override_settings": {"samples_save": False, "grid_save": False},
            }
            last_b64 = _img2img(forge_url, img2img_payload)
            _save_frame(tmp_dir, i, last_b64)
            if on_progress:
                on_progress(i + 1, frames)

        out_dir  = video_output_dir()
        fname    = f"vid_{time.time_ns()}.mp4"
        out_path = os.path.join(out_dir, fname)
        _stitch_ffmpeg(tmp_dir, out_path, fps)
        print(f"[sd_app] video done — {out_path}")
        return f"static/outputs/{fname}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        forge.unload_checkpoint()
        _resume_alice_llm()


def _save_frame(tmp_dir: str, index: int, b64_data: str) -> None:
    with open(os.path.join(tmp_dir, f"frame_{index:05d}.png"), "wb") as f:
        f.write(base64.b64decode(b64_data))


def _stitch_ffmpeg(tmp_dir: str, out_path: str, fps: int) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", os.path.join(tmp_dir, "frame_%05d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-500:]}")


def interrupt():
    _gen_cancel.set()
    try:
        req.post(f"{config.CFG['forge_url']}/sdapi/v1/interrupt", timeout=5)
    except Exception:
        pass
