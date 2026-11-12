#!/usr/bin/env python3
"""Check what the overlay actually painted, by differencing two screenshots.

Take one screenshot with the overlay running and one without, and the differing
pixels are the overlay -- an exact coverage mask, independent of what it was
composited over. Coverage then distinguishes shapes that look alike in a
description and not alike in fact:

    disc      pi/4            = 0.785 of its bounding box
    d-pad     cross on 180    = 0.556
    rounded pill  r=h/2       = 0.948  (rectangle minus 4*(1-pi/4) corner areas)
    square    1.000

So "the d-pad came out as a square" is a measurable failure, and so is "the sticks
rendered at 96 px when the app draws 119": the mask's bounding box says so.

Usage:
    python3 tools/verify_screen.py BEFORE_AFTER... --after AFTER2 ...

    python3 tools/verify_screen.py --before b1.png b2.png --after a1.png a2.png

Two screenshots per state, not one. The first version of this tool took one of
each and asserted the centre of the screen was untouched -- and it "failed", at
0.64 coverage, until two screenshots taken minutes apart with the overlay not
running at all differed in exactly the same diffuse way. The wallpaper here is a
slideshow, and it cross-fades. So the second shot of each state measures what the
desktop does by itself, and that noise is subtracted before anything is concluded.
"""
from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from glasspad import layer_style as S  # noqa: E402
from glasspad.layer_geom import default_frames, render_shape  # noqa: E402


def ideal_fraction(shape: str, w: float, h: float) -> float:
    """Fraction of its own bounding box that each figure paints.

    A pill is NOT "a rectangle with two round ends at half the area": with r = h/2
    it loses only the four corner slivers of the two end squares, which on a 110x55
    trigger is 5% of the box, not 43%. Getting this wrong turns a correct render
    into a reported failure, which is how the first run of this tool behaved.
    """
    if shape == "circle" or shape == "stick":
        return math.pi / 4.0
    if shape == "dpad":
        return (S.DPAD_BASE ** 2 - 4 * S.DPAD_ARM ** 2) / S.DPAD_BASE ** 2
    corner_loss = (1.0 - math.pi / 4.0) * (2 * (h / 2.0) ** 2) / (w * h)
    return 1.0 - corner_loss


def mask(before: str, after: str, threshold: int = 14):
    """Coverage mask: pixels the overlay changed, as a 0/255 image.

    ImageChops rather than a Python pixel loop -- 4 M pixels through a generator is
    tens of seconds per screenshot, and the max-across-channels step has to be a
    real max (lighter), not convert("L"), which is a weighted sum and would let a
    channel that changed a lot hide inside one that did not.
    """
    from PIL import ImageChops

    a = Image.open(before).convert("RGB")
    b = Image.open(after).convert("RGB")
    if a.size != b.size:
        raise SystemExit(f"sizes differ: {a.size} vs {b.size}")
    r, g, bl = ImageChops.difference(a, b).split()[:3]
    mx = ImageChops.lighter(ImageChops.lighter(r, g), bl)
    return mx.point(lambda v: 255 if v > threshold else 0), a.size


def region_stats(mimg, box, scale):
    x0, y0, x1, y1 = [int(round(v * scale)) for v in box]
    w, h = mimg.size
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, w), min(y1, h)
    if x1 <= x0 or y1 <= y0:
        return 0.0, 0.0, 0.0
    crop = mimg.crop((x0, y0, x1, y1))
    hist = crop.histogram()          # 256 bins; bin 0 is "unchanged"
    hit = sum(hist[1:])
    cov = hit / float((x1 - x0) * (y1 - y0))
    if not hit:
        return 0.0, 0.0, 0.0
    bbox = crop.getbbox()            # painted extent within the frame
    ext = ((bbox[2] - bbox[0]) / scale, (bbox[3] - bbox[1]) / scale)
    return cov, ext[0], ext[1]


def paint_mask(before: list[str], after: list[str], threshold: int = 8):
    """Pixels the overlay changed, with desktop noise removed.

    painted = diff(before, after) AND NOT diff(before, before2) AND NOT diff(after, after2)

    A pixel that moved on its own in either state tells you nothing about the
    overlay, and on a desktop with an animated wallpaper or a slideshow that is most
    of them.
    """
    from PIL import ImageChops

    m = mask(before[0], after[0], threshold)[0]
    noise = []
    if len(before) > 1:
        noise.append(mask(before[0], before[1], threshold)[0])
    if len(after) > 1:
        noise.append(mask(after[0], after[1], threshold)[0])
    for n in noise:
        # keep pixels that differ now but did not differ between two shots of a
        # single state:  subtract  =>  min(painted, 255 - noise)
        m = ImageChops.darker(m, ImageChops.invert(n))
    return m, m.size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", nargs="+", required=True, help="1-2 shots, overlay NOT running")
    ap.add_argument("--after", nargs="+", required=True, help="1-2 shots, overlay running")
    ap.add_argument("--screen", default="1707x1067", help="logical screen size")
    ap.add_argument("--cov-tol", type=float, default=0.10)
    # 4, not 8. The stick ring is #B8202030 at 0.72, times the layout's 0.85
    # opacity, so over a dark wallpaper it can move a pixel by only five or six
    # levels: at threshold 8 the sticks measured 0.64/0.56 against a true 0.785 and
    # the tool reported a shape defect that did not exist. At 4 they measure
    # 0.799/0.799, and the opaque controls are unmoved (A reads 0.791 either way).
    ap.add_argument("--threshold", type=int, default=4)
    ap.add_argument("--size-tol", type=float, default=6.0, help="logical px")
    args = ap.parse_args()

    sw, sh = (int(v) for v in args.screen.lower().split("x"))
    rows, (pw, ph) = paint_mask(args.before, args.after, args.threshold)
    scale = pw / sw
    print(f"  screen {sw}x{sh} logical, {pw}x{ph} px -> scale {scale:.3f}")

    fails = 0
    for f in default_frames(sw, sh):
        box = (f.x, f.y, f.x + f.w, f.y + f.h)
        cov, ex, ey = region_stats(rows, box, scale)
        shape = render_shape(f.name, f.kind)
        want = ideal_fraction(shape, f.w, f.h)
        ok_cov = abs(cov - want) <= args.cov_tol
        ok_size = abs(ex - f.w) <= args.size_tol and abs(ey - f.h) <= args.size_tol
        flag = "PASS" if (ok_cov and ok_size) else "FAIL"
        if flag == "FAIL":
            fails += 1
        print(f"  {flag}  {f.name:<6} {shape:<7}"
              f" coverage {cov:.3f} (want {want:.3f})"
              f"  painted {ex:.0f}x{ey:.0f} (want {f.w:.0f}x{f.h:.0f})")

    # Somewhere with no control in it should show no paint from the overlay.
    # Reported, not failed: on this machine Plasma repaints wide bands of the
    # desktop whenever any layer surface maps -- measured with the ORIGINAL hub
    # build as well as the current one, and with two no-overlay shots minutes apart,
    # so it is not ours. Pass-through itself was proven in-game by tools/layer_probe
    # (taps between surfaces reached the game); a screenshot cannot prove a negative
    # about input anyway, only about paint, and it does that per control above.
    cx0, cy0 = sw * 0.40, sh * 0.40
    cov, _ex, _ey = region_stats(rows, (cx0, cy0, sw * 0.60, sh * 0.60), scale)
    print(f"  {'PASS' if cov < 0.005 else 'warn'}  bare band  coverage {cov:.4f}"
          f"  (0 expected from the overlay; >0 is desktop repaint, not a failure)")

    print(f"\n  {fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
