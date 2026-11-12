#!/usr/bin/env python3
"""Feedback the player can actually perceive: the toast, and the autofire mark.

Both exist because the feature they belong to was complete on paper and invisible in
play. The watchdog's "released A after 8s held" and the turbo arm state were both
going somewhere the user never looks -- stdout, and an edit-mode knob.

Timers and surfaces are stubbed and recorded, so these assert on what the overlay
*asks for*: which surface, what size, which moment. The generation guard on the toast
fade is the interesting one -- without it an older message's timer hides the newer
message that replaced it.

Run directly: python3 tests/test_toast.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gi.repository import GLib  # noqa: E402
import cairo  # noqa: E402

from glasspad import overlay_layer as OL  # noqa: E402


class FakeWin:
    def __init__(self):
        self.visible = False
        self.sizes: list[tuple] = []

    def get_visible(self):
        return self.visible

    def present(self):
        self.visible = True

    def set_visible(self, v):
        self.visible = bool(v)

    def set_default_size(self, w, h):
        self.sizes.append((w, h))

    def set_size_request(self, *_a):
        pass


class FakeArea:
    def __init__(self):
        self.draws = 0
        self.content = (0, 0)

    def queue_draw(self):
        self.draws += 1

    def set_content_width(self, w):
        self.content = (w, self.content[1])

    def set_content_height(self, h):
        self.content = (self.content[0], h)


def make_overlay():
    ov = OL.Overlay.__new__(OL.Overlay)
    ov.sw, ov.sh = 1707, 1067
    ov.hud = None
    ov.hud_area = None
    ov._hud_text = ""
    ov._hud_size = None
    ov._hud_gen = 0
    ov._sizing = None
    ov.menu_open = False
    ov.geo = {"A": [1494.0, 600.0, 75.0, 75.0]}
    ov.vis = {}
    ov.areas = {}
    ov.trace = False
    ov.placed: list[tuple] = []
    ov.toast_calls: list[tuple] = []
    ov.place = lambda win, x, y: ov.placed.append((x, y))
    ov._hub_bbox = lambda _open: (780.0, 12.0, 160.0, 44.0)      # top centre
    ov._make_menu_surface = lambda: None
    # the real _hud_ensure needs GTK; give the code a surface to talk to instead
    def ensure():
        if ov.hud is None:
            ov.hud, ov.hud_area = FakeWin(), FakeArea()
        return ov.hud
    ov._hud_ensure = ensure
    # capture the fade timers instead of registering them
    ov.timers: list[tuple] = []
    ov._timeouts = GLib.timeout_add
    GLib.timeout_add = lambda ms, fn, *a: ov.timers.append((ms, fn)) or 1
    ov._restore = lambda: setattr(GLib, "timeout_add", ov._timeouts)
    return ov


# ------------------------------------------------------------------------ the toast

def test_a_status_message_reaches_the_screen_and_the_terminal():
    ov = make_overlay()
    try:
        ov._say("watchdog released A")
        assert ov.hud is not None, "the message went to stdout and nowhere else"
        assert ov.hud.get_visible(), "the toast surface was never presented"
        assert ov._hud_text == "watchdog released A"
    finally:
        ov._restore()


def test_motion_messages_do_not_hijack_the_surface_from_the_readout():
    """Every motion event used to print a size; toasting those replaces a stable
    readout with a flicker."""
    ov = make_overlay()
    try:
        ov._say_quiet("A  145x145")
        assert ov.hud is None, "the per-motion message took over the toast surface"
    finally:
        ov._restore()


def test_the_toast_surface_is_sized_to_the_text():
    ov = make_overlay()
    try:
        ov._toast("A")
        first = ov.hud.sizes[-1]
        ov._toast("released LT, A after 8s held (stuck input?)")
        second = ov.hud.sizes[-1]
        assert second[0] > first[0], f"a long message got the short surface: {second}"
    finally:
        ov._restore()


def test_the_toast_clears_the_hub_and_stays_on_the_screen():
    ov = make_overlay()
    try:
        ov._toast("hello")
        x, y = ov.placed[-1]
        bx, by, bw, bh = ov._hub_bbox(False)
        iw, ih = ov._hud_size
        assert x >= 0.0 and y >= 0.0, f"placed off screen at {x},{y}"
        assert x + iw <= ov.sw and y + ih <= ov.sh, f"hangs off the edge: {x},{y}"
        assert y >= by + bh, "the toast sits on top of the hub it reports on"
        # a hub at the very bottom leaves no room below, so it must go above
        ov._hub_bbox = lambda _open: (10.0, float(ov.sh) - 60.0, 200.0, 44.0)
        ov._toast("hub at the bottom")
        x2, y2 = ov.placed[-1]
        assert y2 + ih <= ov.sh, f"fell off the bottom: {y2}"
        assert y2 < ov.sh - 60.0, "still stacked on the hub when there was no room"
    finally:
        ov._restore()


def test_an_older_toast_cannot_hide_a_newer_one():
    """The generation guard. Each timer compares against the counter it was born with."""
    ov = make_overlay()
    try:
        ov._toast("first message")
        assert ov.timers, "no fade was scheduled"
        ov._toast("second message")
        _ms, first_fade = ov.timers[0]
        assert first_fade() is False, "the fade re-registered itself"
        assert ov.hud.get_visible(), "the first message's timer hid the second one"
        _ms2, last_fade = ov.timers[-1]
        assert last_fade() is False
        assert not ov.hud.get_visible(), "the live toast never went away"
    finally:
        ov._restore()


def test_a_resize_in_flight_owns_the_surface():
    ov = make_overlay()
    try:
        ov._toast("first")
        ov._sizing = "A"                     # the drag is on the knob right now
        hud = ov.hud
        ov._toast("something said mid-drag")
        assert ov.hud is hud, "the toast replaced the size readout mid-drag"
    finally:
        ov._restore()


# ------------------------------------------------------------------ the autofire mark

def test_the_mark_keys_off_the_blink_not_off_the_armed_set():
    ov = make_overlay()
    from glasspad.layer_turbo import Turbo
    from glasspad.watchdog import Watchdog

    class Svc:
        def press_button(self, n): pass
        def release_button(self, n): pass
        def set_left_trigger(self, v): pass
        def set_right_trigger(self, v): pass

    ov.service = Svc()
    ov.active = {"A": "button"}
    ov.turbo = Turbo(20.0, armed={"A"})
    ov._turbo_timers = {}
    ov.watchdog = Watchdog(8.0)
    ov.areas = {"A": FakeArea()}
    ov._arm_hold_timer = lambda: None
    try:
        assert OL.Overlay._fresh_vis().get("turbo") is False, "the mark has no entry in the one definition of visual state"
        ov._turbo_start("A")
        assert ov.vis["A"]["turbo"] is True, "a firing control shows no mark"
        ov._turbo_stop("A")
        assert ov.vis["A"]["turbo"] is False, "the mark outlived the firing"
    finally:
        ov._restore()


def test_the_ring_paints_amber_on_the_frame_and_nothing_when_off():
    """An offscreen render, so the claim is about pixels and not about a flag."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 200)
    cr = cairo.Context(surf)

    def amber_pixels(surface=None):
        """Count amber pixels, undoing cairo's premultiplication first.

        ARGB32 stores R,G,B already multiplied by alpha, so an 0.9-alpha stroke reads
        as R=205 at best and a filter written against straight values finds nothing on
        thin strokes. The first version of this test did exactly that and reported the
        pill ring as unpainted.
        """
        surface = surface or surf
        surface.flush()
        buf = surface.get_data()
        n = 0
        for i in range(0, len(buf), 4):
            a = buf[i + 3]
            if a < 60:
                continue
            # memory order is B, G, R, A on little-endian -- getting that backwards is
            # what made the circle read as "not amber" once the values were divided
            blue = buf[i] * 255 / a
            green = buf[i + 1] * 255 / a
            red = buf[i + 2] * 255 / a
            if red > 220 and 150 < green < 215 and blue < 70:
                n += 1
        return n

    ov = OL.Overlay.__new__(OL.Overlay)
    assert amber_pixels() == 0, "a blank surface already has amber"
    ov._paint_turbo_ring(cr, 100.0, 100.0, "circle")
    on_circle = amber_pixels()
    assert on_circle > 0, "the ring painted nothing on a circle"

    # a pill (LB/RB) gets the pill outline, not a circle inside it
    surf2 = cairo.ImageSurface(cairo.FORMAT_ARGB32, 240, 96)
    cr2 = cairo.Context(surf2)
    ov._paint_turbo_ring(cr2, 120.0, 48.0, "pill")
    n2 = amber_pixels(surf2)
    assert n2 > 0, "the ring painted nothing on a pill"


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
