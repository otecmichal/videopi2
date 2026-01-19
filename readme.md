# VideoPi2: Passive Video Doorbell Appliance

A lightweight, optimized RTSP stream viewer designed for the Raspberry Pi Zero 2W. This appliance cycles through multiple camera feeds using a touch interface, making it an ideal passive monitor for home security or video doorbells.

## Hardware Requirements

- **Processor:** Raspberry Pi Zero 2W
- **Display:** Waveshare 3.5" SPI LCD (320x480 resolution)
- **Touch Driver:** ADS7846 compatible touchscreen

## Key Features

- **RTSP Streaming:** Low-latency stream handling using OpenCV and FFMPEG (TCP transport).
- **Touch Navigation:** On-screen left/right overlays to manually cycle through camera feeds.
- **Auto-Cycling:** Automatically rotates to the next camera feed after a period of inactivity (default: 30 minutes).
- **Performance Optimized:** Uses `numpy` and `OpenCV` native drawing for minimal CPU overhead on Pi Zero hardware.

## Performance Disclaimer & Recommendations

> [!IMPORTANT]
> Due to the performance limitations of the SPI bus on the Raspberry Pi, the application is capped at **12 FPS**. 
> 
> **Recommendation:** To ensure smooth playback and reduce network/CPU overhead, it is highly recommended to configure your RTSP camera streams to match this framerate (12 FPS) at the source.

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/videopi2.git
   cd videopi2
   ```

2. **Install Dependencies:**
   Ensure you have the necessary system libraries for OpenCV and the framebuffer:
   ```bash
   sudo apt-get update
   sudo apt-get install -y libatlas-base-dev libopenjp2-7 libtiff6
   pip install -r requirements.txt
   ```

3. **Configure Camera Feeds:**
   Copy the template and add your RTSP URLs:
   ```bash
   cp feeds.json.template feeds.json
   nano feeds.json
   ```

4. **Run the Application:**
   The script requires access to the framebuffer (`/dev/fb1`) and input devices:
   ```bash
   sudo python doorbell.py
   ```

## Configuration

Settings can be adjusted at the top of `doorbell.py`:

- `FB_DEVICE`: The framebuffer device path (default `/dev/fb1`).
- `AUTO_CYCLE_SECONDS`: Time before the display automatically switches to the next camera.
- `TARGET_FPS`: Framerate limit (default `12`).

## Troubleshooting

- **Touch Calibration:** If touch coordinates are inverted or misaligned, adjust `X_RAW_MIN`, `X_RAW_MAX`, `Y_RAW_MIN`, and `Y_RAW_MAX` in `doorbell.py`.
- **Stream Stability:** The application uses `rtsp_transport;tcp` to prevent frame corruption common on busy local networks.
