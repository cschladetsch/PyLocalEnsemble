"""Step 5: Kokoro TTS model download."""
import os
from installer.helpers import TTS_DIR, heading, ok, info, _download

_TTS_MODEL_URL  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/kokoro-v0_19.onnx"
_TTS_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/voices.bin"


def install_tts_models():
    heading("5/6", "TTS models (Kokoro)")
    os.makedirs(TTS_DIR, exist_ok=True)
    model_path  = os.path.join(TTS_DIR, "kokoro-v0_19.onnx")
    voices_path = os.path.join(TTS_DIR, "voices.bin")

    stale = os.path.join(TTS_DIR, "voices.json")
    if os.path.exists(stale):
        os.remove(stale)

    if not os.path.exists(model_path):
        info("downloading Kokoro ONNX model (~80 MB) ...")
        _download(_TTS_MODEL_URL, model_path, "kokoro-v0_19.onnx")
    else:
        ok("Kokoro model already present")

    if not os.path.exists(voices_path):
        info("downloading Kokoro voices ...")
        _download(_TTS_VOICES_URL, voices_path, "voices.bin")
    else:
        ok("Kokoro voices already present")
