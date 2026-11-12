#!/usr/bin/env python3
"""Render the transcribed Windows styles to a PNG, and verify them numerically.

The point is not the picture -- it is that a shape spec you cannot measure is a
shape spec you cannot trust. Each control is painted to an off-screen surface and
then checked: a circle must have empty corners, a pill must have a filled bar, a
lit d-pad arm must differ from its neighbours, a stick nub must actually move.

Run:  python3 tools/render_style.py [out.png]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairo  # noqa: E402

from glasspad import layer_style as S  # noqa: E402

from PIL import Image  # noqa: E402  (checks only)

W, H = 920, 380


def render(path: str, backdrop: bool = True) -> None:
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(surf)
    # A backdrop makes the preview readable to a person; it also makes the PNG
    # opaque everywhere, which would silently invalidate every alpha assertion
    # below. Checks therefore render with backdrop=False.
    if backdrop:
        cr.set_source_rgb(0.35, 0.37, 0.40)
        cr.paint()

    # 1. pills, at the sizes GamepadLayout uses
    for i, tag in enumerate(("LT", "LB")):
        x, y = 30 + i * 150, 40
        cr.save(); cr.translate(x, y)
        S.paint_pill(cr, 110, 55 if tag == "LT" else 48, S.FILLS[tag], tag)
        cr.restore()

    # 2. face buttons, with the 1.08 press scale on A
    face = {"Y": 0, "X": 1, "B": 2, "A": 3}
    for tag, i in face.items():
        cr.save(); cr.translate(320 + (i % 2) * 85, 30 + (i // 2) * 85)
        S.paint_circle(cr, 75, 75, S.FILLS[tag], tag,
                       scale=S.PRESS_SCALE if tag == "A" else 1.0)
        cr.restore()

    # 3. stick, resting and deflected
    cr.save(); cr.translate(30, 150); S.paint_stick(cr, 119, 119); cr.restore()
    cr.save(); cr.translate(180, 150)
    S.paint_stick(cr, 119, 119, nub_dx=20.0, nub_dy=-20.0); cr.restore()

    # 4. d-pad 4-way with the north arm lit, and 8-way with its ring + two arms
    cr.save(); cr.translate(340, 150)
    S.paint_dpad(cr, 180, 180, arms={"N": 1.0}, mode=4); cr.restore()
    cr.save(); cr.translate(560, 150)
    S.paint_dpad(cr, 180, 180, arms={"N": 0.7, "E": 0.7}, mode=8); cr.restore()

    # 5. hub buttons, at their real canvas offsets scaled into the row
    for i, (name, (colour, glyph, _cx, _cy, _always)) in enumerate(S.HUB_BUTTONS.items()):
        cr.save(); cr.translate(30 + i * 56, 330)
        S.paint_fab(cr, 44, colour, glyph,
                    scale=0.5 if name == "info" else 1.0,
                    alpha=0.5 if name == "info" else 1.0)
        cr.restore()

    # 6. settings panel with pill buttons
    cr.save(); cr.translate(620, 30)
    S.paint_panel_bg(cr, 270, 120)
    cr.translate(16, 12)
    S.paint_panel_pill(cr, 106, 30, S.BTN_ACTIVE, "4-Way")
    cr.translate(114, 0)
    S.paint_panel_pill(cr, 106, 30, S.BTN_INACTIVE, "8-Way")
    cr.translate(-114, 40)
    S.paint_panel_pill(cr, 106, 32, S.BTN_ACTIVE, "\U0001f4be  Save")
    cr.translate(114, 0)
    S.paint_panel_pill(cr, 106, 32, S.BTN_RESET, "\u21ba  Reset")
    cr.restore()

    surf.write_to_png(path)


def one(size: tuple[int, int], draw) -> "Image.Image":
    """Render one control alone, so no neighbour can contaminate a pixel test."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size[0], size[1])
    draw(cairo.Context(surf))
    out = "/tmp/glasspad_one.png"
    surf.write_to_png(out)
    return Image.open(out).convert("RGBA").load()


def check(path: str) -> int:
    fails = []

    def expect(cond, msg):
        print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
        if not cond:
            fails.append(msg)

    # -- circle: a disc, not a square
    px = one((76, 76), lambda cr: S.paint_circle(cr, 75, 75, S.FILLS["A"], "A"))
    row = [x for x in range(76) if px[x, 37][3] > 20]
    expect(px[1, 1][3] == 0, "circle: bounding corner is empty")
    expect(px[37, 37][3] > 0, "circle: centre is painted")
    # inscribed disc of d=75 minus a 1.5 px stroke: the mid-row run is ~73 px wide
    expect(70 <= len(row) <= 75, f"circle: mid-row run is the diameter, not the box ({len(row)})")
    top = [x for x in range(76) if px[x, 6][3] > 20]
    expect(len(top) < len(row) * 0.6,
           f"circle: it is round, not a square (mid row {len(row)} vs near-top {len(top)})")

    # -- pill: rounded bar, filled along its middle
    px = one((111, 56), lambda cr: S.paint_pill(cr, 110, 55, S.FILLS["LT"], "LT"))
    expect(px[1, 1][3] == 0, "pill: corner is empty (radius = h/2)")
    expect(px[55, 27][3] > 0, "pill: middle is painted")

    # -- stick: ring disc + nub that actually travels
    rest = one((120, 120), lambda cr: S.paint_stick(cr, 119, 119))
    moved = one((120, 120), lambda cr: S.paint_stick(cr, 119, 119, nub_dx=20.0, nub_dy=-20.0))
    expect(rest[1, 1][3] == 0, "stick: corner is empty (ring is a disc)")
    expect(rest[59, 59][3] > 0, "stick: centre is painted")
    expect(rest[59 + 20, 59 - 20] != moved[59 + 20, 59 - 20],
           "stick: deflected nub is somewhere other than the resting nub")
    # The nub is a translucent disc over a translucent ring, so the pixel is a
    # composite -- compare it against the ring it replaced rather than against the
    # source colour, which never appears verbatim in the output.
    full = one((120, 120), lambda cr: S.paint_stick(cr, 119, 119,
                                                   nub_dx=S.STICK_MAX_TRAVEL, nub_dy=0))
    # Sample where the deflected nub covers but the resting one does not: the nub
    # is 57 px across and travels 28, so +28 is inside the nub in both states.
    # Centre of the deflected nub is 59.5+28; 47 px out is past the resting nub's
    # 28.5 px radius but inside the deflected one.
    expect(full[59 + 47, 59][0] > rest[59 + 47, 59][0],
           "stick: the nub moved onto pixels the resting nub never touched")
    # Outer ring radius is (119 - 1.5)/2 = 58.75 about 59.5, so x=119 is outside it
    expect(full[119, 59][3] == 0 and full[119, 119][3] == 0,
           "stick: nothing paints outside the outer ring, even at full travel")

    # -- d-pad: cross with notched corners, arms that light up
    dark = one((181, 181), lambda cr: S.paint_dpad(cr, 180, 180))
    lit = one((181, 181), lambda cr: S.paint_dpad(cr, 180, 180, arms={"N": 1.0}))
    expect(dark[5, 5][3] == 0, "d-pad: corner notch is empty")
    expect(dark[175, 5][3] == 0, "d-pad: other corner notch is empty")
    expect(dark[90, 30][3] > 0, "d-pad: north arm is painted")
    expect(lit[90, 30][3] > dark[90, 30][3], "d-pad: lit north arm is more opaque")
    expect(lit[30, 90][3] == dark[30, 90][3], "d-pad: lighting N leaves W untouched")

    # Sample on the diagonal: outside the cross notch in both modes, so any paint
    # there can only be the 200 px 8-way ring.
    ring4 = one((181, 181), lambda cr: S.paint_dpad(cr, 180, 180, mode=4))
    ring8 = one((181, 181), lambda cr: S.paint_dpad(cr, 180, 180, mode=8))
    expect(ring4[160, 160][3] == 0, "d-pad: 4-way has nothing on the diagonal")
    expect(ring8[160, 160][3] > 0, "d-pad: 8-way's wider ring reaches the diagonal")

    # -- hub button
    px = one((45, 45), lambda cr: S.paint_fab(cr, 44, S.HUB_BUTTONS["fab"][0], "\u2261"))
    expect(px[1, 1][3] == 0, "fab: corner is empty")
    expect(px[22, 22][3] > 0, "fab: disc is painted")

    # -- press scale must actually enlarge (1.08 is the Windows value)
    small = one((76, 76), lambda cr: S.paint_circle(cr, 75, 75, S.FILLS["A"], "A"))
    big = one((76, 76), lambda cr: S.paint_circle(cr, 75, 75, S.FILLS["A"], "A",
                                                  scale=S.PRESS_SCALE))
    # NB threshold: the fills are #B3 (alpha 179), so a >200 cutoff would count
    # only strokes and labels and tell you nothing about the shape's area.
    n_small = sum(1 for y in range(76) for x in range(76) if small[x, y][3] > 20)
    n_big = sum(1 for y in range(76) for x in range(76) if big[x, y][3] > 20)
    expect(n_big > n_small, f"press: 1.08 scale covers more pixels ({n_small} -> {n_big})")

    # -- back/start labels: a bare minus and plus, not a box around them
    from glasspad.layer_geom import label_for, label_size

    def glyph_probe(name):
        """Describe the label the painter would actually stroke on this control.

        The disc's stroke is white too, so only pixels inside r=18 count. Returns
        (lit, w, h, box_corners): box_corners counts lit pixels at the corners of the
        glyph's own bounding box, which is what a squared glyph like \u229e is.
        """
        dia = 52
        lbl = label_for(name)
        size = label_size(name, dia, S.CIRCLE_LABEL_RATIO)
        px = one((dia + 1, dia + 1),
                 lambda cr: S.paint_circle(cr, dia, dia, S.FILLS["BACK"], lbl,
                                           text_size=size))
        c = (dia + 1) / 2.0
        pts = [(x, y) for y in range(dia + 1) for x in range(dia + 1)
               if (x - c) ** 2 + (y - c) ** 2 <= 18.0 ** 2
               and all(v > 190 for v in px[x, y][:3])]
        if not pts:
            return 0, 0, 0, 0
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        corners = sum(1 for cc in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))
                      if cc in set(pts))
        return len(pts), x1 - x0 + 1, y1 - y0 + 1, corners

    def raw_glyph(gl, ratio=S.CIRCLE_LABEL_RATIO):
        dia = 52
        px = one((dia + 1, dia + 1),
                 lambda cr: S.paint_circle(cr, dia, dia, S.FILLS["BACK"], gl,
                                           text_size=dia * ratio))
        c = (dia + 1) / 2.0
        pts = [(x, y) for y in range(dia + 1) for x in range(dia + 1)
               if (x - c) ** 2 + (y - c) ** 2 <= 18.0 ** 2
               and all(v > 190 for v in px[x, y][:3])]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return len(pts), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1

    # control case first: the probe must be able to see a box, or "no box" means
    # nothing. Antialiasing costs a corner, hence >= 2 rather than == 4.
    n, gw, gh = raw_glyph("\u229e")
    expect(gh >= 8 and gw >= 8, f"probe sees the app's boxed plus as a box ({gw}x{gh})")
    n_box, _, h_box = raw_glyph("\u229f")
    n2, gw2, gh2, _c = glyph_probe("BACK")
    expect(gh2 <= 5 and h_box >= 8,
           f"BACK paints a bar, not a box (h {gh2} vs the glyph's {h_box})")
    expect(gw2 >= 12 and gh2 >= 3, f"BACK's bar is big enough to see ({gw2}x{gh2})")
    n3, gw3, gh3, corners3 = glyph_probe("START")
    expect(corners3 == 0, f"START has no box corners ({gw3}x{gh3})")
    expect(gh3 >= 10, f"START is a real plus, not a hyphen ({gw3}x{gh3})")
    # More ink than the boxed glyph, and that is the point: ink is not the enemy,
    # clutter is. The claim worth pinning is that it is drawn bigger than the app
    # drew its boxed version, which is what makes it readable at arm's length.
    _, w_box, h_box2 = raw_glyph("\u229e")
    expect(gh3 > h_box2 and gw3 > w_box - 2,
           f"START is drawn larger than the app's boxed glyph "
           f"({gw3}x{gh3} vs {w_box}x{h_box2})")

    # -- panel background must stay inside its own rounded rect
    px = one((271, 121), lambda cr: S.paint_panel_bg(cr, 270, 120))
    expect(px[1, 1][3] == 0, "panel: corner is empty")
    expect(px[2, 60][3] > 0, "panel: interior is painted")

    print(f"\n  {len(fails)} failure(s) -> {path}")
    return 1 if fails else 0


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/glasspad_style.png"
    render(out)
    print(f"  rendered {out}")
    try:
        sys.exit(check(out))
    except ImportError:
        print("  (Pillow missing: rendered without numeric checks)")
        sys.exit(0)
