#!/usr/bin/env python3
"""Print the stick response as thumb travel in millimetres, which is the only unit
that means anything when you are deciding whether a curve is too twitchy.

Percentages of a radius are easy to state and impossible to feel. The Z13's panel is
299 x 187 mm, so a logical pixel is 299/1707 mm and the app's 119 px stick is about
21 mm across -- roughly the size of a thumb pad. The table says how far the thumb has
to move, in mm, before the game sees 25 / 50 / 75 / 100 % deflection.

    python3 tools/render_stick_curve.py
    python3 tools/render_stick_curve.py --saturate 0.6 --deadzone 0.05 --curve 0.8
"""
from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad.layer_geom import load_screen_size  # noqa: E402
from glasspad.layer_stick import (  # noqa: E402
    MAX_TRAVEL, PRESETS, StickTuning, deflect,
)

DEFAULT_LAYOUT = os.path.expanduser("~/.config/GlassPad/layout.json")
PANEL_MM_W = 299.0          # ROG Flow Z13 GZ302EA, 16:10 ~13.4"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=119,
                    help="stick control size in logical px (default: the app's 119)")
    ap.add_argument("--screen", default=None, help="SCREEN_WxH for the mm scale")
    ap.add_argument("--layout", default=DEFAULT_LAYOUT)
    ap.add_argument("--saturate", type=float)
    ap.add_argument("--deadzone", type=float)
    ap.add_argument("--curve", type=float)
    args = ap.parse_args()

    sw, _sh = load_screen_size(args.layout) or (1707, 1067)
    if args.screen:
        sw = int(args.screen.lower().split("x")[0])
    mm = (PANEL_MM_W / sw) * args.size / 2.0      # radius, in millimetres
    radius = args.size / 2.0

    print(f"  a {args.size} px stick on a {sw} px-wide screen is "
          f"{(PANEL_MM_W / sw) * args.size:.1f} mm across "
          f"({radius:.1f} px = {mm:.1f} mm of thumb travel to the rim)")
    print()
    print("                                  thumb travel for deflection of")
    print("    tuning                     25%     50%     75%    100%   (mm)")
    print("    " + "-" * 66)

    rows = [(k, v) for k, v in PRESETS.items()]
    if args.saturate is not None or args.deadzone is not None or args.curve is not None:
        base = StickTuning()
        rows.append(("yours", StickTuning(
            args.saturate if args.saturate is not None else base.saturate,
            args.deadzone if args.deadzone is not None else base.deadzone,
            args.curve if args.curve is not None else base.curve).clamp()))

    for name, t in rows:
        cells = []
        for want in (0.25, 0.50, 0.75, 1.0):
            # binary search the travel that first reaches `want`
            lo, hi = 0.0, radius * 1.5
            for _ in range(60):
                mid = (lo + hi) / 2.0
                # 1e-9, not >=: full deflection is full, but comparing a float to 1.0
                # exactly makes the search chase an ulp instead of the saturation point
                if math.hypot(*deflect(mid, 0.0, radius, t)) >= want - 1e-9:
                    hi = mid
                else:
                    lo = mid
            cells.append(f"{hi * (mm / radius):5.1f}")
        tag = "  " + t.describe()
        print(f"    {name:<12}{'':<8}{cells[0]}    {cells[1]}    {cells[2]}    "
              f"{cells[3]}{tag}")

    print()
    print(f"    for reference, a console thumbstick's whole travel is about 7 mm;")
    print(f"    the original's 100 % is at {MAX_TRAVEL * (mm / radius):.1f} mm, and the rim is "
          f"{mm:.1f} mm.")
    print("    glass is slick and a thumb slides further than it intends: if a game")
    print("    registers 'run' too early, move saturation OUT (wide); if it never")
    print("    reaches full, move it IN (crisp).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
