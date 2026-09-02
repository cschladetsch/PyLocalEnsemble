import sys, time
import requests as req

_C = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "dim":    "\033[2m",
    "red":    "\033[91m",
    "green":  "\033[92m",
    "yellow": "\033[93m",
    "blue":   "\033[94m",
    "magenta":"\033[95m",
    "cyan":   "\033[96m",
    "white":  "\033[97m",
}

def _c(color: str, text: str) -> str:
    return f"{_C.get(color, '')}{text}{_C['reset']}"

def step(msg):  print(f"\n{_c('cyan', '[sd_app]')} {msg}")
def ok(msg):    print(f"        {_c('green', 'ok:')} {msg}")
def warn(msg):  print(f"        {_c('yellow', 'WARNING:')} {msg}")
def fail(msg):  print(f"\n        {_c('red', 'ERROR:')} {msg}"); sys.exit(1)


def http_ok(url, timeout=2):
    try:
        req.get(url, timeout=timeout)
        return True
    except Exception:
        return False


def wait_for(url, label, retries=60, delay=2):
    """Block until `url` responds OK.

    Polls `url` every `delay` seconds, up to `retries` attempts.
    """
    spin = iter("|/-\\|/-\\".__mul__(999))
    for i in range(retries):
        if http_ok(url, timeout=2):
            print(f"\r        {label} ready.{' ' * 20}")
            return True
        ch = next(spin)
        print(f"\r        {ch}  Waiting for {label}... ({i+1}/{retries})", end="", flush=True)
        time.sleep(delay)
    print(f"\r        Waiting for {label}... timed out.{' ' * 10}")
    return False
