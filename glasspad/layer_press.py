"""How a button shows that it is being pressed.

The problem this solves
-----------------------
The Windows client scales a button to 1.08 on TouchDown (MainWindow.xaml.cs,
ScaleTransform set outright). That works in WPF because the button sits in a canvas
with room around it. Here every control is its own layer surface sized exactly to
the control's frame, so 1.08 pushes 8 % of the shape past all four edges and
KWin clips it. Measured on a 52-unit circle, the pressed state covers 0.886 of the
surface where a circle covers pi/4 = 0.785, and ink reaches 91.6 % of the way to
the surface corner. Four flat sides appear on a round button: a square block, which
is what it looks like, and the rounded ends of every pill get sawn off the same way.

A highlight drawn as a rectangle over the control has the same problem in a
different form, so nothing in here paints a rectangle that is not a pill.

The modes
---------
    bevel   (default) the key is raised at rest -- light along its top edge, shade
            along its bottom -- and pressing pushes it *inward* to 0.955 while the
            shading flips: shade now along the top, a rim of light along the bottom.
            Inward never clips, the frame size is untouched at rest, and a shading
            flip is far more legible peripherally than a 8 % size change.
    glow    no geometry change at all: a radial brighten from the centre plus a
            brighter rim. For a pad that should not move under the finger.
    outset  the client's 1.08, kept for comparison and knowingly blocky.

`fit()` exists for anyone who wants the client's growing press without the clip:
the base scale that lets a given peak stay inside the frame.
"""
from __future__ import annotations

import cairo

from .layer_style import (
    LABEL_COLOR,
    PRESS_SCALE,
    STROKE_COLOR,
    STROKE_WIDTH,
    _text,
    argb,
)

#: How far each mode moves the shape while pressed.
PEAK_SCALE = {"bevel": 0.955, "glow": 1.0, "outset": PRESS_SCALE}

#: Resting and pressed inner shading, top->bottom, as (colour, alpha) at each end.
BEVEL_REST = ((1.0, 1.0, 1.0, 0.10), (0.0, 0.0, 0.0, 0.16))
BEVEL_PRESS = ((0.0, 0.0, 0.0, 0.20), (1.0, 1.0, 1.0, 0.12))
BEVEL_WASH = (0.0, 0.0, 0.0, 0.06)           # the whole body dims a touch
GLOW_CORE = (1.0, 1.0, 1.0, 0.20)
GLOW_RIM = (1.0, 1.0, 1.0, 0.85)
LABEL_DIM = 0.75                             # glyph alpha multiplier while pressed

MODES = tuple(PEAK_SCALE)


def press_scale(mode: str, press: float = 1.0) -> float:
    """Visual scale for a press amount in 0..1 (0 is at rest)."""
    peak = PEAK_SCALE.get(mode, PEAK_SCALE["bevel"])
    return 1.0 + (peak - 1.0) * max(0.0, min(1.0, press))


def lerp(a: tuple, b: tuple, t: float) -> tuple:
    """Blend two RGBA tuples. The press amount drives everything continuously: a
    value that snaps between two states is how you get a press that reads as a
    blink rather than a movement."""
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(4))


def fit(base: float, peak: float) -> float:
    """The base scale that lets `peak` stay inside the frame.

    A control that grows to `peak` needs to start at 1/peak: peak * (1/peak) = 1.0,
    exactly the frame. Kept for `outset` users who would rather keep the growing
    press than the block -- passing fit(1.0, 1.08) as the resting scale gives an
    8 % grow with nothing clipped, at the cost of a 7 % smaller button at rest.
    """
    return base / max(peak, 1.0)


def _body_path(cr, w: float, h: float, shape: str):
    """The control's outline, inset by half the stroke so the rim itself is inside
    the surface -- the client's own stroke straddles the path, which is why even an
    unpressed control loses the outer half a pixel of its border."""
    inset = STROKE_WIDTH / 2.0
    iw, ih = w - STROKE_WIDTH, h - STROKE_WIDTH
    x0, y0 = inset, inset
    if shape == "circle":
        r = min(iw, ih) / 2.0
        cr.arc(x0 + iw / 2.0, y0 + ih / 2.0, r, 0.0, 6.283185307179586)
        return r
    r = ih / 2.0
    cr.new_sub_path()
    cr.arc(x0 + iw - r, y0 + r, r, -1.5708, 0.0)
    cr.arc(x0 + iw - r, y0 + ih - r, r, 0.0, 1.5708)
    cr.arc(x0 + r, y0 + ih - r, r, 1.5708, 3.14159)
    cr.arc(x0 + r, y0 + r, r, 3.14159, 4.71239)
    cr.close_path()
    return r


def _shaded(cr, w: float, h: float, top: tuple, bottom: tuple) -> None:
    """Clip to whatever path is current and lay a vertical top->bottom shading in."""
    cr.save()
    cr.clip()
    g = cairo.LinearGradient(0.0, 0.0, 0.0, h)
    g.add_color_stop_rgba(0.0, *top)
    g.add_color_stop_rgba(0.46, top[0], top[1], top[2], 0.0)
    g.add_color_stop_rgba(0.54, bottom[0], bottom[1], bottom[2], 0.0)
    g.add_color_stop_rgba(1.0, *bottom)
    cr.set_source(g)
    cr.paint()
    cr.restore()


def paint(cr, w: float, h: float, shape: str, fill: str, label: str, *,
          mode: str = "bevel", press: float = 0.0, scale: float | None = None,
          text_size: float | None = None) -> None:
    """Draw one button/pill control with the chosen press treatment.

    `press` is 0 at rest and 1 fully held; every part of the look -- scale, inner
    shading, wash, glow, glyph dimming -- interpolates along it, so the overlay can
    ease the release without any of the layers disagreeing about how pressed the
    control currently is.
    """
    p = max(0.0, min(1.0, press))
    if scale is None:
        scale = press_scale(mode, p)

    cx, cy = w / 2.0, h / 2.0
    cr.save()
    if scale != 1.0:
        cr.translate(cx, cy)
        cr.scale(scale, scale)
        cr.translate(-cx, -cy)

    if mode == "glow" and p > 0.0:
        # the glow is drawn under the body so the body's own translucency carries it,
        # and it stays inside the frame: radius is the control's radius, not a
        # percentage beyond it
        g = cairo.RadialGradient(cx, cy, 0.0, cx, cy, max(w, h) / 2.0)
        g.add_color_stop_rgba(0.0, GLOW_CORE[0], GLOW_CORE[1], GLOW_CORE[2],
                              GLOW_CORE[3] * p)
        g.add_color_stop_rgba(1.0, GLOW_CORE[0], GLOW_CORE[1], GLOW_CORE[2], 0.0)
        cr.arc(cx, cy, max(w, h) / 2.0, 0.0, 6.283185307179586)
        cr.set_source(g)
        cr.fill()

    _body_path(cr, w, h, shape)
    cr.set_source_rgba(*argb(fill))
    cr.fill_preserve()
    cr.set_line_width(STROKE_WIDTH)
    cr.set_source_rgba(*(lerp(STROKE_COLOR, GLOW_RIM, p)
                         if mode == "glow" else STROKE_COLOR))
    cr.stroke()

    if mode == "bevel":
        top = lerp(BEVEL_REST[0], BEVEL_PRESS[0], p)
        bottom = lerp(BEVEL_REST[1], BEVEL_PRESS[1], p)
        _body_path(cr, w, h, shape)
        _shaded(cr, w, h, top, bottom)
        if p > 0.0:
            # the wash is what says "this key went down" to peripheral vision; the
            # shading flip does the rest up close
            _body_path(cr, w, h, shape)
            cr.set_source_rgba(BEVEL_WASH[0], BEVEL_WASH[1], BEVEL_WASH[2],
                               BEVEL_WASH[3] * p)
            cr.fill()
    elif mode == "glow" and p > 0.0:
        _body_path(cr, w, h, shape)
        cr.set_source_rgba(GLOW_CORE[0], GLOW_CORE[1], GLOW_CORE[2], GLOW_CORE[3] * p)
        cr.fill_preserve()
        cr.set_line_width(STROKE_WIDTH)
        cr.set_source_rgba(*lerp(STROKE_COLOR, GLOW_RIM, p))
        cr.stroke()

    if label:
        size = text_size
        if size is None:
            size = (min(w, h) * 0.32) if shape == "circle" else (h * 0.38)
        r, g, b, a = LABEL_COLOR
        cr.select_font_face("Sans")
        _text(cr, label, size,
              (r, g, b, a * (1.0 + (LABEL_DIM - 1.0) * p)), cx, cy)

    cr.restore()

