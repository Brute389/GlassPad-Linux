#!/usr/bin/env python3
"""The lockout: a contact we released must not press the control again until it ends.

The bug this pins is measured, not hypothetical -- left stick axis 0 on the live
overlay went 1.0 (held) -> 0.5 (watchdog released) -> 1.0 (one more drag-update). Run
directly: python3 tests/test_lockout.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad.layer_lock import Lockout  # noqa: E402


def test_a_fresh_control_takes_press_and_motion():
    lk, active = Lockout(), {}
    assert lk.may_press("A", active)
    active["A"] = "button"
    assert lk.may_motion("A", active)


def test_a_held_control_takes_no_second_press():
    lk, active = Lockout(), {"A": "button"}
    assert not lk.may_press("A", active), "a second contact re-pressed a held control"


def test_motion_after_a_normal_release_is_refused():
    """A stray drag-update after the release is the plain half of the ghost."""
    lk, active = Lockout(), {"LS": "stick"}
    assert lk.may_motion("LS", active)
    active.pop("LS")
    assert not lk.may_motion("LS", active), "a released stick still moved the pad"


def test_a_forced_release_locks_and_refuses_both():
    lk, active = Lockout(), {"LS": "stick"}
    assert lk.force("LS")
    active.pop("LS")
    assert not lk.may_motion("LS", active), "the ghost got through"
    assert not lk.may_press("LS", active), "the same finger pressed it again"
    assert lk.is_locked("LS")


def test_the_contact_actually_ending_lifts_the_lock():
    """Otherwise a lockout would be a permanent disable, which nobody would accept."""
    lk, active = Lockout(), {"LS": "stick"}
    lk.force("LS")
    active.pop("LS")                      # the release we asked for
    lk.clear("LS")                        # drag-end / cancel arrives afterwards
    assert not lk.is_locked("LS")
    assert lk.may_press("LS", active), "still fenced after the finger lifted"


def test_the_whole_cycle_in_order():
    lk, active = Lockout(), {}
    assert lk.may_press("LS", active)
    active["LS"] = "stick"
    assert lk.may_motion("LS", active)
    lk.force("LS")                        # the watchdog
    active.pop("LS")
    for _ in range(50):                   # GTK keeps sending updates all the way out
        assert not lk.may_motion("LS", active)
    lk.clear("LS")                        # the finger lifts
    assert not lk.may_motion("LS", active), "motion without a press"
    assert lk.may_press("LS", active), "the control is dead for the next touch"


def test_forcing_twice_does_not_double_lock():
    lk = Lockout()
    assert lk.force("A") is True
    assert lk.force("A") is False, "a second force implies a nested lock that never clears"
    assert lk.clear("A") is True
    assert lk.clear("A") is False, "clear reported lifting a lock that was not there"
    assert lk.locked() == ()


def test_one_lock_does_not_spread_to_its_neighbours():
    lk, active = Lockout(), {"A": "button", "B": "button"}
    lk.force("A")
    assert lk.may_motion("B", active) and lk.may_press("B", active) is False
    assert lk.may_press("X", active)


def test_clear_all_reports_and_empties():
    lk = Lockout()
    for n in ("A", "B", "LS"):
        lk.force(n)
    assert lk.clear_all() == 3
    assert lk.locked() == () and lk.clear_all() == 0


def test_unknown_names_are_inert_rather_than_raising():
    lk = Lockout()
    assert lk.clear("nope") is False
    assert not lk.is_locked("nope")
    assert not lk.may_motion("nope", {})
    assert lk.may_press("nope", {})


def test_the_lock_set_does_not_grow_across_many_presses():
    lk = Lockout()
    for _ in range(500):
        lk.force("A")
        lk.clear("A")
    assert lk.locked() == (), "a lock leaked"


def test_dropped_motion_is_counted_and_never_counts_a_permitted_one():
    lk, active = Lockout(), {"LS": "stick"}
    before = lk.dropped
    for _ in range(10):
        lk.may_motion("LS", active)
    assert lk.dropped == before, "permitted motion was counted as refused"
    lk.force("LS")
    active.pop("LS")
    for _ in range(4):
        lk.may_motion("LS", active)
    assert lk.dropped == before + 4
    assert "locked 1" in lk.describe()


def test_lockout_survives_a_hide_that_forces_everything():
    """The hide button drops every contact; a finger on the glass must stay dropped."""
    lk, active = Lockout(), {"LS": "stick", "A": "button", "RT": "trigger"}
    for n in list(active):
        lk.force(n)
    active.clear()
    for n in ("LS", "A", "RT"):
        assert not lk.may_motion(n, active) and not lk.may_press(n, active)
    # coming back needs the contacts to have ended, one by one
    for n in ("LS", "A"):
        lk.clear(n)
    assert lk.may_press("LS", active) and lk.may_press("A", active)
    assert not lk.may_press("RT", active), "RT never lifted and is still fenced"


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
