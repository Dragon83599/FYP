#!/usr/bin/env python3
"""Draw the PWA icons.

Two PNGs, 192 and 512 px, both derived from the same drawing so the maskable
icon and the install prompt match the page. Kept as a script rather than two
committed blobs so the icon can be regenerated when the palette changes.

    .venv/bin/python tools/ai/make_pwa_icons.py
    .venv/bin/python tools/ai/make_pwa_icons.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PWA = Path(__file__).resolve().parents[2] / "pwa"
SIZES = (192, 512)
BACKGROUND = (20, 24, 28)
ACCENT = (108, 213, 139)
DIM = (108, 213, 139, 90)


def draw_icon(size: int) -> Image.Image:
    """A heart outline with a PCG-like trace across it, on a dark square."""
    scale = 4  # draw big, then downscale: cheap anti-aliasing
    canvas = Image.new("RGB", (size * scale, size * scale), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    w = h = size * scale
    margin = w * 0.12

    # Heart: two circles plus a triangle, which reads well at 192 px.
    r = (w - 2 * margin) * 0.28
    cx, cy = w / 2, h * 0.40
    left = (cx - r, cy)
    right = (cx + r, cy)
    for centre in (left, right):
        draw.ellipse(
            [centre[0] - r, centre[1] - r, centre[0] + r, centre[1] + r],
            fill=ACCENT,
        )
    draw.polygon(
        [(cx - 2 * r, cy + r * 0.45), (cx + 2 * r, cy + r * 0.45), (cx, h - margin)],
        fill=ACCENT,
    )

    # Trace: one systolic spike, drawn dark so it reads as a cut-out.
    mid = h * 0.52
    amp = h * 0.13
    points = [
        (w * 0.18, mid),
        (w * 0.34, mid),
        (w * 0.42, mid - amp),
        (w * 0.50, mid + amp * 0.8),
        (w * 0.58, mid - amp * 0.55),
        (w * 0.66, mid),
        (w * 0.82, mid),
    ]
    draw.line(points, fill=BACKGROUND, width=max(3, int(w * 0.035)), joint="curve")

    return canvas.resize((size, size), Image.LANCZOS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="verify the committed icons match this drawing")
    args = parser.parse_args(argv)

    out_dir = PWA / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for size in SIZES:
        image = draw_icon(size)
        path = out_dir / f"icon-{size}.png"
        if args.check:
            if not path.exists():
                print(f"missing {path}")
                failures += 1
                continue
            existing = Image.open(path).convert("RGB")
            if existing.size != image.size or list(existing.getdata()) != list(image.getdata()):
                print(f"{path} is stale (run without --check)")
                failures += 1
            else:
                print(f"ok {path} ({size}x{size}, {path.stat().st_size} B)")
            continue
        image.save(path, optimize=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        print(f"wrote {path} ({size}x{size}, {path.stat().st_size} B, sha256 {digest})")

    if args.check and failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
