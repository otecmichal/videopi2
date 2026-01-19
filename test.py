import pygame
import os

# Tell Pygame to use the framebuffer
os.putenv('SDL_FBDEV', '/dev/fb0')

pygame.init()

# Get screen size
info = pygame.display.Info()
screen = pygame.display.set_mode((info.current_w, info.current_h), pygame.FULLSCREEN)

# Colors
WHITE = (255, 255, 255)
BLUE = (0, 0, 255)

# Draw a circle: (surface, color, center_coords, radius)
screen.fill((0, 0, 0)) # Black background
pygame.draw.circle(screen, BLUE, (info.current_w // 2, info.current_h // 2), 100)
pygame.display.flip()

# Keep it open for 5 seconds
import time
time.sleep(5)
pygame.quit()
