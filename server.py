
import sys
import subprocess
from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.post("/api/pics/toggle")
def toggle_pics_window():
    # Spawns the decoupled PyQt6 GUI process independently
    subprocess.Popen([sys.executable, "gui.py"])
    return {"status": "started"}

if __name__ == "__main__":
    print("Starting unified Alice server with Pics companion route on http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)

