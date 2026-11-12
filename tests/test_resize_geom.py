#!/usr/bin/env python3
"""Edit-mode resizing: geometry, the grab decision, and the state machine.

The geometry is pure and testable; the surface work is not, so the overlay-level
tests stub `_resize_surface` and `place` and assert on what the overlay *asks for*.
Whether a mapped layer surface really changes size in place is a compositor
question, and it was measured rather than assumed -- see tools/resize_probe.py.

Run directly: python3 tests/test_resize_geom.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import overlay_layer as OL  # noqa: E402
from glasspad.layer_geom import (  # noqa: E402
    GRAB_ZONE,
    grip_zone,
    MIN_CONTROL,
    grip_centre,
    on_grip,
    size_from_drag,
)

SW, SH = 1707, 1067          # this machine's logical screen, from a real session


# -- the grab decision -----------------------------------------------------
def test_only_the_bottom_right_corner_sizes():
    w = h = 75
    assert on_grip(w - 1, h - 1, w, h), "the corner itself must size"
    assert on_grip(w - GRAB_ZONE + 1, h - GRAB_ZONE + 1, w, h), "the zone's inner edge"
    assert not on_grip(1, 1, w, h), "the opposite corner must move"
    assert not on_grip(w / 2, h / 2, w, h), "the middle must move"
    # a corner alone is not enough: it has to be the bottom-right one
    assert not on_grip(w - 1, 1, w, h), "top-right must move"
    assert not on_grip(1, h - 1, w, h), "bottom-left must move"


def test_grip_zone_is_usable_on_the_smallest_controls():
    """The zone must not swallow a control that is already at its minimum size."""
    m = MIN_CONTROL
    assert on_grip(m - 1, m - 1, m, m)
    assert not on_grip(0, 0, m, m)
    # at least three quarters of the face must remain a move target at the minimum,
    # or the smallest controls cannot be repositioned at all
    moves = sum(1 for y in range(0, int(m), 2) for x in range(0, int(m), 2)
                if not on_grip(x, y, m, m))
    total = len(range(0, int(m), 2)) ** 2
    assert moves > 0.7 * total, f"at the minimum only {moves / total:.0%} of the face moves"
    # and the zone never collapses to nothing, or the knob becomes unusable
    assert grip_zone(8, 8) >= 8.0, "the zone vanished on a tiny control"
    assert grip_zone(400, 400) == GRAB_ZONE, "a big control should get the full zone"


# -- the sizing ------------------------------------------------------------
def test_aspect_is_kept_for_every_shape():
    for w, h in ((75, 75), (110, 48), (180, 180), (119, 119), (40, 30)):
        for dx, dy in ((30, 0), (0, 30), (30, 30), (-10, -4), (5, -5)):
            nw, nh = size_from_drag(w, h, dx, dy)
            assert abs(nw / nh - w / h) < 1e-6, f"{w}x{h} lost its proportions"


def test_the_dominant_axis_wins():
    """A mostly-horizontal drag grows the control even if the finger wobbles."""
    nw, _nh = size_from_drag(100, 100, 40, 2)
    assert nw > 139, f"a wobble should not cancel the drag ({nw:.1f})"


def test_minimum_is_respected_on_both_axes():
    for w, h in ((75, 75), (200, 40)):
        nw, nh = size_from_drag(w, h, -5000, -5000)
        assert nw >= MIN_CONTROL - 1e-6 and nh >= MIN_CONTROL - 1e-6, \
            f"{w}x{h} shrank past the minimum to {nw:.1f}x{nh:.1f}"


def test_a_control_below_the_minimum_is_left_alone_until_you_drag():
    """The client's own BACK/GUIDE/START pills are 30 px, below the 32 px floor.

    The floor is about what you may drag *to*. Applying it to what you grabbed means
    touching the knob rewrites the layout before the finger has moved.
    """
    for w, h in ((110.0, 30.0), (30.0, 30.0)):
        nw, nh = size_from_drag(w, h, 0.0, 0.0)
        assert (nw, nh) == (w, h), f"a 30 px control jumped to {nw:.0f}x{nh:.0f}"
    # shrinking it is still refused, though
    nw, nh = size_from_drag(30.0, 30.0, -40.0, -40.0)
    assert (nw, nh) == (30.0, 30.0), "a below-floor control was shrunk further"
    # growing it is allowed, and the floor then applies normally
    nw, nh = size_from_drag(30.0, 30.0, 20.0, 20.0)
    assert nw > 45.0, f"a below-floor control would not grow ({nw:.1f})"


def test_screen_bounds_are_respected():
    # a control near the right edge cannot be grown off screen
    x, y, w, h = SW - 200, 900, 120, 120
    nw, nh = size_from_drag(w, h, 5000, 5000, max_w=SW - x, max_h=SH - y)
    assert x + nw <= SW + 1e-6, f"grew through the right edge: {x + nw:.0f} > {SW}"
    assert y + nh <= SH + 1e-6, f"grew through the bottom edge: {y + nh:.0f} > {SH}"
    assert abs(nw / nh - w / h) < 1e-6, "clamping to the screen distorted the control"


def test_rest_always_sits_inside_the_allowed_range():
    """The clamp can never make a control jump the moment you touch the knob.

    At rest a control is on screen and above the minimum, so scale 1.0 must already
    be inside [lo, hi]; otherwise the first pixel of drag would resize it.
    """
    for x, y, w, h in ((0, 0, 75, 75), (SW - 76, SH - 76, 75, 75), (400, 300, 30, 30)):
        nw, nh = size_from_drag(w, h, 0.0, 0.0, max_w=SW - x, max_h=SH - y)
        assert abs(nw - w) < 1e-9 and abs(nh - h) < 1e-9, \
            f"a zero drag changed the size: {w}x{h} -> {nw:.1f}x{nh:.1f}"


def test_grip_knob_is_fully_inside_the_frame():
    """The surface IS the frame, so a knob centred on the corner is drawn as a
    quarter circle. It has to be inset by its own radius."""
    for w, h in ((75, 75), (110, 48), (32, 32)):
        cx, cy = grip_centre(w, h)
        r = 13.0 / 2.0
        assert cx + r <= w and cy + r <= h and cx >= r and cy >= r, \
            f"knob at {cx:.0f},{cy:.0f} escapes a {w}x{h} frame"


# -- the overlay's edit state machine -------------------------------------
class FakeService:
    def press_button(self, n): pass
    def release_button(self, n): pass
    def set_left_trigger(self, v): pass
    def set_right_trigger(self, v): pass
    def set_left_stick(self, x, y): pass
    def set_right_stick(self, x, y): pass
    def set_dpad(self, d): pass
    def reset(self): pass
    def init(self, *a, **k): pass
    def dispose(self): pass


def make_overlay(geom):
    ov = OL.Overlay(FakeService(), 0.85)
    ov.loop = None
    ov.edit = True
    ov.sw, ov.sh = SW, SH
    ov.geo = {k: list(v) for k, v in geom.items()}
    ov.surfaces = {k: object() for k in geom}
    class FakeArea:
        def queue_draw(self): pass

    ov.areas = {k: FakeArea() for k in geom}
    ov.requested = {}
    placed = []

    def _resize(name, w, h):
        ov.requested[name] = (int(round(w)), int(round(h)))

    ov._resize_surface = _resize
    ov.place = lambda win, x, y: placed.append((round(x), round(y)))
    ov._placed = placed
    ov._hud_show = lambda name: None
    ov._hud_hide = lambda delay_ms=0: None
    ov._say = lambda text: None
    return ov


def test_corner_drag_sizes_and_anchors_the_top_left():
    ov = make_overlay({"A": [400.0, 300.0, 75.0, 75.0]})
    ov._edit_begin("A", 74.0, 74.0)
    assert ov._grab["A"]["mode"] == "size"
    ov._edit_update("A", 45.0, 45.0)
    x, y, w, h = ov.geo["A"]
    assert (x, y) == (400.0, 300.0), "the anchor corner moved"
    assert w == 120.0 and h == 120.0, f"expected 120x120, got {w}x{h}"
    assert ov.requested["A"] == (120, 120), "the surface was not asked to resize"
    assert ov._placed and ov._placed[-1] == (400, 300), "surface not re-placed at the anchor"


def test_body_drag_moves_without_touching_the_size():
    ov = make_overlay({"A": [400.0, 300.0, 75.0, 75.0]})
    ov._edit_begin("A", 10.0, 10.0)
    assert ov._grab["A"]["mode"] == "move"
    ov._edit_update("A", 60.0, -30.0)
    x, y, w, h = ov.geo["A"]
    assert (x, y) == (460.0, 270.0), f"did not move as dragged: {(x, y)}"
    assert (w, h) == (75.0, 75.0), "a move changed the size"
    assert not ov.requested, "a move should not resize the surface"


def test_move_is_clamped_to_the_screen():
    ov = make_overlay({"A": [40.0, 40.0, 75.0, 75.0]})
    ov._edit_begin("A", 10.0, 10.0)
    ov._edit_update("A", -9000.0, -9000.0)
    assert ov.geo["A"][0] == 0.0 and ov.geo["A"][1] == 0.0, "moved off the top-left"
    ov._edit_update("A", 90000.0, 90000.0)
    assert ov.geo["A"][0] <= SW - 75 and ov.geo["A"][1] <= SH - 75, "moved off the bottom-right"


def test_sizing_state_is_cleaned_up_on_release():
    ov = make_overlay({"A": [400.0, 300.0, 75.0, 75.0]})
    ov._edit_begin("A", 74.0, 74.0)
    assert ov._sizing == "A"
    ov._edit_end("A")
    assert ov._sizing is None, "the overlay still thinks a control is being sized"
    assert "A" not in ov._grab


def test_cancel_is_treated_as_a_release():
    """KWin can cancel a gesture at any moment; a stuck sizing state would leave the
    next drag resizing a control nobody grabbed."""
    ov = make_overlay({"A": [400.0, 300.0, 75.0, 75.0]})
    ov._edit_begin("A", 74.0, 74.0)
    ov._edit_end("A")
    ov._edit_begin("A", 10.0, 10.0)          # a fresh touch on the body
    assert ov._grab["A"]["mode"] == "move", "the previous sizing leaked into a new drag"


def test_reset_restores_size_as_well_as_position():
    """Reset used to write the default geometry and leave the surface alone.

    A control's size *is* its surface's size, so the pad went on drawing -- and
    swallowing taps -- at the size Reset was meant to have thrown away, while the
    file a save would write said something different.
    """
    from glasspad.layer_geom import default_frames

    ov = make_overlay({"A": [400.0, 300.0, 145.0, 145.0],
                       "LB": [40.0, 35.0, 110.0, 55.0]})
    want = {f.name: (f.w, f.h) for f in default_frames(SW, SH)}
    ov._reset_geometry()
    assert ov.geo["A"][2] == want["A"][0], "geometry not restored"
    assert ov.requested.get("A") == (round(want["A"][0]), round(want["A"][1])), \
        f"surface not resized back: {ov.requested.get('A')} want {want['A']}"
    assert ov.requested.get("LB") == (round(want["LB"][0]), round(want["LB"][1]))


def test_reset_touches_every_control_not_just_the_ones_it_thinks_changed():
    from glasspad.layer_geom import default_frames

    geom = {f.name: [f.x, f.y, f.w, f.h] for f in default_frames(SW, SH)}
    assert len(geom) == 16, f"expected the full pad, got {len(geom)} controls"
    ov = make_overlay(geom)
    ov._reset_geometry()
    assert set(ov.requested) == set(geom), \
        f"reset skipped {sorted(set(geom) - set(ov.requested))}"


def test_dirty_flag_is_set_so_saving_happens():
    ov = make_overlay({"A": [400.0, 300.0, 75.0, 75.0]})
    ov._dirty = False
    ov._edit_begin("A", 74.0, 74.0)
    ov._edit_update("A", 20.0, 20.0)
    assert ov._dirty is True, "a resize will not be offered for saving"


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
        except Exception as exc:                       # noqa: BLE001
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n  {len(tests) - failed} passed, {failed} failed of {len(tests)}")
    sys.exit(1 if failed else 0)
