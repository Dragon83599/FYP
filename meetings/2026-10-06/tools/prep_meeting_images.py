# -*- coding: utf-8 -*-
"""Prepare images for the 2026-10-06 group-meeting deck."""
import math, os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "assets"
TMP = Path(os.environ.get("LOCALAPPDATA", r"C:\Temp")) / "Temp"
os.makedirs(TMP, exist_ok=True)

# 1) crop the product render: drop the "Product Name" placeholder text on the right
src = Image.open(os.path.join(ASSETS, "fyp-product-render.png")).convert("RGB")
w, h = src.size                      # 1600 x 1000
crop = src.crop((0, 0, 1150, 1000))  # keep device + waveform
out = os.path.join(TMP, "fyp-product-crop.png")
crop.save(out)
print("product crop:", crop.size, out)

# 2) heart-sound (lub-dub) trace strip on the cover background colour
INK = (31, 41, 51)          # 1F2933
CORAL = (201, 85, 63)       # C9553F
W, H = 1500, 260
im = Image.new("RGB", (W, H), INK)
d = ImageDraw.Draw(im)
base = H * 0.62


def bump(x, center, width, amp):
    return amp * math.exp(-((x - center) / width) ** 2)


pts = []
cycles = 4
seg_w = W / cycles
for x in range(0, W + 1, 2):
    local = (x % seg_w) / seg_w          # 0..1 within one lub-dub cycle
    y = base
    y -= bump(local, 0.16, 0.045, 92)    # S1 (lub)
    y -= bump(local, 0.30, 0.035, 40)    # S1 tail
    y -= bump(local, 0.44, 0.05, 66)     # S2 (dub)
    y -= bump(local, 0.56, 0.03, 26)     # S2 tail
    y -= 4 * math.sin(x / 46.0)          # tiny baseline wander
    pts.append((x, y))

glow = Image.new("RGB", (W, H), INK)
gd = ImageDraw.Draw(glow)
gd.line(pts, fill=(120, 70, 60), width=14, joint="curve")
glow = glow.filter(ImageFilter.GaussianBlur(6))
im = Image.blend(im, glow, 0.55)
d = ImageDraw.Draw(im)
d.line(pts, fill=CORAL, width=7, joint="curve")
# faint reference line
d.line([(0, base + 4), (W, base + 4)], fill=(58, 70, 84), width=2)

# 3) trim white margins off the architecture diagram
arch = Image.open(os.path.join(ASSETS, "fyp-system-architecture.png")).convert("RGB")
from PIL import ImageChops
bg = Image.new("RGB", arch.size, (255, 255, 255))
diff = ImageChops.difference(arch, bg).convert("L").point(lambda v: 255 if v > 12 else 0)
bbox = diff.getbbox()
if bbox:
    pad = 14
    bbox = (max(bbox[0] - pad, 0), max(bbox[1] - pad, 0),
            min(bbox[2] + pad, arch.size[0]), min(bbox[3] + pad, arch.size[1]))
    arch = arch.crop(bbox)
out3 = os.path.join(TMP, "fyp-architecture-trim.png")
arch.save(out3)
print("arch trimmed:", arch.size, "ratio %.3f" % (arch.size[0] / arch.size[1]), out3)

out2 = os.path.join(TMP, "heart-sound-trace.png")
im.save(out2)
print("trace:", im.size, out2)
