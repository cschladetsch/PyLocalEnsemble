"""Centralized CLI argument parsing for Alice.

Single source of truth for every command-line flag. Imported once at startup
by ``alice.py``; other modules read the pre-parsed ``ARGS`` namespace rather
than re-scanning ``sys.argv``.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Dataclass — all flags in one place
# ---------------------------------------------------------------------------

@dataclass
class Args:
    """Pre-parsed command-line flags.  Instances are cheap to copy and safe to
    pass around; the module-level ``ARGS`` singleton is the one real instance.
    """

    no_speech:  bool = False
    no_forge:   bool = False
    test_mode:  bool = False
    auto_image: bool = False
    voices:     bool = False          # --voices → print voices and exit
    persona:    str | None = None     # --persona=<name>
    raw:        list[str] = field(default_factory=list)  # unknown args
    _raw_argv:  list[str] = field(default_factory=list)  # frozen sys.argv[1:]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_PARSER = argparse.ArgumentParser(
    prog="alice.py",
    add_help=False,
    description="Alice — local AI assistant server",
)

_PARSER.add_argument("--no-speech",  action="store_true", dest="no_speech")
_PARSER.add_argument("--no-forge",   action="store_true", dest="no_forge")
_PARSER.add_argument("--test",       action="store_true", dest="test_mode")
_PARSER.add_argument("--auto-image", action="store_true", dest="auto_image")
_PARSER.add_argument("--voices",     action="store_true", dest="voices")
_PARSER.add_argument("--persona",    type=str, default=None, dest="persona")
_PARSER.add_argument("raw",          nargs="*", default=[])


# ---------------------------------------------------------------------------
# Public singleton
# ---------------------------------------------------------------------------

ARGS = Args()
_ARGS_PARSED = False


def parse() -> Args:
    """Parse ``sys.argv`` once and cache the result.

    Unknown flags and positional tokens are captured into ``args.raw`` so that
    ``alice.py`` remains the single place that defines which flags are "official".
    """
    global ARGS, _ARGS_PARSED
    if _ARGS_PARSED:
        return ARGS
    _ARGS_PARSED = True
    # Keep a frozen copy of the raw argv so callers can still test for the
    # presence of arbitrary substrings (e.g. "pytest") without re-scanning.
    _raw_argv = sys.argv[1:]
    known, unknown = _PARSER.parse_known_args()
    ARGS = Args(
        no_speech=known.no_speech,
        no_forge=known.no_forge,
        test_mode=known.test_mode,
        auto_image=known.auto_image,
        voices=known.voices,
        persona=known.persona,
        raw=unknown,
        _raw_argv=_raw_argv,
    )
    return ARGS


def has(*needles: str) -> bool:
    """Return True if any of *needles* appears as a substring in any raw argv token.

    Mirrors the old ``any("pytest" in arg for arg in sys.argv)`` checks that
    modules used to write themselves.  After ``parse()`` this reads the frozen
    copy stored on the ``ARGS`` instance.
    """
    for n in needles:
        for tok in ARGS._raw_argv:
            if n in tok:
                return True
    return False


def reset() -> None:
    """Forget the cached parse.  Useful for tests that need to re-parse."""
    global ARGS, _ARGS_PARSED
    ARGS = Args()
    _ARGS_PARSED = False
