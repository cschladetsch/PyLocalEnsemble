import sys, os, time
import requests as req
import config


def is_wsl() -> bool:
    """True when running inside WSL1 or WSL2."""
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False

IS_WSL = is_wsl()


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

def step(msg):  print(f"\n{_c('cyan', f'[{config.NAME}]')} {msg}")
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
