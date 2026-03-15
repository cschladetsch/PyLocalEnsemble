"""
Shared test fixtures and heavy-dependency mocking.

kokoro_onnx / faster_whisper / av may not be installed in CI.
Stub them before alice.py or tts.py import them.
"""
import sys
from unittest.mock import MagicMock

for _mod in ("kokoro_onnx", "faster_whisper", "av"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
