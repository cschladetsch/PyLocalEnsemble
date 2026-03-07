# Alice

A single-file local AI companion with chat and image generation.
Powered by [llama.cpp](https://github.com/ggerganov/llama.cpp) (LLM via `llama-cpp-python`) and [Stable Diffusion WebUI Forge](https://github.com/lllyasviel/stable-diffusion-webui-forge) (images).
Everything runs locally -- no cloud, no API keys, no subscriptions.

---

## Quick Start

```
python alice.py
```

On first run it installs everything missing, then opens the browser at `http://localhost:8000`.

---

## Prerequisites

These must be installed before running `alice.py`. Everything else is handled automatically.

### 1. Python 3.10 or later

Download from https://python.org

> **Important:** During installation, tick **"Add Python to PATH"**. Without this, `python` won't be found from the terminal.

Verify:
```
python --version
```

### 2. Git

Download from https://git-scm.com

Verify:
```
git --version
```

### 3. A GPU (strongly recommended)

Stable Diffusion image generation is extremely slow on CPU. An NVIDIA GPU with 6GB+ VRAM is recommended. The RTX 2070 (8GB) works well.

Ensure you have up-to-date drivers from https://nvidia.com/drivers

---

## First Run Walkthrough

```
cd F:\alice
python alice.py
```

Alice works through the following steps automatically:

| Step | What happens |
|------|-------------|
| Python deps | Installs `fastapi`, `uvicorn`, `requests`, `pydantic`, `llama-cpp-python` via pip |
| LLM model | Finds or downloads a GGUF model (~7GB) if not present |
| Forge | Clones Stable Diffusion WebUI Forge if not present |
| webui.bat | Patches Forge with `--api --cuda-malloc` flags |
| Checkpoint | Pauses if no `.safetensors` model file is found (see below) |
| Forge service | Starts Forge (takes ~30-90 seconds on first load) |
| Browser | Opens `http://localhost:8000` |

---

## Checkpoint (Required for Image Generation)

Stable Diffusion needs a model file to generate images. Alice will pause and tell you if one is missing.

**Recommended:** [DreamShaper 8](https://civitai.com/models/4384/dreamshaper)

1. Download `dreamshaper_8.safetensors` from Civitai
2. Place it in:
   ```
   stable-diffusion-webui-forge\models\Stable-diffusion\
   ```
3. Re-run `python alice.py`

Any `.safetensors` checkpoint will work.

---

## Directory Structure

```
alice\
  alice.py                          <- the entire app
  alice.json                        <- your config (not committed)
  stable-diffusion-webui-forge\     <- auto-cloned, not committed
    models\
      Stable-diffusion\
        dreamshaper_8.safetensors   <- you provide this
    webui.bat
    venv\
```

---

## Configuration

All user-facing settings live in `alice.json`. This file is not committed to git (it contains your persona text). On first run it is created automatically with defaults.

```json
{
    "forge_url":    "http://localhost:7860",
    "model_path":   "",
    "appearance":   "woman, Alice, very long blonde hair, blue eyes, ...",
    "negative_prompt": "ugly, deformed, extra limbs, blurry, ...",
    "system_prompt": "You are Alice. You are enigmatic, intelligent, ...",

    "image": {
        "steps": 25,
        "width": 512,
        "height": 768,
        "cfg_scale": 7,
        "sampler_name": "DPM++ 2M Karras",
        "suffix": "photorealistic, highly detailed, 8k, masterpiece"
    }
}
```

| Field | Purpose |
|-------|---------|
| `forge_url` | Forge API base URL |
| `model_path` | Optional path to a GGUF model (blank = auto-download) |
| `appearance` | Prepended to every image prompt for visual consistency |
| `negative_prompt` | Always passed to Stable Diffusion as negative |
| `system_prompt` | System prompt used for the chat persona |
| `image` | SD generation parameters (steps, size, sampler, etc.) |

Edit `alice.json` to change Alice's personality, appearance, image quality settings, or swap models. Restart `alice.py` to apply changes.

---

## Using Alice

### Chat

Type a message and press **Enter** or **Send**. Alice responds in character.

### Image generation

Type `/image` to generate an image based on the current conversation history.

Add instructions after `/image` to control the scene. Tokens starting with `no ` are routed to the **negative prompt**:

```
/image
/image holding a rose, candlelight, close up
/image standing in a doorway, backlit, flowing dress, soft glow
```

The extra positive text is prepended to the SD prompt so Stable Diffusion weights it highest. `no X` tokens are added to the negative prompt alongside the base negatives from `alice.json`.

### Clear history

Click **Clear** in the top right to reset the conversation.

---

## How It Works

```
You
 │
 ▼
alice.py (FastAPI on port 8000)
 │
 ├─ /chat  ──► llama.cpp (GGUF model)
 │              └─ maintains conversation history
 │
 └─ /image ──► llama.cpp (prompt extractor)
                └─ extracts SD prompt tags from conversation history
                    └─ Forge API (port 7860)
                        └─ dreamshaper_8 on GPU
```

The `/image` command passes the last 12 messages to the LLM with instructions to extract Stable Diffusion prompt tags. Those tags are combined with `appearance` from `alice.json` and any extra instructions you typed.

---

## Customising the Persona

Alice's personality is defined by the `"system_prompt"` field in `alice.json`. This prompt is injected at the start of every conversation -- this is where you define personality, appearance, backstory, and the user's details.

### Tips

- Be specific about physical appearance -- the system prompt feeds both the chat responses and (indirectly) the image prompts extracted from conversation
- Keep the persona consistent with the `"appearance"` field in `alice.json` -- if the system prompt says blonde but appearance says brunette, the images and text will contradict each other
- You can swap to a different GGUF model by setting `"model_path"` or dropping one into `models\`

---



### `python` not found
Reinstall Python and tick **"Add Python to PATH"**.

### Model download is slow
The default GGUF model is ~7GB. Place your own `.gguf` file in `models\` to avoid the download.

### `launch.py not found` from Forge
Do **not** run `webui.bat` directly from the command line. Let `alice.py` launch it -- it sets the correct working directory. Just run `python alice.py`.

### Forge 404 on `/image`
Forge must run with `--api`. `alice.py` patches `webui.bat` automatically on first clone. If you installed Forge manually, add to `webui.bat`:
```bat
set COMMANDLINE_ARGS=--api --cuda-malloc
```

### Forge times out on startup
First load takes 2-3 minutes (installs venv, downloads PyTorch). Subsequent starts are ~30 seconds. Alice waits up to 5 minutes automatically.

### No image generated
Check the terminal for a `Forge error:` line. Common causes:
- Forge crashed -- alice.py auto-restarts it on the next `/image`
- No checkpoint in `models\Stable-diffusion\`

### Slow image generation
Normal on first generation while the model loads into VRAM. Check the Forge terminal shows `Device: cuda:0 NVIDIA GeForce ...` to confirm GPU is being used.

### Non-interactive sessions
If you run Alice in a non-interactive shell, it will skip opening the browser and will exit instead of waiting for input.

---

## .gitignore

The following are excluded from the repo:

```
/stable-diffusion-webui-forge
/tmp
/alice.modelfile
/.claude
/backups
alice.json
```

`alice.json` contains your personal persona text and must not be committed to a public repo.

---

## Recommendations

- Keep any custom persona or private notes in `alice.json`; it is excluded from git.
- Use `backups\` for local-only copies of files you want to keep private; it is git-ignored.
- Review defaults in `alice.py` and `Readme.md` before sharing the repo publicly.

---

## Requirements

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| OS | Windows 10/11 | Linux/macOS untested |
| Python | 3.10+ | Must be in PATH |
| Git | Any recent | Must be in PATH |
| RAM | 16GB | 32GB recommended |
| VRAM | 6GB | 8GB+ recommended |
| Disk | 30GB free | 7GB models + 15GB Forge venv + checkpoints |
| GPU | NVIDIA | AMD untested with Forge |

## Ports

| Port | Service |
|------|---------|
| 8000 | Alice (FastAPI) |
| 7860 | Stable Diffusion Forge |
