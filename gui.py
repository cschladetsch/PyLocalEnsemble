import os
import sys
import time
import base64
import threading
from io import BytesIO
from PIL import Image
import requests

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication, QLabel, QVBoxLayout, QWidget, QPushButton, QHBoxLayout
)

from config import FORGE_URL, CACHE_DIR, PROMPTS

class PicsWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Alice — Pics")
        self.resize(600, 680)
        self.prompt_index = 0

        layout = QVBoxLayout()
        
        self.prompt_label = QLabel("Current Prompt: Initializing...")
        self.prompt_label.setWordWrap(True)
        self.prompt_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #333;")
        layout.addWidget(self.prompt_label)

        self.image_label = QLabel("Waiting for first generation...")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(512, 512)
        self.image_label.setStyleSheet("background-color: #111; color: #fff; border-radius: 6px;")
        layout.addWidget(self.image_label)

        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Generate Now")
        self.refresh_btn.clicked.connect(self.generate_and_update)
        btn_layout.addWidget(self.refresh_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

        # Timer to trigger generation every 15 seconds
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.generate_and_update)
        self.timer.start(15000)

        # Kick off first generation immediately via background worker to keep UI fluid
        QTimer.singleShot(500, self.generate_and_update)

    def generate_and_update(self):
        prompt = PROMPTS[self.prompt_index % len(PROMPTS)]
        self.prompt_index += 1
        self.prompt_label.setText(f"Prompt: {prompt}")
        self.image_label.setText("Generating image from Forge...")

        # Run network call in a separate thread so the GUI window doesn't freeze
        threading.Thread(target=self._fetch_image, args=(prompt,), daemon=True).start()

    def _fetch_image(self, prompt):
        payload = {
            "prompt": prompt,
            "steps": 20,
            "width": 512,
            "height": 512,
            "cfg_scale": 7.0
        }

        try:
            response = requests.post(f"{FORGE_URL}/sdapi/v1/txt2img", json=payload, timeout=60)
            if response.status_code == 200:
                r_json = response.json()
                img_data = base64.b64decode(r_json['images'][0])
                image = Image.open(BytesIO(img_data))

                timestamp = int(time.time())
                filename = CACHE_DIR / f"pic_{timestamp}.png"
                image.save(filename)

                self.prune_cache()

                # Update UI safely from main thread
                QTimer.singleShot(0, lambda: self._update_pixmap(str(filename)))
            else:
                QTimer.singleShot(0, lambda: self.image_label.setText(f"Forge Error: HTTP {response.status_code}"))
        except Exception as e:
            QTimer.singleShot(0, lambda: self.image_label.setText(f"Connection Error (Is Forge running?)\n{str(e)}"))

    def _update_pixmap(self, filepath):
        pixmap = QPixmap(filepath)
        self.image_label.setPixmap(pixmap.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def prune_cache(self):
        images = sorted(CACHE_DIR.glob("pic_*.png"), key=os.path.getmtime)
        while len(images) > 15:
            images[0].unlink()
            images.pop(0)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PicsWindow()
    window.show()
    sys.exit(app.exec())
