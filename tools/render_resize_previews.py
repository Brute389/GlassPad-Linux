#!/usr/bin/env python3
"""Preview sheet: how should edit-mode resizing be grabbed?

Renders the four affordances at real size over the same mid-tone backdrop used for
the d-pad and press choices, drawn with the same painters the overlay uses, so what
is chosen is what ships.

    python3 tools/render_resize_previews.py
"""
from __future__ import annotations

import math
import os
import sys

import cairo
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import layer_press as LP  # noqa: E402
from tools.render_dpad import backdrop  # noqa: E402

SCALE = 1.5
CELL = 190          # logical px per cell
GAP = 14
LABEL_H = 34
CTRL = 75           # the A/B/X/Y size in the default layout
GRIP = 13.0


def grips(cr, w, h, which="all", colour=(1, 1, 1, 0.9)):
    """Corner knobs plus a dashed outline: the classic 'selected, can be sized' cue."""
    cr.save()
    cr.set_dash([4.0, 3.0], 0.0)
    cr.set_line_width(1.0)
    cr.set_source_rgba(1, 1, 1, 0.35)
    cr.rectangle(0.5, 0.5, w - 1, h - 1)
    cr.stroke()
    cr.set_dash([], 0)
    pts = {"nw": (0, 0), "ne": (w, 0), "sw": (0, h), "se": (w, h)}
    for tag, (px, py) in pts.items():
        if which != "all" and tag not in which:
            continue
        r = GRIP / 2.0
        cr.arc(px, py, r, 0, 6.2832)
        cr.set_source_rgba(*colour)
        cr.fill()
        cr.set_line_width(1.5)
        cr.set_source_rgba(0.1, 0.35, 0.25, 0.95)
        cr.stroke()
    cr.restore()


def pinch_marks(cr, w, h):
    """Two fingers, drawn as a pair of chevrons along the diagonal."""
    cr.save()
    cr.set_line_width(2.0)
    cr.set_source_rgba(1, 1, 1, 0.75)
    for sx, sy in ((1, 1), (-1, -1)):
        cx, cy = w / 2 + sx * (w * 0.34), h / 2 + sy * (h * 0.34)
        ax, ay = w / 2 + sx * (w * 0.52), h / 2 + sy * (h * 0.52)
        cr.move_to(cx, cy)
        cr.line_to(ax, ay)
        cr.stroke()
        # arrow head
        dx, dy = ax - cx, ay - cy
        ln = math.hypot(dx, dy) or 1.0
        ux, uy = dx / ln, dy / ln
        px_, py_ = -uy, ux
        cr.move_to(ax, ay)
        cr.line_to(ax - ux * 7 + px_ * 5, ay - uy * 7 + py_ * 5)
        cr.line_to(ax - ux * 7 - px_ * 5, ay - uy * 7 - py_ * 5)
        cr.close_path()
        cr.set_source_rgba(1, 1, 1, 0.75)
        cr.fill()
    cr.restore()


def badge(cr, w, h, text):
    """A size readout pinned under the control."""
    cr.select_font_face("Sans")
    cr.set_font_size(11)
    ext = cr.text_extents(text)
    bw, bh = ext.width + 12, 18
    x, y = (w - bw) / 2.0, h + 4
    cr.round_rect(x, y, bw, bh, 5) if hasattr(cr, "round_rect") else cr.rectangle(x, y, bw, bh)
    cr.set_source_rgba(0.05, 0.06, 0.10, 0.85)
    cr.fill()
    cr.set_source_rgba(1, 1, 1, 0.95)
    cr.move_to(x + 6 - ext.x_bearing, y + 13 - ext.y_bearing)
    cr.show_text(text)


def cell(kind, caption, back):
    size = int(CELL * SCALE)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    cr.scale(SCALE, SCALE)
    cr.translate((CELL - CTRL) / 2.0, (CELL - CTRL) / 2.0 - 8)
    LP.paint(cr, CTRL, CTRL, "circle", "#963070A0", "A", mode="bevel", press=0.0)
    if kind == "four":
        grips(cr, CTRL, CTRL, "all")
    elif kind == "one":
        grips(cr, CTRL, CTRL, ("se",))
    elif kind == "pinch":
        pinch_marks(cr, CTRL, CTRL)
        grips(cr, CTRL, CTRL, ("se",))
    elif kind == "badge":
        grips(cr, CTRL, CTRL, ("se",))
        badge(cr, CTRL, CTRL, "75 x 75")
    cr.show_page()
    surf.flush()
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
    out = Image.alpha_composite(back.convert("RGBA"), ov)
    sheet = Image.new("RGB", (size, size + LABEL_H), (18, 18, 22))
    sheet.paste(out.convert("RGB"), (0, 0))
    ImageDraw.Draw(sheet).text((8, size + 9), caption, fill=(228, 228, 238))
    return sheet


def main() -> int:
    back = backdrop(int(CELL * SCALE), int(CELL * SCALE))
    cells = [
        cell("four", "A  four corner grips", back),
        cell("one", "B  one corner grip (SE)", back),
        cell("pinch", "C  pinch, with a grip for small buttons", back),
        cell("badge", "D  grip plus a live size readout", back),
    ]
    w = sum(c.width for c in cells) + int(GAP * SCALE) * (len(cells) - 1)
    h = max(c.height for c in cells)
    out = Image.new("RGB", (w, h), (12, 12, 16))
    x = 0
    for c in cells:
        out.paste(c, (x, 0))
        x += c.width + int(GAP * SCALE)
    path = "/tmp/resize_options.png"
    out.save(path)
    print(f"  {path}  {out.width}x{out.height}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
