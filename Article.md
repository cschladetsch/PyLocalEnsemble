# Building Alice: The Architecture of a Private, Local AI Companion

In an era of centralised AI guardrails and cloud-hosted censorship, the demand for truly private, uncensored digital companions has never been higher. This article is a technical deep-dive into **Alice** — a local-first AI companion that combines streaming chat, high-fidelity voice synthesis, contextual image generation, and a multi-persona group chat system, all running entirely on your own hardware.

## The Vision: Privacy Without Compromise

Alice was built on a simple premise: your most private conversations should never leave your machine. By leveraging open-weights models and local inference engines, Alice provides an experience that is:

- **Uncensored** — no corporate filters, no "As an AI language model…" disclaimers.
- **Private** — zero data sent to the cloud. History, images, and voice stay on your disk.
- **Performant** — real-time token streaming, hardware-accelerated image generation, and sentence-level TTS pipelining.

---

## System Overview

```mermaid
graph TD
    User([User]) <--> UI["Web UI — HTML/CSS/JS\nor Android App"]
    UI <-->|SSE + REST :8000| Server["Alice — FastAPI Python Server"]

    subgraph Engines ["Local Inference Engines"]
        Server <-->|OpenAI-compat API :8080| LLM["llama-server\nGGUF model — Vulkan/CUDA/Metal"]
        Server <-->|REST :7860| SD["SD Forge\nStable Diffusion"]
        Server <-->|in-process| TTS["Kokoro ONNX\nNeural TTS"]
        Server <-->|in-process| STT["Faster-Whisper\nSTT transcription"]
    end

    subgraph Data ["Persistent Data"]
        Server --- Cfg["alice.json\nconfig + state"]
        Server --- Hist["history.json\nchat + memory"]
        Server --- Growth["group_growth.json\nrelationship memos"]
        Server --- Personas["personas/\ncharacter packs"]
    end
```

---

## The Technical Stack

### Python Backend (FastAPI)

The primary orchestrator is a FastAPI server. It manages the complete lifecycle of multiple sub-processes and in-process engines:

- **llama-server** — GGUF model inference via an OpenAI-compatible `/v1/chat/completions` endpoint, streamed token-by-token.
- **SD Forge** — Stable Diffusion image generation with ADetailer post-processing and hires-fix upscaling.
- **Kokoro ONNX** — offline neural TTS, streamed sentence-by-sentence so the first sentence plays before the reply finishes generating.
- **Faster-Whisper** — real-time mic transcription via push-to-talk with auto-stop silence detection.

### Module Structure

```mermaid
graph TD
    subgraph App ["alice.py — entry point + FastAPI app"]
        Routes["routes/ — chat · image · audio · persona · system · group"]
        Group["group.py — chatter loop · persona growth"]
    end

    subgraph Core ["Core modules"]
        Config["config.py — paths · defaults · persona merging"]
        LLM["llm.py — llama-server lifecycle · history · memory compression"]
        State["state.py — nudity state · seed · appearance"]
        TTS_M["tts.py — Kokoro synthesis · sentence streaming · effects"]
        STT_M["stt.py — Whisper transcription · RMS noise gate"]
        VRAM["vram.py — ResourceOrchestrator · priority-based VRAM arbitration"]
        Utils["utils.py — step · ok · warn · is_wsl · http_ok"]
    end

    subgraph ImagePkg ["image/ package"]
        Prompt["prompt.py — LLM prompt extraction · _detect_action · _build_tags"]
        Forge_["forge.py — Forge process start/stop · checkpoint switch"]
        Generate["generate.py — txt2img · ADetailer · nudity flag handling"]
    end

    subgraph InstallPkg ["installer/ package"]
        Steps["packages · llama · model · tts_install · forge_install"]
    end

    subgraph Extern ["External processes (GPU)"]
        LlamaServer["llama-server :8080"]
        ForgeProc["SD Forge :7860"]
    end

    Routes --> Config
    Routes --> LLM
    Routes --> State
    Routes --> TTS_M
    Routes --> STT_M
    Routes --> VRAM
    Routes --> ImagePkg
    Group --> LLM
    Group --> Config
    ImagePkg --> LLM
    ImagePkg --> State
    LLM --> LlamaServer
    VRAM --> LlamaServer
    VRAM --> ForgeProc
    Forge_ --> ForgeProc
    Generate --> ForgeProc
```

### The Rust Core

For mobile (Android) and future high-performance desktop paths, a **Rust core** under `core/` provides:

- **JNI bindings** — the Android app runs local LLM and TTS inference natively via `alice-core`.
- **Memory safety** — critical for handling large model weights and concurrent inference tasks without garbage-collection pauses.

---

## Startup Sequence

Alice's startup is a carefully ordered sequence that brings each engine up in dependency order.

```mermaid
flowchart LR
    Start([python alice.py]) --> Cfg["Load alice.json\nconfig.py"]
    Cfg --> Port["Kill stale listener\non configured port"]
    Port --> LLMConnect["Connect to llama-server\nllm.load_llm()"]
    LLMConnect -->|not running| Spawn["Spawn llama-server\nsubprocess"]
    Spawn --> Retry["Retry /health\nup to 2 min"]
    LLMConnect -->|already up| Retry
    Retry --> Hist["Restore history + memory\nllm.load_history()"]
    Hist --> TTS_Load["Load Kokoro ONNX\ntts.load_tts() — background thread"]
    TTS_Load --> ForgeStart{Forge\nalready up?}
    ForgeStart -->|no| ForgeSpawn["Start webui.bat / webui.sh\nimage.start_forge()"]
    ForgeSpawn --> ForgeReady["Push sd_checkpoints_keep_in_cpu=False\nbefore first checkpoint load"]
    ForgeStart -->|yes| ForgeReady
    ForgeReady --> CkPt["Set SD checkpoint\nimage.set_forge_model()"]
    CkPt --> VRAM_S["Notify VRAM orchestrator\nvram.notify_llm_ready()"]
    VRAM_S --> Browser["Open browser\nlocalhost:8000"]
```

---

## Chat Turn — Request Flow

The core user experience: type, receive streaming reply, hear it spoken, see a scene image appear.

```mermaid
sequenceDiagram
    participant U as User
    participant B as Browser
    participant A as alice.py
    participant L as llama-server
    participant K as Kokoro TTS
    participant F as SD Forge

    U->>B: types message (or speaks via mic)
    B->>A: POST /chat (SSE stream)
    Note over A: llm._chat_in_progress.set()
    A->>L: POST /v1/chat/completions (stream:true)
    loop token stream
        L-->>A: delta token
        A-->>B: data: {"delta": "..."}
        B-->>U: word appears live
    end
    Note over A: llm._chat_in_progress.clear()
    A-->>B: data: {"done": true, "reply": "...", "auto_image": true}

    B->>A: POST /tts/stream
    loop sentence-by-sentence
        A->>K: synthesise next sentence
        K-->>B: WAV chunk (base64 SSE)
        B-->>U: audio plays as chunks arrive
    end

    Note over A: Background: extract SD prompt\n(deferred if chat active)
    A->>L: extract_sd_prompt() — structured scene fields
    L-->>A: ACTION / BODY / CAMERA / NUDITY fields
    A->>F: POST /sdapi/v1/txt2img
    F-->>A: base64 PNG
    A-->>B: {"image_url": "/static/outputs/img_...png"}
    B-->>U: scene image appears + prompt caption
```

> **LLM non-contention:** `_chat_in_progress` is a `threading.Event` held for the full lifetime of any streaming chat call. Background LLM callers (SD prompt extraction, pair compression, scene synthesis) use `llm_chat_deferred()`, which raises immediately if the flag is set rather than queuing. This keeps chat responsive at all times.

---

## VRAM Arbitration

On 8 GB GPUs running a full-size model (e.g. Mistral-12B at ~6.5 GB), the LLM and Forge (~2.2 GB) cannot coexist. The `ResourceOrchestrator` in `vram.py` manages GPU ownership with a priority system.

```mermaid
flowchart TD
    subgraph Pri ["Priority — lower number wins"]
        P1["① INTERACTIVE\nchat · live STT"]
        P2["② GENERATION\nimage generation"]
        P3["③ BACKGROUND\nLLM idle warmup"]
    end

    ChatReq(["Chat request"]) -->|acquire 'llm'\nINTERACTIVE| Orch
    ImageReq(["Image request"]) -->|acquire 'forge'\nGENERATION| Orch

    Orch{"ResourceOrchestrator"} -->|evict lower-priority holders| Interrupt["interrupt() + unload()"]
    Interrupt -->|sleep 2 s — Windows mmap reclaim\n_llm_unload| PollRAM["wait_for_ram()\nRAM < 82%"]
    PollRAM --> Load["load requested resource"]

    Load --> LL["llama-server :8080"]
    Load --> FG["SD Forge :7860"]

    ImageDone(["Image done"]) -->|release 'forge'\nkeep checkpoint hot| Hot["Forge stays in VRAM\n(skip 15 s reload next time)"]
    Hot -->|_reload_default_async| LLMBg["LLM reloads in background\nat BACKGROUND priority"]
    LLMBg -->|_forge_unload +\n_wait_vram_free()| LL
```

Key design decisions:
- **Keep-hot**: after image gen, the Forge checkpoint stays in VRAM. The LLM evicts it lazily only when it actually needs to reload — saving ~15 s per image cycle.
- **Poll, don't sleep**: `_wait_vram_free()` samples `nvidia-smi` every 0.5 s until VRAM free ≥ 4 GB (or 15 s timeout), rather than a fixed blind sleep.
- **`sd_checkpoints_keep_in_cpu: false`** is pushed to Forge *before* each checkpoint load so the driver never duplicates model weights in CPU RAM (~2–4 GB saved).

---

## Image Generation Pipeline

```mermaid
flowchart TD
    Trigger(["User message\n/image · auto_image"]) --> Group{Group\nsession?}
    Group -->|yes| Scene["LLM synthesises scene:\n'Two women in a forest clearing…'"]
    Group -->|no| Last["Last user message"]
    Scene --> Extract
    Last --> Extract["LLM extracts structured fields\nACTION · BODY · CAMERA · NUDITY · POSE · EXTRA"]

    Extract --> Pattern["_detect_action()\npattern-match → body + camera + nudity hints"]
    Extract --> Acc["_detect_accessories()\nglasses · heels · choker …"]
    Pattern --> Build["_build_tags()\nweighted SD tag string"]
    Acc --> Build

    Build --> Rules["apply_exposure_rules()\nlighting keyword adjustments"]
    Rules --> Suffix["Append image.suffix\n(perfect hands:1.3) (five fingers:1.2) …"]
    Suffix --> Quick{quick_mode?}

    Quick -->|yes| QP["quick_steps + quick_sampler\nno hires-fix · no ADetailer"]
    Quick -->|no| FP["full steps · hires-fix upscale pass"]

    QP --> Forge["POST /sdapi/v1/txt2img\nSD Forge :7860"]
    FP --> Forge

    Forge --> AD{adetailer_hands\nenabled?}
    AD -->|yes| ADP["ADetailer inpaint pass\nhand_yolov8n.pt"]
    AD -->|no| Save
    ADP --> Save["Save PNG → outputs/"]
    Save --> URL(["Return image URL to browser"])
```

---

## Group Chat & Persona Growth

```mermaid
sequenceDiagram
    participant U as User
    participant A as alice.py / group.py
    participant L as llama-server
    participant G as group_growth.json

    U->>A: POST /group/chat {"message": "..."}
    Note over A: select speaker (consecutive-sender guard)
    Note over A: inject relationship memo + mood from G
    A->>L: stream reply for persona N
    L-->>A: token stream
    A-->>U: SSE token stream

    Note over A: _chatter_loop fires async
    loop until user sends next message
        Note over A: pick next speaker (not same as last)
        Note over A: check phrase avoidance (4-gram + content-word blacklist)
        A->>L: stream next persona reply
        L-->>A: tokens
        A-->>U: SSE stream
    end

    Note over A: if pair exchange count > 20
    A->>L: _compress_pair() — background, deferred
    L-->>A: RELATIONSHIP: + MOOD: update
    A->>G: save updated memo + emotional state
    Note over G: persists across server restarts
```

### Repetition Suppression Stack

```mermaid
flowchart TD
    Turn["New turn generated"] --> CS{"Same speaker\nas last turn?"}
    CS -->|yes| Reroll["Re-roll speaker selection"]
    CS -->|no| Ngram

    Ngram{"4-gram appears\n≥ 2 times?"} -->|yes| Avoid["Add to AVOID list\nin next system prompt"]
    Ngram -->|no| Jaccard

    Jaccard{"Jaccard similarity\n> 0.65 vs recent?"} -->|yes| Strip["Strip turn from context"]
    Jaccard -->|no| Streak

    Streak{"Same-sender streak\n> 2?"} -->|yes| Cap["Cap streak in context"]
    Streak -->|no| Emit["Emit turn to client"]
    Reroll --> Ngram
    Avoid --> Jaccard
    Strip --> Emit
    Cap --> Emit
```

---

## Memory Compression

Long conversations compress automatically so context stays fresh without growing unbounded.

```mermaid
flowchart TD
    NewMsg["New message arrives"] --> Check{"history >\nmax_history (16)?"}
    Check -->|no| Reply["Normal reply"]
    Check -->|yes| Take["Take oldest N msgs\nkeep keep_recent (8) untouched"]
    Take --> Summarise["LLM call: summarise\ninto 2–4 sentences"]
    Summarise --> Append["Append to memory string"]
    Append --> Cap{"memory >\nmax_chars (1500)?"}
    Cap -->|yes| Trim["Trim to last N chars\nat sentence boundary"]
    Cap -->|no| Inject
    Trim --> Inject["Inject memory into\nevery system prompt"]
    Inject --> Reply
```

---

## Repetition Suppression & Growth

To prevent the "AI loop" where models repeat the same phrases, Alice implements a multi-tiered suppression system:

- **N-gram blocking** — dynamically identifies and bans 2–4 word phrases appearing too frequently within a session. Injected as a hard `AVOID:` constraint in the system prompt.
- **Banned phrases** — a static, user-configurable list (e.g. `"moonlight"`, `"shadows dance"`, `"as an AI"`) always blocked regardless of session content.
- **Jaccard deduplication** — strips turns with > 65% word overlap against a recent turn before building LLM context.

The **Growth** system tracks relationship dynamics in group chats. After every 20 exchanges between a pair of personas, a background LLM call generates a structured update:

```
RELATIONSHIP: They share a wary mutual respect, each probing the other's limits…
ALICE_MOOD: guarded but intrigued
MORRIGAN_MOOD: amused, watching carefully
```

These memos are saved to `group_growth.json` and injected into each persona's system prompt, allowing the relationship to deepen continuously across sessions.

---

## Conclusion

Alice is a demonstration that the future of AI companions is local. Rather than a single monolithic model, it orchestrates multiple specialised engines — each best-in-class for its domain — through a FastAPI backend that manages their lifecycles, arbitrates hardware resources, and keeps the user experience responsive.

The VRAM arbitrator, memory compression, repetition suppression stack, and group persona growth system are the pieces that transform a chatbot into a companion: one that remembers, responds in real time, speaks naturally, and generates visual context — entirely on your hardware.

---

*Project source: [github.com/cschladetsch/PyAlice](https://github.com/cschladetsch/PyAlice)*
