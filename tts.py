import os, io, re, wave, base64
from kokoro_onnx import Kokoro as _Kokoro
import config
from utils import step, ok, warn

_SLOW_RE = re.compile(r'\b(whisper|murmur|softly|gently|slowly|breathe|hush|quiet)\b|\.{3}', re.I)
_FAST_RE = re.compile(r'\b(gasp|scream|cry out|moan|desperately|urgently|panting)\b|!{2,}', re.I)


def _emotion_speed(text: str, base: float) -> float:
    """Adjust TTS speed based on mood signals in the reply text."""
    slow = len(_SLOW_RE.findall(text))
    fast = len(_FAST_RE.findall(text))
    if slow > fast and slow >= 1:
        return max(base * 0.82, 0.5)
    if fast > slow and fast >= 2:
        return min(base * 1.15, 1.4)
    return base

TTS    = None
VOICES = ["af_nicole", "af_bella", "af_sarah", "af_sky",
          "am_adam", "am_michael",
          "bf_emma", "bf_isabella",
          "bm_george", "bm_lewis"]


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


def _android_effect(samples, sr):
    """Ring modulation + mild overdrive for a robotic voice character."""
    import numpy as np
    t        = np.arange(len(samples)) / sr
    carrier  = np.sin(2 * np.pi * 60 * t)   # 60 Hz buzz
    ring_mod = samples * carrier
    # Blend: 40% dry + 60% ring-modulated
    blended  = 0.4 * samples + 0.6 * ring_mod
    # Mild overdrive — soft-clip to add harmonics
    blended  = np.tanh(blended * 1.8) / np.tanh(np.float32(1.8))
    # Normalise to original peak
    peak = np.max(np.abs(samples))
    if peak > 0:
        blended *= peak / max(np.max(np.abs(blended)), 1e-6)
    return blended.astype(samples.dtype)


def _cathedral_effect(samples, sr):
    """Synthetic reverb — long exponential decay for a temple/cathedral resonance."""
    import numpy as np
    duration = 2.5          # seconds of tail
    decay    = 5.0          # higher = shorter tail
    n        = int(sr * duration)
    t        = np.linspace(0, duration, n, dtype=np.float32)
    rng      = np.random.default_rng(42)
    ir       = (rng.standard_normal(n).astype(np.float32) * np.exp(-decay * t))
    ir[0]    = 1.0          # preserve direct sound
    reverb   = np.convolve(samples, ir, mode='full')[:len(samples)].astype(np.float32)
    blended  = 0.75 * samples + 0.25 * reverb
    peak = np.max(np.abs(samples))
    if peak > 0:
        blended *= peak / max(np.max(np.abs(blended)), 1e-6)
    return blended.astype(samples.dtype)


def _sentence_chunks(text: str, max_chars: int) -> list:
    """Split text at sentence boundaries into chunks ≤ max_chars each."""
    text = text.strip()
    sentences = re.split(r'(?<=[.!?…])\s+', text)
    chunks, current = [], ""
    for s in sentences:
        if not s:
            continue
        if len(current) + len(s) + 1 <= max_chars:
            current = (current + " " + s).strip()
        else:
            if current:
                chunks.append(current)
            # sentence itself longer than max_chars — hard-split on commas then chars
            while len(s) > max_chars:
                cut = s.rfind(",", 0, max_chars)
                cut = cut + 1 if cut > 0 else max_chars
                chunks.append(s[:cut].strip())
                s = s[cut:].strip()
            current = s
    if current:
        chunks.append(current)
    return chunks or [text[:max_chars]]


def tts_wav_b64(text: str) -> str:
    import numpy as np
    tts_cfg  = config.CFG.get("tts", {})
    voice    = tts_cfg.get("voice", "af_nicole")
    speed    = _emotion_speed(text, tts_cfg.get("speed", 0.85))
    effects  = tts_cfg.get("effects", "")
    max_chars = tts_cfg.get("max_chars", 1800)

    text = text[:max_chars]
    print(f"[tts] voice={voice}, speed={speed}, effects={effects!r}, {len(text)} chars")

    chunks = _sentence_chunks(text, max_chars=300)
    parts, sr = [], None
    for chunk in chunks:
        s, sr = TTS.create(chunk, voice=voice, speed=speed, lang="en-us")
        parts.append(s)
    samples = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    if effects == "android":
        samples = _android_effect(samples, sr)
    elif effects == "cathedral":
        samples = _cathedral_effect(samples, sr)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((samples * 32767).astype(np.int16).tobytes())
    return base64.b64encode(buf.getvalue()).decode()
