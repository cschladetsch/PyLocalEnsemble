import builtins
import logging
import os
import sys
from datetime import datetime


ALICE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ALICE_DIR, "log")
_COMPONENT = "python-server"


def log_dir() -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    return LOG_DIR


def log_file(component: str | None = None) -> str:
    name = (component or _COMPONENT).replace("/", "-").replace("\\", "-")
    return os.path.join(log_dir(), f"{name}.log")


def _install_print_capture(logger: logging.Logger):
    if getattr(builtins, "_alice_print_wrapped", False):
        return

    real_print = builtins.print

    def _ts_print(*args, **kwargs):
        end = kwargs.get("end", "\n")
        first = args[0] if args else ""
        is_overwrite = isinstance(first, str) and first.startswith("\r")
        is_partial = end in ("", "\r")
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
