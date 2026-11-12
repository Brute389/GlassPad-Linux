#!/usr/bin/env python3
"""Edge bands: refuse the bezel, never the button.

The failure mode this guards is a silent one -- a rejection rule that swallows
legitimate presses reads as a broken overlay, and nothing else in the test suite would
notice. So the load-bearing assertions are about the *real* frames: the app's own
defaults, this machine's saved layout, and the defaults after a rotation. Run directly:
python3 tests/test_edge.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad.layer_edge import (  # noqa: E402
    DEFAULT_MARGIN, MAX_FRACTION, MAX_MARGIN, applied, bands, clamp_margin, describe,
    refuses,
)
from glasspad.layer_geom import default_frames, fit_frames, load_frames  # noqa: E402
from glasspad.layer_geom import ELEMENT_ORDER  # noqa: E402

LAYOUT = os.path.expanduser("~/.config/GlassPad/layout.json")
SW, SH = 1707, 1067


def _tuples(frames):
    return [(f.name, f.x, f.y, f.w, f.h) for f in frames]


# ------------------------------------------------------------- the layout is untouched

def test_the_default_margin_touches_nothing_in_the_app_layout():
    fr = default_frames(SW, SH)
    assert applied(fr, (SW, SH), DEFAULT_MARGIN) == {},  f"the default margin carves up {applied(fr, (SW, SH), DEFAULT_MARGIN)}"
    assert "no control" in describe(fr, (SW, SH), DEFAULT_MARGIN)


def test_it_touches_nothing_in_the_saved_layout_either():
    if not os.path.exists(LAYOUT):
        print("      (no saved layout here; skipped)")
        return
    fr = load_frames(LAYOUT, SW, SH)[0]
    assert applied(fr, (SW, SH), DEFAULT_MARGIN) == {}


def test_and_it_touches_nothing_after_a_rotation():
    """Rotation clamps frames to stay on screen; 'stay on screen' must not mean flush."""
    fr = default_frames(SW, SH)
    for sw, sh in ((1067, 1707), (1707, 1067), (800, 1280)):
        rotated = fit_frames(fr, SW, SH, sw, sh)
        assert applied(rotated, (sw, sh), DEFAULT_MARGIN) == {},  f"{sw}x{sh}: {applied(rotated, (sw, sh), DEFAULT_MARGIN)}"


def test_a_flush_control_keeps_three_quarters_of_its_face():
    """The safety rule. A band that disables a button is worse than no band."""
    for size in (119.0, 75.0, 40.0, 32.0):
        frame = (0.0, 500.0, size, size)
        left = bands(frame, (SW, SH), DEFAULT_MARGIN)[0]
        assert left <= size * MAX_FRACTION + 1e-9,  f"a {size}px control lost {left}px of its left edge"
        # and the centre of that control is always pressable
        assert refuses(size / 2.0, size / 2.0, frame, (SW, SH), DEFAULT_MARGIN) is None


# ------------------------------------------------------------------- the gate itself

def test_only_the_outer_band_is_refused():
    frame = (0.0, 500.0, 119.0, 119.0)
    assert refuses(2.0, 60.0, frame, (SW, SH), DEFAULT_MARGIN) == "left"
    assert refuses(DEFAULT_MARGIN - 1, 60.0, frame, (SW, SH), DEFAULT_MARGIN) == "left"
    assert refuses(DEFAULT_MARGIN + 1, 60.0, frame, (SW, SH), DEFAULT_MARGIN) is None
    assert refuses(60.0, 60.0, frame, (SW, SH), DEFAULT_MARGIN) is None


def test_all_four_edges_and_the_middle_of_a_flush_control():
    m = DEFAULT_MARGIN
    cases = {
        "left":   ((0.0, 500.0, 119.0, 119.0), (2.0, 60.0)),
        "right":  ((SW - 119.0, 500.0, 119.0, 119.0), (117.0, 60.0)),
        "top":    ((500.0, 0.0, 119.0, 119.0), (60.0, 2.0)),
        "bottom": ((500.0, SH - 119.0, 119.0, 119.0), (60.0, 117.0)),
    }
    for want, (frame, pt) in cases.items():
        assert refuses(pt[0], pt[1], frame, (SW, SH), m) == want
    # the same control's middle is fine at every edge
    for frame in (c[0] for c in cases.values()):
        assert refuses(frame[2] / 2.0, frame[3] / 2.0, frame, (SW, SH), m) is None


def test_zero_margin_refuses_anything_nowhere():
    for frame in ((0.0, 0.0, 119.0, 119.0), (SW - 119.0, SH - 119.0, 119.0, 119.0)):
        for x in (0.0, 1.0, 60.0):
            assert refuses(x, x, frame, (SW, SH), 0.0) is None
    assert bands((0.0, 0.0, 119.0, 119.0), (SW, SH), 0.0) == (0.0, 0.0, 0.0, 0.0)


def test_a_huge_margin_still_leaves_the_face_usable():
    """Someone will ask for 64. The cap is what stops that being a mass disable."""
    frame = (0.0, 0.0, 75.0, 75.0)
    l, _r, t, _b = bands(frame, (SW, SH), MAX_MARGIN)
    assert l == 75.0 * MAX_FRACTION and t == 75.0 * MAX_FRACTION
    assert refuses(37.0, 37.0, frame, (SW, SH), MAX_MARGIN) is None


def test_a_control_a_whole_screen_away_refuses_nothing():
    frame = (600.0, 400.0, 119.0, 119.0)
    for x in range(0, 119, 7):
        assert refuses(x, 59, frame, (SW, SH), MAX_MARGIN) is None,  f"refused x={x} on a control that is nowhere near an edge"


def test_corner_contact_has_one_deterministic_reason():
    frame = (0.0, 0.0, 119.0, 119.0)
    reasons = {refuses(1.0, 1.0, frame, (SW, SH), DEFAULT_MARGIN) for _ in range(50)}
    assert reasons == {"left"}, reasons


def test_margin_junk_falls_back_to_the_default_not_to_zero():
    """Zero means off. Reading junk as zero would switch the protection off silently."""
    assert clamp_margin(None) == DEFAULT_MARGIN
    assert clamp_margin("wide") == DEFAULT_MARGIN
    assert clamp_margin(float("nan")) == DEFAULT_MARGIN
    assert clamp_margin(float("inf")) == DEFAULT_MARGIN
    assert clamp_margin(-10) == 0.0 and clamp_margin(10_000) == MAX_MARGIN
    assert clamp_margin(16) == 16.0


def test_degenerate_frames_are_never_refused():
    for frame in ((0.0, 0.0, 0.0, 0.0), (0.0, 0.0, -5.0, 10.0)):
        assert bands(frame, (SW, SH), DEFAULT_MARGIN) == (0.0, 0.0, 0.0, 0.0)
        assert refuses(0.0, 0.0, frame, (SW, SH), DEFAULT_MARGIN) is None


def test_applied_accepts_frames_and_plain_tuples_alike():
    fr = default_frames(SW, SH)
    flush = [(fr[0].name, 0.0, 500.0, 119.0, 119.0)]
    assert set(applied(flush, (SW, SH), DEFAULT_MARGIN)) == {fr[0].name}
    assert len(ELEMENT_ORDER) == 16      # the frames above are indexed by position


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"    ok    {name}")
            except Exception as exc:                       # noqa: BLE001
                fails += 1
                import traceback
                print(f"    FAIL  {name}: {type(exc).__name__}: {exc}")
                if os.environ.get("VERBOSE"):
                    traceback.print_exc()
    total = sum(1 for n, f in globals().items()
                if n.startswith("test_") and callable(f))
    print(f"\n    {total - fails} passed, {fails} failed of {total}")
    sys.exit(1 if fails else 0)
