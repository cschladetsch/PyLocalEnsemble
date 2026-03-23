import os, json, re, time, threading, subprocess, shutil
import requests as req
import config
import state
from utils import step, ok, warn, http_ok

LLM_READY = False
history   = []
memory    = ""
_history_lock     = threading.Lock()
_chat_in_progress = threading.Event()   # set for the full lifetime of any streaming chat call

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
    try:
        r = req.get(f"{LLAMA_URL}/v1/models", timeout=5)
        return [(m["id"], m["id"]) for m in r.json().get("data", [])]
    except Exception:
        return []


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
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(flags, **kw)


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


def load_llm():
    step(f"Connecting to llama.cpp server at {LLAMA_URL} ...")
    if not http_ok(LLAMA_URL + "/health", timeout=2):
        _start_server()

    def _retry():
        for _ in range(60):
            if LLM_READY:
                return
            time.sleep(2)
            if _try_connect(silent=True):
                return
        warn("Gave up waiting for llama.cpp server. Start it manually and restart alice.py.")

    if not _try_connect():
        warn("llama.cpp server not ready yet — retrying in background. UI will unlock when ready.")
        threading.Thread(target=_retry, daemon=True).start()


def llm_chat(messages: list) -> str:
    # Try to get the actual model name from the server to avoid 400 errors
    model = llm_model()
    
    p = config.CFG.get("llm_params", config._DEFAULT_CONFIG["llm_params"])
    payload = {
        "model":            model,
        "messages":         messages,
        "stream":           False,
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
        return llm_chat([
            {"role": "system", "content": (
                "Summarise the following conversation into a brief memory paragraph. "
                "Capture key facts, what happened, preferences, and relationship dynamics. "
                "Be concise — two to four sentences max."
            )},
            {"role": "user", "content": text},
        ]).strip()
    except Exception as e:
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
