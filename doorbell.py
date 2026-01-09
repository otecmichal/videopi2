import cv2
import json
import time
import threading
import os
import sys
from luma.core.device import linux_framebuffer
from PIL import Image, ImageDraw
from evdev import InputDevice, list_devices, ecodes

# --- HARDWARE CONFIG ---
FB_DEVICE = '/dev/fb1'
CONFIG_FILE = 'feeds.json'
AUTO_CYCLE_SECONDS = 1800  # 30 minutes

# --- CALIBRATION ---
X_RAW_MIN, X_RAW_MAX = 300, 3900
Y_RAW_MIN, Y_RAW_MAX = 300, 3950

class RTSPViewer:
    def __init__(self):
        # Initialize Display
        self.device = linux_framebuffer(FB_DEVICE)
        self.w, self.h = self.device.width, self.device.height
        
        with open(CONFIG_FILE, 'r') as f:
            self.cameras = json.load(f)
        
        self.current_idx = 0
        self.frame = None
        self.running = True
        self.btn_width = 80 
        self.last_interaction_time = time.time() # Tracks both touch and auto-cycle

    def find_touch_device(self):
        devices = [InputDevice(path) for path in list_devices()]
        for dev in devices:
            if "ADS7846" in dev.name or "Touchscreen" in dev.name:
                return dev.path
        return None

    def map_coordinates(self, rx, ry):
        """Maps and fixes the inverted landscape orientation."""
        try:
            # Swap rx/ry for landscape
            new_x_raw = ry 
            new_y_raw = rx
            # Interpolate pixels
            tx = (new_x_raw - Y_RAW_MIN) * self.w // (Y_RAW_MAX - Y_RAW_MIN)
            ty = (new_y_raw - X_RAW_MIN) * self.h // (X_RAW_MAX - X_RAW_MIN)
            # Flip X axis to fix 'Left is Right' inversion
            tx = self.w - tx 
            return max(0, min(self.w - 1, tx)), max(0, min(self.h - 1, ty))
        except:
            return 0, 0

    def video_worker(self):
        """Decodes RTSP with optimized buffer and TCP transport."""
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|probesize;5000000"
        
        while self.running:
            cam = self.cameras[self.current_idx]
            cap = cv2.VideoCapture(cam['url'], cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
            
            print(f"Streaming: {cam['name']}")
            
            while self.running and self.cameras[self.current_idx] == cam:
                ret, img = cap.read()
                if not ret: break
                
                try:
                    img = cv2.resize(img, (self.w, self.h))
                    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    pil_img = Image.fromarray(img)
                    
                    draw = ImageDraw.Draw(pil_img)
                    # UI Overlay
                    draw.polygon([(10, self.h//2), (30, self.h//2-20), (30, self.h//2+20)], fill="white")
                    draw.polygon([(self.w-10, self.h//2), (self.w-30, self.h//2-20), (self.w-30, self.h//2+20)], fill="white")
                    
                    #draw.rectangle([self.w//2 - 60, 5, self.w//2 + 60, 25], fill=(0,0,0))
                    draw.text((self.w//2 - 40, 8), cam['name'], fill="yellow")
                    
                    self.frame = pil_img
                except: continue
            cap.release()
            time.sleep(0.1)

    def touch_worker(self):
        dev_path = self.find_touch_device()
        if not dev_path: return
        try:
            touch_hw = InputDevice(dev_path)
            raw_x, raw_y = 0, 0
            for event in touch_hw.read_loop():
                if event.type == ecodes.EV_ABS:
                    if event.code == ecodes.ABS_X: raw_x = event.value
                    if event.code == ecodes.ABS_Y: raw_y = event.value
                elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                    if event.value == 0: # Release
                        px, py = self.map_coordinates(raw_x, raw_y)
                        if px < self.btn_width:
                            self.current_idx = (self.current_idx - 1) % len(self.cameras)
                        elif px > (self.w - self.btn_width):
                            self.current_idx = (self.current_idx + 1) % len(self.cameras)
                        
                        # Reset the timer whenever the user touches the screen
                        self.last_interaction_time = time.time()
        except: pass

    def start(self):
        # Launch workers
        threading.Thread(target=self.video_worker, daemon=True).start()
        threading.Thread(target=self.touch_worker, daemon=True).start()

        print("System Live. Direct Buffer Mode active.")
        
        try:
            while self.running:
                # --- AUTO-CYCLE LOGIC ---
                if time.time() - self.last_interaction_time > AUTO_CYCLE_SECONDS:
                    self.current_idx = (self.current_idx + 1) % len(self.cameras)
                    self.last_interaction_time = time.time()

                if self.frame:
                    self.device.display(self.frame)
                
                time.sleep(0.02)
                
        except (KeyboardInterrupt, SystemExit):
            # This catches Ctrl+C and systemctl stop (SIGTERM)
            self.running = False
        finally:
            # --- CLEANUP LOGIC ---
            print("Cleaning up display...")
            try:
                # Fill the buffer with black pixels before exiting
                self.device.clear()
                # Some versions of luma require an explicit display update
                # so we can also just send a blank black image:
                black_frame = Image.new("RGB", (self.w, self.h), (0, 0, 0))
                self.device.display(black_frame)
            except:
                pass
            print("Shutdown complete.")

if __name__ == "__main__":
    viewer = RTSPViewer()
    viewer.start()