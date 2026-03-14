import os, io, wave, base64
from kokoro_onnx import Kokoro as _Kokoro
import config
from utils import step, ok, warn

TTS    = None
VOICES = ["af_nicole", "af_bella", "af_sky", "bf_emma", "bf_isabella"]


def load_tts():
    global TTS
    step("Loading TTS (Kokoro)...")
    model_path  = os.path.join(config.TTS_DIR, "kokoro-v0_19.onnx")
    voices_path = os.path.join(config.TTS_DIR, "voices.bin")
    if not os.path.exists(model_path) or not os.path.exists(voices_path):
        warn("TTS models not found — run install.py. Audio will be disabled.")
        return
    try:
        import numpy as np
        _orig_load = np.load
        np.load = lambda *a, **kw: _orig_load(*a, **{**kw, "allow_pickle": True})
        try:
            TTS = _Kokoro(model_path, voices_path)
        finally:
            np.load = _orig_load
        ok("TTS ready.")
    except Exception as e:
        warn(f"TTS failed to load: {e} — audio will be disabled.")


def tts_wav_b64(text: str) -> str:
    import numpy as np
    tts_cfg = config.CFG.get("tts", {})
    voice   = tts_cfg.get("voice", "af_nicole")
    speed   = tts_cfg.get("speed", 0.85)
    print(f"[tts] voice={voice}, speed={speed}, {len(text)} chars: {text[:60]!r}{'...' if len(text)>60 else ''}")
    samples, sr = TTS.create(text[:600], voice=voice, speed=speed, lang="en-us")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((samples * 32767).astype(np.int16).tobytes())
    return base64.b64encode(buf.getvalue()).decode()
