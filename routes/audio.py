import re, asyncio, json, queue, threading
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import config
import tts
import stt

router = APIRouter()


class VoiceRequest(BaseModel):
    voice: str


class TtsRequest(BaseModel):
    text: str


def _tts_clean(text: str) -> str:
    """Strip markdown that TTS would read aloud."""
    text = re.sub(r'\*+([^*]+)\*+', r'\1', text)
    text = re.sub(r'_+([^_]+)_+',   r'\1', text)
    text = re.sub(r'^#{1,6}\s+',    '',    text, flags=re.MULTILINE)
    return re.sub(r'\s+', ' ', text).strip()


@router.get("/voices")
async def list_voices():
    current = config.CFG.get("tts", {}).get("voice", "af_nicole")
    return JSONResponse({"voices": tts.VOICES, "current": current})


@router.post("/voice")
async def set_voice(body: VoiceRequest):
    if body.voice not in tts.VOICES:
        return JSONResponse({"error": "Unknown voice"}, status_code=400)
    config.CFG.setdefault("tts", {})["voice"] = body.voice
    return JSONResponse({"status": "ok", "voice": body.voice})


@router.post("/tts")
async def speak(body: TtsRequest):
    import sys
    if "--no-speech" in sys.argv:
        return JSONResponse({"audio": None})
    if tts.TTS is None:
        return JSONResponse({"error": "TTS not ready"}, status_code=503)
    try:
        audio = await asyncio.get_running_loop().run_in_executor(
            None, lambda: tts.tts_wav_b64(_tts_clean(body.text))
        )
        return JSONResponse({"audio": audio})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/tts/stream")
async def speak_stream(body: TtsRequest):
    import sys
    if "--no-speech" in sys.argv:
        async def _empty():
            yield 'data: {"done":true}\n\n'
        return StreamingResponse(_empty(), media_type="text/event-stream")
    if tts.TTS is None:
        async def _err():
            yield 'data: {"error":"TTS not ready"}\n\n'
        return StreamingResponse(_err(), media_type="text/event-stream", status_code=503)

    clean = _tts_clean(body.text)
    q:    queue.Queue     = queue.Queue()
    stop: threading.Event = threading.Event()

    def _worker():
        try:
            for b64, is_last in tts.tts_wav_b64_stream(clean):
                if stop.is_set():
                    break
                q.put(('chunk', b64, is_last))
        except Exception as e:
            q.put(('error', str(e), True))
        q.put(('done', None, True))

    threading.Thread(target=_worker, daemon=True).start()

    async def _stream():
        loop = asyncio.get_running_loop()
        try:
            while True:
                item = await loop.run_in_executor(None, q.get)
                kind, data, _ = item
                if kind == 'done':
                    break
                elif kind == 'error':
                    yield f'data: {json.dumps({"error": data})}\n\n'
                    break
                else:
                    yield f'data: {json.dumps({"chunk": data})}\n\n'
        finally:
            stop.set()          # tell worker to stop generating
            q.put(('done', None, True))  # unblock any pending q.get in the executor

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.post("/stt")
async def stt_endpoint(request: Request):
    data = await request.body()
    if not data:
        return JSONResponse({"error": "No audio data"}, status_code=400)
    loop = asyncio.get_running_loop()
    try:
        text = await loop.run_in_executor(
            None, lambda: stt.transcribe(data, request.headers.get("content-type", ""))
        )
        return JSONResponse({"text": text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
