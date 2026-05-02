import re, json, asyncio, time
import queue as _queue
import requests as req
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

_LLM_WAIT_TIMEOUT = 60   # seconds; override in tests via monkeypatch

import config
import llm
import state
import image as image_mod

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(max_length=4000)


@router.post("/chat")
async def chat(body: ChatRequest):
    print(f"\n[backend] Received /chat request: {body.message[:50]}...")

    async def _stream():
        # --- Phase 1: wait for LLM if restarting after image generation ---
        if not llm.LLM_READY:
            msg = 'LLM restarting after image generation' if llm.LLM_SUSPENDED else 'Alice is starting up'
            yield f"data: {json.dumps({'status': msg + '…'})}\n\n"
            deadline = time.monotonic() + _LLM_WAIT_TIMEOUT
            dots = 0
            while not llm.LLM_READY:
                if time.monotonic() >= deadline:
                    yield f"data: {json.dumps({'error': 'LLM not ready — please reload and try again.'})}\n\n"
                    return
                await asyncio.sleep(1)
                dots += 1
                if dots % 5 == 0:
                    elapsed = int(time.monotonic() - (deadline - _LLM_WAIT_TIMEOUT))
                    yield f"data: {json.dumps({'status': msg + f' ({elapsed}s)…'})}\n\n"
            yield f"data: {json.dumps({'status': 'Ready.'})}\n\n"

        # --- Phase 2: normal chat ---
        try:
            llm.history.append({"role": "user", "content": body.message})
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
            reply = re.sub(r'^[Aa]lice\s*[:”]\s*', '', reply).strip().strip('"“”')
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

            if auto_img:
                state._pre_sd_prompt   = None
                state._pre_sd_negative = ""
                state._pre_sd_nudity   = None
                recent    = llm.history[-8:]
                last_user = body.message.strip()
                msgs_text = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in recent)
                nudity_floor = state._nudity_state

                async def _pre_extract():
                    try:
                        base_prompt, new_nudity = await loop.run_in_executor(
                            None,
                            lambda: image_mod.extract_sd_prompt(
                                msgs_text,
                                appearance=state.ALICE_APPEARANCE,
                                last_user_msg=last_user,
                                persona=state.SYSTEM_PROMPT,
                                nudity_floor=nudity_floor,
                            )
                        )
                        prompt = image_mod.clean_tags(base_prompt)
                        prompt, extra_neg = image_mod.apply_exposure_rules(msgs_text, prompt, "")
                        state._pre_sd_prompt   = prompt
                        state._pre_sd_negative = extra_neg
                        state._pre_sd_nudity   = new_nudity
                        print(f"[chat] pre-extracted SD prompt ({len(prompt)} chars)")
                    except Exception as e:
                        print(f"[chat] SD prompt pre-extraction failed: {e}")

                asyncio.create_task(_pre_extract())

        except Exception as e:
            import traceback
            traceback.print_exc()
            if llm.history and llm.history[-1].get("role") == "user":
                llm.history.pop()
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")
