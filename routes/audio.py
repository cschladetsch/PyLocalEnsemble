import re, asyncio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
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
