"""Draw the gppcc app icon (1024x1024 PNG) with pygame. Usage: make_icon.py out.png"""
import os
import sys

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import pygame  # noqa: E402

S = 1024
surf = pygame.Surface((S, S), pygame.SRCALPHA)

# background: rounded square with vertical gradient (macOS icon grid: 824px body)
m = 100
bg = pygame.Surface((S, S), pygame.SRCALPHA)
for y in range(m, S - m):
    t = (y - m) / (S - 2 * m)
    c = (int(40 + 20 * t), int(90 - 40 * t), int(220 - 90 * t))
    pygame.draw.line(bg, c, (m, y), (S - m, y))
mask = pygame.Surface((S, S), pygame.SRCALPHA)
pygame.draw.rect(mask, (255, 255, 255, 255), (m, m, S - 2 * m, S - 2 * m), border_radius=185)
bg.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
surf.blit(bg, (0, 0))

# controller body
W = (238, 242, 250)
pygame.draw.rect(surf, W, (250, 380, 524, 230), border_radius=115)
pygame.draw.circle(surf, W, (330, 600), 105)
pygame.draw.circle(surf, W, (694, 600), 105)

# d-pad
D = (45, 60, 110)
pygame.draw.rect(surf, D, (300, 470, 110, 34), border_radius=8)
pygame.draw.rect(surf, D, (338, 432, 34, 110), border_radius=8)

# face buttons
for (x, y), col in zip([(694, 440), (694, 540), (644, 490), (744, 490)],
                       [(250, 200, 60), (70, 200, 120), (80, 140, 240), (240, 85, 90)]):
    pygame.draw.circle(surf, col, (x, y), 26)

# sticks
for x in (445, 579):
    pygame.draw.circle(surf, D, (x, 575), 42)
    pygame.draw.circle(surf, (90, 110, 170), (x, 575), 26)

pygame.image.save(surf, sys.argv[1])
