"""d-pad visual variants: how the cross is drawn, and how 8-way is signalled.

Why this exists separately from layer_style.py
----------------------------------------------
layer_style.py is a transcription: every number there cites a file and a line of
the Windows client. The d-pad is the one control where the client's look is the
problem rather than the spec -- in 8-way mode it paints a #00DCFF ring at 60/255
fill and 200/255 stroke, which sits over the middle of the screen for the whole
game. So the variants here are deliberate departures, and they are named so that
"how faithful are we?" stays an answerable question:

    style="app"   the client's look, unchanged (the default in layer_style)
    style="notch" no ring; four faint dots in the corner gaps
    style="spoke" no ring; four faint diagonal bars behind the cross
    style="hairline"
                  the ring, but neutral and one pixel wide
    style="bare"  no 8-way indicator at all

Two facts about the client's ring, both visible in tools/render_dpad.py:

* It is 200 units across (DPAD_RING_SIZE) and the control's surface is 180
  (DpadZone.Base), so s = 1.0 and the ring's radius of 100 overflows a surface
  whose half-width is 90. What ships is not a circle but four arcs, cut off at
  the left, right, top and bottom.
* The ring's colour is also the press colour, so the resting state and the
  feedback state are the same hue -- the overlay cannot both announce 8-way and
  stay quiet, and the client chose to announce.

The press highlight stays separate from all of that: `press=` selects it
independently of `style=`, because feedback wants to be visible even when the
resting state is dim.
"""
from __future__ import annotations

from .layer_style import (
    DPAD_ARM,
    DPAD_ARM_FILL,
    DPAD_ARM_RADIUS,
    DPAD_BASE,
    DPAD_CARDINAL,
    DPAD_DIAGONAL,
    DPAD_FADE_MS,
    DPAD_FILL,
    DPAD_RING_SIZE,
    DPAD_RING_STROKE_W,
    DPAD_STROKE,
    STROKE_WIDTH,
    arm_rects,
    argb,
    cross_polygon,
)

# -- indicator: how 8-way is signalled in the resting state ----------------
#: (r, g, b, a) triples. The dot/spoke alphas are chosen against a mid-tone
#: backdrop: low enough to read as part of the cross, high enough to find with a
#: glance before pressing.
DOT_COLOR = (1.0, 1.0, 1.0, 0.15)
DOT_ACTIVE = (1.0, 1.0, 1.0, 0.42)
DOT_SIZE = 11.0                      # in cross units, of DPAD_BASE
SPOKE_COLOR = (1.0, 1.0, 1.0, 0.11)
SPOKE_ACTIVE = (1.0, 1.0, 1.0, 0.34)
SPOKE_WIDTH = 9.0
RING_ICE_FILL = (0.0, 180 / 255, 1.0, 60 / 255)
RING_ICE_STROKE = (0.0, 220 / 255, 1.0, 200 / 255)
RING_HAIR_FILL = (1.0, 1.0, 1.0, 0.045)
RING_HAIR_STROKE = (1.0, 1.0, 1.0, 0.16)
RING_HAIR_WIDTH = 1.0

INDICATORS = ("ice", "hairline", "notch", "spoke", "none")
STYLE_TO_INDICATOR = {
    "app": "ice",
    "hairline": "hairline",
    "notch": "notch",
    "spoke": "spoke",
    "bare": "none",
}

# -- cross silhouette ------------------------------------------------------
CROSS_STYLES = {
    # the client: #C8404055 fill, white stroke at 180/255
    "app": dict(fill=DPAD_FILL, stroke=DPAD_STROKE, width=STROKE_WIDTH,
                tip_radius=0.0),
    # the same silhouette with the outline turned down; the stroke is what makes
    # the cross look like a sticker rather than part of the scene
    "soft": dict(fill="#C0404055", stroke=(1.0, 1.0, 1.0, 110 / 255),
                 width=1.5, tip_radius=0.0),
    # soft, plus rounded arm tips
    "rounded": dict(fill="#C0404055", stroke=(1.0, 1.0, 1.0, 110 / 255),
                    width=1.5, tip_radius=14.0),
}

# -- press highlight -------------------------------------------------------
PRESS_COLORS = {
    # DpadZone.AddArmHighlight: Color.FromArgb(100, 0, 220, 255)
    "ice": DPAD_ARM_FILL,
    # same brightness, desaturated: still obviously "lit", not cyan signage
    "cool": (205 / 255, 232 / 255, 1.0, 96 / 255),
    # no colour at all: the arm brightens rather than tints
    "neutral": (1.0, 1.0, 1.0, 78 / 255),
}


def _rounded_rect(cr, x, y, w, h, r):
    import math
    r = min(r, w / 2.0, h / 2.0)
    if r <= 0.0:
        cr.rectangle(x, y, w, h)
        return
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def _cross_path(cr, ox, oy, s, tip_radius: float) -> None:
    """The 12-point cross, optionally with the arm ends rounded.

    The cross from DpadZone.CrossGeom has eight convex corners (the ends of the
    four arms) and four concave ones (where the arms meet). Every edge is
    axis-aligned, so both halves are easy: a convex corner is exactly a corner of
    the control's bounding box, and its fillet is a quarter circle tangent to both
    arm edges. A general polygon-offset routine would be the wrong tool here.
    """
    pts = cross_polygon()
    n = len(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)

    def on_bbox(x, y) -> bool:
        return x in (lo_x, hi_x) or y in (lo_y, hi_y)

    if tip_radius <= 0.0:
        cr.move_to(ox + pts[0][0] * s, oy + pts[0][1] * s)
        for x, y in pts[1:]:
            cr.line_to(ox + x * s, oy + y * s)
        cr.close_path()
        return

    import math
    r = min(tip_radius, DPAD_ARM / 2.0)
    started = False
    for i in range(n):
        prev_, p, next_ = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        if not on_bbox(*p):
            if not started:
                cr.move_to(ox + p[0] * s, oy + p[1] * s)
                started = True
            else:
                cr.line_to(ox + p[0] * s, oy + p[1] * s)
            continue
        def unit(ax, ay, bx, by):
            dx, dy = bx - ax, by - ay
            ln = (dx * dx + dy * dy) ** 0.5 or 1.0
            return dx / ln, dy / ln
        u1x, u1y = unit(*prev_, *p)          # arriving at p
        u2x, u2y = unit(*p, *next_)          # leaving p
        rr = min(r, ((p[0] - prev_[0]) ** 2 + (p[1] - prev_[1]) ** 2) ** 0.5 / 2.0,
                 ((next_[0] - p[0]) ** 2 + (next_[1] - p[1]) ** 2) ** 0.5 / 2.0)
        a = (p[0] - u1x * rr, p[1] - u1y * rr)
        b = (p[0] + u2x * rr, p[1] + u2y * rr)
        c = (a[0] + b[0] - p[0], a[1] + b[1] - p[1])
        a0 = math.atan2(a[1] - c[1], a[0] - c[0])
        a1 = math.atan2(b[1] - c[1], b[0] - c[0])
        # pick the sweep that bulges toward p rather than away from it; deriving the
        # sign from the polygon winding is a coin flip that goes wrong the day the
        # geometry is touched, and a wrong one draws a bite instead of a corner
        def mid(sweep):
            da = (a1 - a0) % (2 * math.pi) if sweep > 0 else -((a0 - a1) % (2 * math.pi))
            m = a0 + da / 2.0
            return ((c[0] + rr * math.cos(m) - p[0]) ** 2
                    + (c[1] + rr * math.sin(m) - p[1]) ** 2)
        sweep = 1 if mid(1) <= mid(-1) else -1
        if not started:
            cr.move_to(ox + a[0] * s, oy + a[1] * s)
            started = True
        else:
            cr.line_to(ox + a[0] * s, oy + a[1] * s)
        if sweep > 0:
            cr.arc(ox + c[0] * s, oy + c[1] * s, rr * s, a0, a1)
        else:
            cr.arc_negative(ox + c[0] * s, oy + c[1] * s, rr * s, a0, a1)
    cr.close_path()


def _gap_centres() -> list[tuple[float, float, str]]:
    """Centres of the four empty corner squares, with the arms each diagonal needs.

    With Base 180 and BaseArm 60 the arms occupy the middle cross band, so each
    corner gap is a 60x60 square -- the NE one spanning x 120..180, y 0..60, centred
    on (150, 30). That is where an 8-way affordance belongs: in the space the cross
    leaves empty, not painted over the cross.
    """
    c = (DPAD_BASE - DPAD_ARM) / 2.0
    mx = c + DPAD_ARM
    east, west = (mx + DPAD_BASE) / 2.0, c / 2.0
    north, south = c / 2.0, (mx + DPAD_BASE) / 2.0
    return [(east, north, "NE"), (east, south, "SE"),
            (west, north, "NW"), (west, south, "SW")]


def _pair_for(tag: str) -> tuple[str, str]:
    return {"NE": ("N", "E"), "NW": ("N", "W"),
            "SE": ("S", "E"), "SW": ("S", "W")}[tag]


def paint(cr, w: float, h: float, arms: dict[str, float] | None = None,
          mode: int = 4, style: str = "app", cross: str = "app",
          press: str = "ice", scale: float = 1.0, alpha: float = 1.0) -> None:
    """Draw the d-pad. `style` picks the 8-way indicator, `cross` the silhouette,
    `press` the lit-arm colour."""
    spec = CROSS_STYLES.get(cross, CROSS_STYLES["app"])
    indicator = STYLE_TO_INDICATOR.get(style, style if style in INDICATORS else "ice")
    arms = arms or {}
    s = min(w, h) / DPAD_BASE
    cx, cy = w / 2.0, h / 2.0
    ox, oy = cx - DPAD_BASE * s / 2.0, cy - DPAD_BASE * s / 2.0

    if scale != 1.0:
        cr.translate(cx, cy)
        cr.scale(scale, scale)
        cr.translate(-cx, -cy)

    if mode == 8:
        if indicator == "ice":
            cr.arc(cx, cy, DPAD_RING_SIZE * s / 2.0, 0.0, 6.28319)
            cr.set_source_rgba(*RING_ICE_FILL)
            cr.fill_preserve()
            cr.set_line_width(DPAD_RING_STROKE_W * s)
            cr.set_source_rgba(*RING_ICE_STROKE)
            cr.stroke()
        elif indicator == "hairline":
            # inscribed, not copied: the client asks for a 200-unit ring inside a
            # 180-unit surface and gets four cut arcs. A ring worth keeping has to
            # fit the surface it is drawn on.
            r = min(w, h) / 2.0 - RING_HAIR_WIDTH
            cr.arc(cx, cy, max(r, 1.0), 0.0, 6.28319)
            cr.set_source_rgba(*RING_HAIR_FILL)
            cr.fill_preserve()
            cr.set_line_width(RING_HAIR_WIDTH)
            cr.set_source_rgba(*RING_HAIR_STROKE)
            cr.stroke()
        elif indicator == "spoke":
            cr.save()
            cr.translate(cx, cy)
            for rot in (0.7853981633974483, -0.7853981633974483):
                cr.save()
                cr.rotate(rot)
                active = _any_diagonal(arms, rot)
                cr.rectangle(-DPAD_BASE * s / 2.0, -SPOKE_WIDTH * s / 2.0,
                             DPAD_BASE * s, SPOKE_WIDTH * s)
                cr.set_source_rgba(*(SPOKE_ACTIVE if active else SPOKE_COLOR))
                cr.fill()
                cr.restore()
            cr.restore()
        # dots are drawn after the cross, so they sit in the gaps rather than under it

    cr.new_path()
    _cross_path(cr, ox, oy, s, spec["tip_radius"])
    cr.set_source_rgba(*argb(spec["fill"]))
    cr.fill_preserve()
    cr.set_line_width(spec["width"])
    cr.set_source_rgba(*spec["stroke"])
    cr.stroke()

    if mode == 8 and indicator == "notch":
        for gx, gy, tag in _gap_centres():
            on = _both_lit(arms, _pair_for(tag))
            r = DOT_SIZE * s / 2.0
            px, py = ox + gx * s, oy + gy * s
            cr.save()
            cr.translate(px, py)
            cr.rotate(0.7853981633974483)
            cr.rectangle(-r, -r, 2 * r, 2 * r)
            cr.set_source_rgba(*(DOT_ACTIVE if on else DOT_COLOR))
            cr.fill()
            cr.restore()

    pr, pg, pb, pa = PRESS_COLORS.get(press, PRESS_COLORS["ice"])
    for key, (rx, ry, rw, rh) in arm_rects().items():
        level = arms.get(key, 0.0)
        if level <= 0.0:
            continue
        _rounded_rect(cr, ox + rx * s, oy + ry * s, rw * s, rh * s,
                      DPAD_ARM_RADIUS * s)
        cr.set_source_rgba(pr, pg, pb, pa * level)
        cr.fill()

    # `alpha` is accepted for symmetry with layer_style's painters and is applied
    # by the surface itself (Overlay sets win.set_opacity once, for every control);
    # painting the context here would flood the whole surface with the last fill.


def _both_lit(arms: dict[str, float], pair: tuple[str, str]) -> bool:
    return arms.get(pair[0], 0.0) > 0.0 and arms.get(pair[1], 0.0) > 0.0


def _any_diagonal(arms: dict[str, float], rot: float) -> bool:
    """True when a diagonal that uses this spoke is currently held."""
    pairs = (("N", "E"), ("S", "E")) if rot > 0 else (("N", "W"), ("S", "W"))
    return any(arms.get(a, 0.0) > 0.0 and arms.get(b, 0.0) > 0.0 for a, b in pairs)


__all__ = ["paint", "CROSS_STYLES", "PRESS_COLORS", "INDICATORS",
           "STYLE_TO_INDICATOR", "DPAD_CARDINAL", "DPAD_DIAGONAL", "DPAD_FADE_MS"]
