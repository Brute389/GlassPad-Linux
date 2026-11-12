#!/usr/bin/env python3
"""The hold watchdog: deadlines, and the wiring that makes them mean something.

The pure part (arm/disarm/due/next_deadline) is tested with a supplied clock, which
is the only way to test a nine-second timeout without waiting nine seconds. What a
deadline *does* -- release the pad, clear the visual, not re-fire under a finger that
is still down -- is behavioural, and those tests drive the overlay's own methods with
the GLib timer replaced by a call, exactly as the compositor would call it.

Run directly: python3 tests/test_watchdog.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad.watchdog import (  # noqa: E402
    DEFAULT_HOLD_LIMIT,
    MIN_HOLD_LIMIT,
    Watchdog,
    clamp_limit,
)


def test_limit_is_clamped_into_a_usable_range():
    assert clamp_limit(0) == 0.0, "0 must mean off, not 'fire immediately'"
    assert clamp_limit(-5) == 0.0, "negative must mean off"
    assert clamp_limit("nonsense") == DEFAULT_HOLD_LIMIT
    assert clamp_limit(None) == DEFAULT_HOLD_LIMIT
    assert clamp_limit(0.001) == MIN_HOLD_LIMIT, "a sub-frame limit cuts every press"
    assert clamp_limit(30) == 30.0


def test_a_disabled_watchdog_does_nothing_at_all():
    w = Watchdog(0)
    assert not w.enabled
    assert w.arm("A", 100.0) is None, "an off watchdog must not hand out deadlines"
    assert w.due(10_000.0) == []
    assert w.next_deadline(10_000.0) is None


def test_nothing_is_due_before_the_limit_and_everything_after():
    w = Watchdog(8.0)
    w.arm("A", 100.0)
    w.arm("LT", 100.5)
    assert w.due(107.9) == [], "released a legitimate hold 0.1s early"
    assert w.due(108.0) == ["A"], "at exactly the limit it is due"
    assert w.due(109.0) == ["A", "LT"], f"oldest first, got {w.due(109.0)}"


def test_releasing_a_control_takes_it_off_the_list():
    w = Watchdog(8.0)
    w.arm("A", 100.0)
    w.disarm("A")
    assert w.due(500.0) == [], "a released control still fired the watchdog later"
    # and disarming something that was never armed is not an error
    w.disarm("nope")


def test_the_oldest_deadline_drives_the_single_timer():
    w = Watchdog(8.0)
    now = 1000.0
    w.arm("A", now)
    w.arm("B", now + 2.0)
    w.arm("C", now + 1.0)
    assert w.next_deadline(now) == now + 8.0, "the timer must be set for the earliest"
    w.disarm("A")
    assert w.next_deadline(now) == now + 9.0
    w.disarm("B")
    w.disarm("C")
    assert w.next_deadline(now) is None, "an empty overlay should hold no timer"
    # once everything is past due there is nothing left to schedule
    w.arm("A", now)
    assert w.next_deadline(now + 100.0) is None


def test_re_arming_moves_the_deadline():
    w = Watchdog(8.0)
    w.arm("A", 100.0)
    w.arm("A", 106.0)
    assert w.due(107.0) == [], "re-arming did not refresh the deadline"
    assert w.due(114.0) == ["A"]


def test_clear_drops_everything():
    w = Watchdog(8.0)
    for i, n in enumerate(("A", "B", "X", "Y")):
        w.arm(n, 100.0 + i)
    w.clear()
    assert w.holding() == []
    assert w.due(999.0) == []


# --------------------------------------------------------------------------- wiring

def make_overlay(hold_limit: float = 8.0):
    """The shared stub (see tests/_overlay_stub.py).

    This file used to build its own Overlay attribute by attribute, which meant every
    new piece of Overlay state broke it -- and, worse, meant the presses being tested
    here ran against a slightly different object than the ones in test_turbo.py.
    """
    from _overlay_stub import make_overlay as shared
    return shared(hold_limit=hold_limit)


def test_a_press_arms_the_watchdog_and_a_release_disarms_it():
    ov = make_overlay()
    ov._press("A", "button", 10.0, 10.0)
    assert ov.watchdog.deadline("A") is not None, "a press did not arm the watchdog"
    ov._release("A", "button")
    assert ov.watchdog.deadline("A") is None, "a normal release left a deadline behind"


def test_the_timer_releases_the_control_on_the_pad():
    """Not just our bookkeeping: the pad must be told to let go.

    This is the whole point of the watchdog. A release that updates self.active but
    leaves the uinput button down would look correct in the logs while the game kept
    seeing the button held.
    """
    ov = make_overlay()
    ov._press("A", "button", 10.0, 10.0)
    assert ov.service.events[-1] == ("press", "A")
    # force the deadline into the past, then call the callback the compositor would
    ov.watchdog._due["A"] = 0.0
    assert ov._on_hold_timeout() is False, "the callback must not re-register itself"
    assert ("release", "A") in ov.service.events, "the watchdog never released the pad"
    assert "A" not in ov.active
    assert any("stuck" in m for m in ov.sayings), "a forced release said nothing to us"


def test_it_releases_by_the_kind_currently_held_not_the_kind_it_remembered():
    """A stale timer firing after A became something else must not send a wrong event.

    The control names are stable but a control's kind is what determines which pad
    event releases it, so the callback looks the kind up at fire time.
    """
    ov = make_overlay()
    ov.active["A"] = "trigger"
    ov.watchdog.arm("A", 0.0)
    ov._on_hold_timeout()
    assert ("rt", 0.0) in ov.service.events or ("lt", 0.0) in ov.service.events, \
        f"released a trigger as something else: {ov.service.events}"
    assert ("release", "A") not in ov.service.events


def test_a_control_the_timer_does_not_know_about_is_left_alone():
    ov = make_overlay()
    ov._press("B", "button", 5.0, 5.0)
    ov.watchdog._due["B"] = 0.0
    ov.watchdog._due["Ghost"] = 0.0          # armed, then dropped by some other path
    ov._on_hold_timeout()
    assert ov.service.events[-1] == ("release", "B")
    assert "Ghost" not in ov.active


def test_disabled_watchdog_never_releases_anything():
    ov = make_overlay(hold_limit=0)
    ov._press("A", "button", 10.0, 10.0)
    ov.watchdog._due["A"] = -100.0           # even with a bogus deadline in there
    ov._on_hold_timeout()
    assert ("release", "A") not in ov.service.events, "the watchdog fired while disabled"
    assert "A" in ov.active


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
