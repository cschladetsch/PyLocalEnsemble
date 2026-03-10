\# Alice — Product Plan



\## What It Is



Alice is a local-first AI companion with voice, chat, and image generation. It runs entirely on the user's hardware via Ollama and Stable Diffusion WebUI Forge. No data leaves the machine. No cloud subscription required.



Key features:

\- Natural language conversation via Ollama (mistral-nemo based model)

\- Real-time image generation from conversation context via Forge

\- Voice output

\- Video playback

\- NSFW capable — uncensored, fully local

\- Split UI: chat on left, generated scene on right



\## Why It's Different



\- \*\*Fully local\*\* — nothing sent to OpenAI, Anthropic, or any cloud API

\- \*\*Privacy by design\*\* — suitable for users who won't trust cloud providers

\- \*\*One product\*\* — chat, voice, and image in a single interface

\- \*\*No ongoing subscription to an AI provider\*\*



The target user is already running Ollama or Stable Diffusion. They understand local AI. They want an experience, not a chatbot.



\---



\## Identity / Alias Strategy



Alice must be developed, sold, and supported under a separate identity to protect the developer's professional profile.



\- Separate GitHub account (alias)

\- Separate email address

\- Separate Gumroad and/or SubscribeStar account

\- Separate PayPal or Wise account for receiving payments

\- No cross-linking to real LinkedIn, GitHub (cschladetsch), or professional identity

\- Alias Reddit account for community engagement



The tech stack (Ollama, Forge, FastAPI, Python) is generic and leaves no fingerprints.



\---



\## Distribution



\### Platform: Gumroad



Gumroad is a digital product sales platform. Upload a zip or installer, set a price, share the link. Buyer pays and gets access to the download. Gumroad takes \~10% cut. No storefront to build.



\- Simple to set up under an alias

\- Handles payments, delivery, and receipts

\- Suitable for one-time purchases



\### Secondary: SubscribeStar



For recurring revenue once a user base exists. More permissive than Patreon for NSFW content.



\---



\## Pricing



\- \*\*One-time purchase: $25 USD\*\*

\- Optional future tier: $5/month for updates and new features



Rationale: the target audience is accustomed to paying for this category of software. $25 is an impulse buy that doesn't require justification. Underpricing signals low quality.



A free tier with limitations (capped usage, watermarked output) can drive paid conversions later.



\---



\## Technical Roadmap



\### Phase 1 — Make It Distributable



Current barrier to entry is high: requires manual setup of Ollama, Forge, Python 3.10, correct models, and configured endpoints.



\*\*Goal:\*\* `docker-compose up` brings everything up.



\- Single `docker-compose.yml` defining all services

\- Wrapper shell script that checks for Docker and runs compose

\- User pastes one command into terminal — that's the install



The target user is comfortable with a terminal. Full GUI installer is out of scope for v1.



\### Phase 2 — Performance



Current image generation time: \~60 seconds on RTX 2070 (8GB VRAM).



End users will typically have better hardware. However, for development experience and demos:



\- Switch sampler to \*\*DPM++ 2M Karras\*\*

\- Reduce steps to \*\*20\*\*

\- Lower CFG to \*\*5-6\*\*

\- Use 512x768 resolution for portraits instead of higher

\- Evaluate \*\*LCM or Lightning LoRA\*\* at \~0.6 weight — can reduce to 6-8 steps, sub-10 second generation



Text generation already runs first, image generates async in a second thread. This gives perceived responsiveness — the right architecture.



\### Phase 3 — UX Polish



\- Subtle loading animation on image panel during generation

\- Progressive/preview image display if Forge supports it

\- Ensure "Alice is thinking" indicator is consistent



\---



\## Launch Strategy



\### No LinkedIn



The developer's LinkedIn presence (\~800k impressions) must remain completely firewalled from Alice. Do not reference, link, or hint at Alice from any professional account.



\### Reddit — Primary Channel



The natural audience is already on:



\- r/LocalLLaMA

\- r/StableDiffusion

\- r/AICompanion



A demo gif or short clip posted to r/LocalLLaMA showing the full loop (chat → voice → image generation, all local) will reach the right audience organically. No ad spend required.



Post under the alias account. Let the product speak.



\### Landing Page (Phase 2)



Not required for launch. When needed:

\- Single HTML file

\- Alias domain via Cloudflare

\- Screenshots and demo gif

\- One buy button linking to Gumroad



\---



\## Summary



| Item | Detail |

|---|---|

| Product | Local AI companion, chat + voice + image |

| Stack | Ollama, Stable Diffusion Forge, FastAPI, HTML/JS |

| Price | $25 one-time |

| Platform | Gumroad (alias) |

| Distribution | Reddit organic |

| Identity | Fully separated alias |

| Launch blocker | Docker compose packaging |

| Current status | Working, demo-ready |

