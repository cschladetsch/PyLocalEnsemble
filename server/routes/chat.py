import re, json, asyncio, random
import queue as _queue
import requests as req
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

import config
import llm
import state
import image as image_mod

_WAKING_REPLIES = [
    "Just a moment… I'm still waking up.",
    "One second — almost ready.",
    "Still coming to. Give me just a moment.",
    "Bear with me — almost there.",
    "Not quite ready yet. Just a moment.",
]

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(max_length=4000)


@router.post("/chat")
async def chat(body: ChatRequest):
    print(f"\n[backend] Received /chat request: {body.message[:50]}...")

    async def _stream():
        # --- Phase 1: instant scripted reply if LLM not ready yet ---
        if not llm.LLM_READY:
            reply = random.choice(_WAKING_REPLIES)
            words = reply.split()
            for i, word in enumerate(words):
                token = word + (' ' if i < len(words) - 1 else '')
                yield f"data: {json.dumps({'delta': token})}\n\n"
                await asyncio.sleep(0.04)
            yield f"data: {json.dumps({'done': True, 'reply': reply, 'auto_image': False, 'retry': True})}\n\n"
            return

        # --- Phase 2: normal chat ---
        try:
            # Rewrite "verb your <body>" → "verb your own <body>" so the model
            # cannot misread self-action commands as acting on the user.
            effective_msg = re.sub(
                r'\b(suck|lick|touch|rub|stroke|squeeze|finger|spread|grab|cup|pinch|'
                r'caress|massage|insert|bite|tease|flick|pull|twist)\b(\s+your\b)(?!\s+own\b)',
                r'\1\2 own',
                body.message, flags=re.IGNORECASE
            )
            llm.history.append({"role": "user", "content": effective_msg})
            sys_prompt = state.SYSTEM_PROMPT + "\n\nMatch reply length to the message. A casual greeting or one-liner: one sentence only. A question or request: two sentences maximum. Never lecture, philosophize, or ask multiple questions back." + config.banned_phrases_note()
            if llm.memory:
                sys_prompt += f"\n\nMemory of earlier conversation:\n{llm.memory}"
            messages = [{"role": "system", "content": sys_prompt}] + list(llm.history)
            print(f"[chat] user: {body.message!r}")

            q = _queue.Queue()
            collected = []

            def _run():
                llm._chat_in_progress.set()
                try:
                    p = config.CFG.get("llm_params", config._DEFAULT_CONFIG["llm_params"])
                    trimmed = list(messages)
                    r = None
                    while True:
                        r = req.post(f"{llm.LLAMA_URL}/v1/chat/completions", json={
                            "model":             llm.llm_model(),
                            "messages":          trimmed,
                            "stream":            True,
                            "cache_prompt":      True,
                            **p,
                        }, stream=True, timeout=120)
                        if r.status_code == 400:
                            try:
                                err = r.json().get("error", {})
                            except Exception:
                                err = {}
                            if "exceed_context_size" in err.get("type", "") or "exceed_context_size" in err.get("message", ""):
                                n_prompt  = err.get("n_prompt_tokens", 0)
                                n_ctx     = err.get("n_ctx", 4096)
                                need_free = max((n_prompt - n_ctx) + 512, 512)
                                freed = 0
                                non_sys = [i for i, m in enumerate(trimmed) if m["role"] != "system"]
                                while freed < need_free and len(non_sys) >= 2:
                                    idx = non_sys.pop(0)
                                    freed += len(trimmed[idx].get("content", "")) // 4
                                    del trimmed[idx]
                                    non_sys = [i for i, m in enumerate(trimmed) if m["role"] != "system"]
                                if freed > 0:
                                    print(f"[chat] context overflow — trimmed to {len(trimmed)} msgs (~{freed} tokens freed), retrying")
                                    continue
                        if r.status_code != 200:
                            print(f"[chat] Error {r.status_code}: {r.text}")
                            r.raise_for_status()
                        break
                    for line in r.iter_lines():
                        if not line:
                            continue
                        if isinstance(line, bytes):
                            line = line.decode("utf-8")
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload == "[DONE]":
                            break
                        data = json.loads(payload)
                        delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            collected.append(delta)
                            q.put(delta)
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    q.put(e)
                finally:
                    llm._chat_in_progress.clear()
                q.put(None)

            loop = asyncio.get_running_loop()
            fut  = loop.run_in_executor(None, _run)

            while True:
                try:
                    item = q.get_nowait()
                except _queue.Empty:
                    await asyncio.sleep(0.005)
                    continue
                if item is None:
                    break
                if isinstance(item, Exception):
                    if llm.history and llm.history[-1].get("role") == "user":
                        llm.history.pop()
                    yield f"data: {json.dumps({'error': str(item)})}\n\n"
                    return
                yield f"data: {json.dumps({'delta': item})}\n\n"

            await fut

            reply = "".join(collected)
            print(f"[chat] raw reply ({len(reply)} chars): {reply!r}")
            reply = re.sub(r'\s*\(.*?\)', '', reply).strip()
            reply = re.sub(r'^[Aa]lice\s*[:”]\s*', '', reply).strip().strip('”””')
            reply = re.sub(r'\bAs Alice,?\s*', '', reply, flags=re.IGNORECASE).strip()
            # Strip Dolphin boilerplate closers that survive banned_phrases injection
            reply = re.sub(
                r'\s*(This intimate act connects us.*|'
                r'binding us together with.*|'
                r'It\'?s an act (?:of pure pleasure|that connects).*|'
                r'a (?:sensual )?dance (?:of passion|between us).*)',
                '', reply, flags=re.DOTALL | re.IGNORECASE
            ).strip()
            reply = re.sub(
                r'\s*(Please note\b|Note that\b|I should mention\b|I\'ve aimed\b|I have aimed\b|'
                r'I want to note\b|It\'s worth noting\b|As an AI\b|I\'m an AI\b|'
                r'Here\'s a revised\b|Here is a revised\b).*',
                '', reply, flags=re.DOTALL | re.IGNORECASE
            ).strip()

            llm.history.append({"role": "assistant", "content": reply})

            auto_img = state.should_auto_image(body.message)
            print(f"[chat] reply sent ({len(reply)} chars), auto_image={auto_img}")
            yield f"data: {json.dumps({'done': True, 'reply': reply, 'auto_image': auto_img})}\n\n"

            # Compress history and save in background — don't block stream close.
            # compress_history may trigger a full LLM summarisation call when history
            # overflows; doing it before `done` caused 30-60 s UI freezes.
            loop.run_in_executor(None, lambda: (llm.compress_history(), llm.save_history()))

            # Clear any stale pre-extracted prompt so image gen re-extracts fresh.
            state._pre_sd_prompt   = None
            state._pre_sd_negative = ""
            state._pre_sd_nudity   = None

        except Exception as e:
            import traceback
            traceback.print_exc()
            if llm.history and llm.history[-1].get("role") == "user":
                llm.history.pop()
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")
