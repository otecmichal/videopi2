import os
import pygame
import time
import sys

# Force SDL to use the framebuffer device
os.environ["SDL_VIDEODRIVER"] = "fbcon"
os.environ["SDL_FBDEV"] = "/dev/fb0"


def test_display():
    print("Initializing Pygame...")
    try:
        pygame.init()
    except Exception as e:
        print(f"Error initializing Pygame: {e}")
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
