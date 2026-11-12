#!/usr/bin/env python3
"""Fitting a layout to a different screen: the maths, the file field, the wiring.

Rotation on this tablet changes 1707x1067 into 1067x1707, which is not a resize of
anything the layout vocabulary describes: the numbers are absolute pixels, and the
Windows client never recorded which screen they were absolute *pixels of*. So this
tests three things separately -- that the fit itself is sane, that the design size
survives a save/load, and that apply_screen drives the surfaces it is given.

The live end of it (does KWin actually tell us) was measured with a real
kscreen-doctor rotation, and the selftest drives apply_screen against real layer
surfaces. Neither is claimed here.

Run directly: python3 tests/test_fit_screen.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad.layer_geom import (  # noqa: E402
    EDGE_ANCHOR,
    Frame,
    ELEMENT_ORDER,
    default_frames,
    fit_frames,
    load_screen_size,
    write_frames,
)

SW, SH = 1707, 1067          # this tablet's logical size at 1.5 scale


def test_the_same_screen_is_the_identity_exactly():
    """No drift when nothing changed: not a pixel, not a rounding step."""
    frames = default_frames(SW, SH)
    out = fit_frames(frames, SW, SH, SW, SH)
    for a, b in zip(frames, out):
        assert (a.x, a.y, a.w, a.h) == (b.x, b.y, b.w, b.h), f"{a.name} moved on itself"


def test_sizes_scale_uniformly_so_nothing_becomes_an_ellipse():
    frames = [Frame("A", "button", 100.0, 100.0, 80.0, 80.0)]
    # Twice as wide, same height: the height constraint binds, so the square stays
    # 80x80. Stretching it to 160x80 is exactly what non-uniform scaling does, and it
    # would disagree with the round hit region and with the pad the button drives.
    out = fit_frames(frames, 1000.0, 1000.0, 2000.0, 1000.0)
    assert (out[0].w, out[0].h) == (80.0, 80.0), f"became {out[0].w}x{out[0].h}"
    out = fit_frames(frames, 1000.0, 1000.0, 2000.0, 2000.0)      # both axes doubled
    assert (out[0].w, out[0].h) == (160.0, 160.0)
    # portrait on this tablet: min(0.625, 1.6) -> 0.625, and nothing is stretched
    out = fit_frames(frames, 1707.0, 1067.0, 1067.0, 1707.0)
    assert abs(out[0].w - out[0].h) < 0.001, "the fit stretched the control"


def test_a_control_against_an_edge_stays_against_that_edge():
    """Within EDGE_ANCHOR of the border means it was put there to be reachable."""
    left = Frame("LB", "trigger", 12.0, 30.0, 110.0, 55.0)
    right = Frame("RB", "trigger", SW - 122.0, 30.0, 110.0, 55.0)
    out = fit_frames([left, right], SW, SH, 1067, 1707)
    scale = 1067 / SW
    assert out[0].x == left.x * scale, "a left-anchored control drifted off the left edge"
    gap_right = 1067 - (out[1].x + out[1].w)
    assert abs(gap_right - (SW - (right.x + right.w)) * scale) < 0.01, f"a right-anchored control lost its edge: gap {gap_right:.1f}"


def test_a_control_in_the_middle_keeps_its_fraction_of_the_span():
    w, h = 60.0, 60.0
    mid = Frame("A", "button", 400.0, 500.0, w, h)
    out = fit_frames([mid], 1600.0, 1000.0, 800.0, 500.0)
    want_frac = (mid.x + w / 2) / 1600.0
    got_frac = (out[0].x + out[0].w / 2) / 800.0
    assert abs(want_frac - got_frac) < 0.001, f"centre moved from {want_frac:.3f}"


def test_nothing_ends_up_off_the_rotated_screen():
    frames = default_frames(SW, SH)
    for (tw, th) in ((1067, 1707), (800, 1280), (1920, 1200), (500, 400)):
        out = fit_frames(frames, SW, SH, tw, th)
        for f in out:
            assert f.x >= -0.001 and f.y >= -0.001, f"{f.name} went negative on {tw}x{th}"
            assert f.x + f.w <= tw + 0.001, f"{f.name} hung off the right on {tw}x{th}"
            assert f.y + f.h <= th + 0.001, f"{f.name} hung off the bottom on {tw}x{th}"


def test_rotating_away_and_back_returns_the_layout_exactly():
    """The property the whole design is for.

    Fitting is always done from the authored geometry, never from the current one, so
    a round trip is exact. Fit-the-fitted instead and every rotation multiplies the
    rounding error, and a tablet that is rotated daily walks its controls off screen.
    """
    frames = default_frames(SW, SH)
    port = fit_frames(frames, SW, SH, 1067, 1707)
    back = fit_frames(frames, SW, SH, SW, SH)          # from base, as apply_screen does
    for a, b in zip(frames, back):
        assert (a.x, a.y, a.w, a.h) == (b.x, b.y, b.w, b.h)
    # and the naive version really would drift, which is why this is worth testing
    naive = fit_frames(port, 1067, 1707, SW, SH)
    worst = max(abs(a.w - n.w) for a, n in zip(frames, naive))
    assert worst > 0.001, "the drift this guards against is not reachable?"


def test_a_control_too_big_for_the_destination_is_clamped_not_hidden():
    frames = [Frame("dpad", "dpad", 0.0, 300.0, 180.0, 180.0)]
    out = fit_frames(frames, 1707.0, 1067.0, 60.0, 900.0)
    f = out[0]
    assert f.x == 0.0 and f.w <= 60.0 + 0.001, f"{f.w:.1f} wide on a 60 px screen"


def test_nonsense_screens_are_refused_without_losing_the_layout():
    frames = default_frames(SW, SH)
    for bad in ((0, 100), (100, 0), (-5, 500)):
        out = fit_frames(frames, *bad, 1000, 800)
        assert [f.name for f in out] == [f.name for f in frames]


# ------------------------------------------------------------------ the file field

def test_the_design_size_round_trips_and_absence_is_respected():
    frames = default_frames(SW, SH)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "layout.json")
        write_frames(p, frames, 0.85, 4, screen=(SW, SH))
        assert load_screen_size(p) == (SW, SH), "design size did not survive the save"
        root = json.load(open(p))
        assert root["screenWidth"] == SW and root["screenHeight"] == SH

        write_frames(p, frames, 0.85, 4)              # not given: no field, as before
        assert "screenWidth" not in json.load(open(p)), "invented a design size"
        assert load_screen_size(p) is None, "a file that predates the field must be taken at face value"


def test_a_broken_design_size_is_none_not_an_exception():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "layout.json")
        for bad in ({"opacity": 1}, {"screenWidth": 0, "screenHeight": 0},
                    {"screenWidth": "wide", "screenHeight": "tall"},
                    {"screenWidth": None, "screenHeight": None}):
            json.dump(bad, open(p, "w"))
            assert load_screen_size(p) is None, f"{bad} was not rejected"
        assert load_screen_size(os.path.join(d, "nope.json")) is None


# ----------------------------------------------------------------------- the wiring

def make_overlay():
    from glasspad import overlay_layer as OL

    ov = OL.Overlay.__new__(OL.Overlay)
    ov.geo = {}
    ov.surfaces = {}
    ov.base_frames = [Frame("A", "button", 1494.0, 600.0, 75.0, 75.0),
                      Frame("dpad", "dpad", 85.0, 464.84, 180.0, 180.0)]
    for f in ov.base_frames:
        ov.geo[f.name] = [f.x, f.y, f.w, f.h]
    ov.design_screen = (SW, SH)
    ov.sw, ov.sh = SW, SH
    ov._dirty = False
    ov.asked: list = []
    ov.placed: list = []
    ov.said: list = []
    ov.surfaces = {f.name: object() for f in ov.base_frames}
    ov._resize_surface = lambda name, w, h: ov.asked.append((name, round(w), round(h)))
    ov.place = lambda win, x, y: ov.placed.append((x, y))
    ov.trace = False
    ov._place_hub = lambda: None
    ov._say = ov.said.append
    return ov


def test_apply_screen_asks_for_the_new_size_and_position_of_everything():
    ov = make_overlay()
    assert ov.apply_screen(1067, 1707, reason="test")
    assert sorted(n for n, _w, _h in ov.asked) == ["A", "dpad"], "not everything resized"
    assert len(ov.placed) == len(ov.base_frames), "not everything re-placed"
    a = ov.geo["A"]
    assert abs(a[2] - 75.0 * (1067 / SW)) < 0.01, f"A was not scaled: {a}"
    assert ov.screen() == (1067, 1707)
    assert ov._dirty, "a relayout that is never saved is a relayout nobody wanted"
    assert any("test" in m for m in ov.said)


def test_apply_screen_does_nothing_when_nothing_changed():
    ov = make_overlay()
    assert not ov.apply_screen(SW, SH), "reported a change with the same screen"
    assert ov.asked == [], "resized every surface for no reason"
    assert ov.apply_screen(SW, SH, force=True), "force must still be able to re-apply"


def test_a_second_rotation_relayouts_from_the_authored_geometry_not_the_last_one():
    """The same guarantee as the maths test, checked through the wiring."""
    ov = make_overlay()
    ov.apply_screen(1067, 1707)
    ov.apply_screen(SW, SH)
    a = ov.geo["A"]
    assert a == [1494.0, 600.0, 75.0, 75.0], f"A came back as {a}"


def test_a_bogus_screen_is_refused():
    ov = make_overlay()
    assert not ov.apply_screen(0, 0)
    assert not ov.apply_screen(-100, 800)
    assert ov.screen() == (SW, SH), "the overlay adopted a screen that does not exist"


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
