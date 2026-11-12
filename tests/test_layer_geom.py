"""Tests for the layer-shell overlay's geometry rules -- no GTK, no Qt.

These exist because the first in-game build was unusable in a way no amount of
staring at the compositor would have explained: the sticks could only reach one
quadrant, and releasing parked them at half deflection. Cause was a units
contract -- PadService takes zone space (-1..1), the overlay was passing 0..1.

Run: python3 tests/test_layer_geom.py     (or: pytest tests/test_layer_geom.py -q)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Skip(Exception):
    """A cross-check that could not run at all (missing toolkit, no display).

    Reported as skipped, never as passed -- a comparison that silently did not
    happen is the worst kind of green.
    """


SKIPPED: list[tuple[str, str]] = []

from glasspad import pad, xd360  # noqa: E402
import tempfile

from glasspad.layer_geom import (  # noqa: E402
    ELEMENT_ORDER,
    Frame,
    default_frames,
    hat_with_hysteresis,
    load_frames,
    stick_deflection,
    to_hat4,
    write_frames,
)


class Recorder(pad.PadBackend):
    """Captures the frame PadService would have written to uinput."""

    def __init__(self) -> None:
        self.last_axes: dict[int, float] = {}
        self.last_buttons: set[str] = set()
        self.last_hat = "None"

    def submit(self, buttons, hat, axes) -> None:
        self.last_buttons = set(buttons)
        self.last_hat = hat
        self.last_axes = dict(axes)

    def close(self) -> None:
        pass


def service():
    rec = Recorder()
    return pad.PadService(rec), rec


def device(code: int, normalised: float) -> int:
    """What the kernel would actually see for one axis of a submitted frame."""
    return xd360.normalise(xd360.AXIS_SPECS[code], normalised)


# -- stick units contract --------------------------------------------------
def test_zone_space_rests_at_device_zero():
    svc, rec = service()
    dx, dy = stick_deflection(48, 48, 48, 48, 96, 96)          # press = centre
    svc.set_left_stick(dx, dy)
    assert device(xd360.ABS_X, rec.last_axes[xd360.ABS_X]) == 0
    assert device(xd360.ABS_Y, rec.last_axes[xd360.ABS_Y]) == 0


def test_negative_half_of_the_axis_is_reachable():
    # The regression: feeding 0..1 into the zone contract made every axis value
    # land in [0, +320]. 672 axis events, not one negative.
    svc, rec = service()
    dx, dy = stick_deflection(0, 0, 48, 48, 96, 96)            # far up-left
    svc.set_left_stick(dx, dy)
    assert device(xd360.ABS_X, rec.last_axes[xd360.ABS_X]) == -320
    assert device(xd360.ABS_Y, rec.last_axes[xd360.ABS_Y]) == -320


def test_full_deflection_reaches_both_rails():
    svc, rec = service()
    for pos, expect in (((96, 96), 320), ((0, 0), -320)):
        dx, dy = stick_deflection(pos[0], pos[1], 48, 48, 96, 96)
        svc.set_right_stick(dx, dy)
        assert device(xd360.ABS_RX, rec.last_axes[xd360.ABS_RX]) == expect
        assert device(xd360.ABS_RY, rec.last_axes[xd360.ABS_RY]) == expect


def test_release_returns_to_centre_not_half_throttle():
    # The line that broke the game: releasing sent 0.5 into a -1..1 contract,
    # which is to_hm(0.5) = +160 -- half deflection, held forever.
    svc, rec = service()
    svc.set_left_stick(*stick_deflection(96, 96, 48, 48, 96, 96))
    assert device(xd360.ABS_X, rec.last_axes[xd360.ABS_X]) == 320
    svc.set_left_stick(0.0, 0.0)
    assert device(xd360.ABS_X, rec.last_axes[xd360.ABS_X]) == 0
    assert device(xd360.ABS_Y, rec.last_axes[xd360.ABS_Y]) == 0


def test_deflection_clamps_at_the_rim():
    assert stick_deflection(9999, -9999, 48, 48, 96, 96) == (1.0, -1.0)


# -- d-pad ----------------------------------------------------------------
def test_hat_boundaries_match_zones():
    for angle, want in ((-90, "N"), (0, "E"), (90, "S"), (180, "W"), (-135, "W"), (-45, "N")):
        assert to_hat4(angle) == want


def test_hysteresis_holds_across_a_boundary():
    prev = "None"
    for angle in (-70, -60, -46, -44, -46, -44, -45):
        prev = hat_with_hysteresis(prev, angle)
    assert prev == "N", "wobble on the N/E boundary must not alternate"


def test_hysteresis_still_switches_when_meant_to():
    prev = hat_with_hysteresis(hat_with_hysteresis("None", -90), -20)
    assert prev == "E"
    assert hat_with_hysteresis(prev, -70) == "N"


# -- layout.json ----------------------------------------------------------
def test_element_order_matches_the_qt_layout():
    """layout.json identifies controls by INDEX alone, so ELEMENT_ORDER is load-
    bearing. This asserts it against the Qt layout itself rather than trusting a
    copy -- the failure mode is a control silently wearing another's rectangle.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])          # noqa: F841
    from glasspad.layout import GamepadLayout

    canvas = QWidget()
    canvas.resize(1280, 800)
    layout = GamepadLayout(canvas)
    layout.build(1280, 800)

    button_tags = [tag for _el, tag in layout.buttons]
    stick_tags = [getattr(el, "name", "dpad") for el in layout.all_editable[len(button_tags):]]
    assert button_tags + stick_tags == [tag for _n, _k, tag in ELEMENT_ORDER]


def test_defaults_are_in_element_order():
    assert [f.name for f in default_frames(1280, 800)] == [n for n, _k, _t in ELEMENT_ORDER]


def test_write_then_load_round_trips():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "layout.json")
        frames = default_frames(1707, 1067)
        write_frames(path, frames, 0.7, 8)
        got, opacity, dpad_mode, note = load_frames(path, 1707, 1067)
        assert got == frames, note
        assert opacity == 0.7
        assert dpad_mode == 8


def test_wrong_element_count_is_refused_not_reassigned():
    # Loading this file would hand every control some other control's rectangle.
    import json

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "layout.json")
        # Written by hand: write_frames() rightly refuses a short list, and the
        # point here is what load_frames() does when it meets one anyway.
        root = {"opacity": 1.0, "dpadMode": 4,
                "elements": [{"left": 10, "top": 10, "width": 50, "height": 50}
                             for _ in range(len(ELEMENT_ORDER) - 1)]}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(root, fh)
        got, _o, _d, note = load_frames(path, 1280, 800)
        assert got == default_frames(1280, 800)
        assert "refusing" in note


def test_malformed_element_is_refused():
    import json

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "layout.json")
        root = {"opacity": 1.0, "dpadMode": 4,
                "elements": [{"left": 10, "top": 10, "width": 50, "height": 50}
                             for _ in ELEMENT_ORDER]}
        del root["elements"][7]["width"]        # the 'A' button loses its size
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(root, fh)
        got, _o, _d, note = load_frames(path, 1280, 800)
        assert got == default_frames(1280, 800)
        assert "'A'" in note


# -- shape gating ---------------------------------------------------------
def test_round_button_refuses_its_own_corner():
    from glasspad.layer_geom import inside_shape

    assert inside_shape("button", 80, 80, 160, 160), "centre must fire"
    assert inside_shape("button", 80, 20, 160, 160), "top of the disc must fire"
    for corner in ((0, 0), (159, 0), (0, 159), (159, 159)):
        assert not inside_shape("button", *corner, 160, 160), f"{corner} must not fire"
    # The exact point KWin handed the surface in the hit test: 102 px out.
    assert not inside_shape("button", 10, 5, 160, 160)


def test_trigger_pill_keeps_its_bar():
    from glasspad.layer_geom import inside_shape

    assert inside_shape("trigger", 55, 27, 110, 55)
    assert inside_shape("trigger", 3, 27, 110, 55), "pill ends must fire"
    assert not inside_shape("trigger", 0, 0, 110, 55)


def test_dpad_is_a_square_and_nothing_is_outside_the_surface():
    from glasspad.layer_geom import inside_shape

    assert inside_shape("dpad", 0, 0, 96, 96)
    assert inside_shape("dpad", 95, 95, 96, 96)
    assert not inside_shape("dpad", 96, 96, 96, 96)


# -- 8-way d-pad ----------------------------------------------------------
def test_hat8_sectors():
    from glasspad.layer_geom import to_hat8

    for angle, want in ((0, "E"), (45, "SE"), (90, "S"), (180, "W"), (-90, "N"), (-45, "NE")):
        assert to_hat8(angle) == want


def test_hat8_hysteresis_holds_then_switches():
    from glasspad.layer_geom import hat_with_hysteresis

    prev = "None"
    for angle in (-90, -68, -66, -68, -66):          # wobble on the N/NE edge (-67.5)
        prev = hat_with_hysteresis(prev, angle, mode=8)
    assert prev == "N", "8-way wobble on a sector edge must not chatter"
    assert hat_with_hysteresis(prev, -40, mode=8) == "NE"


# -- input regions --------------------------------------------------------
def test_circle_region_excludes_the_corners_it_paints_through():
    from glasspad.layer_geom import circle_region

    region = circle_region(160, 160)
    assert region.contains_point(80, 80), "centre must be hittable"
    assert region.num_rectangles() > 0
    for corner in ((0, 0), (159, 0), (0, 159), (159, 159)):
        assert not region.contains_point(*corner), f"{corner} must fall through"
    # The point the old build swallowed: 97 px from the centre of a 78 px disc.
    assert not region.contains_point(14, 9)
    # Just inside the drawn edge stays hittable (radius is 78 after the inset).
    assert region.contains_point(80, 80 - 70)


def test_pill_region_keeps_the_bar_and_drops_the_corners():
    from glasspad.layer_geom import rounded_rect_region

    region = rounded_rect_region(110, 55)
    assert region.contains_point(55, 27)
    assert region.contains_point(5, 27)          # mid-height spans full width
    assert not region.contains_point(0, 0)
    assert not region.contains_point(109, 54)


def test_rect_region_is_the_whole_surface():
    from glasspad.layer_geom import rect_region

    region = rect_region(96, 96)
    assert region.contains_point(0, 0) and region.contains_point(95, 95)


def test_degenerate_shape_is_empty_not_crashing():
    from glasspad.layer_geom import circle_region

    assert circle_region(3, 3).is_empty()


def test_default_geometry_matches_the_qt_layout():
    """The overlay's defaults must be the geometry the user already has.

    Sizes and positions were transcribed from layout.GamepadLayout.build(); this
    pins them, because "exactly like the app" is not something to eyeball. An
    earlier revision used invented 96 px d-pad/stick sizes against the app's 180
    and 119, and nothing failed.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt6.QtWidgets import QApplication, QWidget
    except Exception as exc:                                   # noqa: BLE001
        raise _Skip(f"PyQt6 unavailable: {exc}") from exc

    app = QApplication.instance() or QApplication([])          # noqa: F841
    from glasspad.layout import GamepadLayout

    canvas = QWidget()
    canvas.resize(1280, 800)
    lay = GamepadLayout(canvas)
    lay.build(1280, 800)

    pairs = ([(tag, el) for el, tag in lay.buttons]
             + [(getattr(el, "name", "dpad"), el)
                for el in lay.all_editable[len(lay.buttons):]])

    # Compare by index, translating the Qt widget's printed tag back to the
    # semantic name: back/guide/start are labelled with glyphs (\u229f \u2299 \u229e) rather than
    # words, so matching on the visible text would KeyError on the first of them.
    tag_to_name = {tag: name for name, _kind, tag in ELEMENT_ORDER}
    ours = default_frames(1280, 800)
    assert len(pairs) == len(ours) == 16, f"expected 16 controls, got {len(pairs)}"
    got_order = [tag_to_name.get(t, t) for t, _el in pairs]
    assert got_order == [n for n, _k, _t in ELEMENT_ORDER], \
        f"order differs: {got_order}"

    bad = []
    for (tag, el), f in zip(pairs, ours):
        g = el.geometry()
        delta = max(abs(g.x() - f.x), abs(g.y() - f.y),
                    abs(g.width() - f.w), abs(g.height() - f.h))
        # 0.51, not 0.01: QWidget.geometry() hands back an integer QRect, so a
        # rectangle at 1067.5 is reported as 1068 and rounding accounts for at most
        # half a pixel. Any slack beyond that would let a real offset hide in it --
        # the drift this test exists to catch was 100 px.
        if delta > 0.51:
            bad.append(f"{f.name} ({tag}): qt ({g.x()},{g.y()},{g.width()},{g.height()})"
                       f" vs overlay ({f.x:.1f},{f.y:.1f},{f.w:.1f},{f.h:.1f})  off by {delta:.1f}px")
    assert not bad, "geometry drifted from layout.py:\n    " + "\n    ".join(bad)


def test_stick_travel_clamps_onto_the_stop():
    """StickZone translates then scales back onto the circle; that rule is testable."""
    import math

    from glasspad.layer_geom import clamp_travel

    # StickZone.MaxTravel = (Outer - Inner) / 2 = (119 - 57) / 2 = 28
    dx, dy, norm = clamp_travel(20.0, 0.0, 28.0)
    assert (round(dx, 3), round(dy, 3)) == (20.0, 0.0)
    assert round(norm, 3) == 0.714, norm

    dx, dy, norm = clamp_travel(200.0, 0.0, 28.0)
    assert round(math.hypot(dx, dy), 3) == 28.0
    assert norm > 1.0, "the caller still gets to see how far past the stop it went"

    dx, dy, _ = clamp_travel(100.0, 100.0, 28.0)
    assert round(math.hypot(dx, dy), 3) == 28.0
    assert dx > 0 and dy > 0, "clamping must not collapse the direction"

    assert clamp_travel(0.0, 0.0, 28.0) == (0.0, 0.0, 0.0), "no division by zero at rest"


def test_missing_file_falls_back_to_defaults():
    got, _o, _d, note = load_frames("/nonexistent/GlassPad/layout.json", 1280, 800)
    assert got == default_frames(1280, 800)
    assert note.startswith("no ")



def test_shoulder_buttons_are_pills_not_circles():
    """GamepadLayout.Build() calls Pill() for LT/RT/LB/RB, Circle() for the rest.

    LB/RB are ordinary EV_KEY buttons, so their input kind is BUTTON; an early
    version painted according to kind and drew both shoulder buttons as 48 px
    circles inside their 120 x 48 boxes. Measured on screen: coverage 0.32 where a
    pill measures 0.96.
    """
    from glasspad.layer_geom import render_shape

    assert render_shape("LT", "trigger") == "pill"
    assert render_shape("RT", "trigger") == "pill"
    assert render_shape("LB", "button") == "pill", "LB is a Pill, not a Circle"
    assert render_shape("RB", "button") == "pill", "RB is a Pill, not a Circle"
    for nm in ("A", "B", "X", "Y", "L3", "R3", "BACK", "GUIDE", "START"):
        assert render_shape(nm, "button") == "circle"
    assert render_shape("LS", "stick") == "stick"
    assert render_shape("dpad", "dpad") == "dpad"


def test_centre_cluster_labels_are_legible_inside_a_disc():
    """\u229f / \u229e are squared minus/plus. Kept as identity, dropped from the paint.

    The glyph is what layout.json and the Qt backend address these controls by, so
    ELEMENT_ORDER keeps it. What gets *stroked* is a bare \u2212 and +, because each of
    these already sits inside a 52 px disc and the glyph's own box turns to mud.
    """
    from glasspad.layer_geom import PAINT_LABELS, ELEMENT_ORDER, label_for

    tags = {name: tag for name, _kind, tag in ELEMENT_ORDER}
    assert tags["BACK"] == "\u229f" and tags["START"] == "\u229e", "identity must not drift"

    assert label_for("BACK") == "\u2212"          # minus sign, not a box
    assert label_for("START") == "+"
    assert label_for("GUIDE") == "\u2299"          # dot-in-ring still reads fine
    assert label_for("A") == "A" and label_for("LB") == "LB"
    assert set(PAINT_LABELS) <= set(tags), "only real controls may override their label"


# -- runner ---------------------------------------------------------------
# pytest is not installed here, and "python3 tests/test_layer_geom.py" exiting 0
# while running zero tests is worse than no runner at all: it reports success for
# work it never did. This collects every test_* in module order and says, out loud,
# how many ran.
if __name__ == "__main__":
    import sys as _sys
    import traceback

    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in fns:
        try:
            fn()
            print(f"  ok    {name}")
        except _Skip as skip:
            SKIPPED.append((name, str(skip)))
            print(f"  skip  {name}: {skip}")
        except AssertionError as exc:
            failed.append((name, str(exc)))
            print(f"  FAIL  {name}: {exc}")
        except Exception:                                  # noqa: BLE001
            failed.append((name, traceback.format_exc(limit=2)))
            print(f"  ERROR {name}")
    print(f"\n  {len(fns) - len(failed) - len(SKIPPED)} passed, {len(failed)} failed, "
          f"{len(SKIPPED)} skipped of {len(fns)}")
    for name, why in SKIPPED:
        print(f"    skipped {name}: {why}")
    _sys.exit(1 if failed else 0)