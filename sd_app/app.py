"""sd_app — standalone Stable Diffusion generation console.

Runs as its own process on its own port, independent of Alice's chat/LLM/TTS
stack. Manages (or reuses, if already running) the same Forge install Alice's
server/ uses by default.

Usage:
    python app.py              # start on the configured port (default 8010)
"""
import os
import config
from forge import start_forge
from routes import router

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

app = FastAPI(title="sd_app")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
app.include_router(router)


@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(config.STATIC_DIR, "sd-page.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    import uvicorn
    # Idempotent — no-ops if Forge (started by this process or by Alice) is
    # already up, so it's safe to run alongside Alice's own Forge management.
    # Run before uvicorn.run() (not as a FastAPI startup event) so the blocking
    # wait-for-Forge poll doesn't tie up the event loop.
    start_forge()
    uvicorn.run(app, host="0.0.0.0", port=config.CFG["port"])
