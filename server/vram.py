"""VRAM Orchestrator — priority-based GPU arbitration between llama-server and Forge.

Ownership model:
  - LLM is the *default* GPU tenant: reloaded whenever the GPU is otherwise idle.
  - Image generation acquires Forge at GENERATION priority, evicting the LLM first.
  - A future INTERACTIVE-priority acquire (e.g. urgent chat) would evict Forge mid-gen
    via the Forge /interrupt API before loading the LLM back.

Priority: lower number wins.
  INTERACTIVE = 1  (user waiting — chat, live STT)
  GENERATION  = 2  (image generation)
  BACKGROUND  = 3  (warmup, preload)
"""

import threading
import time
import requests as req
from contextlib import contextmanager
from utils import step, ok, warn


# ── Priority constants ────────────────────────────────────────────────────────

class Priority:
    INTERACTIVE = 1
    GENERATION  = 2
    BACKGROUND  = 3


# ── Internal resource descriptor ──────────────────────────────────────────────

class _GPUResource:
    def __init__(self, name: str, load_fn, unload_fn, interrupt_fn=None):
        self.name          = name
        self._load_fn      = load_fn
        self._unload_fn    = unload_fn
        self._interrupt_fn = interrupt_fn

    def load(self) -> bool:
        try:
            return bool(self._load_fn())
        except Exception as e:
            warn(f"[vram] {self.name}.load() raised: {e}")
            return False

    def unload(self) -> bool:
        try:
            return bool(self._unload_fn())
        except Exception as e:
            warn(f"[vram] {self.name}.unload() raised: {e}")
            return False

    def interrupt(self) -> None:
        if self._interrupt_fn:
            try:
                self._interrupt_fn()
            except Exception as e:
                warn(f"[vram] {self.name}.interrupt() raised: {e}")


# ── Orchestrator ──────────────────────────────────────────────────────────────

class VRAMOrchestrator:
    """
    Priority-based VRAM arbiter.

    A higher-priority acquire evicts any current holders with a lower priority
    (higher Priority number).  Eviction calls interrupt() then unload() so
    in-flight work (e.g. Forge generation) is cancelled cleanly.

    After the last non-default holder releases, the default resource (LLM) is
    reloaded automatically in a background thread.

    Priority semantics (lower number wins):
      INTERACTIVE (1) evicts GENERATION (2) and BACKGROUND (3)
      GENERATION  (2) evicts BACKGROUND (3) — used for image gen vs LLM
      BACKGROUND  (3) is the LLM's idle-holding priority; evicted by both
    """

    def __init__(self):
        self._lock            = threading.Lock()
        self._resources       : dict[str, _GPUResource] = {}
        self._holders         : dict[str, int]           = {}   # name -> priority
        self._default         : str | None               = None
        self._default_priority: int                      = Priority.BACKGROUND

    # ── Registration ─────────────────────────────────────────────────────────

    def register(self, name: str, load_fn, unload_fn,
                 interrupt_fn=None, is_default: bool = False,
                 default_priority: int = Priority.BACKGROUND) -> None:
        self._resources[name] = _GPUResource(name, load_fn, unload_fn, interrupt_fn)
        if is_default:
            self._default          = name
            self._default_priority = default_priority

    # ── Acquire ───────────────────────────────────────────────────────────────

    def acquire(self, name: str, priority: int = Priority.GENERATION) -> None:
        """Evict lower-priority holders then load *name* onto the GPU."""
        # Snapshot eviction targets and update _holders atomically.
        with self._lock:
            to_evict = [(n, p) for n, p in list(self._holders.items())
                        if n != name and p > priority]
            for n, _ in to_evict:
                del self._holders[n]
            self._holders[name] = priority

        # Run eviction callbacks outside the lock so they can safely re-enter.
        for n, p in to_evict:
            r = self._resources.get(n)
            if r:
                print(f"[vram] evicting '{n}' (priority {p}) "
                      f"for '{name}' (priority {priority})")
                r.interrupt()
                r.unload()

        r = self._resources.get(name)
        if r:
            r.load()

        print(f"[vram] '{name}' acquired GPU  priority={priority}  "
              f"active={self._snapshot()}")

    # ── Release ───────────────────────────────────────────────────────────────

    def release(self, name: str) -> None:
        """Unload *name* and reload the default resource if the GPU is now idle."""
        r = self._resources.get(name)
        if r:
            r.unload()

        reload_default = False
        with self._lock:
            self._holders.pop(name, None)
            if self._default and self._default not in self._holders:
                # Reserve the slot now to prevent a concurrent acquire from
                # racing with the background reload.
                self._holders[self._default] = self._default_priority
                reload_default = True

        print(f"[vram] '{name}' released GPU  active={self._snapshot()}")

        if reload_default:
            self._reload_default_async(priority=self._default_priority)

    # ── Interrupt ─────────────────────────────────────────────────────────────

    def interrupt(self, name: str) -> None:
        """Cancel in-flight work for *name* without releasing GPU ownership."""
        r = self._resources.get(name)
        if r:
            r.interrupt()

    # ── Context manager ───────────────────────────────────────────────────────

    @contextmanager
    def using(self, name: str, priority: int = Priority.GENERATION):
        """Acquire on entry, release on exit — even if the body raises."""
        self.acquire(name, priority)
        try:
            yield
        finally:
            self.release(name)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _snapshot(self) -> str:
        with self._lock:
            return repr(dict(self._holders))

    def _reload_default_async(self, priority: int = Priority.BACKGROUND) -> None:
        r = self._resources.get(self._default)
        if not r:
            return

        def _run():
            print(f"[vram] GPU idle — reloading default '{self._default}'")
            success = r.load()
            if not success:
                with self._lock:
                    self._holders.pop(self._default, None)

        threading.Thread(target=_run, daemon=True).start()


# ── Module-level singleton ────────────────────────────────────────────────────

_orch         = VRAMOrchestrator()
_forge_url    = ""
_forge_loaded = True   # assume loaded at Forge startup; _forge_unload sets False


def setup(forge_url: str) -> None:
    global _forge_url
    _forge_url = forge_url
    _register_resources()


def _register_resources() -> None:
    _orch.register(
        "llm",
        load_fn    = _llm_load,
        unload_fn  = _llm_unload,
        is_default = True,
    )
    _orch.register(
        "forge",
        load_fn      = _forge_load,
        unload_fn    = _forge_unload,
        interrupt_fn = _forge_interrupt,
    )


# ── LLM callbacks ─────────────────────────────────────────────────────────────

def _llm_load() -> bool:
    import llm as _llm
    if _llm.LLM_READY:
        return True
    if _llm.LLM_SUSPENDED:
        _llm.resume_after_image()   # starts server in background thread
    else:
        _llm.load_llm()
    return True


def _llm_unload() -> bool:
    import llm as _llm
    _llm.suspend_for_image()
    time.sleep(2.0)   # give the CUDA driver time to reclaim VRAM
    return True


# ── Forge callbacks ───────────────────────────────────────────────────────────

def _forge_load() -> bool:
    global _forge_loaded
    if not _forge_url or _forge_loaded:
        return True
    try:
        step("[vram] Reloading Forge checkpoint into VRAM...")
        r = req.post(f"{_forge_url}/sdapi/v1/reload-checkpoint", timeout=60)
        if r.status_code == 200:
            _forge_loaded = True
            ok("[vram] Forge model in VRAM.")
            from image.forge import _push_forge_settings
            _push_forge_settings(_forge_url)
            return True
        if r.status_code == 404:
            warn("[vram] reload-checkpoint not supported — skipping.")
        else:
            warn(f"[vram] reload-checkpoint returned HTTP {r.status_code}.")
        return False
    except Exception as e:
        warn(f"[vram] reload-checkpoint failed: {e}")
        return False


def _forge_unload() -> bool:
    global _forge_loaded
    if not _forge_url or not _forge_loaded:
        return True
    try:
        r = req.post(f"{_forge_url}/sdapi/v1/unload-checkpoint", timeout=15)
        if r.status_code == 200:
            _forge_loaded = False
            print("[vram] Forge checkpoint evicted from VRAM.")
            return True
        if r.status_code == 404:
            warn("[vram] unload-checkpoint not supported.")
        else:
            warn(f"[vram] unload-checkpoint returned HTTP {r.status_code}.")
        return False
    except Exception as e:
        warn(f"[vram] unload-checkpoint failed: {e}")
        return False


def _forge_interrupt() -> None:
    if not _forge_url:
        return
    try:
        req.post(f"{_forge_url}/sdapi/v1/interrupt", timeout=5)
        print("[vram] Forge generation interrupted.")
    except Exception:
        pass


# ── Backward-compatible surface ───────────────────────────────────────────────
# Called by generate.py and alice.py — keep these working unchanged.

def unload_forge() -> bool:
    return _forge_unload()


def reload_forge() -> bool:
    return _forge_load()


def acquire_for_image() -> None:
    _orch.acquire("forge", Priority.GENERATION)


def release_from_image() -> None:
    _orch.release("forge")
