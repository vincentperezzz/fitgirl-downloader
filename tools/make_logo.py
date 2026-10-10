"""Generates assets/logo.png and assets/icon.ico (run: python tools/make_logo.py)."""
import os
from PIL import Image, ImageDraw

S = 1024
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
os.makedirs(OUT, exist_ok=True)

TOP, BOTTOM = (82, 232, 168), (31, 157, 107)   # emerald gradient, matches the UI accent
INK = (6, 35, 26)                                # ON_ACCENT

# Gradient background clipped to a rounded square
grad = Image.new("RGB", (S, S))
px = grad.load()
for y in range(S):
    t = y / (S - 1)
    c = tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
    for x in range(S):
        px[x, y] = c
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.225), fill=255)
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
img.paste(grad, (0, 0), mask)

# Glyph: download arrow dropping into a tray
d = ImageDraw.Draw(img)
cx = S // 2
shaft_w, head_w = 120, 400
d.rounded_rectangle((cx - shaft_w // 2, 215, cx + shaft_w // 2, 520), radius=40, fill=INK)
d.polygon([(cx - head_w // 2, 470), (cx + head_w // 2, 470), (cx, 690)], fill=INK)
tray = [(235, 720), (235, 800), (S - 235, 800), (S - 235, 720)]
d.line(tray, fill=INK, width=64, joint="curve")
for x, y in ((235, 720), (S - 235, 720), (235, 800), (S - 235, 800)):
    d.ellipse((x - 32, y - 32, x + 32, y + 32), fill=INK)

img.resize((512, 512), Image.LANCZOS).save(os.path.join(OUT, "logo.png"))
img.save(os.path.join(OUT, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", OUT)
