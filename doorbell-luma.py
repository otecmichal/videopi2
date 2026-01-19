import cv2
import json
import time
import threading
import os
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import i2c, spi
from luma.core.render import canvas
from luma.core.device import linux_framebuffer
from evdev import InputDevice, list_devices, ecodes

# --- RTSP OPTIMIZATION ---
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

CONFIG_FILE = "feeds.json"
AUTO_CYCLE_SECONDS = 1800
TARGET_FPS = 12
FRAME_TIME = 1.0 / TARGET_FPS

# --- CALIBRATION ---
X_RAW_MIN, X_RAW_MAX = 300, 3900
Y_RAW_MIN, Y_RAW_MAX = 300, 3950


class RTSPViewer:
    def __init__(self):
        print("Initializing RTSP Viewer (Luma/Framebuffer)...")

        # Initialize Luma linux_framebuffer device
        try:
            self.device = linux_framebuffer("/dev/fb0")
        except Exception as e:
            print(f"Error initializing framebuffer: {e}")
            sys.exit(1)

        self.w, self.h = self.device.width, self.device.height
        print(f"Display resolution: {self.w}x{self.h}")

        print(f"Loading camera config from {CONFIG_FILE}...")
        with open(CONFIG_FILE, "r") as f:
            self.cameras = json.load(f)

        print(f"Loaded {len(self.cameras)} cameras")
        self.current_idx = 0
        self.frame = None
        self.running = True
        self.btn_width = 80
        self.last_interaction_time = time.time()

        # --- UI Assets ---
        try:
            self.font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32
            )
        except:
            self.font = ImageFont.load_default()

        self.ui_overlay = self._create_ui_overlay()

        print("RTSP Viewer initialized successfully")

    def _create_ui_overlay(self):
        """Create a static-ish UI overlay for the current camera."""
        cam_name = self.cameras[self.current_idx]["name"]
        overlay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # 1. Camera Name (Centered)
        try:
            # Use textbbox if available (Pillow >= 8.0.0)
            bbox = draw.textbbox((0, 0), cam_name, font=self.font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except AttributeError:
            # Fallback to textsize for older Pillow
            tw, th = draw.textsize(cam_name, font=self.font)

        tx = (self.w - tw) // 2
        # Drop shadow
        draw.text((tx + 2, 22), cam_name, font=self.font, fill="black")
        draw.text((tx, 20), cam_name, font=self.font, fill="yellow")

        # 2. Navigation Triangles
        if len(self.cameras) > 1:
            tri_color = (200, 200, 200, 180)
            size = 40
            mid_y = self.h // 2
            # Left
            draw.polygon(
                [(10, mid_y), (10 + size, mid_y - size), (10 + size, mid_y + size)],
                fill=tri_color,
            )
            # Right
            draw.polygon(
                [
                    (self.w - 10, mid_y),
                    (self.w - 10 - size, mid_y - size),
                    (self.w - 10 - size, mid_y + size),
                ],
                fill=tri_color,
            )

        return overlay

    def find_touch_device(self):
        print("Scanning for touch devices...")
        devices = []
        try:
            devices = [InputDevice(path) for path in list_devices()]
        except:
            print("Error scanning input devices")

        for dev in devices:
            name_lower = dev.name.lower()
            if any(k in name_lower for k in ["waveshare", "ads7846", "touchscreen"]):
                print(f"Using touch device: {dev.name} at {dev.path}")
                return dev.path
        return None

    def map_coordinates(self, rx, ry):
        try:
            # Coordinate mapping based on previous doorbell-hdmi.py
            new_x_raw = ry
            new_y_raw = rx
            tx = (new_x_raw - Y_RAW_MIN) * self.w // (Y_RAW_MAX - Y_RAW_MIN)
            ty = (new_y_raw - X_RAW_MIN) * self.h // (X_RAW_MAX - X_RAW_MIN)
            tx = self.w - tx
            return max(0, min(self.w - 1, tx)), max(0, min(self.h - 1, ty))
        except:
            return 0, 0

    def video_worker(self):
        print("Starting video worker thread...")
        while self.running:
            cam = self.cameras[self.current_idx]
            print(f"Connecting to: {cam['name']}")
            cap = cv2.VideoCapture(cam["url"], cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                time.sleep(5)
                continue

            # Update UI overlay for new camera
            self.ui_overlay = self._create_ui_overlay()

            while self.running and self.cameras[self.current_idx] == cam:
                if not cap.grab():
                    break

                if self.frame is not None:
                    time.sleep(0.005)
                    continue

                ret, img = cap.retrieve()
                if not ret:
                    break

                # Color conversion (BGR -> RGB)
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

                # Resize if necessary
                if (img_rgb.shape[1], img_rgb.shape[0]) != (self.w, self.h):
                    img_rgb = cv2.resize(
                        img_rgb, (self.w, self.h), interpolation=cv2.INTER_NEAREST
                    )

                # Convert to PIL Image
                pil_img = Image.fromarray(img_rgb)

                # Composite with UI
                pil_img.paste(self.ui_overlay, (0, 0), self.ui_overlay)

                self.frame = pil_img

            cap.release()
            time.sleep(1)

    def touch_worker(self):
        dev_path = self.find_touch_device()
        if not dev_path:
            return
        try:
            touch_hw = InputDevice(dev_path)
            raw_x, raw_y = 0, 0
            for event in touch_hw.read_loop():
                if event.type == ecodes.EV_ABS:
                    if event.code == ecodes.ABS_X:
                        raw_x = event.value
                    if event.code == ecodes.ABS_Y:
                        raw_y = event.value
                elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                    if event.value == 0:  # Release
                        px, py = self.map_coordinates(raw_x, raw_y)
                        if px < self.btn_width:
                            self.current_idx = (self.current_idx - 1) % len(
                                self.cameras
                            )
                        elif px > (self.w - self.btn_width):
                            self.current_idx = (self.current_idx + 1) % len(
                                self.cameras
                            )
                        self.last_interaction_time = time.time()
        except:
            pass

    def start(self):
        threading.Thread(target=self.video_worker, daemon=True).start()
        threading.Thread(target=self.touch_worker, daemon=True).start()

        try:
            while self.running:
                start_loop = time.time()

                if time.time() - self.last_interaction_time > AUTO_CYCLE_SECONDS:
                    self.current_idx = (self.current_idx + 1) % len(self.cameras)
                    self.last_interaction_time = time.time()

                if self.frame:
                    # Display the PIL Image directly using luma.lcd hdmi device
                    self.device.display(self.frame)
                    self.frame = None

                loop_time = time.time() - start_loop
                time.sleep(max(0, FRAME_TIME - loop_time))

        except (KeyboardInterrupt, SystemExit):
            self.running = False
        finally:
            print("Cleaning up...")
            sys.exit(0)


if __name__ == "__main__":
    viewer = RTSPViewer()
    viewer.start()
