#!/usr/bin/env python3
"""Press and release have to reach the virtual pad, not just the screen.

These exist because of a real regression: adding the eased press turned part of
_release's body into the body of the new _ease_press method. It parsed, the AST
checker was happy (every self.X resolved), the hub self-test passed (it drives hub
buttons, not gamepad controls), and the screenshots looked correct. Releasing a
button simply never reached the pad, so every press would have latched forever.

Pixels cannot fail a missing release. This can, and mostly needs no display: Overlay's
constructor makes no windows, so a fake service is enough for eight of these ten. The two
edit-mode tests build real input controllers, which is the one place GTK has to be
initialised -- and on a headless box (a CI runner, a fresh clone over ssh) that raises
"Gtk couldn't be initialized". They now SKIP out loud rather than fail, because a suite that
fails for want of a monitor teaches people to ignore the suite.

That claim used to read "it needs no display", which was true of the machine it was written
on and of exactly eight of its tests.

Run directly: python3 tests/test_press_release.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import overlay_layer as OL  # noqa: E402
from glasspad.layer_geom import BUTTON, DPAD, STICK, TRIGGER  # noqa: E402


class FakeService:
    """Records what the overlay tells the pad to do."""

    def __init__(self):
        self.calls = []

    def press_button(self, n): self.calls.append(("press", n))
    def release_button(self, n): self.calls.append(("release", n))
    def set_left_trigger(self, v): self.calls.append(("LT", v))
    def set_right_trigger(self, v): self.calls.append(("RT", v))
    def set_left_stick(self, x, y): self.calls.append(("LS", x, y))
    def set_right_stick(self, x, y): self.calls.append(("RS", x, y))
    def set_dpad(self, d): self.calls.append(("hat", d))
    def reset(self): self.calls.append(("reset",))
    def init(self, *a, **k): pass
    def dispose(self): pass

    def had(self, *need):
        return any(c[:len(need)] == need for c in self.calls)


def fresh(press_anim_ms=0):
    svc = FakeService()
    ov = OL.Overlay(svc, 0.85)
    ov.loop = None
    ov.press_anim_ms = press_anim_ms
    return ov, svc


def test_release_reaches_the_pad_for_every_kind():
    for name, kind, need in (("A", BUTTON, ("release", "A")),
                             ("LT", TRIGGER, ("LT", 0.0)),
                             ("LS", STICK, ("LS", 0.0, 0.0)),
                             ("dpad", DPAD, ("hat", "None"))):
        ov, svc = fresh()
        ov.active[name] = kind
        ov.vis[name] = ov._fresh_vis()
        ov._release(name, kind)
        assert svc.had(*need), f"{name} release never reached the pad: {svc.calls}"


def test_press_reaches_the_pad_and_lights_the_visual():
    ov, svc = fresh()
    ov._press("A", BUTTON, 10, 10, 52, 52)
    assert svc.had("press", "A"), f"press never reached the pad: {svc.calls}"
    assert ov.vis["A"]["press"] == 1.0, "the pressed state was not recorded"


def test_press_outside_the_shape_is_ignored_by_both():
    """A circle's corner is dead space; it must not press and must not light."""
    ov, svc = fresh()
    ov.hit_shapes = True
    ov._press("A", BUTTON, 1, 1, 52, 52)          # corner of a 52-unit circle
    assert not svc.calls, f"a corner tap pressed the pad: {svc.calls}"
    assert ov.vis.get("A", {}).get("press", 0.0) == 0.0, "a corner tap lit the button"


def test_release_with_no_active_press_does_not_double_release():
    ov, svc = fresh()
    ov._release("A", BUTTON)                       # nothing was ever pressed
    assert svc.calls == [], f"a stale release reached the pad: {svc.calls}"


def test_visual_state_has_one_definition():
    """Every key the painters read must exist on a fresh control.

    The state dict used to be spelled out in three places; a key added in one and
    read in another is a crash that only happens on press, which is the least
    discoverable time to find it.
    """
    v = OL.Overlay._fresh_vis()
    for key in ("scale", "nub", "arms", "press", "arms_gen"):
        assert key in v, f"fresh visual state is missing {key!r}"
    # and each painter's lookup must survive an untouched control
    ov, _svc = fresh()
    ov.vis["A"] = ov._fresh_vis()
    assert ov.vis["A"].get("press", 0.0) == 0.0


def test_snap_mode_zeroes_the_press_without_an_animation():
    ov, _svc = fresh(press_anim_ms=0)
    ov._press("A", BUTTON, 26, 26, 52, 52)
    ov._release("A", BUTTON)
    assert ov.vis["A"]["press"] == 0.0, "snap mode left the button lit"


def test_easing_starts_from_where_the_press_is():
    ov, _svc = fresh(press_anim_ms=200)
    ov._press("A", BUTTON, 26, 26, 52, 52)
    ov._release("A", BUTTON)
    assert ov._anim_gen["A"] == 1, "no animation was registered"
    ov._release("A", BUTTON)
    assert ov._anim_gen["A"] == 2, (
        "a second release did not retire the first animation; the older timer "
        "would keep dragging the control back toward rest during a new press")


def test_the_animation_actually_decays_over_time():
    """The easing is a GLib timer, so drive a real main loop for it.

    A press that never returns to rest is the same visible failure as one that never
    lights: the button stays looking held. Reading the value once after _release()
    would prove nothing, because the whole point is that it changes afterwards.
    """
    from gi.repository import GLib

    ov, _svc = fresh(press_anim_ms=200)
    ov._press("A", BUTTON, 26, 26, 52, 52)
    assert ov.vis["A"]["press"] == 1.0

    seen = []
    loop = GLib.MainLoop()

    def sample():
        seen.append(ov.vis["A"]["press"])
        return True

    def stop():
        loop.quit()
        return False

    GLib.timeout_add(16, sample)
    GLib.timeout_add(400, stop)
    ov._release("A", BUTTON)
    loop.run()

    assert seen, "no samples taken; the animation never ran"
    assert seen[0] > 0.5, f"the press did not start near 1.0: {seen[:3]}"
    assert seen[-1] < 0.05, f"the press did not return to rest: {seen[-3:]}"
    deltas = [b - a for a, b in zip(seen, seen[1:])]
    assert all(d <= 1e-6 for d in deltas), f"press went up during its release: {seen}"



class SkipTest(Exception):
    """Raised by skip_without_display(); the runner below counts it apart from FAIL."""


def skip_without_display() -> None:
    """Two of these reach `Gtk.Window()` -- edit mode makes a real surface for the knob.

    The probe is `Gdk.Display.get_default()`, measured rather than guessed, because the
    thing GTK's own error message tells you to call is not a check: on GTK 4
    `Gtk.init_check()` returns True with no compositor at all, while `Gtk.Window()` then
    raises "Gtk couldn't be initialized. Use Gtk.init_check()". Verified on this machine in
    both states -- headless: init_check True, display None; on the Wayland session: both
    fine. Constructing a throwaway window to find out would be a more honest probe and a
    worse one, because a window that appears and vanishes during a test suite is exactly
    the sort of thing people then guard with a skip.
    """
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk
    except Exception as exc:                                # noqa: BLE001
        raise SkipTest(f"no GTK bindings ({exc.__class__.__name__})") from exc
    try:
        have_display = Gdk.Display.get_default() is not None
    except Exception as exc:                                # noqa: BLE001
        # "we could not find out" has to mean SKIP, not ERROR. That distinction is the
        # whole reason the guard exists: an environment that cannot be tested must not be
        # reported as code that failed.
        raise SkipTest(f"display probe raised {exc.__class__.__name__}") from exc
    if not have_display:
        raise SkipTest("no Wayland/X11 display -- edit mode needs a real Gtk.Window")

def test_edit_mode_swaps_controllers_both_ways():
    """The bug this guards: dropping the input controller and never reattaching it."""
    skip_without_display()
    ov, _svc = fresh()
    attached = {"A": "input"}

    class FakeWin:
        def remove_controller(self, c): attached["A"] = None
        def add_controller(self, c): attached["A"] = "restored"

    ov.surfaces = {"A": FakeWin()}
    ov.ctrl_input = {"A": object()}
    ov.ctrl_move = {"A": object()}
    ov.panel = None
    ov.menu_open = False
    ov._set_edit(True)
    assert ov.edit is True
    assert attached["A"] == "restored", "entering edit mode left a control unmovable"
    ov._set_edit(False)
    assert attached["A"] == "restored", "leaving edit mode left the pad dead"


def test_edit_mode_repaints_every_control():
    """Toggling edit mode has to repaint the controls, not just flip a flag.

    The sizing knob is drawn by each control, so with only the flag changed nothing
    drew it and the affordance was invisible. Two things hid this: `--edit` at
    startup was correct because its very first paint already had the flag set, and
    pressing Reset appeared to fix it -- Reset resizes the surfaces, which queues a
    redraw by accident. Opening edit mode from the hub is the only real path, and it
    was the broken one.
    """
    skip_without_display()
    class FakeWin:
        def remove_controller(self, _c): pass
        def add_controller(self, _c): pass

    class FakeArea:
        def __init__(self): self.draws = 0
        def queue_draw(self): self.draws += 1

    ov, _svc = fresh()
    names = ("A", "B", "X", "Y", "dpad", "LS", "RS", "BACK")
    ov.surfaces = {n: FakeWin() for n in names}
    ov.areas = {n: FakeArea() for n in names}
    ov.ctrl_input = {n: object() for n in names}
    ov.ctrl_move = {n: object() for n in names}
    ov.panel = None
    ov.menu_open = False

    ov._set_edit(True)
    missing = [n for n, a in ov.areas.items() if a.draws == 0]
    assert not missing, f"entering edit mode left these unpainted: {missing}"

    ov._set_edit(False)
    stale = [n for n, a in ov.areas.items() if a.draws < 2]
    assert not stale, f"leaving edit mode left the knob on screen for: {stale}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = skipped = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok    {fn.__name__}")
        except SkipTest as exc:
            skipped += 1
            print(f"  SKIP  {fn.__name__}: {exc}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
        except Exception as exc:                       # noqa: BLE001
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    # The tally keeps its shape -- check_all.py fails a suite that prints no number, and
    # a skip has to be counted there or a headless run looks like a silent pass.
    print(f"\n  {len(tests) - failed - skipped} passed, {failed} failed, "
          f"{skipped} skipped of {len(tests)}")
    sys.exit(1 if failed else 0)
