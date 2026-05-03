"""Resource Orchestrator — optional VRAM arbitration for GPU-constrained setups.

WHEN THIS MODULE IS ACTIVE
  Only when `vram_swap_for_image: true` in alice.json. With a 7B model and
  n_gpu_layers<=24, both the LLM and Forge fit on an 8GB GPU simultaneously and
  this module's acquire/release paths are never called.

  Enable vram_swap only if your model exceeds ~5GB VRAM and you cannot reduce
  n_gpu_layers further without unacceptable inference slowdown.

OWNERSHIP MODEL (when vram_swap is enabled)
  - LLM is the *default* GPU tenant: reloaded whenever the GPU is otherwise idle.
  - Image generation acquires Forge at GENERATION priority, evicting the LLM first.
  - INTERACTIVE-priority acquire (e.g. urgent chat) would evict Forge mid-gen.

Priority: lower number wins.
  INTERACTIVE = 1  (user waiting — chat, live STT)
  GENERATION  = 2  (image generation)
  BACKGROUND  = 3  (warmup, preload, idle LLM)

VRAM BUDGET GUIDANCE (RTX 2070 / 8GB)
  n_gpu_layers=24, ctx_size=2048 → LLM ~4.5GB, Forge ~2.2GB, total ~6.7GB  ✓ fits
  n_gpu_layers=-1,  ctx_size=4096 → LLM ~6.5GB, Forge ~2.2GB, total ~8.7GB  ✗ needs swap
"""

import dataclasses
import subprocess
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


# ── Resource snapshot ─────────────────────────────────────────────────────────

@dataclasses.dataclass
class Resources:
    cpu_pct:    float = -1.0   # 0–100, -1 = unavailable
    ram_pct:    float = -1.0   # 0–100, -1 = unavailable
    vram_used:  int   = 0      # MB
    vram_free:  int   = 0      # MB
    vram_total: int   = 0      # MB
    gpu_pct:    float = -1.0   # 0–100, -1 = unavailable

    @property
    def vram_pct(self) -> float:
        return 100.0 * self.vram_used / self.vram_total if self.vram_total else 0.0

    def summary(self) -> str:
        parts = []
        if self.ram_pct >= 0:
            flag = " !! HIGH" if self.ram_pct >= 85 else ""
            parts.append(f"RAM {self.ram_pct:.0f}%{flag}")
        if self.vram_total > 0:
            flag = " !!" if self.vram_pct >= 90 else ""
            parts.append(f"VRAM {self.vram_used}/{self.vram_total}MB{flag}")
        if self.gpu_pct >= 0:
            parts.append(f"GPU {self.gpu_pct:.0f}%")
        if self.cpu_pct >= 0:
            parts.append(f"CPU {self.cpu_pct:.0f}%")
        return "  ".join(parts) if parts else "(no data)"


def sample_resources() -> Resources:
    """Query current CPU, RAM, VRAM, and GPU utilisation."""
    res = Resources()

    # RAM — Windows ctypes (no dependency)
    try:
        import ctypes
        class _MEM(ctypes.Structure):
            _fields_ = [
                ("dwLength",                ctypes.c_ulong),
                ("dwMemoryLoad",            ctypes.c_ulong),
                ("ullTotalPhys",            ctypes.c_ulonglong),
                ("ullAvailPhys",            ctypes.c_ulonglong),
                ("ullTotalPageFile",        ctypes.c_ulonglong),
                ("ullAvailPageFile",        ctypes.c_ulonglong),
                ("ullTotalVirtual",         ctypes.c_ulonglong),
                ("ullAvailVirtual",         ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        s = _MEM(); s.dwLength = ctypes.sizeof(s)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
        res.ram_pct = float(s.dwMemoryLoad)
    except Exception:
        pass

    # CPU — psutil preferred, fall back to a short GetSystemTimes delta
    try:
        import psutil
        res.cpu_pct = psutil.cpu_percent(interval=None)
    except ImportError:
        try:
            import ctypes
            class _FT(ctypes.Structure):
                _fields_ = [("lo", ctypes.c_ulong), ("hi", ctypes.c_ulong)]
            def _ft(ft): return (ft.hi << 32) | ft.lo
            i1, k1, u1 = _FT(), _FT(), _FT()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i1), ctypes.byref(k1), ctypes.byref(u1))
            time.sleep(0.15)
            i2, k2, u2 = _FT(), _FT(), _FT()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i2), ctypes.byref(k2), ctypes.byref(u2))
            total = (_ft(k2) - _ft(k1)) + (_ft(u2) - _ft(u1))
            idle  = _ft(i2) - _ft(i1)
            res.cpu_pct = 100.0 * (total - idle) / total if total > 0 else 0.0
        except Exception:
            pass

    # VRAM + GPU — nvidia-smi
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=memory.used,memory.free,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            parts = [p.strip() for p in r.stdout.strip().split(",")]
            if len(parts) >= 3:
                res.vram_used  = int(parts[0])
                res.vram_free  = int(parts[1])
                res.vram_total = res.vram_used + res.vram_free
                res.gpu_pct    = float(parts[2])
    except Exception:
        pass

    return res


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
            warn(f"[orch] {self.name}.load() raised: {e}")
            return False

    def unload(self) -> bool:
        try:
            return bool(self._unload_fn())
        except Exception as e:
            warn(f"[orch] {self.name}.unload() raised: {e}")
            return False

    def interrupt(self) -> None:
        if self._interrupt_fn:
            try:
                self._interrupt_fn()
            except Exception as e:
                warn(f"[orch] {self.name}.interrupt() raised: {e}")


# ── Orchestrator ──────────────────────────────────────────────────────────────

class ResourceOrchestrator:
    """
    Priority-based GPU arbiter with continuous resource monitoring.

    Monitors CPU, RAM, VRAM, and GPU utilisation in a background thread.
    Proactively evicts idle BACKGROUND holders when RAM becomes critical so
    the system never hits 100% RAM before an image generation starts.

    Priority semantics (lower number wins):
      INTERACTIVE (1) evicts GENERATION (2) and BACKGROUND (3)
      GENERATION  (2) evicts BACKGROUND (3) — image gen vs LLM
      BACKGROUND  (3) is the LLM's idle-holding priority
    """

    # Thresholds
    RAM_WARN_PCT     = 78   # log a warning
    RAM_CRITICAL_PCT = 87   # proactively evict idle holders
    RAM_GATE_PCT     = 82   # wait before loading a new resource
    MONITOR_INTERVAL = 4.0  # seconds between background samples

    def __init__(self):
        self._lock            = threading.Lock()
        self._resources       : dict[str, _GPUResource] = {}
        self._holders         : dict[str, int]           = {}   # name → priority
        self._default         : str | None               = None
        self._default_priority: int                      = Priority.BACKGROUND
        self._last_res        : Resources                = Resources()
        self._monitor_thread  : threading.Thread | None  = None
        self._stop_monitor    = threading.Event()

    # ── Registration ─────────────────────────────────────────────────────────

    def register(self, name: str, load_fn, unload_fn,
                 interrupt_fn=None, is_default: bool = False,
                 default_priority: int = Priority.BACKGROUND) -> None:
        self._resources[name] = _GPUResource(name, load_fn, unload_fn, interrupt_fn)
        if is_default:
            self._default          = name
            self._default_priority = default_priority

    # ── Monitoring ────────────────────────────────────────────────────────────

    def start_monitor(self) -> None:
        """Start the background resource sampling thread."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="resource-monitor")
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        while not self._stop_monitor.wait(timeout=self.MONITOR_INTERVAL):
            res = sample_resources()
            prev = self._last_res
            self._last_res = res

            # Log whenever anything crosses a notable threshold
            ram_crossed_warn = res.ram_pct >= self.RAM_WARN_PCT > prev.ram_pct
            ram_critical     = res.ram_pct >= self.RAM_CRITICAL_PCT
            if ram_crossed_warn or ram_critical:
                print(f"[orch] {res.summary()}")

            # Proactive eviction disabled: the acquire() path handles eviction
            # on demand and the LLM needs to stay resident between image gens.

    @property
    def resources(self) -> Resources:
        return self._last_res

    def log(self, label: str = "") -> None:
        """Sample and print current resources."""
        res = sample_resources()
        self._last_res = res
        prefix = f"[orch] {label}" if label else "[orch]"
        print(f"{prefix}  {res.summary()}")

    def wait_for_ram(self, timeout: float = 20.0) -> bool:
        """Block until RAM drops below RAM_GATE_PCT or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            res = sample_resources()
            self._last_res = res
            if res.ram_pct < 0 or res.ram_pct <= self.RAM_GATE_PCT:
                return True
            remaining = deadline - time.time()
            print(f"[orch] RAM {res.ram_pct:.0f}% — waiting for ≤{self.RAM_GATE_PCT}%  "
                  f"({remaining:.0f}s left)  {res.summary()}")
            time.sleep(2.0)
        res = sample_resources()
        self._last_res = res
        if res.ram_pct > self.RAM_GATE_PCT:
            warn(f"[orch] RAM still {res.ram_pct:.0f}% after {timeout:.0f}s — proceeding")
            return False
        return True

    # ── Acquire ───────────────────────────────────────────────────────────────

    def acquire(self, name: str, priority: int = Priority.GENERATION) -> None:
        """Evict lower-priority holders, then load *name* onto the GPU."""
        with self._lock:
            to_evict = [(n, p) for n, p in list(self._holders.items())
                        if n != name and p > priority]
            for n, _ in to_evict:
                del self._holders[n]
            self._holders[name] = priority

        for n, p in to_evict:
            r = self._resources.get(n)
            if r:
                print(f"[orch] evicting '{n}' (priority {p}) for '{name}' (priority {priority})")
                r.interrupt()
                r.unload()

        if to_evict:
            self.wait_for_ram(timeout=20.0)

        r = self._resources.get(name)
        if r:
            r.load()

        print(f"[orch] '{name}' acquired  priority={priority}  active={self._snapshot()}")

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
                self._holders[self._default] = self._default_priority
                reload_default = True

        print(f"[orch] '{name}' released  active={self._snapshot()}")

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
            print(f"[orch] GPU idle — reloading default '{self._default}'")
            success = r.load()
            if not success:
                with self._lock:
                    self._holders.pop(self._default, None)

        threading.Thread(target=_run, daemon=True).start()


# ── Module-level singleton ────────────────────────────────────────────────────

# Keep the old name as an alias so any code that referenced VRAMOrchestrator still works.
VRAMOrchestrator = ResourceOrchestrator

_orch         = ResourceOrchestrator()
_forge_url    = ""
_forge_loaded = True   # assume loaded at Forge startup; _forge_unload sets False


def setup(forge_url: str) -> None:
    global _forge_url
    _forge_url = forge_url
    _register_resources()
    _orch.start_monitor()


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

def _wait_vram_free(needed_mb: int = 4000, timeout: float = 15.0) -> None:
    """Poll until VRAM free >= needed_mb or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        res = sample_resources()
        if res.vram_total == 0 or res.vram_free >= needed_mb:
            return
        remaining = deadline - time.monotonic()
        print(f"[orch] waiting for VRAM reclaim: {res.vram_free}MB free, need {needed_mb}MB  ({remaining:.0f}s left)")
        time.sleep(0.5)
    print(f"[orch] VRAM reclaim timeout after {timeout:.0f}s — proceeding anyway")


def _llm_load() -> bool:
    import llm as _llm
    if _llm.LLM_READY:
        return True
    # Evict Forge checkpoint first so the LLM gets full VRAM.
    # (Forge is kept hot after image gen; we only free it here when LLM actually needs to load.)
    if _forge_loaded:
        print("[orch] evicting Forge before LLM load (lazy evict after keep-hot)")
        _forge_unload()
        _wait_vram_free()
    if _llm.LLM_SUSPENDED:
        _llm.resume_after_image()   # starts server in background thread
    else:
        _llm.load_llm()
    return True


def _llm_unload() -> bool:
    import llm as _llm
    import gc
    _llm.suspend_for_image()
    gc.collect()
    time.sleep(2.0)   # give Windows time to reclaim mmap pages after process kill
    return True


# ── Forge callbacks ───────────────────────────────────────────────────────────

def _forge_load() -> bool:
    global _forge_loaded
    if not _forge_url or _forge_loaded:
        return True
    try:
        step("[orch] Reloading Forge checkpoint into VRAM...")
        r = req.post(f"{_forge_url}/sdapi/v1/reload-checkpoint", timeout=60)
        if r.status_code == 200:
            _forge_loaded = True
            ok("[orch] Forge model in VRAM.")
            from image.forge import _push_forge_settings
            _push_forge_settings(_forge_url)
            return True
        if r.status_code == 404:
            warn("[orch] reload-checkpoint not supported — skipping.")
        else:
            warn(f"[orch] reload-checkpoint returned HTTP {r.status_code}.")
        return False
    except Exception as e:
        warn(f"[orch] reload-checkpoint failed: {e}")
        return False


def _forge_unload() -> bool:
    global _forge_loaded
    if not _forge_url or not _forge_loaded:
        return True
    try:
        r = req.post(f"{_forge_url}/sdapi/v1/unload-checkpoint", timeout=15)
        if r.status_code == 200:
            _forge_loaded = False
            print("[orch] Forge checkpoint evicted from VRAM.")
            return True
        if r.status_code == 404:
            warn("[orch] unload-checkpoint not supported.")
        else:
            warn(f"[orch] unload-checkpoint returned HTTP {r.status_code}.")
        return False
    except Exception as e:
        warn(f"[orch] unload-checkpoint failed: {e}")
        return False


def _forge_interrupt() -> None:
    if not _forge_url:
        return
    try:
        req.post(f"{_forge_url}/sdapi/v1/interrupt", timeout=5)
        print("[orch] Forge generation interrupted.")
    except Exception:
        pass


# ── Public surface ────────────────────────────────────────────────────────────

def log_resources(label: str = "") -> None:
    _orch.log(label)


def unload_forge() -> bool:
    return _forge_unload()


def reload_forge() -> bool:
    return _forge_load()


def notify_llm_ready() -> None:
    """Call once llama-server finishes loading so the orchestrator knows to evict it before image gen."""
    with _orch._lock:
        _orch._holders["llm"] = Priority.BACKGROUND


def acquire_for_image() -> None:
    _orch.acquire("forge", Priority.GENERATION)


def release_from_image() -> None:
    # Keep the Forge checkpoint hot in VRAM — LLM and an idle Forge can coexist on 8 GB,
    # and skipping the unload saves the ~15s reload at the start of the next image gen.
    # Only the LLM kill + sleep overhead remains (~7s) instead of 7s + 15s reload.
    with _orch._lock:
        _orch._holders.pop("forge", None)
        reload_default = bool(
            _orch._default and _orch._default not in _orch._holders
        )
        if reload_default:
            _orch._holders[_orch._default] = _orch._default_priority
    print(f"[orch] 'forge' released (checkpoint kept in VRAM)  active={_orch._snapshot()}")
    if reload_default:
        _orch._reload_default_async(priority=_orch._default_priority)
