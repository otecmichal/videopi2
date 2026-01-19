import cv2
import json
import time
import threading
import os
import sys
import numpy as np
import pygame
from evdev import InputDevice, list_devices, ecodes

# --- RTSP OPTIMIZATION ---
# Set these BEFORE importing cv2 if possible, but definitely before VideoCapture
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# --- HARDWARE CONFIG ---
FB_DEVICE = "/dev/fb0"
os.environ["SDL_FBDEV"] = FB_DEVICE
# For modern Pi OS, sometimes kmsdrm is needed, but we follow test.py
# os.environ["SDL_VIDEODRIVER"] = "fbcon"

CONFIG_FILE = "feeds.json"
AUTO_CYCLE_SECONDS = 1800
TARGET_FPS = 12
FRAME_TIME = 1.0 / TARGET_FPS

# --- CALIBRATION ---
X_RAW_MIN, X_RAW_MAX = 300, 3900
Y_RAW_MIN, Y_RAW_MAX = 300, 3950


class RTSPViewer:
    def __init__(self):
        print("Initializing RTSP Viewer...")
        pygame.init()
        pygame.mouse.set_visible(False)

        # Get screen size from logic in test.py
        info = pygame.display.Info()
        self.w, self.h = info.current_w, info.current_h
        print(f"Display resolution: {self.w}x{self.h}")

        self.screen = pygame.display.set_mode((self.w, self.h), pygame.FULLSCREEN)

        # Immediate visual feedback (like test.py)
        self.screen.fill((0, 0, 255))  # Blue screen
        font = pygame.font.SysFont(None, 48)
        img = font.render("Loading Cameras...", True, (255, 255, 255))
        self.screen.blit(img, (self.w // 2 - 150, self.h // 2))
        pygame.display.flip()
        print("Splash screen displayed")

        print(f"Loading camera config from {CONFIG_FILE}...")
        with open(CONFIG_FILE, "r") as f:
            self.cameras = json.load(f)

        print(f"Loaded {len(self.cameras)} cameras")
        self.current_idx = 0
        self.frame = None
        self.running = True
        self.btn_width = 80
        self.last_interaction_time = time.time()

        # --- OPTIMIZATION: Pre-render UI ---
        self.ui_font = pygame.font.SysFont(None, 40)
        self.camera_surfaces = {}  # Cache for camera name surfaces
        self.last_rendered_idx = -1
        self.nav_surfaces = self._create_nav_surfaces()

        print("RTSP Viewer initialized successfully")

    def _create_nav_surfaces(self):
        """Pre-render navigation triangles as pygame surfaces."""
        surfaces = {}
        if len(self.cameras) <= 1:
            return surfaces

        tri_color = (200, 200, 200)
        size = 60
        # Left triangle
        left_surf = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.polygon(
            left_surf, tri_color, [(0, size // 2), (size, 0), (size, size)]
        )
        surfaces["left"] = (left_surf, (10, self.h // 2 - size // 2))

        # Right triangle
        right_surf = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.polygon(
            right_surf, tri_color, [(size, size // 2), (0, 0), (0, size)]
        )
        surfaces["right"] = (right_surf, (self.w - size - 10, self.h // 2 - size // 2))
        return surfaces

    def _get_camera_name_surface(self, name):
        """Get or create a cached surface for the camera name."""
        if name not in self.camera_surfaces:
            # Render with drop shadow
            shadow = self.ui_font.render(name, True, (0, 0, 0))
            text = self.ui_font.render(name, True, (0, 255, 255))

            surf = pygame.Surface(
                (text.get_width() + 2, text.get_height() + 2), pygame.SRCALPHA
            )
            surf.blit(shadow, (2, 2))
            surf.blit(text, (0, 0))

            x = (self.w - surf.get_width()) // 2
            self.camera_surfaces[name] = (surf, (x, 20))
        return self.camera_surfaces[name]

    def find_touch_device(self):
        print("Scanning for touch devices...")
        devices = []
        try:
            devices = [InputDevice(path) for path in list_devices()]
        except:
            print("Error scanning input devices")

        print(f"Found {len(devices)} input devices")
        for dev in devices:
            if (
                "WaveShare" in dev.name
                or "ADS7846" in dev.name
                or "Touchscreen" in dev.name
                or "waveshare" in dev.name.lower()
            ):
                print(f"Using touch device: {dev.name} at {dev.path}")
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
        print("Starting video worker thread...")

        while self.running:
            cam = self.cameras[self.current_idx]
            print(f"Connecting to camera: {cam['name']} at {cam['url']}")

            # Add a timeout to VideoCapture if possible (not directly supported, but we can try)
            cap = cv2.VideoCapture(cam["url"], cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimal buffer for low latency

            if not cap.isOpened():
                print(f"Failed to open camera: {cam['name']}")
                time.sleep(5)
                continue

            print(f"Streaming: {cam['name']}")

            while self.running and self.cameras[self.current_idx] == cam:
                # 1. Grab the frame (cheap)
                if not cap.grab():
                    print(f"Lost connection to {cam['name']}, reconnecting...")
                    break

                # 2. Only decode and process if the main thread is ready
                if self.frame is not None:
                    time.sleep(0.005)  # Tiny sleep to yield
                    continue

                # 3. Retrieve/Decode (expensive)
                ret, img = cap.retrieve()
                if not ret:
                    break

                # 4. Color convert SMALL frame (much faster)
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

                # 5. Resize SMALL RGB to screen size (only if needed)
                if (img_rgb.shape[1], img_rgb.shape[0]) != (self.w, self.h):
                    img_rgb = cv2.resize(
                        img_rgb, (self.w, self.h), interpolation=cv2.INTER_NEAREST
                    )

                # 6. Create surface from buffer without copying
                self.frame = pygame.image.frombuffer(img_rgb, (self.w, self.h), "RGB")

            cap.release()
            print(f"Released camera: {cam['name']}")
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

                if self.frame:
                    self.screen.blit(self.frame, (0, 0))
                    self.frame = None  # Signal ready for next frame

                    # --- OPTIMIZED UI BLIT ---
                    # 1. Draw Name
                    cam_name = self.cameras[self.current_idx]["name"]
                    name_surf, name_pos = self._get_camera_name_surface(cam_name)
                    self.screen.blit(name_surf, name_pos)

                    # 2. Draw Nav
                    for surf, pos in self.nav_surfaces.values():
                        self.screen.blit(surf, pos)

                    pygame.display.flip()

                # FPS Governor: sleep just enough to maintain TARGET_FPS
                loop_time = time.time() - start_loop
                time.sleep(max(0, FRAME_TIME - loop_time))

        except (KeyboardInterrupt, SystemExit):
            self.running = False
        finally:
            print("Cleaning up...")
            try:
                self.screen.fill((0, 0, 0))
                pygame.display.flip()
                pygame.quit()
            except:
                pass
            sys.exit(0)


if __name__ == "__main__":
    viewer = RTSPViewer()
    viewer.start()
