#!/usr/bin/env python3
"""Offscreen checks for the d-pad variants in glasspad/layer_dpad.py.

Every assertion here is about pixels, because the whole point of the variants is
what they leave unpainted. Run directly: python3 tests/test_dpad_variants.py
"""
from __future__ import annotations

import os
import sys

import cairo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import layer_dpad as DP  # noqa: E402

BASE = 180            # DpadZone.Base, and the layout's d-pad frame
S = 2                 # measurement scale
GRID = 4              # sample every 4 device px


def overlay(*, arms=None, mode=8, style="app", cross="app", press="ice"):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, BASE * S, BASE * S)
    cr = cairo.Context(surf)
    cr.scale(S, S)
    DP.paint(cr, BASE, BASE, arms or {}, mode, style=style, cross=cross, press=press)
    surf.flush()
    buf = surf.get_data()
    return {(x, y): buf[(y * BASE * S + x) * 4 + 3]
            for y in range(0, BASE * S, GRID) for x in range(0, BASE * S, GRID)}


def ink(mask):
    return sum(1 for v in mask.values() if v > 8)


def at(mask, lx, ly):
    """Alpha at a logical coordinate."""
    return mask[(int(lx * S) // GRID * GRID, int(ly * S) // GRID * GRID)]


# the four empty corner squares, and a point on each arm's end
GAPS = [(150, 30), (150, 150), (30, 30), (30, 150)]


def test_indicator_ink_budget():
    """The quieter designs must paint less than the client's ring, in that order."""
    bare = ink(overlay(style="bare"))
    notch = ink(overlay(style="notch"))
    spoke = ink(overlay(style="spoke"))
    ice = ink(overlay(style="app"))
    assert bare > 1000, "the cross itself should still be painted"
    assert ice - bare > 0.2 * bare, f"ice ring should add a lot of ink ({ice} vs {bare})"
    assert notch - bare < 0.06 * bare, f"dots should be nearly free ({notch} vs {bare})"
    assert spoke - bare < 0.15 * bare, f"spokes should stay modest ({spoke} vs {bare})"


def test_dots_live_in_the_gaps_only():
    """Dots go where the cross leaves room; `bare` leaves those pixels empty."""
    bare = overlay(style="bare")
    notch = overlay(style="notch")
    for gx, gy in GAPS:
        assert at(bare, gx, gy) == 0, f"bare painted the gap at {gx},{gy}"
        assert at(notch, gx, gy) > 20, f"no dot at {gx},{gy}"


def test_dot_brightens_only_for_its_own_diagonal():
    rest = overlay(style="notch")
    ne = overlay(style="notch", arms={"N": 0.7, "E": 0.7})
    assert at(ne, 150, 30) > at(rest, 150, 30) + 25, "pressed diagonal should light"
    for gx, gy in GAPS[1:]:
        assert at(ne, gx, gy) == at(rest, gx, gy), f"unrelated dot lit at {gx},{gy}"
    # a cardinal press lights no dot: it is not a diagonal
    n_only = overlay(style="notch", arms={"N": 1.0})
    for gx, gy in GAPS:
        assert at(n_only, gx, gy) == at(rest, gx, gy), "cardinal press lit a dot"


def test_client_ring_is_clipped_and_hairline_is_not():
    """The claim about the client's geometry, kept honest by a measurement.

    DPAD_RING_SIZE (200) over DPAD_BASE (180): the ring crosses the surface edge,
    so it renders at full strength right up to the last pixel of the boundary. The
    hairline variant is inscribed instead, and must not.
    """
    ice = overlay(style="app")
    hair = overlay(style="hairline")
    bare = overlay(style="bare")
    # the top edge, in the columns the cross does not reach: x 44..58. The N arm
    # spans x 60..120, so anything painted at x < 60 on y == 0 is the ring, and the
    # ring can only be there at all by crossing the boundary.
    cols = [x for x in range(int(44 * S), int(58 * S), GRID)]
    assert max(bare[(x, 0)] for x in cols) == 0, "the cross itself should not reach x<60"
    assert max(ice[(x, 0)] for x in cols) > 150, \
        "expected the client's ring to be cut by the top edge"
    assert max(hair[(x, 0)] for x in cols) == 0, \
        "hairline ring should sit inside the surface, not cross its edge"


def test_rounded_cross_removes_a_little_and_not_a_lot():
    sharp = ink(overlay(mode=4, style="bare", cross="soft"))
    rounded = ink(overlay(mode=4, style="bare", cross="rounded"))
    lost = (sharp - rounded) / sharp
    assert 0.005 < lost < 0.05, f"fillets removed {lost:.1%} of the cross"
    # the corner itself is gone, and the middle of the arm end is untouched
    r = overlay(mode=4, style="bare", cross="rounded")
    assert at(r, 119, 1) == 0, "arm-end corner should be filleted away"
    assert at(r, 90, 1) > 150, "middle of the arm end should still be solid"


def test_press_colours_are_distinct_and_visible():
    """Whatever the resting state does, a held arm has to read as held."""
    lit = {k: overlay(mode=4, style="bare", press=k, arms={"N": 1.0})
           for k in DP.PRESS_COLORS}
    for name, mask in lit.items():
        assert mask[(int(90 * S), int(30 * S))] > 150, f"{name} press invisible"
    # ice is a hue, neutral is a lift: their rgb at the lit arm must differ
    seen = {}
    for name in DP.PRESS_COLORS:
        cr_surf = cairo.ImageSurface(cairo.FORMAT_RGB24, 1, 1)
        cr = cairo.Context(cr_surf)
        cr.set_source_rgba(*DP.PRESS_COLORS[name])
        cr.paint()
        cr_surf.flush()
        b, g, r, _a = cr_surf.get_data()
        seen[name] = (r, g, b)
    assert len(set(seen.values())) == len(seen), f"press colours collide: {seen}"
    assert seen["ice"][2] > seen["neutral"][2], "ice should carry blue that neutral lacks"


def test_every_style_paints_in_both_modes():
    for style in DP.STYLE_TO_INDICATOR:
        for cross in DP.CROSS_STYLES:
            for press in DP.PRESS_COLORS:
                for mode in (4, 8):
                    m = overlay(style=style, cross=cross, press=press, mode=mode,
                                arms={"N": 1.0, "E": 0.7})
                    assert ink(m) > 1000, f"{style}/{cross}/{press}/{mode} painted nothing"


def test_unknown_names_fall_back_rather_than_crashing():
    """A bad value from a flag or an env var must degrade to something visible."""
    for style in ("notch-not-a-style", ""):
        assert ink(overlay(style=style, mode=8)) > 1000
    assert ink(overlay(mode=8, cross="nope", press="nope")) > 1000


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok    {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
    print(f"\n  {len(tests) - failed} passed, {failed} failed of {len(tests)}")
    sys.exit(1 if failed else 0)
