import cv2
import json
import time
import threading
from luma.core.device import linux_framebuffer
from PIL import Image, ImageDraw
from evdev import InputDevice, list_devices, ecodes

# --- HARDWARE CONFIG ---
FB_DEVICE = '/dev/fb1'
CONFIG_FILE = 'feeds.json'

# --- CALIBRATION ---
X_RAW_MIN, X_RAW_MAX = 300, 3900
Y_RAW_MIN, Y_RAW_MAX = 300, 3950

class RTSPViewer:
    def __init__(self):
        self.device = linux_framebuffer(FB_DEVICE)
        self.w, self.h = self.device.width, self.device.height
        
        with open(CONFIG_FILE, 'r') as f:
            self.cameras = json.load(f)
        
        self.current_idx = 0
        self.frame = None
        self.running = True
        self.btn_width = 80 
        self.last_switch_time = 0  # To prevent double-switching

    def find_touch_device(self):
        """Automatically find the ADS7846 event file."""
        devices = [InputDevice(path) for path in list_devices()]
        for dev in devices:
            if "ADS7846" in dev.name or "Touchscreen" in dev.name:
                print(f"Auto-detected touch device: {dev.path} ({dev.name})")
                return dev.path
        return None

    def map_coordinates(self, rx, ry):
        try:
            # 1. Swap rx and ry (Landscape correction)
            new_x_raw = ry 
            new_y_raw = rx
            
            # 2. Map to pixels 0-480 and 0-320
            tx = (new_x_raw - Y_RAW_MIN) * self.w // (Y_RAW_MAX - Y_RAW_MIN)
            ty = (new_y_raw - X_RAW_MIN) * self.h // (X_RAW_MAX - X_RAW_MIN)
            
            # 3. FIX INVERSION: Flip the X axis
            tx = self.w - tx 

            # Constrain to screen bounds
            return max(0, min(self.w - 1, tx)), max(0, min(self.h - 1, ty))
        except:
            return 0, 0

    def touch_worker(self):
        dev_path = self.find_touch_device()
        if not dev_path:
            print("CRITICAL: Touchscreen device not found!")
            return

        try:
            touch_hw = InputDevice(dev_path)
            raw_x, raw_y = 0, 0
            
            for event in touch_hw.read_loop():
                if event.type == ecodes.EV_ABS:
                    if event.code == ecodes.ABS_X: raw_x = event.value
                    if event.code == ecodes.ABS_Y: raw_y = event.value
                
                elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                    # value 1 = Press, 0 = Release. We use Release for better control.
                    if event.value == 0:
                        px, py = self.map_coordinates(raw_x, raw_y)
                        now = time.time()
                        
                        # Debug: See exactly where you touched in the terminal
                        print(f"Touch detected at Pixel ({px}, {py})")
                        self.ui_visible_until = time.time() + 10

                        # Switch logic with 0.5s debounce
                        if now - self.last_switch_time > 0.5:
                            if px < self.btn_width:
                                print("Navigating Left...")
                                self.current_idx = (self.current_idx - 1) % len(self.cameras)
                                self.last_switch_time = now
                            elif px > (self.w - self.btn_width):
                                print("Navigating Right...")
                                self.current_idx = (self.current_idx + 1) % len(self.cameras)
                                self.last_switch_time = now
        except Exception as e:
            print(f"Touch Thread Crashed: {e}")

    def video_worker(self):
        self.ui_visible_until = 0
        
        while self.running:
            cam = self.cameras[self.current_idx]
            cap = cv2.VideoCapture(cam['url'], cv2.CAP_FFMPEG)
            
            while self.running and self.cameras[self.current_idx] == cam:
                ret, img = cap.read()
                if not ret: break
                
                img = cv2.resize(img, (self.w, self.h))
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(img)
                
                # Only draw UI if touched in the last 10 seconds
                if time.time() < self.ui_visible_until:
                    draw = ImageDraw.Draw(pil_img)
                    # Draw Arrows
                    draw.polygon([(10, self.h//2), (30, self.h//2-20), (30, self.h//2+20)], fill="white")
                    draw.polygon([(self.w-10, self.h//2), (self.w-30, self.h//2-20), (self.w-30, self.h//2+20)], fill="white")
                    # Draw Name
                    draw.rectangle([self.w//2 - 60, 5, self.w//2 + 60, 25], fill=(0,0,0))
                    draw.text((self.w//2 - 40, 8), cam['name'], fill="yellow")
                
                self.frame = pil_img
            cap.release()

    def start(self):
        threading.Thread(target=self.video_worker, daemon=True).start()
        threading.Thread(target=self.touch_worker, daemon=True).start()

        try:
            while self.running:
                if self.frame:
                    self.device.display(self.frame)
                time.sleep(0.01)
        except KeyboardInterrupt:
            self.running = False

if __name__ == "__main__":
    viewer = RTSPViewer()
    viewer.start()