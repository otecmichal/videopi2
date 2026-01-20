import cv2
import json
import time
import threading
import os
import sys
import numpy as np  # Required for high-performance drawing
from luma.core.device import linux_framebuffer
from PIL import Image
from evdev import InputDevice, list_devices, ecodes

# --- HARDWARE CONFIG ---
FB_DEVICE = "/dev/fb1"
CONFIG_FILE = "feeds.json"
AUTO_CYCLE_SECONDS = 1800
TARGET_FPS = 12  # Reducing FPS is the best way to save CPU on Pi Zero 2W
FRAME_TIME = 1.0 / TARGET_FPS

# --- CALIBRATION ---
X_RAW_MIN, X_RAW_MAX = 300, 3900
Y_RAW_MIN, Y_RAW_MAX = 300, 3950


class RTSPViewer:
    def __init__(self):
        self.device = linux_framebuffer(FB_DEVICE)
        self.w, self.h = self.device.width, self.device.height

        with open(CONFIG_FILE, "r") as f:
            self.cameras = json.load(f)

        self.current_idx = 0
        self.frame = None
        self.running = True
        self.btn_width = 80
        self.last_interaction_time = time.time()

    def find_touch_device(self):
        devices = [InputDevice(path) for path in list_devices()]
        for dev in devices:
            if "ADS7846" in dev.name or "Touchscreen" in dev.name:
                return dev.path
        return None

    def map_coordinates(self, rx, ry):
        try:
            new_x_raw = ry
            new_y_raw = rx
            tx = (new_x_raw - Y_RAW_MIN) * self.w // (Y_RAW_MAX - Y_RAW_MIN)
            ty = (new_y_raw - X_RAW_MIN) * self.h // (X_RAW_MAX - X_RAW_MIN)
            tx = self.w - tx
            return max(0, min(self.w - 1, tx)), max(0, min(self.h - 1, ty))
        except:
            return 0, 0

    def video_worker(self):
        """Ultra-optimized worker using OpenCV native drawing."""
        # Use TCP to prevent 'overread' errors and stabilize stream
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

        while self.running:
            cam = self.cameras[self.current_idx]
            cap = cv2.VideoCapture(cam["url"], cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)

            print(f"Streaming: {cam['name']}")

            while self.running and self.cameras[self.current_idx] == cam:
                ret, img = cap.read()
                if not ret:
                    break

                # 1. Faster Resize (INTER_NEAREST is much lighter than default)
                img = cv2.resize(img, (self.w, self.h), interpolation=cv2.INTER_NEAREST)

                # 2. Draw UI using OpenCV (BGR Colors)
                # Left Arrow
                pts_l = np.array(
                    [[10, self.h // 2], [30, self.h // 2 - 20], [30, self.h // 2 + 20]],
                    np.int32,
                )
                cv2.fillPoly(img, [pts_l], (255, 255, 255))

                # Right Arrow
                pts_r = np.array(
                    [
                        [self.w - 10, self.h // 2],
                        [self.w - 30, self.h // 2 - 20],
                        [self.w - 30, self.h // 2 + 20],
                    ],
                    np.int32,
                )
                cv2.fillPoly(img, [pts_r], (255, 255, 255))

                # Top Label (Black box + Text)
                # cv2.rectangle(img, (self.w//2 - 60, 5), (self.w//2 + 60, 30), (0, 0, 0), -1)
                cv2.putText(
                    img,
                    cam["name"],
                    (self.w // 2 - 45, 22),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 255),
                    1,
                    cv2.LINE_AA,
                )

                # 3. Final conversion to RGB and PIL (Only once per frame)
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                self.frame = Image.fromarray(img_rgb)

            cap.release()
            time.sleep(0.5)

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
                    if event.value == 0:
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

                # Auto-cycle check
                if time.time() - self.last_interaction_time > AUTO_CYCLE_SECONDS:
                    self.current_idx = (self.current_idx + 1) % len(self.cameras)
                    self.last_interaction_time = time.time()

                if self.frame is not None:
                    self.device.display(self.frame)

                # FPS Governor: sleep just enough to maintain TARGET_FPS
                loop_time = time.time() - start_loop
                time.sleep(max(0, FRAME_TIME - loop_time))

        except (KeyboardInterrupt, SystemExit):
            self.running = False
        finally:
            print("Cleaning up...")
            try:
                black = Image.new("RGB", (self.w, self.h), (0, 0, 0))
                self.device.display(black)
            except:
                pass
            sys.exit(0)


if __name__ == "__main__":
    viewer = RTSPViewer()
    viewer.start()
