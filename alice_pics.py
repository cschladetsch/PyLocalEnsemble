import os
import time
import requests
from pathlib import Path
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget, QLineEdit, QPushButton

# Configuration
FORGE_URL = "http://127.0.0.1:7860" # Default WebUI Forge port, adjust if needed
CACHE_DIR = Path("server/pics_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

PROMPTS = [
    "A cyberpunk street in Melbourne, neon rain, hyper-detailed, 8k",
    "A cozy room with a sleeping Persian cat, cinematic lighting",
    "Abstract fractal geometry, glowing energy lines, octane render",
    "A futuristic computer laboratory with glowing server racks",
]

class PicsWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Alice — Pics")
        self.resize(600, 650)
        self.prompt_index = 0

        layout = QVBoxLayout()
        
        self.prompt_label = QLabel("Current Prompt: None")
        self.prompt_label.setWordWrap(True)
        layout.addWidget(self.prompt_label)

        self.image_label = QLabel("Waiting for first generation...")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(512, 512)
        layout.addWidget(self.image_label)

        self.setLayout(layout)

        # Timer to trigger generation every 15 seconds
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.generate_and_update)
        self.timer.start(15000)

        # Kick off first generation immediately
        self.generate_and_update()

    def generate_and_update(self):
        prompt = PROMPTS[self.prompt_index % len(PROMPTS)]
        self.prompt_index += 1
        self.prompt_label.setText(f"Prompt: {prompt}")

        payload = {
            "prompt": prompt,
            "steps": 20,
            "width": 512,
            "height": 512,
            "cfg_scale": 7.0
        }

        try:
            response = requests.post(f"{FORGE_URL}/sdapi/v1/txt2img", json=payload, timeout=30)
            if response.status_code == 200:
                r_json = response.json()
                import base64
                from io import BytesIO
                from PIL import Image

                img_data = base64.b64decode(r_json['images'][0])
                image = Image.open(BytesIO(img_data))

                # Save to cache folder
                timestamp = int(time.time())
                filename = CACHE_DIR / f"pic_{timestamp}.png"
                image.save(filename)

                # Maintain cache limit of last 15 images
                self.prune_cache()

                # Display in UI
                pixmap = QPixmap(str(filename))
                self.image_label.setPixmap(pixmap.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio))
            else:
                self.image_label.setText(f"Forge Error: {response.status_code}")
        except Exception as e:
            self.image_label.setText(f"Connection Error: {str(e)}")

    def prune_cache(self):
        images = sorted(CACHE_DIR.glob("pic_*.png"), key=os.path.getmtime)
        while len(images) > 15:
            images[0].unlink()
            images.pop(0)

if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    window = PicsWindow()
    window.show()
    sys.exit(app.exec()):wq

