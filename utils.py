import sys, time, builtins
from datetime import datetime
import requests as req
import config

_real_print = builtins.print
def _ts_print(*args, **kwargs):
    end   = kwargs.get('end', '\n')
    first = args[0] if args else ''
    # Skip: empty calls, carriage-return overwrites, and partial-line writes
    if args and end not in ('', '\r') and not (isinstance(first, str) and first.startswith('\r')):
        kwargs = {**kwargs}
        args = (f'\033[33m[{datetime.now().strftime("%H:%M:%S")}]\033[0m',) + args
    _real_print(*args, **kwargs)
builtins.print = _ts_print


def step(msg):  print(f"\n[{config.NAME}] {msg}")
def ok(msg):    print(f"        ok: {msg}")
def warn(msg):  print(f"        WARNING: {msg}")
def fail(msg):  print(f"\n        ERROR: {msg}"); sys.exit(1)


def http_ok(url, timeout=2):
    try:
        req.get(url, timeout=timeout)
        return True
    except Exception:
        return False


def wait_for(url, label, retries=40, delay=3):
    spin = iter("|/-\\|/-\\".__mul__(999))
    for _ in range(retries):
        if http_ok(url, timeout=2):
            print(f"\r        {label} ready.{' ' * 20}")
            return True
        ch = next(spin)
        print(f"\r        {ch}  Waiting for {label}...", end="", flush=True)
        time.sleep(delay)
    print(f"\r        Waiting for {label}... timed out.{' ' * 10}")
    return False
