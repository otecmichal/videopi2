# Doorbell Rust (doorbell-rs)

A high-performance rewrite of the Video Doorbell application in Rust, designed for the Raspberry Pi Zero 2.

## Performance
- **FPS**: Targets 30 FPS (hardware limit of camera/screen) vs ~12 FPS in Python.
- **CPU**: Significantly lower CPU usage due to hardware-accelerated decoding (`v4l2h264dec`) and zero-copy rendering to Framebuffer.

## Prerequisites (on Pi Zero 2)
Ensure you have the necessary development libraries:

```bash
sudo apt-get update
sudo apt-get install -y \
    pkg-config libglib2.0-dev \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav \
    libgudev-1.0-dev libinput-dev
```

You also need Rust installed:
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

## Building
1. Navigate to the directory:
   ```bash
   cd doorbell-rs
   ```

2. Build for release:
   ```bash
   cargo build --release
   ```

## Running
Since this application accesses `/dev/fb0` and `/dev/input/event*` directly, it typically requires root privileges or adding your user to `video` and `input` groups.

```bash
sudo ./target/release/doorbell-rs
```

## Configuration
The app looks for `feeds.json` in the current directory. Ensure it matches the format:
```json
[
    {
        "name": "Front Door",
        "url": "rtsp://user:pass@ip:554/stream",
        "comment": ""
    }
]
```
