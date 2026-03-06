# Alice

A single-file local AI companion with chat and image generation.
Powered by [Ollama](https://ollama.com) (LLM) and [Stable Diffusion WebUI Forge](https://github.com/lllyasviel/stable-diffusion-webui-forge) (images).
Everything runs locally -- no cloud, no API keys, no subscriptions.

---

## Quick Start

```
python alice.py
```

That's it. On first run it installs everything missing, then opens the browser at `http://localhost:8000`.

---

## Prerequisites

These must be installed before running `alice.py`. Everything else is handled automatically.

### 1. Python 3.10 or later

Download from https://python.org

> **Important:** During installation, tick **"Add Python to PATH"**. Without this, `python` won't be found from the terminal.

Verify:
```powershell
python --version
```

### 2. Git

Download from https://git-scm.com

Verify:
```powershell
git --version
```

### 3. A GPU (strongly recommended)

Stable Diffusion image generation is extremely slow on CPU. An NVIDIA GPU with 6GB+ VRAM is recommended. The RTX 2070 (8GB) works well.

If you have an NVIDIA GPU, ensure you have up-to-date drivers from https://nvidia.com/drivers

---

## First Run Walkthrough

Run from a terminal (PowerShell or Command Prompt) in the folder containing `alice.py`:

```powershell
cd C:\path\to\alice
python alice.py
```

Alice will work through the following steps automatically:

| Step | What happens |
|------|-------------|
| Python deps | Installs `fastapi`, `uvicorn`, `requests`, `pydantic` via pip |
| Ollama | Downloads and installs Ollama if not found |
| Ollama service | Starts the Ollama background service |
| mistral-nemo | Pulls the model (~7GB) if not present |
| alice-nemo | Creates the Alice persona modelfile if not present |
| Forge | Clones Stable Diffusion WebUI Forge (~46MB repo) if not present |
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
   alice\stable-diffusion-webui-forge\models\Stable-diffusion\
   ```
3. Re-run `python alice.py`

Any other `.safetensors` checkpoint will also work.

---

## Directory Structure

After first run, your folder will look like this:

```
alice\
  alice.py                          <- the app (only file you need)
  alice.modelfile                   <- Alice persona (auto-generated)
  stable-diffusion-webui-forge\     <- Stable Diffusion (auto-cloned)
    models\
      Stable-diffusion\
        dreamshaper_8.safetensors   <- you provide this
    webui.bat
    venv\
    ...
```

---

## Using Alice

### Chat

Type a message and press **Enter** or click **Send**. Alice responds in character.

### Image generation

Type `/image` to generate an image based on the current conversation.

You can add extra instructions after `/image` to override or extend the scene:

```
/image
/image holding a rose, candlelight, close up
/image standing in a doorway, backlit, dramatic lighting
```

The extra text is prepended to the generated prompt so Stable Diffusion weights it highest.

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
 ├─ /chat  ──► Ollama (alice-nemo / mistral-nemo)
 │              └─ maintains conversation history
 │
 └─ /image ──► Ollama (mistral-nemo)
                └─ extracts SD prompt from history
                    └─ Forge API (port 7860)
                        └─ dreamshaper_8 on RTX 2070
```

### Alice persona (alice-nemo)

Alice is `mistral-nemo` with a custom system prompt stored in `alice.modelfile`. Edit this file and re-run `ollama create alice-nemo -f alice.modelfile` to change her personality, appearance, or backstory.

### Image appearance

Alice's physical appearance is hardcoded in `alice.py` as `ALICE_APPEARANCE` and prepended to every Stable Diffusion prompt to maintain visual consistency across images. Edit this string to change her look.

---

## Troubleshooting

### `python` not found
Reinstall Python and ensure **"Add Python to PATH"** is ticked.

### Ollama not found after install
Ollama installs to `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`. If it installed elsewhere, edit `OLLAMA_EXE` at the top of `alice.py`.

### `launch.py not found` error from Forge
Do **not** run `webui.bat` directly. Let `alice.py` start Forge -- it sets the correct working directory automatically. If you ran it manually from the wrong directory, just run `python alice.py` instead.

### Images not generating / Forge 404
Forge must be running with the `--api` flag. `alice.py` patches `webui.bat` automatically on first clone. If you installed Forge separately, add this line to your `webui.bat`:
```bat
set COMMANDLINE_ARGS=--api --cuda-malloc
```

### Forge times out on startup
First load can take 2-3 minutes while it installs its own venv and downloads PyTorch. Subsequent starts are faster (~30 seconds). Alice will wait up to 5 minutes automatically.

### `No image generated`
Check the terminal running `alice.py` for a `Forge error:` line. Common causes:
- Forge crashed -- `alice.py` will attempt to restart it automatically on the next `/image`
- No checkpoint in `models\Stable-diffusion\` -- Forge loads but can't generate without a model

### Slow image generation
Expected on first generation as the model loads into VRAM. Subsequent generations on the same session are faster. Make sure Forge is using your GPU -- the terminal should show `Device: cuda:0 NVIDIA GeForce ...`.

### `alice-nemo model not found`
The persona wasn't created. Run manually:
```powershell
ollama create alice-nemo -f alice\alice.modelfile
```

---

## Configuration

All configuration is at the top of `alice.py`:

```python
ALICE_DIR    = os.path.dirname(os.path.abspath(__file__))   # where alice.py lives
FORGE_DIR    = os.path.join(ALICE_DIR, "stable-diffusion-webui-forge")
OLLAMA_EXE   = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
ALICE_MODEL  = "alice-nemo"       # the chat model (persona)
PROMPT_MODEL = "mistral-nemo"     # the model used to extract SD prompts

ALICE_APPEARANCE = (              # prepended to every image prompt
    "woman, Alice, very long blonde hair, blue eyes, 5'8\", DD breasts, "
    "beautiful face, elegant, sultry"
)
```

---

## Requirements Summary

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| OS | Windows 10/11 | Linux/macOS untested |
| Python | 3.10+ | Must be in PATH |
| Git | Any recent | Must be in PATH |
| RAM | 16GB | 32GB recommended |
| VRAM | 6GB | 8GB+ recommended |
| Disk | 30GB free | 7GB models + 15GB Forge venv + checkpoints |
| GPU | NVIDIA | AMD untested with Forge |

---

## Ports Used

| Port | Service |
|------|---------|
| 8000 | Alice (FastAPI) |
| 7860 | Stable Diffusion Forge |
| 11434 | Ollama |