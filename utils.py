import sys, time
import requests as req
import config


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
