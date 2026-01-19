from luma.core.device import linux_framebuffer
from PIL import Image, ImageDraw
import time
import sys


def test_luma_hdmi():
    FB_DEVICE = "/dev/fb0"
    WIDTH = 1024
    HEIGHT = 600

    print(f"Initializing luma.core.device.linux_framebuffer on {FB_DEVICE}...")
    try:
        # This is the exact same initialization used in doorbell-hdmi.py
        device = linux_framebuffer(FB_DEVICE)

        # Create a new image with blue background
        print(f"Creating {WIDTH}x{HEIGHT} image...")
        image = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 255))  # Blue
        draw = ImageDraw.Draw(image)

        # Draw a red circle
        print("Drawing red circle...")
        center_x, center_y = WIDTH // 2, HEIGHT // 2
        radius = 200
        draw.ellipse(
            [
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
            ],
            fill=(255, 0, 0),
        )

        # Draw a white border
        draw.rectangle([0, 0, WIDTH - 1, HEIGHT - 1], outline=(255, 255, 255), width=5)

        print("Sending image to display...")
        device.display(image)

        print("Success! You should see a red circle on a blue background.")
        print("Displaying for 15 seconds...")
        time.sleep(15)

    except Exception as e:
        print(f"Error: {e}")
        print("\nPossible fixes:")
        print("1. Run with sudo: sudo ./venv/bin/python test_hdmi_luma.py")
        print("2. Ensure luma.core is installed: pip install luma.core")
        sys.exit(1)


if __name__ == "__main__":
    test_luma_hdmi()
