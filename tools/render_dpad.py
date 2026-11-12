#!/usr/bin/env python3
"""Render d-pad variants over a mid-tone backdrop, for choosing between them.

Why a backdrop at all: an overlay's distraction is a property of the pair. The same
cyan ring looks restrained over a dark wallpaper and loud over a lit game scene, so
these sheets are composited over a synthetic mid-tone (mean luminance ~118) with
blob and fine detail, which is closer to gameplay than a desktop and closer to the
worst case than black.

Each cell is the control at its real size (180 logical px at DPR 1.5) and the real
layout opacity, and the faint square in each cell is the edge of the layer surface:
useful, because the client's 200-unit ring does not fit inside it.

    python3 tools/render_dpad.py [--out-dir /tmp] [--opacity 0.85]
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys

import cairo
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import layer_dpad as D  # noqa: E402

SCALE = 1.5                 # this machine's DPR
BASE = 180                  # DpadZone.Base, and the layout's d-pad frame
LABEL_H = 30
GAP = 14


def backdrop(w: int, h: int, seed: int = 7) -> Image.Image:
    """A mid-tone, busy stand-in for a game scene."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        for x in range(w):
            u = x / max(w - 1, 1)
            # a diagonal gradient from slate to warm grey, mid luminance
            r = 96 + 46 * u + 18 * t
            g = 104 + 30 * (1 - u) + 14 * t
            b = 118 + 18 * u - 8 * t
            px[x, y] = (int(r), int(g), int(b))
    for _ in range(46):                     # large shapes: geometry in the scene
        col = tuple(int(c) for c in [rnd.randint(40, 200), rnd.randint(45, 195),
                                     rnd.randint(50, 205)])
        x0, y0 = rnd.randint(-40, w), rnd.randint(-40, h)
        rr = rnd.randint(30, 170)
        layer = Image.new("RGB", (w, h), col)
        mask = Image.new("L", (w, h), 0)
        mask.load()
        from PIL import ImageDraw
        ImageDraw.Draw(mask).ellipse([x0 - rr, y0 - rr, x0 + rr, y0 + rr], fill=150)
        img = Image.composite(layer, img, mask)
    from PIL import ImageDraw, ImageFilter
    draw = ImageDraw.Draw(img)
    for _ in range(240):                    # fine detail: particles, text, HUD bits
        x0, y0 = rnd.randint(0, w - 1), rnd.randint(0, h - 1)
        ln = rnd.randint(2, 26)
        ang = rnd.uniform(0, 6.283)
        col = tuple(rnd.randint(30, 235) for _ in range(3))
        draw.line([x0, y0, x0 + ln * math.cos(ang), y0 + ln * math.sin(ang)],
                  fill=col, width=rnd.choice((1, 1, 2)))
    return img.filter(ImageFilter.GaussianBlur(0.6))


def cell(style: str, cross: str, press: str, mode: int, arms: dict | None,
         label: str, opacity: float, back: Image.Image) -> Image.Image:
    """One control, painted at real size over a crop of the backdrop."""
    size = int(BASE * SCALE)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    cr.scale(SCALE, SCALE)                   # GTK hands draw funcs logical units
    D.paint(cr, BASE, BASE, arms or {}, mode, style=style, cross=cross,
            press=press, alpha=1.0)
    surf.flush()
    # cairo ARGB32 is premultiplied; unpremultiply into a straight-alpha RGBA
    buf = surf.get_data()
    rgba = bytearray(len(buf))
    for i in range(0, len(buf), 4):
        b, g, r, a = buf[i], buf[i + 1], buf[i + 2], buf[i + 3]
        if a:
            rgba[i] = min(255, int(r * 255 / a))
            rgba[i + 1] = min(255, int(g * 255 / a))
            rgba[i + 2] = min(255, int(b * 255 / a))
        rgba[i + 3] = a
    ov = Image.frombytes("RGBA", (size, size), bytes(rgba))
    alpha = ov.getchannel("A").point(lambda v: int(v * opacity))
    ov.putalpha(alpha)

    out = back.crop((0, 0, size, size)).convert("RGBA")
    out = Image.alpha_composite(out, ov)
    from PIL import ImageDraw
    d = ImageDraw.Draw(out)
    # the layer surface's own bounds: anything outside it is not drawn and not hit
    d.rectangle([0, 0, size - 1, size - 1], outline=(255, 255, 255, 90), width=1)
    rgb = out.convert("RGB")
    sheet = Image.new("RGB", (size, size + LABEL_H), (18, 18, 22))
    sheet.paste(rgb, (0, 0))
    ImageDraw.Draw(sheet).text((8, size + 7), label, fill=(225, 225, 235))
    return sheet


def row(cells: list[Image.Image]) -> Image.Image:
    w = sum(c.width for c in cells) + GAP * (len(cells) - 1)
    h = max(c.height for c in cells)
    out = Image.new("RGB", (w, h), (12, 12, 16))
    x = 0
    for c in cells:
        out.paste(c, (x, 0))
        x += c.width + GAP
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default="/tmp")
    ap.add_argument("--opacity", type=float, default=0.85)
    args = ap.parse_args()

    size = int(BASE * SCALE)
    back = backdrop(size, size)
    os.makedirs(args.out_dir, exist_ok=True)

    q1 = row([
        cell("app", "app", "ice", 8, None, "A  ice ring (current)", args.opacity, back),
        cell("hairline", "app", "ice", 8, None, "B  hairline ring", args.opacity, back),
        cell("notch", "app", "ice", 8, None, "C  corner dots", args.opacity, back),
        cell("spoke", "app", "ice", 8, None, "D  diagonal spokes", args.opacity, back),
        cell("bare", "app", "ice", 8, None, "E  no indicator", args.opacity, back),
    ])
    q2 = row([
        cell("bare", "app", "ice", 4, None, "A  current outline", args.opacity, back),
        cell("bare", "soft", "ice", 4, None, "B  softer outline", args.opacity, back),
        cell("bare", "rounded", "ice", 4, None, "C  rounded arm ends", args.opacity, back),
    ])
    lit = {"N": 1.0}
    q3 = row([
        cell("bare", "app", "ice", 4, lit, "A  ice cyan (current)", args.opacity, back),
        cell("bare", "app", "cool", 4, lit, "B  cool white", args.opacity, back),
        cell("bare", "app", "neutral", 4, lit, "C  neutral brighten", args.opacity, back),
    ])
    # one extra: the two most likely picks working together, plus a diagonal press
    combo = row([
        cell("notch", "soft", "cool", 8, None, "C+B+B resting", args.opacity, back),
        cell("notch", "soft", "cool", 8, {"N": 0.7, "E": 0.7}, "C+B+B pressing NE",
             args.opacity, back),
        cell("spoke", "rounded", "cool", 8, {"N": 1.0}, "D+C+B pressing N",
             args.opacity, back),
    ])

    paths = {}
    for name, img in (("dpad_Q1_indicator", q1), ("dpad_Q2_cross", q2),
                      ("dpad_Q3_press", q3), ("dpad_combo", combo)):
        p = os.path.join(args.out_dir, f"{name}.png")
        img.save(p)
        paths[name] = p
        print(f"  {p}  {img.width}x{img.height}")

    # the clipping claim, measured rather than asserted
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    cr.scale(SCALE, SCALE)
    D.paint(cr, BASE, BASE, {}, 8, style="app")
    surf.flush()
    buf = surf.get_data()
    edge = [buf[i + 3] for y in (0, size - 1) for x in range(size)
            for i in [(y * size + x) * 4]]
    print(f"\n  ice ring at the surface edge (alpha where the frame boundary is): "
          f"max {max(edge) if edge else 0}/255")
    print("  -> the 200-unit ring overflows the 180-unit surface, so it lands as "
          "four cut arcs, and its colour is also the press colour")
    return 0


if __name__ == "__main__":
    sys.exit(main())
