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

from types import ModuleType
from importlib.util import spec_from_loader

for _mod in ("kokoro_onnx", "faster_whisper", "av"):
    if _mod not in sys.modules:
        _m = ModuleType(_mod)
        _m.__spec__ = spec_from_loader(_mod, None)
        if _mod == "av":
            # av.open is called in stt._webm_to_wav — stub it
            _m.open = MagicMock()
        sys.modules[_mod] = _m
