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

## Why It's Different

- **Fully local** — nothing sent to any cloud API
- **Privacy by design** — suitable for users who won't trust cloud providers
- **One product** — chat, voice, STT, and image in a single interface
- **No ongoing subscription to an AI provider**
- **Works on NVIDIA and AMD GPUs** via Vulkan backend

The target user is already running local AI tools. They understand LLMs and Stable Diffusion. They want an experience, not a chatbot.

---

## Technical Stack

```mermaid
graph TD
    subgraph Runtime ["Runtime (alice.py)"]
        FastAPI["FastAPI :8000\n(chat, TTS, STT, image API)"]
        Kokoro["Kokoro ONNX\nTTS — CPU"]
        Whisper["faster-whisper\nSTT — CPU"]
    end

    subgraph GPU ["GPU Process"]
        LlamaServer["llama-server :8080\nOpenAI-compatible API\nGGUF model — Vulkan/CUDA/Metal"]
        Forge["SD Forge :7860\nStable Diffusion"]
    end

    subgraph Install ["install.py (run once)"]
        Pip["pip packages"]
        BinaryDl["llama-server binary\nVulkan/platform build"]
        ModelDl["GGUF model download\nor scan existing"]
        TTSDl["Kokoro model + voices"]
        ForgeSetup["git clone Forge\n+ RV5 checkpoint"]
    end

    FastAPI --> LlamaServer
    FastAPI --> Forge
    FastAPI --> Kokoro
    FastAPI --> Whisper
```

---

## Roadmap

```mermaid
gantt
    dateFormat  YYYY-MM
    title Alice Development Phases

    section Phase 1 · Foundation
    llama.cpp backend (Vulkan)     :done, 2025-11, 2026-01
    STT mic input                  :done, 2026-01, 2026-02
    Config / persona system        :done, 2026-01, 2026-02
    install.py one-command setup   :done, 2026-02, 2026-03

    section Phase 2 · Distribution
    Packaging / release zip        :active, 2026-03, 2026-04
    Landing page                   :2026-04, 2026-05
    Gumroad listing                :2026-04, 2026-05

    section Phase 3 · Performance
    LCM / Lightning LoRA           :2026-05, 2026-06
    Streaming image preview        :2026-05, 2026-06

    section Phase 4 · UX
    Mobile-friendly layout         :2026-06, 2026-07
    LoRA / style picker            :2026-06, 2026-08
```

### Phase 1 — Foundation (complete)

- [x] Switch LLM backend from Ollama to llama.cpp server (Vulkan, cross-GPU)
- [x] OpenAI-compatible API (`/v1/chat/completions`, streaming SSE)
- [x] STT push-to-talk with device selector, silence auto-stop, auto-send
- [x] Config hygiene — `alice.json.example` in repo, personal config gitignored
- [x] Persona system with runtime switching
- [x] Rolling conversation memory with LLM-based compression
- [x] `install.py` — one command sets up everything (llama-server, model, TTS, Forge)
- [x] `alice.py` — assumes installed, just runs

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
    Dev([Developer\nalias identity]) -->|upload zip| Gumroad
    Gumroad -->|download link| Buyer
    Buyer -->|python install.py| Local["Local machine\nno cloud"]
    Dev -->|demo post| Reddit["r/LocalLLaMA\nr/StableDiffusion"]
    Reddit -->|organic traffic| Gumroad
```

### Identity separation

Alice must be developed, sold, and supported under a separate identity to protect the developer's professional profile.

- Separate GitHub account (alias)
- Separate email address
- Separate Gumroad and SubscribeStar accounts
- No cross-linking to real LinkedIn, GitHub, or professional identity
- Alias Reddit account for community engagement

The tech stack (llama.cpp, Forge, FastAPI, Python) is generic and leaves no fingerprints.

---

## Pricing

- **One-time purchase: $25 USD**
- Optional future tier: $5/month for updates and new personas

Rationale: the target audience is accustomed to paying for this category of software. $25 is an impulse buy. Underpricing signals low quality.

A free tier with limitations (capped personas, watermarked output) can drive paid conversions later.

---

## Summary

| Item | Detail |
|---|---|
| Product | Local AI companion — chat + voice + STT + image |
| Stack | llama.cpp, Stable Diffusion Forge, FastAPI, HTML/JS |
| GPU | NVIDIA and AMD via Vulkan |
| Price | $25 one-time |
| Platform | Gumroad (alias) |
| Distribution | Reddit organic |
| Identity | Fully separated alias |
| Current status | Working, install.py complete |
| Launch blocker | Packaging + Gumroad listing |
