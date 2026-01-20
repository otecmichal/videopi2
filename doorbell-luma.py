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
    def _set_cursor(self, visible):
        """Try multiple ways to hide/show the console cursor."""
        try:
            # 1. ANSI Escape sequences
            if visible:
                sys.stdout.write("\033[?25h")
            else:
                sys.stdout.write("\033[?25l")
            sys.stdout.flush()

            # 2. Linux framebuffer console cursor blink
            # This is often the cause of the artifact on Pi
            path = "/sys/class/graphics/fbcon/cursor_blink"
            if os.path.exists(path):
                with open(path, "w") as f:
                    f.write("1" if visible else "0")

            # 3. setterm (requires being in a real tty)
            if not visible:
                os.system("setterm -cursor off > /dev/tty1 2>&1")
            else:
                os.system("setterm -cursor on > /dev/tty1 2>&1")
        except:
            pass

    def __init__(self):
        print("Initializing RTSP Viewer (Direct Framebuffer)...")

        # Hide terminal cursor and disable blanking
        self._set_cursor(False)
        os.system("setterm -blank 0 > /dev/tty1 2>&1")

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

        self.ui_overlay = None
        self.ui_regions = []  # List of (y1, y2, x1, x2, overlay_rgb, mask)

        # Pre-allocate buffers to avoid per-frame allocation
        self.out_buffer = np.zeros(
            (self.h, self.w, 4 if self.bpp == 32 else 3), dtype=np.uint8
        )
        self.full_rgb = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        self.small_rgb = None  # Will be allocated on first frame

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
        """Pre-render UI elements and identify their regions for fast blitting."""
        cam_name = self.cameras[self.current_idx]["name"]
        self.ui_regions = []

        def add_region(text, font, color, pos_func):
            # Temporary image to get dimensions
            temp_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            try:
                bbox = temp_draw.textbbox((0, 0), text, font=font)
                tw, th = int(bbox[2] - bbox[0]), int(bbox[3] - bbox[1])
            except AttributeError:
                # Fallback for older Pillow versions
                tw, th = temp_draw.textsize(text, font=font)

            x, y = pos_func(tw, th)
            # Create the actual region
            img = Image.new("RGBA", (int(tw) + 4, int(th) + 4), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            # Shadow
            draw.text((2, 2), text, font=font, fill=(0, 0, 0, 255))
            # Main text
            draw.text((0, 0), text, font=font, fill=color)

            arr = np.array(img)
            self.ui_regions.append(
                {
                    "y1": y,
                    "y2": y + arr.shape[0],
                    "x1": x,
                    "x2": x + arr.shape[1],
                    "rgb": arr[:, :, :3],
                    "mask": arr[:, :, 3:4] / 255.0,
                }
            )

        # 1. Camera Name (Centered)
        add_region(
            cam_name,
            self.font,
            (255, 255, 0, 255),
            lambda tw, th: ((self.w - tw) // 2, 20),
        )

        # 2. Navigation Triangles
        if len(self.cameras) > 1:
            size = 40
            mid_y = self.h // 2

            for side in ["left", "right"]:
                img = Image.new("RGBA", (size + 4, size * 2 + 4), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                pts = (
                    [(0, size), (size, 0), (size, size * 2)]
                    if side == "left"
                    else [(size, size), (0, 0), (0, size * 2)]
                )
                draw.polygon(pts, fill=(200, 200, 200, 180))

                arr = np.array(img)
                x = 10 if side == "left" else self.w - size - 10
                y = mid_y - size
                self.ui_regions.append(
                    {
                        "y1": y,
                        "y2": y + arr.shape[0],
                        "x1": x,
                        "x2": x + arr.shape[1],
                        "rgb": arr[:, :, :3],
                        "mask": arr[:, :, 3:4] / 255.0,
                    }
                )

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

                # 1. Color convert small and Resize using pre-allocated buffers
                if self.small_rgb is None or self.small_rgb.shape[:2] != img.shape[:2]:
                    self.small_rgb = np.zeros(
                        (img.shape[0], img.shape[1], 3), dtype=np.uint8
                    )

                cv2.cvtColor(img, cv2.COLOR_BGR2RGB, dst=self.small_rgb)
                cv2.resize(
                    self.small_rgb,
                    (self.w, self.h),
                    dst=self.full_rgb,
                    interpolation=cv2.INTER_NEAREST,
                )

                # 2. Fast Targeted Blending of UI (Integer math)
                for reg in self.ui_regions:
                    roi = self.full_rgb[reg["y1"] : reg["y2"], reg["x1"] : reg["x2"]]
                    # Integer blending: (src * (255-alpha) + overlay * alpha) >> 8
                    # We use uint16 for intermediate to avoid overflow
                    alpha = (reg["mask"] * 255).astype(np.uint16)
                    blended = (
                        (
                            roi.astype(np.uint16) * (255 - alpha)
                            + reg["rgb"].astype(np.uint16) * alpha
                        )
                        >> 8
                    ).astype(np.uint8)
                    self.full_rgb[reg["y1"] : reg["y2"], reg["x1"] : reg["x2"]] = (
                        blended
                    )

                # 3. Handle different BPP efficiently
                if self.bpp == 32:
                    # RGB -> RGBA expansion
                    cv2.cvtColor(self.full_rgb, cv2.COLOR_RGB2RGBA, dst=self.out_buffer)
                    self.frame = self.out_buffer.tobytes()
                elif self.bpp == 16:
                    # RGB565 conversion
                    r = (self.full_rgb[:, :, 0] >> 3).astype(np.uint16)
                    g = (self.full_rgb[:, :, 1] >> 2).astype(np.uint16)
                    b = (self.full_rgb[:, :, 2] >> 3).astype(np.uint16)
                    self.frame = ((r << 11) | (g << 5) | b).astype(np.uint16).tobytes()
                else:
                    self.frame = self.full_rgb.tobytes()

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

                if self.frame is not None:
                    self.fb_map.seek(0)
                    self.fb_map.write(self.frame)
                    self.frame = None

                time.sleep(max(0, FRAME_TIME - (time.time() - start_loop)))
        except Exception as e:
            print(f"Main loop error: {e}")
            import traceback

            traceback.print_exc()
            self.running = False
        finally:
            # Restore terminal cursor
            self._set_cursor(True)
            if hasattr(self, "fb_map"):
                self.fb_map.close()
            sys.exit(0)


if __name__ == "__main__":
    RTSPViewer().start()
