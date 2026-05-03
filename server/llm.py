import os, json, re, time, threading, subprocess, shutil
import requests as req
import config
import state
from utils import step, ok, warn, http_ok

LLM_READY     = False
LLM_SUSPENDED = False   # True while VRAM is yielded to image generation
history   = []
memory    = ""
_history_lock     = threading.Lock()
_chat_in_progress = threading.Event()   # set for the full lifetime of any streaming chat call
_server_proc: subprocess.Popen | None = None   # process started by _start_server()

LLAMA_URL = os.environ.get("LLAMA_URL", config.CFG.get("llama_url", "http://127.0.0.1:8080"))
if not LLAMA_URL.startswith("http"):
    LLAMA_URL = "http://" + LLAMA_URL


_DETECTED_MODEL = None


def _history_path() -> str:
    persona_key = state._active_persona_key
    default_history = os.path.normpath(os.path.join(config.ALICE_DIR, "history.json"))
    configured_history = os.path.normpath(config.HISTORY_FILE)
    if persona_key and configured_history == default_history:
        return config.history_file_for(persona_key)
    return config.HISTORY_FILE

def llm_model() -> str:
    global _DETECTED_MODEL
    if _DETECTED_MODEL:
        return _DETECTED_MODEL
    try:
        r = req.get(f"{LLAMA_URL}/v1/models", timeout=2)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                _DETECTED_MODEL = data[0]["id"]
                return _DETECTED_MODEL
    except Exception:
        pass
    return config.CFG.get("llama_model", "mistral-nemo")


def list_models() -> list:
    """Return (name, path, size_gb) triples for every .gguf found on disk."""
    found = {}

    search_roots = []
    # Directory of the currently configured model
    current = config.CFG.get("model_path", "")
    if current:
        search_roots.append(os.path.dirname(current))
    # LM Studio cache
    lm_studio = os.path.join(os.path.expanduser("~"), ".cache", "lm-studio", "models")
    if os.path.isdir(lm_studio):
        search_roots.append(lm_studio)
    # Any explicit model_dir in config
    extra = config.CFG.get("model_dir", "")
    if extra and os.path.isdir(extra):
        search_roots.append(extra)

    for root in search_roots:
        for dirpath, _, files in os.walk(root):
            for fname in files:
                if fname.lower().endswith(".gguf") and ".part" not in fname:
                    full = os.path.join(dirpath, fname)
                    if full not in found:
                        try:
                            size_gb = round(os.path.getsize(full) / 1e9, 1)
                        except OSError:
                            size_gb = 0.0
                        found[full] = (fname, size_gb)
    return [(name, path, size) for path, (name, size) in sorted(found.items(), key=lambda x: x[1][0].lower())]


def _start_server():
    model_path = config.CFG.get("model_path", "")
    if not model_path or not os.path.exists(model_path):
        warn("model_path not set or file not found — start llama-server manually.")
        return
    exe = (config.CFG.get("llama_server_path", "") or
           shutil.which("llama-server") or
           shutil.which("llama-server.exe"))
    if not exe:
        warn("llama-server not found — run install.py or start it manually.")
        return
    sc = {**config._DEFAULT_CONFIG["llama_server"], **config.CFG.get("llama_server", {})}
    print(f"        server: using config: {sc}")
    ok(f"Starting llama-server with {os.path.basename(model_path)} (ngl={sc['n_gpu_layers']}, ctx={sc['ctx_size']})...")
    flags = [
        exe, "-m", model_path,
        "--host", "127.0.0.1", "--port", "8080",
        "-ngl",         str(sc["n_gpu_layers"]),
        "--ctx-size",   str(sc["ctx_size"]),
        "--batch-size", str(sc["batch_size"]),
        "--threads",    str(sc["threads"]),
    ]
    if sc.get("chat_template"):
        flags += ["--chat-template", sc["chat_template"]]
    log_dir = os.path.join(os.path.dirname(__file__), "log")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "llama-server.log")
    print(f"[llm] logging llama-server output to {log_path}")
    log_fh = open(log_path, "w", encoding="utf-8", errors="replace")
    kw = {"stdout": log_fh, "stderr": log_fh}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    global _server_proc
    _server_proc = subprocess.Popen(flags, **kw)


def _try_connect(silent=False) -> bool:
    global LLM_READY
    try:
        r = req.get(f"{LLAMA_URL}/health", timeout=3)
        if r.json().get("status") == "ok":
            LLM_READY = True
            ok(f"llama.cpp server ready at {LLAMA_URL}")
            return True
        return False
    except Exception as e:
        if not silent:
            warn(f"llama.cpp server not reachable: {e}")
        return False


def wait_until_ready(timeout: int = 120) -> bool:
    if LLM_READY:
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _try_connect(silent=True):
            return True
        time.sleep(2)
    return False


def _kill_server_proc() -> bool:
    """Terminate the tracked server process. Returns True if a process was killed."""
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        _server_proc.kill()
        try:
            _server_proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            pass
        _server_proc = None
        return True
    _server_proc = None
    return False


def _kill_by_port() -> None:
    """Kill whatever is listening on the llama-server port (fallback)."""
    import re as _re
    m = _re.search(r':(\d+)(?:/|$)', LLAMA_URL)
    if not m:
        return
    port = m.group(1)
    try:
        if os.name == "nt":
            r = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                               capture_output=True, text=True, check=False)
            for line in r.stdout.splitlines():
                parts = line.split()
                if (len(parts) >= 5 and parts[0].upper() == "TCP"
                        and parts[1].endswith(f":{port}")
                        and parts[3].upper() == "LISTENING"):
                    try:
                        pid = int(parts[4])
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                       capture_output=True, check=False)
                        print(f"[llm] killed llama-server PID {pid} (port {port})")
                    except (ValueError, Exception):
                        pass
                    return
        else:
            r = subprocess.run(["fuser", "-k", f"{port}/tcp"],
                               capture_output=True, check=False)
            if r.returncode != 0:
                r = subprocess.run(["lsof", "-ti", f"tcp:{port}"],
                                   capture_output=True, text=True, check=False)
                for pid_str in r.stdout.split():
                    if pid_str.isdigit():
                        os.kill(int(pid_str), 15)
    except Exception as e:
        warn(f"[llm] kill-by-port failed: {e}")


def suspend_for_image() -> None:
    """Kill the llama-server to free its VRAM for image generation."""
    global LLM_READY, LLM_SUSPENDED
    if LLM_SUSPENDED:
        return
    # Mark unavailable BEFORE killing so the chat route immediately sees
    # LLM_READY=False and returns a retry response rather than a connection error.
    LLM_READY     = False
    LLM_SUSPENDED = True
    print("[llm] suspending server to free VRAM for image generation...")
    killed = _kill_server_proc()
    if not killed:
        _kill_by_port()


def resume_after_image() -> None:
    global LLM_SUSPENDED
    if not LLM_SUSPENDED:
        return
    LLM_SUSPENDED = False
    print("[llm] resuming server after image generation...")

    def _restart():
        _start_server()
        wait_until_ready(timeout=180)
        if LLM_READY:
            print("[llm] server back up.")
            try:
                import vram as _vram
                _vram.notify_llm_ready()
            except Exception:
                pass
        else:
            warn("[llm] server did not come back in time — restart Alice if chat is unresponsive.")

    threading.Thread(target=_restart, daemon=True).start()

def _server_model_matches() -> bool:
    """Return True if the running llama-server is serving the configured model_path."""
    configured = os.path.basename(config.CFG.get("model_path", "")).lower()
    if not configured:
        return True  # no model configured — can't verify
    try:
        r = req.get(f"{LLAMA_URL}/v1/models", timeout=2)
        if r.status_code == 200:
            ids = [m.get("id", "").lower() for m in r.json().get("data", [])]
            return any(configured in i or i in configured for i in ids)
    except Exception:
        pass
    return True  # if we can't check, assume it's fine


def load_llm():
    step(f"Connecting to llama.cpp server at {LLAMA_URL} ...")
    if http_ok(LLAMA_URL + "/health", timeout=2):
        if not _server_model_matches():
            warn("llama-server is running a different model — restarting with the configured model...")
            _kill_server_proc()
            _kill_by_port()
            time.sleep(1)
            _start_server()
    else:
        _start_server()

    def _retry():
        for _ in range(60):
            if LLM_READY:
                return
            time.sleep(2)
            if _try_connect(silent=True):
                try:
                    import vram as _vram
                    _vram.notify_llm_ready()
                except Exception:
                    pass
                return
        warn("Gave up waiting for llama.cpp server. Start it manually and restart alice.py.")

    if not _try_connect():
        warn("llama.cpp server not ready yet — retrying in background. UI will unlock when ready.")
        threading.Thread(target=_retry, daemon=True).start()


def llm_chat(messages: list) -> str:
    # If the server was just suspended for image gen, wait for it to come back.
    if not LLM_READY:
        wait_until_ready(timeout=120)
    model = llm_model()
    
    p = config.CFG.get("llm_params", config._DEFAULT_CONFIG["llm_params"])
    payload = {
        "model":            model,
        "messages":         messages,
        "stream":           False,
        "cache_prompt":     True,
        **p
    }
    
    r = req.post(f"{LLAMA_URL}/v1/chat/completions", json=payload, timeout=120)
    
    # If 400, try a minimal payload (some versions don't like certain parameters)
    if r.status_code == 400:
        print(f"[llm] 400 Bad Request. Retrying with minimal payload... Server said: {r.text}")
        minimal = {
            "model":    model,
            "messages": messages,
            "stream":   False,
        }
        r = req.post(f"{LLAMA_URL}/v1/chat/completions", json=minimal, timeout=120)

    if r.status_code != 200:
        print(f"[llm] Error {r.status_code}: {r.text}")
        print(f"[llm] Payload was: {json.dumps(payload, indent=2)}")
        r.raise_for_status()
        
    return r.json()["choices"][0]["message"]["content"]


def llm_chat_deferred(messages: list, label: str = "") -> str:
    """Like llm_chat but raises immediately if a chat stream is active.

    Use this for background / lower-priority LLM calls (image extraction,
    pair compression, scene synthesis) so they yield to live chat without
    ever queuing behind a streaming response.
    """
    if _chat_in_progress.is_set():
        tag = f" ({label})" if label else ""
        raise RuntimeError(f"chat in progress — deferring LLM call{tag}")
    return llm_chat(messages)


def save_history():
    persona_key = state._active_persona_key
    path = _history_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "history":     history,
                "memory":      memory,
                "persona_key": persona_key,
            }, f, ensure_ascii=False)
    except Exception as e:
        warn(f"Could not save history: {e}")


def load_history():
    global memory
    persona_key = state._active_persona_key
    path = _history_path()
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        saved_persona = data.get("persona_key", "")
        if not saved_persona:
            warn("History has no persona tag — discarding to prevent lore bleed.")
            os.remove(path)
            return
        if persona_key and saved_persona != persona_key:
            warn(f"History was for persona '{saved_persona}', current is '{persona_key}' — discarding.")
            os.remove(path)
            return
        history.extend(data.get("history", []))
        memory = data.get("memory", "")
        ok(f"History loaded ({len(history)} messages, memory: {bool(memory)})")
    except Exception as e:
        warn(f"Could not load history: {e}")


def switch_history(new_persona_key: str):
    """Save current persona's history, then load the new persona's history."""
    global memory
    if history or memory:
        save_history()
    history.clear()
    memory = ""
    state._active_persona_key = new_persona_key
    load_history()


def clear_history():
    global memory
    history.clear()
    memory = ""
    path = _history_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _summarise(messages: list) -> str:
    text = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in messages)
    try:
        return llm_chat_deferred([
            {"role": "system", "content": (
                "Summarise the following conversation into a brief memory paragraph. "
                "Capture key facts, what happened, preferences, and relationship dynamics. "
                "Be concise — two to four sentences max."
            )},
            {"role": "user", "content": text},
        ], label="compress_history").strip()
    except Exception as e:
        if "chat in progress" not in str(e):
            warn(f"Summary failed: {e}")
        return ""


def compress_history():
    global memory
    with _history_lock:
        mem_cfg   = config.CFG.get("memory", config._DEFAULT_CONFIG["memory"])
        max_hist  = mem_cfg["max_history"]
        keep      = mem_cfg["keep_recent"]
        max_chars = mem_cfg["max_chars"]
        if len(history) <= max_hist:
            return
        old = history[:len(history) - keep]
        del history[:len(history) - keep]
    summary = _summarise(old)
    if summary:
        with _history_lock:
            memory = (memory + "\n" + summary).strip() if memory else summary
            if len(memory) > max_chars:
                trimmed = memory[-max_chars:]
                first_period = trimmed.find('. ')
                memory = trimmed[first_period + 2:] if first_period != -1 else trimmed
    save_history()
