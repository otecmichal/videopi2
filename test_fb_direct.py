import os
import sys
import time


def test_direct_fb():
    fb_path = "/dev/fb0"
    width = 1024
    height = 600
    bpp = 32  # Usually 32-bit on HDMI

    print(f"Testing direct write to {fb_path}...")

    try:
        # Open the framebuffer device
        with open(fb_path, "wb") as fb:
            # Create a blue screen buffer (BGRA or RGBA)
            # 1024 * 600 * 4 bytes
            print("Drawing blue background...")
            blue_frame = bytearray([255, 0, 0, 255] * (width * height))  # B, G, R, A
            fb.write(blue_frame)
            fb.flush()

            # Draw a simple white square in the middle
            print("Drawing white square...")
            for y in range(200, 400):
                # Seek to the start of the row
                fb.seek(y * width * 4 + (400 * 4))
                # Write 200 pixels of white
                fb.write(bytearray([255, 255, 255, 255] * 200))

            fb.flush()
            print(
                "Success! If you see a blue screen with a white square, /dev/fb0 is working."
            )
            print(
                "The screen will stay like this until something else updates the framebuffer."
            )
            time.sleep(5)

    except Exception as e:
        print(f"Error: {e}")
        print("Make sure you are running with sudo.")


if __name__ == "__main__":
    test_direct_fb()
