"""
Shared test fixtures and heavy-dependency mocking.

kokoro_onnx / faster_whisper / av may not be installed in CI.
Stub them before alice.py or tts.py import them.
"""
import os, sys
from unittest.mock import MagicMock

# Make the repo root importable so test_install.py can find install.py
_repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

for _mod in ("kokoro_onnx", "faster_whisper", "av"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
