import os, json, re, time, threading, subprocess, shutil
import requests as req
import config
from utils import step, ok, warn, http_ok

LLM_READY = False
history   = []
memory    = ""

LLAMA_URL = os.environ.get("LLAMA_URL", config.CFG.get("llama_url", "http://127.0.0.1:8080"))
if not LLAMA_URL.startswith("http"):
    LLAMA_URL = "http://" + LLAMA_URL


def llm_model() -> str:
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
    ok(f"Starting llama-server with {os.path.basename(model_path)} "
       f"(ngl={sc['n_gpu_layers']}, ctx={sc['ctx_size']})...")
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
    r = req.post(f"{LLAMA_URL}/v1/chat/completions", json={
        "model":            llm_model(),
        "messages":         messages,
        "stream":           False,
        "temperature":      0.9,
        "top_p":            0.95,
        "repeat_penalty":   1.15,
        "presence_penalty": 0.6,
    }, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def save_history():
    try:
        with open(config.HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"history": history, "memory": memory}, f, ensure_ascii=False)
    except Exception as e:
        warn(f"Could not save history: {e}")


def load_history():
    global memory
    if not os.path.exists(config.HISTORY_FILE):
        return
    try:
        with open(config.HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        history.extend(data.get("history", []))
        memory = data.get("memory", "")
        ok(f"History loaded ({len(history)} messages, memory: {bool(memory)})")
    except Exception as e:
        warn(f"Could not load history: {e}")


def clear_history():
    global memory
    history.clear()
    memory = ""
    try:
        if os.path.exists(config.HISTORY_FILE):
            os.remove(config.HISTORY_FILE)
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
        memory = (memory + "\n" + summary).strip() if memory else summary
        if len(memory) > max_chars:
            memory = memory[-max_chars:]
    save_history()
