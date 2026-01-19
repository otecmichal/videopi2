import os
import pygame
import time
import sys

# Attempt to use kmsdrm (modern Pi OS standard) or let SDL2 choose
# We no longer force "fbcon" as it is often unavailable in newer SDL2 builds
if "SDL_VIDEODRIVER" not in os.environ:
    os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
# os.environ["SDL_FBDEV"] = "/dev/fb0" # kmsdrm usually handles the device automatically


def test_display():
    print("Initializing Pygame...")
    # Initialize only video to avoid ALSA/Audio errors if not needed
    try:
        pygame.display.init()
    except Exception as e:
        print(f"Error initializing Pygame video: {e}")
        print("Retrying without forcing SDL_VIDEODRIVER...")
        if "SDL_VIDEODRIVER" in os.environ:
            del os.environ["SDL_VIDEODRIVER"]
        try:
            pygame.display.init()
        except Exception as e2:
            print(f"Final video init failed: {e2}")
            return

    # Display dimensions
    width, height = 1024, 600

    print(f"Setting up display mode {width}x{height} on /dev/fb0...")
    try:
        # Some systems might require different flags or drivers
        screen = pygame.display.set_mode((width, height), pygame.FULLSCREEN)
    except pygame.error as e:
        print(f"Failed to set display mode: {e}")
        print("Attempting to initialize without FULLSCREEN flag...")
        try:
            screen = pygame.display.set_mode((width, height))
        except pygame.error as e:
            print(f"Final attempt failed: {e}")
            sys.exit(1)

    # Clear screen with blue (to distinguish from black if nothing shows)
    screen.fill((0, 0, 255))

    # Draw a large red circle in the center
    center = (width // 2, height // 2)
    radius = 200
    pygame.draw.circle(screen, (255, 0, 0), center, radius)

    # Draw a white border to check screen edges
    pygame.draw.rect(screen, (255, 255, 255), (0, 0, width, height), 5)

    # Update display
    pygame.display.flip()

    print(
        "Display updated. You should see a red circle on a blue background with a white border."
    )
    print("This will stay on screen for 15 seconds. Press Ctrl+C to exit early.")

    try:
        time.sleep(15)
    except KeyboardInterrupt:
        print("\nExiting...")

    pygame.quit()


if __name__ == "__main__":
    test_display()
