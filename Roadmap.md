# Alice — Product Roadmap

## What It Is

Alice is a local-first AI companion with voice, chat, and image generation. It runs entirely on the user's hardware. No data leaves the machine. No cloud subscription required.

Key features:
- Natural language conversation via llama.cpp (GGUF models, OpenAI-compatible API)
- Real-time image generation from conversation context via Stable Diffusion Forge
- Neural TTS (Kokoro ONNX) and STT (Whisper) — fully offline
- NSFW capable — uncensored, fully local
- Split UI: chat + voice on left, generated scene on right
- Persona system — swap character, appearance, and system prompt at runtime
- Configurable via `alice.json`
- Cross-platform: Windows 10/11, macOS, Linux, WSL2

## Why It's Different

- **Fully local** — nothing sent to any cloud API
- **Privacy by design** — suitable for users who won't trust cloud providers
- **One product** — chat, voice, STT, and image in a single interface
- **No ongoing subscription to an AI provider**
- **Works on NVIDIA, AMD (Vulkan), and Apple Silicon (Metal)**

The target user is already running local AI tools. They understand LLMs and Stable Diffusion. They want an experience, not a chatbot.

---

## Technical Stack

```mermaid
graph TD
    subgraph App ["alice.py — entry point"]
        Routes["FastAPI :8000 — routes + startup"]
    end

    subgraph Core ["Core modules"]
        Config["config.py"]
        LLM["llm.py"]
        TTS_M["tts.py"]
        STT_M["stt.py"]
        Utils["utils.py"]
    end

    subgraph ImagePkg ["image/ package"]
        Prompt["prompt.py"]
        Forge_["forge.py"]
        Generate["generate.py"]
    end

    subgraph InstallPkg ["installer/ package"]
        Helpers["helpers.py"]
        Steps["packages · llama · model · tts_install · forge_install"]
    end

    subgraph GPU ["External GPU Processes"]
        LlamaServer["llama-server :8080 — GGUF Vulkan/CUDA/Metal"]
        Forge["SD Forge :7860 — Stable Diffusion"]
    end

    subgraph CPU ["In-process (CPU)"]
        Kokoro["Kokoro ONNX — TTS"]
        Whisper["faster-whisper — STT"]
    end

    Routes --> Core
    Routes --> ImagePkg
    ImagePkg --> LLM
    LLM --> LlamaServer
    Forge_ --> Forge
    Generate --> Forge
    TTS_M --> Kokoro
    STT_M --> Whisper
    Steps --> Helpers
```

---

## Roadmap

```mermaid
gantt
    dateFormat  YYYY-MM
    title Alice Development Phases

    section Phase 1 · Foundation
    llama.cpp backend (Vulkan)          :done, 2025-11, 2026-01
    STT mic input                       :done, 2026-01, 2026-02
    Config / persona system             :done, 2026-01, 2026-02
    install.py one-command setup        :done, 2026-02, 2026-03
    Module split + config-driven memory :done, 2026-03, 2026-03

    section Phase 1b · Quality
    Cross-platform (macOS, Linux, WSL2) :done, 2026-03, 2026-03
    Refactor image/ + installer/ pkgs   :done, 2026-03, 2026-03
    Test suite (39 tests)               :done, 2026-03, 2026-03
    Built-in personas + image fixes     :done, 2026-03, 2026-03

    section Phase 2 · Distribution
    Packaging / release zip             :active, 2026-03, 2026-04
    Landing page                        :2026-04, 2026-05
    Gumroad listing                     :2026-04, 2026-05

    section Phase 3 · Performance
    LCM / Lightning LoRA                :2026-05, 2026-06
    Streaming image preview             :2026-05, 2026-06

    section Phase 4 · UX
    Mobile-friendly layout              :2026-06, 2026-07
    LoRA / style picker                 :2026-06, 2026-08
```

### Phase 1 — Foundation (complete)

- [x] Switch LLM backend from Ollama to llama.cpp server (Vulkan, cross-GPU)
- [x] OpenAI-compatible API (`/v1/chat/completions`, streaming SSE)
- [x] STT push-to-talk with device selector, silence auto-stop, auto-send
- [x] Config hygiene — `conf/alice.example.json` in repo, personal config gitignored
- [x] Persona system with runtime switching
- [x] Rolling conversation memory with LLM-based compression
- [x] `install.py` — one command sets up everything (llama-server, model, TTS, Forge)
- [x] `alice.py` — assumes installed, just runs
- [x] Module split — `config`, `llm`, `tts`, `stt`, `image`, `utils`
- [x] Memory limits configurable in `alice.json`

### Phase 1b — Quality (complete)

- [x] Cross-platform support — macOS (Metal/MPS), Linux, WSL2, Windows
- [x] Forge launch flags per platform (`--cuda-malloc` Windows, `--skip-torch-cuda-test` macOS, `--xformers` Linux)
- [x] Forge Python detection: 3.10 or 3.11 from PATH / Homebrew / pyenv on all platforms
- [x] WSL2: browser opens via `explorer.exe`/`wslview`, IP hint printed at startup, `chmod +x` on llama-server
- [x] `image/` package — split into `prompt.py`, `forge.py`, `generate.py`
- [x] `installer/` package — split into `helpers`, `packages`, `llama`, `model`, `tts_install`, `forge_install`
- [x] `conf/` directory — example configs out of root
- [x] Test suite — 39 tests across config, image utils, installer, and API endpoints
- [x] 4 built-in personas: Egyptian Goddess, Victorian Lady, Android, Forest Witch
- [x] Image fix: nude characters no longer render clothed (clothing stripped from appearance when nudity detected)
- [x] Image fix: `/image` no longer returns 400 after persona switch (history cleared)
- [x] Delete key removes current image from disk and session history
- [x] `clean_tags` correctly prefers weighted SD tags over plain duplicates

### Phase 2 — Distribution

- [ ] Package as a zip with a `start.bat` / `start.sh` launcher
- [ ] Gumroad listing under alias identity
- [ ] Demo gif / short clip for Reddit launch (r/LocalLLaMA, r/StableDiffusion)
- [ ] Simple landing page (single HTML, alias domain via Cloudflare)

### Phase 3 — Performance

Current image generation: ~30–60 s on RTX 2070 (8 GB VRAM).

- [ ] Evaluate LCM or Lightning LoRA at ~0.6 weight — target sub-10 s generation
- [ ] Streaming/progressive image preview during generation
- [ ] Reduce default steps to 20, CFG to 5–6 for better responsiveness

### Phase 4 — UX

- [ ] Mobile-friendly layout (portrait, swipeable panels)
- [ ] LoRA / style picker in the UI
- [ ] Persistent persona gallery with portrait thumbnails
- [ ] Voice activity indicator (animated waveform during recording)

---

## Distribution

```mermaid
flowchart LR
    Dev([Developer — alias identity]) -->|upload zip| Gumroad
    Gumroad -->|download link| Buyer
    Buyer -->|python alice.py| Local[Local machine — no cloud]
    Dev -->|demo post| Reddit["r/LocalLLaMA · r/StableDiffusion"]
    Reddit -->|organic traffic| Gumroad
```

### Identity separation

Alice must be developed, sold, and supported under a separate identity to protect the developer's professional profile.

- Separate GitHub account (alias)
- Separate email address
- Separate Gumroad and SubscribeStar accounts
- No cross-linking to real LinkedIn, GitHub, or professional identity
- Alias Reddit account for community engagement

---

## Pricing

- **One-time purchase: $25 USD**
- Optional future tier: $5/month for updates and new personas

Rationale: the target audience is accustomed to paying for this category of software. $25 is an impulse buy. Underpricing signals low quality.

---

## Summary

| Item | Detail |
|---|---|
| Product | Local AI companion — chat + voice + STT + image |
| Stack | llama.cpp, Stable Diffusion Forge, FastAPI, HTML/JS |
| GPU | NVIDIA and AMD (Vulkan), Apple Silicon (Metal) |
| Platform | Windows, macOS, Linux, WSL2 |
| Price | $25 one-time |
| Platform | Gumroad (alias) |
| Distribution | Reddit organic |
| Identity | Fully separated alias |
| Current status | Working — cross-platform, modular, tested |
| Launch blocker | Packaging + Gumroad listing |
