"""StaticDiffusion — lightweight in-process SD image generation server.

Replaces the heavy stable-diffusion-webui-forge with a thin FastAPI server
that loads a checkpoint via diffusers and exposes the same /sdapi/v1/ endpoints
Alice's image module already talks to.

Usage:
    python server.py              # start on default port (from config)
    python server.py --port 7861 # override port

The server auto-loads the checkpoint from ~/.models/forge/ on startup.
"""

import argparse
import io
import logging
import os
import sys
import time
import traceback
from pathlib import Path

import base64
import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import json as _json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("static_diffusion")

# ── Config ──────────────────────────────────────────────────────────────────────

FORGE_DIR = os.environ.get("FORGE_DIR", str(Path(__file__).parent))
_SD_MODELS_DIR = os.path.expanduser(
    os.environ.get("SD_MODELS_DIR", "~/.models/forge")
)
SD_MODELS_DIR = _SD_MODELS_DIR

# Detect if we actually have models in the default dir
_SD_DEFAULT_HAS_MODELS = any(
    f.endswith(".safetensors")
    for root, _dirs, files in os.walk(SD_MODELS_DIR)
    for f in files
) if os.path.isdir(SD_MODELS_DIR) else False

# Forge models directory (where models actually live on this machine)
_FORGE_MODELS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "stable-diffusion-webui-forge", "models", "Stable-diffusion",
)
_USE_FORGE_DIR = (
    not _SD_DEFAULT_HAS_MODELS
    and os.path.isdir(_FORGE_MODELS_DIR)
    and any(f.endswith(".safetensors") for f in os.listdir(_FORGE_MODELS_DIR))
)
if _USE_FORGE_DIR:
    SD_MODELS_DIR = _FORGE_MODELS_DIR

PORT = int(os.environ.get("SD_PORT", "7861"))

# ── Shared state ────────────────────────────────────────────────────────────────

_sd_pipeline = None
_loaded_model_name = ""
_loaded_model_title = ""
_progress_state = {"progress": 0.0, "state": {}, "textinfo": "", "eta_relative": 0.0}


# ── Models (mirror Forge's API shape) ───────────────────────────────────────────

class SDModelItem(BaseModel):
    title: str
    name: str = ""
    model_name: str = ""


class SamplerItem(BaseModel):
    name: str
    sd_name: str = ""
    description: str = ""


class SchedulerItem(BaseModel):
    name: str
    schedulers: List[str] = []


class UpscalerItem(BaseModel):
    name: str
    upscale_with: str = ""


class OptionsModel(BaseModel):
    sd_model_checkpoint: str = ""
    sd_vae: str = "automatic"
    CLIP_stop_at_last_layers: int = 1
    samples_save: bool = False
    grid_save: bool = False
    save_to_dirs: bool = False
    samples_format: str = "png"
    forge_inference_memory: int = 0
    sd_checkpoints_keep_in_cpu: bool = True


class ProgressResponse(BaseModel):
    progress: float = 0.0
    state: Dict[str, Any] = {}
    textinfo: str = ""
    eta_relative: float = 0.0


# ── Request / Response models ───────────────────────────────────────────────────

class Txt2ImgRequest(BaseModel):
    prompt: str = ""
    negative_prompt: str = ""
    steps: int = 20
    width: int = 512
    height: int = 512
    cfg_scale: float = 7.0
    sampler_name: str = "DPM++ 2M Karras"
    seed: int = -1
    batch_size: int = 1
    override_settings: Dict[str, Any] = {}
    alwayson_scripts: Dict[str, Any] = {}
    enable_hr: bool = False
    hr_scale: float = 1.5
    hr_second_pass_steps: int = 15
    denoising_strength: float = 0.45
    hr_upscaler: str = "Latent"


class Txt2ImgResponse(BaseModel):
    images: Optional[List[str]] = None
    parameters: Dict[str, Any] = {}
    info: str = "{}"


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _seed_from_request(req: Txt2ImgRequest) -> int:
    return req.seed if req.seed > 0 else int(torch.randint(0, 2**32, (1,)).item())


def _rescale_to_image(x):
    return (x.clamp(-1, 1) + 1.0) * 0.5


def _numpy_to_b64(image_np: np.ndarray) -> str:
    """Convert HWC uint8 numpy image to base64 PNG string."""
    from PIL import Image
    pil = Image.fromarray(image_np)
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ── Core generation ─────────────────────────────────────────────────────────────

def _denoise(
    prompt: str,
    negative_prompt: str,
    steps: int,
    width: int,
    height: int,
    cfg_scale: float,
    seed: int,
    sampler_name: str,
):
    """Run the diffusion sampling loop."""
    global _progress_state

    if _sd_pipeline is None:
        raise RuntimeError("Model not loaded")

    pipe = _sd_pipeline
    device = pipe.device

    # Prepare conditioning
    prompt_embeds, _neg = pipe.encode_prompt(
        prompt=prompt,
        negative_prompt=negative_prompt,
        device=device,
        num_images_per_prompt=1,
        do_classifier_free_guidance=True,
        max_pooler_output=False,
    )

    # Choose scheduler based on sampler name
    sampler_lower = sampler_name.lower().replace(" ", "")
    sched_kwargs = {}
    if "karras" in sampler_lower:
        sched_kwargs["use_karras_sigmas"] = True
    if "sde" in sampler_lower or "sde" in sampler_name.lower():
        sched_kwargs["use_sde"] = True

    if "euler" in sampler_lower and "a" in sampler_lower:
        sched_kwargs["use_karras_sigmas"] = True
        sched_kwargs["use_exponential_sigmas"] = False
        from diffusers import EulerDiscreteScheduler
        scheduler = EulerDiscreteScheduler(
            num_train_timesteps=pipe.scheduler.config.num_train_timesteps,
            beta_start=pipe.scheduler.config.beta_start,
            beta_end=pipe.scheduler.config.beta_end,
            beta_schedule=pipe.scheduler.config.beta_schedule,
            **sched_kwargs,
        )
    elif "ddim" in sampler_lower:
        from diffusers import DDIMScheduler
        scheduler = DDIMScheduler(
            num_train_timesteps=pipe.scheduler.config.num_train_timesteps,
            beta_start=pipe.scheduler.config.beta_start,
            beta_end=pipe.scheduler.config.beta_end,
            beta_schedule=pipe.scheduler.config.beta_schedule,
            **sched_kwargs,
        )
    elif "dpm" in sampler_lower and ("sde" in sampler_lower or "sdes" in sampler_lower):
        from diffusers import DPMSolverSDEScheduler
        scheduler = DPMSolverSDEScheduler(
            num_train_timesteps=pipe.scheduler.config.num_train_timesteps,
            beta_start=pipe.scheduler.config.beta_start,
            beta_end=pipe.scheduler.config.beta_end,
            beta_schedule=pipe.scheduler.config.beta_schedule,
            **sched_kwargs,
        )
    else:
        from diffusers import DPMSolverMultistepScheduler
        scheduler = DPMSolverMultistepScheduler(
            num_train_timesteps=pipe.scheduler.config.num_train_timesteps,
            beta_start=pipe.scheduler.config.beta_start,
            beta_end=pipe.scheduler.config.beta_end,
            beta_schedule=pipe.scheduler.config.beta_schedule,
            **sched_kwargs,
        )

    generator = torch.Generator(device=device).manual_seed(seed)

    latents_shape = (1, pipe.unet.config.in_channels, height // 8, width // 8)
    latents = torch.randn(latents_shape, generator=generator, device=device)

    guidance_scale = cfg_scale
    timesteps = scheduler.set_timesteps(steps)

    for i, t in enumerate(timesteps):
        latent_model_input = torch.cat([latents] * 2) if guidance_scale > 1.0 else latents
        latent_model_input = scheduler.scale_model_input(latent_model_input, t)

        with torch.no_grad():
            noise_pred = pipe.unet(latent_model_input, t, encoder_hidden_states=prompt_embeds).sample

        if guidance_scale > 1.0:
            noise_pred_uncond, noise_pred_cond = noise_pred.chunk(2)
            noise_pred = noise_pred_uncond + guidance_scale * (noise_pred_cond - noise_pred_uncond)

        latents = scheduler.step(noise_pred, t, latents).prev_sample

        _progress_state["progress"] = (i + 1) / len(timesteps)
        _progress_state["state"] = {"sampling_step": i + 1, "sampling_steps": len(timesteps)}
        _progress_state["textinfo"] = f"Step {i + 1}/{len(timesteps)}"

    return latents, seed, steps


def _generate_image(req: Txt2ImgRequest) -> tuple:
    """Run full generation: denoise + VAE decode. Returns list of base64 strings."""
    global _sd_pipeline

    pipe = _sd_pipeline
    device = pipe.device

    seed = _seed_from_request(req)
    samples, actual_seed, steps_used = _denoise(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt,
        steps=req.steps,
        width=req.width,
        height=req.height,
        cfg_scale=req.cfg_scale,
        seed=seed,
        sampler_name=req.sampler_name,
    )

    # VAE decode
    _progress_state["textinfo"] = "Decoding VAE..."
    samples = samples.to(device)

    with torch.no_grad():
        decoded = pipe.vae.decode(samples, return_dict=False)[0]
        decoded = decoded.squeeze(0)
        image_np = _rescale_to_image(decoded).permute(1, 2, 0).clamp(0, 1).cpu().numpy()
        image_np = (image_np * 255).round().astype(np.uint8)

    b64 = _numpy_to_b64(image_np)

    _progress_state["textinfo"] = ""
    _progress_state["progress"] = 1.0
    _progress_state["state"] = {"sampling_step": steps_used, "sampling_steps": steps_used}

    info = {
        "seed": actual_seed,
        "steps": steps_used,
        "size": (req.width, req.height),
        "cfg_scale": req.cfg_scale,
        "sampler_name": req.sampler_name,
        "model": _loaded_model_name,
        "has_parameters": True,
    }
    info_str = _json.dumps(info, indent=2)

    return [b64], actual_seed, info_str


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(title="StaticDiffusion", version="0.1.0")


@app.on_event("startup")
async def startup_event():
    global _sd_pipeline, _loaded_model_name, _loaded_model_title, _progress_state
    _progress_state = {"progress": 0.0, "state": {}, "textinfo": "", "eta_relative": 0.0}
    log.info("StaticDiffusion starting...")

    # ── Load model ──────────────────────────────────────────────────────────
    model_path = os.path.expanduser(os.environ.get("SD_CHECKPOINT_PATH", ""))
    if not model_path or not os.path.isfile(model_path):
        # Search in SD_MODELS_DIR
        candidates = []
        for root, dirs, files in os.walk(SD_MODELS_DIR):
            for f in files:
                if f.endswith(".safetensors"):
                    candidates.append(os.path.join(root, f))
        if not candidates:
            log.error(f"No .safetensors found in {SD_MODELS_DIR}")
            return
        candidates.sort(key=lambda p: os.path.getsize(p), reverse=True)
        model_path = candidates[0]
        log.info(f"No SD_CHECKPOINT_PATH set; picking {model_path}")

    if not os.path.isfile(model_path):
        log.error(f"Checkpoint not found: {model_path}")
        return

    _loaded_model_name = os.path.basename(model_path)
    _loaded_model_title = _loaded_model_name

    log.info(f"Loading checkpoint: {_loaded_model_name} ...")
    t0 = time.time()

    # ── Monkey-patch safetensors ────────────────────────────────────────────
    import safetensors.torch as sf
    _orig_load_file = sf.load_file

    def _patched_load_file(filename, device="cpu", **kwargs):
        kwargs.setdefault("backend", "pread")
        return _orig_load_file(filename, device=device, **kwargs)

    sf.load_file = _patched_load_file

    # ── Load state dict directly (bypasses safetensors segfault in from_single_file) ──
    log.info("Loading model weights from safetensors...")
    try:
        state_dict = sf.load_file(model_path, device="cpu")
        num_tensors = len(state_dict)
        log.info(f"Loaded {num_tensors} tensors ({time.time() - t0:.1f}s)")
    except Exception as e:
        log.error(f"Failed to load safetensors: {e}")
        sf.load_file = _orig_load_file
        return

    # ── Build pipeline from state dict components ──────────────────────────
    # Extract component state dicts
    unet_sd = {}
    vae_sd = {}
    text_encoder_sd = {}
    for k, v in state_dict.items():
        if "model.diffusion_model." in k:
            unet_sd[k.replace("model.diffusion_model.", "unet.")] = v
        elif "first_stage_model." in k:
            vae_sd[k.replace("first_stage_model.", "vae.")] = v
        elif "cond_stage_model." in k:
            text_encoder_sd[k.replace("cond_stage_model.", "text_encoder.")] = v

    log.info(f"UNet tensors: {len(unet_sd)}, VAE tensors: {len(vae_sd)}, "
             f"TextEncoder tensors: {len(text_encoder_sd)}")

    # Create models from scratch using configs embedded in the state dict
    # UNet config can be inferred from the state dict keys
    log.info("Building UNet from state dict...")
    try:
        # Infer UNet config from state dict keys
        # Find the number of block groups by looking at in_channels patterns
        # The standard SD 1.5 UNet has block_out_channels = [320, 640, 1280, 1280]
        in_channels = unet_sd.get("unet.conv_in.weight").shape[1]
        out_channels = unet_sd.get("unet.conv_out.weight").shape[0]
        # Determine block structure from key patterns
        block_groups = set()
        for k in unet_sd:
            # Look for down_blocks/X.mid_block...
            import re
            m = re.search(r"unet\.down_blocks\.(\d+)\.mid_block\.0\.transformer\.", k)
            if m:
                block_groups.add(int(m.group(1)))
            m = re.search(r"unet\.up_blocks\.(\d+)\.mid_block\.0\.transformer\.", k)
            if m:
                block_groups.add(int(m.group(1)))
        num_groups = max(block_groups) + 1 if block_groups else 4

        unet_config = {
            "_class_name": "UNet2DConditionModel",
            "sample_size": 64,
            "in_channels": in_channels,
            "out_channels": out_channels,
            "center_input_sample": False,
            "flip_sin_to_cos": True,
            "freq_shift": 0,
            "down_block_types": ["CrossAttnDownBlock2D"] * num_groups + ["DownBlock2D"],
            "mid_block_type": "UNetMidBlock2DCrossAttn",
            "up_block_types": ["UpBlock2D"] + ["CrossAttnUpBlock2D"] * num_groups,
            "only_cross_attention": False,
            "block_out_channels": [320, 640, 1280, 1280][:num_groups] + [1280],
            "layers_per_block": 2,
            "downsample_padding": 1,
            "mid_block_scale_factor": 1,
            "dropout": 0.0,
            "act_fn": "silu",
            "norm_num_groups": 32,
            "norm_eps": 1e-05,
            "cross_attention_dim": 768,
            "transformer_layers_per_block": 1,
            "encoder_hid_dim": None,
            "encoder_hid_dim_type": None,
            "attention_head_dim": 8,
            "num_attention_heads": 8,
            "dual_cross_attention": False,
            "use_linear_projection": False,
            "class_embed_type": None,
            "addition_embed_type": None,
            "addition_time_embed_dim": None,
            "num_class_embeds": None,
            "upcast_attention": False,
            "resnet_time_scale_shift": "default",
            "resnet_skip_time_act": False,
            "resnet_out_scale_factor": 1.0,
            "time_embedding_type": "positional",
            "time_embedding_dim": None,
            "time_embedding_act_fn": None,
            "timestep_post_act": None,
            "time_cond_proj_dim": None,
            "conv_in_kernel": 3,
            "conv_out_kernel": 3,
            "projection_class_embeddings_input_dim": None,
            "attention_type": "default",
            "class_embeddings_concat": False,
            "mid_block_only_cross_attention": None,
            "cross_attention_norm": None,
            "addition_embed_type_num_heads": 64,
            "mid_block_cross_attention": True,
        }

        from diffusers.configuration_utils import ConfigMixin
        from diffusers.models.unet_2d_condition import UNet2DConditionModel

        unet = UNet2DConditionModel.from_config(unet_config)
        log.info(f"UNet created: {sum(p.numel() for p in unet.parameters())//1e6:.0f}M params")
    except Exception as e:
        log.error(f"Failed to create UNet: {e}")
        sf.load_file = _orig_load_file
        return

    # VAE
    log.info("Building VAE from state dict...")
    try:
        from diffusers.models.autoencoder_kl import AutoencoderKL

        vae_in_channels = vae_sd.get("vae.decoder.conv_in.weight").shape[1]
        vae_out_channels = vae_sd.get("vae.decoder.conv_out.weight").shape[0]
        vae_latent_channels = vae_sd.get("vae.post_quant_conv.weight").shape[0]

        vae_config = {
            "_class_name": "AutoencoderKL",
            "sample_size": 64,
            "in_channels": vae_in_channels,
            "out_channels": vae_out_channels,
            "latent_channels": vae_latent_channels,
            "scaling_factor": 0.18215,
            "shift_factor": 0.0,
            "force_upcast": False,
            "pooling_type": "avg",
            "norm_num_groups": 32,
            "blocks": 2,
            "use_quantiles": False,
        }

        vae = AutoencoderKL.from_config(vae_config)
        log.info(f"VAE created: {sum(p.numel() for p in vae.parameters())//1e6:.0f}M params")
    except Exception as e:
        log.error(f"Failed to create VAE: {e}")
        sf.load_file = _orig_load_file
        return

    # Text encoder
    log.info("Building text encoder from state dict...")
    try:
        from transformers import CLIPTextConfig, CLIPTextModel

        # Infer config from state dict
        text_encoder_sd_keys = list(text_encoder_sd.keys())
        # Check if it's SD 1.5 CLIP (has text_model.embeddings.position_ids)
        is_sd15_clip = any("text_model.embeddings.position_ids" in k for k in text_encoder_sd_keys)
        # Check projection
        has_projection = any("text_projection.weight" in k for k in text_encoder_sd_keys)

        hidden_size = text_encoder_sd.get("text_encoder.text_model.embeddings.token_embedding.weight").shape[1]
        num_labels = 1
        intermediate_size = int(hidden_size * 4)
        num_attention_heads = 8
        num_hidden_layers = 12

        clip_config_dict = {
            "architectures": ["CLIPTextModel"],
            "attention_dropout": 0.0,
            "bos_token_id": 2,
            "class_name": "CLIPTextModel",
            "hidden_act": "gelu",
            "hidden_size": hidden_size,
            "initializer_range": 0.02,
            "intermediate_size": intermediate_size,
            "layer_norm_eps": 1e-05,
            "model_type": "clip_text_model",
            "pad_token_id": 2,
            "projection_dim": 768,
            "torch_dtype": "float16",
            "transformers_version": "4.30.2",
            "vocab_size": 49408,
            "hidden_dropout_prob": 0.0,
            "num_attention_heads": num_attention_heads,
            "num_hidden_layers": num_hidden_layers,
            "bos_token_id": 2,
            "pad_token_id": 2,
            "projection_dim": 768 if has_projection else None,
        }

        text_config = CLIPTextConfig.from_dict(clip_config_dict)
        text_encoder = CLIPTextModel(text_config)
        log.info(f"TextEncoder created: {sum(p.numel() for p in text_encoder.parameters())//1e6:.1f}M params")
    except Exception as e:
        log.error(f"Failed to create TextEncoder: {e}")
        sf.load_file = _orig_load_file
        return

    # ── Load weights ───────────────────────────────────────────────────────
    log.info("Loading weights into models...")
    t1 = time.time()

    try:
        # UNet weights
        missing_u, unexpected_u = unet.load_state_dict(unet_sd, strict=False)
        log.info(f"UNet: {len(missing_u)} missing, {len(unexpected_u)} unexpected keys")
        if missing_u:
            log.warning(f"  Missing: {missing_u[:5]}...")
        if unexpected_u:
            log.warning(f"  Unexpected: {unexpected_u[:5]}...")

        # VAE weights
        missing_v, unexpected_v = vae.load_state_dict(vae_sd, strict=False)
        log.info(f"VAE: {len(missing_v)} missing, {len(unexpected_v)} unexpected keys")
        if missing_v:
            log.warning(f"  Missing: {missing_v[:5]}...")

        # Text encoder weights — use CLIPTextModel.from_pretrained as base
        # and re-load with state dict. This is the trickiest part because
        # the key names differ between the safetensors format and the
        # transformers format.
        # SD 1.5 safetensors uses: cond_stage_model.transformer.text_model.*
        # Transformers uses: text_encoder.text_model.*
        # We've already mapped them above. Now just call load_state_dict.

        # Convert text_encoder_sd to match CLIPTextModel expected keys
        # Remove any key prefix adjustments already done
        te_load_sd = text_encoder_sd.copy()

        # Handle position_ids which CLIPTextModel may not have in state dict
        if "text_encoder.text_model.embeddings.position_ids" in te_load_sd:
            # This is a non-trainable parameter — just set it directly
            text_encoder.text_model.embeddings.position_ids = torch.nn.Parameter(
                te_load_sd["text_encoder.text_model.embeddings.position_ids"],
                requires_grad=False
            )
            del te_load_sd["text_encoder.text_model.embeddings.position_ids"]

        missing_t, unexpected_t = text_encoder.load_state_dict(te_load_sd, strict=False)
        log.info(f"TextEncoder: {len(missing_t)} missing, {len(unexpected_t)} unexpected keys")
        if missing_t:
            log.warning(f"  Missing: {missing_t[:5]}...")
        if unexpected_t:
            log.warning(f"  Unexpected: {unexpected_t[:5]}...")

        log.info(f"Weights loaded in {time.time() - t1:.1f}s")
    except Exception as e:
        log.error(f"Failed to load weights: {e}")
        import traceback
        traceback.print_exc()
        sf.load_file = _orig_load_file
        return

    sf.load_file = _orig_load_file  # restore original

    # ── Assemble pipeline ───────────────────────────────────────────────────
    log.info("Assembling pipeline...")
    try:
        from diffusers import DDPMScheduler
        from transformers import CLIPTokenizer

        tokenizer = CLIPTokenizer.from_pretrained(
            "runwayml/stable-diffusion-v1-5", subfolder="tokenizer"
        )

        scheduler = DDPMScheduler.from_config(
            _unet_config_to_scheduler_config(unet_config)
        )

        pipe = StableDiffusionPipeline(
            vae=vae,
            text_encoder=text_encoder,
            tokenizer=tokenizer,
            unet=unet,
            scheduler=scheduler,
            safety_checker=None,
        )

        if torch.cuda.is_available():
            pipe = pipe.to("cuda")
            log.info(f"Model on CUDA ({torch.cuda.get_device_name(0)})")
            vram = torch.cuda.memory_allocated(0) / 1e9
            log.info(f"VRAM usage: {vram:.1f}GB")
        else:
            log.info("Model on CPU")

        log.info(f"Model loaded successfully in {time.time() - t0:.1f}s")
    except Exception as e:
        log.error(f"Failed to assemble pipeline: {e}")
        import traceback
        traceback.print_exc()
        return

    _sd_pipeline = pipe
    _loaded_model_title = _loaded_model_name

    # Warm up with a trivial forward pass
    log.info("Warming up model (1-step dummy)...")
    try:
        _ = _generate_image(Txt2ImgRequest(
            prompt="test", negative_prompt="", steps=1,
            width=64, height=64, cfg_scale=1, seed=42,
        ))
        log.info("Warmup complete — model ready")
    except Exception as e:
        log.warning(f"Warmup failed ({e}), but model is loaded")

    log.info(f"StaticDiffusion ready on port {PORT} — model: {_loaded_model_name}")


def _unet_config_to_scheduler_config(unet_config):
    """Create a minimal DDPMScheduler config from UNet config."""
    return {
        "_class_name": "DDPMScheduler",
        "scheduler_name": "DDPMScheduler",
        "num_train_timesteps": 1000,
        "beta_start": 0.0001,
        "beta_end": 0.02,
        "beta_schedule": "linear",
        " trained_betas": None,
        "variance_type": "fixed_small_log",
        "clip_sample": True,
        "prediction_type": "epsilon",
        "use_beta_schedule": True,
    }


# ── SD API endpoints ────────────────────────────────────────────────────────────

@app.get("/sdapi/v1/sd-models", response_model=List[SDModelItem])
async def get_sd_models():
    if _loaded_model_title:
        return [SDModelItem(title=_loaded_model_title, name=_loaded_model_name, model_name=_loaded_model_name)]
    return []


@app.get("/sdapi/v1/options", response_model=OptionsModel)
async def get_options():
    return OptionsModel(sd_model_checkpoint=_loaded_model_title)


@app.post("/sdapi/v1/options", response_model=OptionsModel)
async def set_options(body: OptionsModel):
    if body.sd_model_checkpoint and body.sd_model_checkpoint != _loaded_model_title:
        log.info(f"Checkpoint switch requested: {body.sd_model_checkpoint} — not supported in static mode")
    return OptionsModel(sd_model_checkpoint=_loaded_model_title)


@app.get("/sdapi/v1/samplers", response_model=List[SamplerItem])
async def get_samplers():
    return [
        SamplerItem(name="DDIM", sd_name="DDIM", description="DDIM"),
        SamplerItem(name="DPM++ 2M Karras", sd_name="DPM++ 2M Karras", description="DPM++ 2M Karras"),
        SamplerItem(name="DPM++ 2S a Karras", sd_name="DPM++ 2S a Karras", description="DPM++ 2S a Karras"),
        SamplerItem(name="Euler a", sd_name="Euler a", description="Euler a"),
        SamplerItem(name="Euler", sd_name="Euler", description="Euler"),
        SamplerItem(name="Heun", sd_name="Heun", description="Heun"),
    ]


@app.get("/sdapi/v1/schedulers", response_model=List[SchedulerItem])
async def get_schedulers():
    return [
        SchedulerItem(name="Normal", schedulers=["Normal"]),
        SchedulerItem(name="Karras", schedulers=["Karras"]),
        SchedulerItem(name="Exponential", schedulers=["Exponential"]),
    ]


@app.get("/sdapi/v1/upscalers", response_model=List[UpscalerItem])
async def get_upscalers():
    return [
        UpscalerItem(name="Latent", upscale_with="latent"),
        UpscalerItem(name="Nearest", upscale_with="nearest"),
        UpscalerItem(name="Nearest-exact", upscale_with="nearest-exact"),
        UpscalerItem(name="Lanczos", upscale_with="lanczos"),
        UpscalerItem(name="R-ESRGAN 4x+", upscale_with="realesrgan"),
        UpscalerItem(name="R-ESRGAN 4x+ Anime6B", upscale_with="realesrgan"),
    ]


@app.get("/sdapi/v1/latent-upscale-modes", response_model=List[str])
async def get_latent_upscale_modes():
    return ["Linear", "Nearest"]


@app.get("/sdapi/v1/progress", response_model=ProgressResponse)
async def get_progress():
    return ProgressResponse(
        progress=_progress_state.get("progress", 0.0),
        state=_progress_state.get("state", {}),
        textinfo=_progress_state.get("textinfo", ""),
        eta_relative=_progress_state.get("eta_relative", 0.0),
    )


@app.post("/sdapi/v1/txt2img", response_model=Txt2ImgResponse)
async def txt2img(req: Txt2ImgRequest):
    if _sd_pipeline is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    try:
        images_b64, seed, info_str = _generate_image(req)
        return Txt2ImgResponse(
            images=images_b64,
            parameters={
                "prompt": req.prompt, "negative_prompt": req.negative_prompt,
                "steps": req.steps, "width": req.width, "height": req.height,
                "cfg_scale": req.cfg_scale, "sampler_name": req.sampler_name,
                "seed": seed, "batch_size": req.batch_size,
            },
            info=info_str,
        )
    except Exception as e:
        log.error(f"Generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")


@app.post("/sdapi/v1/interrupt")
async def interrupt():
    return {"interrupt": "ok"}


@app.post("/sdapi/v1/server-restart")
async def server_restart():
    raise HTTPException(status_code=501, detail="Restart not supported in static mode")


@app.post("/sdapi/v1/server-kill")
async def server_kill():
    os._exit(0)


@app.post("/sdapi/v1/unload-checkpoint")
async def unload_checkpoint():
    global _sd_pipeline, _loaded_model_name, _loaded_model_title
    _sd_pipeline = None
    _loaded_model_name = ""
    _loaded_model_title = ""
    torch.cuda.empty_cache() if torch.cuda.is_available() else None
    return {"status": "unloaded"}


@app.get("/sdapi/v1/refresh-checkpoints")
async def refresh_checkpoints():
    return {"status": "ok"}


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="StaticDiffusion server")
    parser.add_argument("--port", type=int, default=PORT, help="Port to listen on")
    parser.add_argument("--checkpoint", type=str, default="", help="Path to .safetensors checkpoint")
    parser.add_argument("--models-dir", type=str, default=SD_MODELS_DIR, help="Directory to search for checkpoints")
    args = parser.parse_args()

    os.environ["SD_PORT"] = str(args.port)
    os.environ["SD_CHECKPOINT_PATH"] = args.checkpoint or ""
    os.environ["SD_MODELS_DIR"] = args.models_dir

    import uvicorn
    log.info(f"Starting StaticDiffusion on 127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
