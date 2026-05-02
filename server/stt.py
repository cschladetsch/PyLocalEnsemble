import os, wave, tempfile, threading
from utils import ok

_WHISPER      = None
_whisper_lock = threading.Lock()

_HALLUCINATIONS = {
    "you", "you.", "you you", "you you.", "bye", "bye.", "bye!", "thanks", "thank you",
    "thank you.", "thank you!", "thanks for watching",
    "thanks for watching!", "thank you for watching", "thank you for watching.",
    ".", "..", "...", "hmmm", "uhm", "uh", "um",
}


def ensure_whisper():
    global _WHISPER
    with _whisper_lock:
        if _WHISPER is None:
            from faster_whisper import WhisperModel
            ok("Loading Whisper STT (small.en, CPU)...")
            _WHISPER = WhisperModel("small.en", device="cpu", compute_type="int8")
            ok("Whisper STT ready.")


def transcribe(data: bytes, content_type: str = "") -> str:
    ensure_whisper()
    suffix = ".webm"
    if "ogg" in content_type:
        suffix = ".ogg"
    elif "wav" in content_type:
        suffix = ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        tmp = f.name
    wav_tmp = tmp + ".wav"
    try:
        try:
            rms = _webm_to_wav(tmp, wav_tmp)
            src = wav_tmp
        except Exception as e:
            print(f"        Audio conversion failed: {e}, trying raw")
            src = tmp
            rms = 9999 # skip threshold if conversion fails
            
        if rms < 500:
            print(f"        STT: [silence ignored, RMS={rms:.1f}]")
            return ""

        segments, _ = _WHISPER.transcribe(src, language="en", beam_size=5,
                                          vad_filter=True, condition_on_previous_text=False)
        raw = " ".join(s.text for s in segments).strip()
        print(f"        STT raw: {repr(raw)}")
        text = "" if raw.lower().rstrip(".! ") in _HALLUCINATIONS or len(raw) <= 2 else raw
        print(f"        STT: {repr(text)}")
        return text
    finally:
        for path in (tmp, wav_tmp):
            try:
                os.unlink(path)
            except Exception:
                pass


def _webm_to_wav(src: str, dst: str):
    import av as _av
    container = _av.open(src)
    audio = next((s for s in container.streams if s.type == "audio"), None)
    if audio is None:
        raise RuntimeError("No audio stream found")
    resampler = _av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=16000)
    buf = bytearray()

    def _drain(rf):
        if rf is None:
            return
        for frame in (rf if isinstance(rf, list) else [rf]):
            buf.extend(bytes(frame.planes[0]))

    for frame in container.decode(audio):
        _drain(resampler.resample(frame))
    _drain(resampler.resample(None))
    container.close()

    import numpy as _np
    samples = _np.frombuffer(bytes(buf), dtype=_np.int16).astype(_np.float32)
    rms = float(_np.sqrt(_np.mean(samples ** 2))) if len(samples) else 0.0
    print(f"        PyAV decoded {len(buf)} bytes of PCM, RMS={rms:.1f}")

    with wave.open(dst, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(bytes(buf))
    return rms
