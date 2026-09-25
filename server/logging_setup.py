import builtins
import logging
import os
import re
import sys
from datetime import datetime


ALICE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ALICE_DIR, "log")

# ---------------------------------------------------------------------------
# Per-component console coloring — every component tags its own lines with
# a "[name]" prefix (e.g. "[Torch] RAM 93%...", "[forge] Checkpoint..."), so
# console output from several subsystems interleaves. Color the message text
# itself (never the "[HH:MM:SS]" timestamp _ts_print already adds) so each
# component's lines are visually distinguishable at a glance.
# ---------------------------------------------------------------------------
_RESET = "\033[0m"
_TAG_COLORS = {
    "alice":   "\033[96m",  # cyan   — matches utils.step()'s own [Alice] color
    "torch":   "\033[95m",  # magenta — vram.py ResourceOrchestrator ([Torch] tag)
    "forge":   "\033[94m",  # blue    — image/forge.py (Stable Diffusion Forge)
    "llm":     "\033[92m",  # green   — llm.py (llama-server lifecycle)
    "chat":    "\033[96m",  # cyan    — routes/chat.py
    "backend": "\033[97m",  # white   — routes/chat.py request log line
    "image":   "\033[93m",  # yellow  — image generation pipeline
    "tts":     "\033[91m",  # red     — tts.py (not an error — just a color slot)
    "group":   "\033[35m",  # magenta (dim) — routes/group.py
}
# Any tag not listed above still gets a stable color, picked deterministically
# (not Python's randomized str hash()) so it doesn't change between runs.
_FALLBACK_COLORS = ["\033[36m", "\033[34m", "\033[32m", "\033[33m", "\033[35m"]
_TAG_RE = re.compile(r"^(\n*)(\[([A-Za-z0-9_.\- ]+)\].*)$", re.DOTALL)


def _colorize_trailer(text: str) -> str:
    """Color a "[tag] message" line's message by tag; leave anything else
    (including messages that already embed their own ANSI codes, like
    utils.ok()/warn()) untouched."""
    m = _TAG_RE.match(text)
    if not m:
        return text
    leading_newlines, rest, tag = m.group(1), m.group(2), m.group(3).lower()
    color = _TAG_COLORS.get(tag)
    if not color:
        color = _FALLBACK_COLORS[sum(ord(c) for c in tag) % len(_FALLBACK_COLORS)]
    return f"{leading_newlines}{color}{rest}{_RESET}"

# ---------------------------------------------------------------------------
# Public helpers — used by llm.py and any other module that needs its own log
# file without re-implementing path logic.
# ---------------------------------------------------------------------------

def log_dir() -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    return LOG_DIR


def log_file(component: str | None = None, suffix: str = ".log") -> str:
    """Return the absolute path for a per-component log file.

    ``component`` is normalised (``/`` and ``\\`` → ``-``) so that e.g.
    ``llm`` → ``server/log/llm.log`` and ``llama-server`` →
    ``server/log/llama-server.log``.
    """
    name = (component or "python-server").replace("/", "-").replace("\\", "-")
    return os.path.join(log_dir(), f"{name}{suffix}")


# ---------------------------------------------------------------------------
# Internal init
# ---------------------------------------------------------------------------

_COMPONENT = "python-server"


def _install_print_capture(logger: logging.Logger):
    if getattr(builtins, "_alice_print_wrapped", False):
        return

    real_print = builtins.print

    def _ts_print(*args, **kwargs):
        end = kwargs.get("end", "\n")
        first = args[0] if args else ""
        is_overwrite = isinstance(first, str) and first.startswith("\r")
        is_partial = end in ("", "\r")
        if args and not is_partial and not is_overwrite and isinstance(args[0], str):
            args = (_colorize_trailer(args[0]),) + args[1:]
        console_args = args
        if args and not is_partial and not is_overwrite:
            console_args = (f"\033[33m[{datetime.now().strftime('%H:%M:%S')}]\033[0m",) + args
        real_print(*console_args, **kwargs)
        if args and not is_partial and not is_overwrite:
            try:
                logger.info(" ".join(str(a) for a in args))
            except Exception:
                pass

    builtins.print = _ts_print
    builtins._alice_print_wrapped = True


def init_logging(component: str = "python-server") -> str:
    global _COMPONENT
    _COMPONENT = component
    path = log_file(component)

    os.environ.setdefault("ALICE_LOG_DIR", log_dir())
    os.environ.setdefault("ALICE_LOG_LEVEL", "INFO")

    root = logging.getLogger()
    root.setLevel(getattr(logging, os.environ["ALICE_LOG_LEVEL"].upper(), logging.INFO))

    if not any(
        isinstance(h, logging.FileHandler) and os.path.abspath(getattr(h, "baseFilename", "")) == os.path.abspath(path)
        for h in root.handlers
    ):
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        root.addHandler(handler)

    logging.captureWarnings(True)
    _install_print_capture(logging.getLogger("alice.console"))

    def _excepthook(exc_type, exc_value, exc_traceback):
        logging.getLogger("alice.crash").exception("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = _excepthook
    return path
