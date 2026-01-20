import cv2
import json
import time
import threading
import os
import sys
import numpy as np
import mmap
from PIL import Image, ImageDraw, ImageFont
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
        print("Initializing RTSP Viewer (Direct Framebuffer)...")

        # Hide terminal cursor
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()

        # 1. Initialize Framebuffer
        try:
            fb = open("/dev/fb0", "r+b")
            # We assume standard 1080p or 720p. For Pi, usually it's fixed at boot.
            # We'll try to get resolution from sysfs if possible, or fallback to common.
            self.w, self.h = self._get_fb_res()
            self.bpp = self._get_fb_bpp()
            print(f"Detected Framebuffer: {self.w}x{self.h} @ {self.bpp}bpp")

            fb_size = self.w * self.h * (self.bpp // 8)
            self.fb_map = mmap.mmap(fb.fileno(), fb_size)
        except Exception as e:
            print(f"Error accessing framebuffer: {e}")
            sys.exit(1)

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

        self.ui_mask = None
        self.ui_overlay = None
        self._update_ui_assets()

        print("RTSP Viewer initialized successfully")

    def _get_fb_res(self):
        try:
            with open("/sys/class/graphics/fb0/virtual_size", "r") as f:
                res = f.read().strip().split(",")
                return int(res[0]), int(res[1])
        except:
            return 1280, 720

    def _get_fb_bpp(self):
        try:
            with open("/sys/class/graphics/fb0/bits_per_pixel", "r") as f:
                return int(f.read().strip())
        except:
            return 32

    def _update_ui_assets(self):
        """Pre-render UI to NumPy arrays for fast blending."""
        cam_name = self.cameras[self.current_idx]["name"]

        # Create a RGBA PIL image for the UI
        pil_ui = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(pil_ui)

        # 1. Camera Name
        try:
            bbox = draw.textbbox((0, 0), cam_name, font=self.font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except AttributeError:
            tw, th = draw.textsize(cam_name, font=self.font)

        tx = (self.w - tw) // 2
        draw.text((tx + 2, 22), cam_name, font=self.font, fill=(0, 0, 0, 255))
        draw.text((tx, 20), cam_name, font=self.font, fill=(255, 255, 0, 255))

        # 2. Navigation Triangles
        if len(self.cameras) > 1:
            tri_color = (200, 200, 200, 180)
            size = 40
            mid_y = self.h // 2
            draw.polygon(
                [(10, mid_y), (10 + size, mid_y - size), (10 + size, mid_y + size)],
                fill=tri_color,
            )
            draw.polygon(
                [
                    (self.w - 10, mid_y),
                    (self.w - 10 - size, mid_y - size),
                    (self.w - 10 - size, mid_y + size),
                ],
                fill=tri_color,
            )

        # Convert to NumPy
        ui_array = np.array(pil_ui)
        self.ui_overlay = ui_array[:, :, :3]  # RGB
        self.ui_mask = ui_array[:, :, 3:4] / 255.0  # Alpha channel [0.0, 1.0]

    def find_touch_device(self):
        devices = [InputDevice(path) for path in list_devices()]
        for dev in devices:
            name = dev.name.lower()
            if any(k in name for k in ["waveshare", "ads7846", "touchscreen"]):
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
        while self.running:
            cam = self.cameras[self.current_idx]
            cap = cv2.VideoCapture(cam["url"], cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                time.sleep(5)
                continue

            self._update_ui_assets()

            while self.running and self.cameras[self.current_idx] == cam:
                if not cap.grab():
                    break
                if self.frame is not None:
                    time.sleep(0.005)
                    continue

                ret, img = cap.retrieve()
                if not ret:
                    break

                # 1. Color convert and Resize efficiently
                img = cv2.resize(img, (self.w, self.h), interpolation=cv2.INTER_NEAREST)
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

                # 2. Fast NumPy Blending of UI
                # out = img * (1 - mask) + overlay * mask
                # Using OpenCV for even faster blending if possible, or just numpy
                img_rgb = (
                    img_rgb * (1.0 - self.ui_mask) + self.ui_overlay * self.ui_mask
                ).astype(np.uint8)

                # 3. Handle different BPP (RGB565 or RGB888/32)
                if self.bpp == 32:
                    # BGRX or RGBX format
                    out = np.zeros((self.h, self.w, 4), dtype=np.uint8)
                    out[:, :, :3] = img_rgb
                    self.frame = out.tobytes()
                elif self.bpp == 16:
                    # RGB565
                    r = (img_rgb[:, :, 0] >> 3).astype(np.uint16)
                    g = (img_rgb[:, :, 1] >> 2).astype(np.uint16)
                    b = (img_rgb[:, :, 2] >> 3).astype(np.uint16)
                    self.frame = ((r << 11) | (g << 5) | b).tobytes()
                else:
                    self.frame = img_rgb.tobytes()

            cap.release()
            time.sleep(1)

    def touch_worker(self):
        dev_path = self.find_touch_device()
        if not dev_path:
            return
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
                        self.current_idx = (self.current_idx - 1) % len(self.cameras)
                    elif px > (self.w - self.btn_width):
                        self.current_idx = (self.current_idx + 1) % len(self.cameras)
                    self.last_interaction_time = time.time()

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
                    self.fb_map.seek(0)
                    self.fb_map.write(self.frame)
                    self.frame = None

                time.sleep(max(0, FRAME_TIME - (time.time() - start_loop)))
        except:
            self.running = False
        finally:
            # Restore terminal cursor
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()
            if hasattr(self, "fb_map"):
                self.fb_map.close()
            sys.exit(0)


if __name__ == "__main__":
    RTSPViewer().start()
