#!/usr/bin/env python3
"""Turbo: the square wave, the arming rules, and the two ways it can go wrong.

The pure part is a function of a clock the caller supplies, so a 20 Hz signal is
tested in constant time. The wiring is driven with a fake monotonic clock and a stub
pad, because what matters here is not whether a timer was created but what the *pad*
is left holding when things stop:

* a finger lifting mid-blink must end on a release, or the game holds the button for
  the rest of the session -- the exact failure the hold watchdog exists for, arriving
  through the feature meant to help;
* the watchdog must not fire on an armed control, because a turbo button is held on
  purpose for minutes, which is precisely the pattern it is tuned to break up.

Run directly: python3 tests/test_turbo.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad.layer_turbo import (  # noqa: E402
    DEFAULT_TURBO_HZ,
    MAX_TURBO_HZ,
    MIN_TURBO_HZ,
    TAP_SLOP,
    Turbo,
    clamp_hz,
    half_period_ms,
    phase,
)


# ------------------------------------------------------------------------- the wave

def test_rate_is_clamped_not_rejected():
    assert clamp_hz(0) == 0.0, "0 must mean off"
    assert clamp_hz(-7) == 0.0
    assert clamp_hz("fast") == DEFAULT_TURBO_HZ
    assert clamp_hz(None) == DEFAULT_TURBO_HZ
    assert clamp_hz(0.1) == MIN_TURBO_HZ, "a rate faster than one poll is not turbo"
    assert clamp_hz(10_000) == MAX_TURBO_HZ


def test_the_interval_is_the_half_period_and_never_silly_small():
    assert half_period_ms(0) == 0
    assert half_period_ms(20) == 25, "20 Hz is 25 ms per edge"
    assert half_period_ms(10) == 50
    assert half_period_ms(10_000) == 4, "a 1 ms GLib timeout drifts more than it fires"


def test_the_wave_alternates_at_the_requested_rate():
    """Count edges over one second: 20 Hz must give 20 pressed intervals."""
    hz = 20.0
    samples = [phase(i / 2000.0, hz) for i in range(2000)]      # 0.5 ms steps, 1 s
    edges = sum(1 for a, b in zip(samples, samples[1:]) if a != b)
    # 39, not 40: the window is [0, 1) and the 40th boundary falls exactly at t=1.0.
    # The presses are the number that matters and that one is exact.
    assert edges == 39, f"20 Hz gave {edges} edges across [0,1), expected 39"
    assert sum(samples) == 1000, "duty cycle is not 50 %"


def test_the_timer_interval_and_the_wave_agree():
    """The invariant that was broken: half_period_ms schedules an edge, phase decides
    which edge it is. Sample exactly on those intervals and the state must alternate
    every single time -- and a full press must take two of them."""
    for hz in (10.0, 20.0, 30.0):
        step = half_period_ms(hz) / 1000.0
        states = [phase(i * step, hz) for i in range(40)]
        flips = sum(1 for a, b in zip(states, states[1:]) if a != b)
        assert flips == 39, f"{hz:g} Hz flipped {flips} times over 40 half-periods"
        presses = sum(1 for i in range(0, 40, 2) if states[i])
        assert presses == 20, f"{hz:g} Hz: presses are not one per cycle"
        # and presses per second really is hz
        one_second = [phase(i * step, hz) for i in range(int(round(1.0 / step)))]
        got = sum(1 for v in one_second if v)
        assert abs(got - hz) <= 1, f"{hz:g} Hz measured {got} presses in a second"


def test_zero_rate_is_always_pressed_so_turbo_cannot_ghost_a_release():
    assert all(phase(t, 0) for t in (0.0, 0.3, 17.25)), "with turbo off the pad must simply follow the finger"


def test_a_dropped_tick_returns_to_step_instead_of_drifting():
    """phase() is a function of the clock, not a counter."""
    hz = 20.0
    assert phase(1.0, hz) == phase(1.0, hz)
    # the same instant always gives the same answer, so a late timer repeats an edge
    # rather than inverting the whole signal from then on
    assert phase(2.0, hz) == phase(2.0 + 1e-9, hz)


# --------------------------------------------------------------------------- arming

def test_toggle_reports_the_new_state():
    t = Turbo(20.0)
    assert t.is_armed("A") is False
    assert t.toggle("A") is True
    assert t.toggle("A") is False
    assert t.is_armed("A") is False


def test_an_armed_control_does_nothing_while_the_rate_is_off():
    t = Turbo(0)
    t.toggle("A")
    assert t.is_armed("A"), "arming should survive the rate being off"
    assert not t.active("A"), "an armed control must not fire with turbo off"
    assert t.state("A", 12.5) is True, "state must fall back to the finger's truth"


def test_describe_says_something_a_log_reader_can_act_on():
    assert Turbo(0).describe() == "turbo off"
    assert Turbo(20).describe() == "turbo 20 Hz"


# -------------------------------------------------------------------------- wiring

class FakeService:
    def __init__(self):
        self.calls: list[tuple] = []

    def press_button(self, name): self.calls.append(("press", name))
    def release_button(self, name): self.calls.append(("release", name))
    def set_left_trigger(self, v): self.calls.append(("LT", v))
    def set_right_trigger(self, v): self.calls.append(("RT", v))
    def set_left_stick(self, x, y): self.calls.append(("LS", x, y))
    def set_right_stick(self, x, y): self.calls.append(("RS", x, y))
    def set_dpad(self, hat): self.calls.append(("hat", hat))
    def reset(self): self.calls.append(("reset",))
    def had(self, *ev): return tuple(ev) in self.calls


class Clock:
    """Replacement for time.monotonic inside overlay_layer."""

    def __init__(self, t=1000.0):
        self.t = t

    def monotonic(self):
        return self.t

    def advance(self, s):
        self.t += s


def make_overlay(armed=(), hz=20.0):
    """The shared stub (see tests/_overlay_stub.py), plus this file's own pieces:

    a recording service the assertions use, and GLib timer patches so the turbo timer
    can be driven without a main loop. Keep the patches here: they are what makes the
    tick tests deterministic, and silently sharing them would make every suite depend
    on a patched GLib.
    """
    from _overlay_stub import make_overlay as shared
    ov = shared(service=FakeService(), edit=True, armed=armed, turbo_hz=hz,
               surfaces={"A": object()})
    # The GLib patches below are this file's own: they let the tick tests drive the
    # timer without a main loop, and they must not leak into the other suites.
    import gi
    from gi.repository import GLib
    ov._registered: list[tuple] = []
    ov._timeout_add = GLib.timeout_add
    GLib.timeout_add = lambda ms, fn, *a: ov._registered.append((ms, fn)) or 1
    ov._source_remove_calls: list[int] = []
    real_remove = GLib.source_remove
    GLib.source_remove = lambda src: ov._source_remove_calls.append(src)
    ov._restore = lambda: (setattr(GLib, "timeout_add", ov._timeout_add),
                          setattr(GLib, "source_remove", real_remove))
    return ov


def test_holding_an_armed_button_puts_the_blink_on_the_pad():
    ov = make_overlay(armed=("A",))
    clock = Clock()
    import glasspad.overlay_layer as OL
    real_time = OL.time
    OL.time = clock
    try:
        ov._press("A", "button", 10.0, 10.0)
        svc = ov.service
        assert svc.calls[-1] == ("press", "A")
        # drive the edges the way the timer would, one half-period apart
        svc.calls.clear()
        for _ in range(8):
            clock.advance(0.025)
            assert ov._turbo_tick("A") is True, "the timer gave up early"
        presses = sum(1 for c in svc.calls if c == ("press", "A"))
        releases = sum(1 for c in svc.calls if c == ("release", "A"))
        assert presses == 4 and releases == 4, f"8 edges at 20 Hz should be 4 press + 4 release, got {svc.calls}"
    finally:
        OL.time = real_time
        ov._restore()


def test_the_watchdog_stands_down_for_an_armed_control():
    """A turbo button is held for minutes deliberately.

    Without this the safety net would disable the feature eight seconds in, which is
    both wrong and nearly impossible to notice while playing.
    """
    ov = make_overlay(armed=("A",))
    ov._press("A", "button", 10.0, 10.0)
    assert ov.watchdog.deadline("A") is None, "the watchdog is still counting a turbo hold"
    ov._restore()


def test_an_unarmed_control_is_left_to_the_watchdog():
    ov = make_overlay(armed=())
    ov._press("A", "button", 10.0, 10.0)
    assert ov.watchdog.deadline("A") is not None
    assert ov._turbo_timers == {}, "a control nobody armed started firing"
    ov._restore()


def test_turbo_is_refused_for_the_stick_and_the_dpad():
    """Rapid-firing a stick is not a thing any game reads, and it would feel broken."""
    ov = make_overlay()
    assert ov._toggle_turbo("dpad") is False, "the d-pad accepted turbo"
    assert ov._toggle_turbo("LS") is False
    assert any("buttons and triggers" in m for m in ov.sayings)
    assert ov._toggle_turbo("A") is True
    assert ov._toggle_turbo("LT") is True, "triggers are the classic turbo target"
    ov._restore()


def test_a_trigger_blinks_through_its_analog_value():
    ov = make_overlay(armed=("LT",))
    ov.active["LT"] = "trigger"
    ov._turbo_apply("LT", True)
    ov._turbo_apply("LT", False)
    assert ov.service.had("LT", 1.0) and ov.service.had("LT", 0.0), ov.service.calls
    ov._restore()


def test_setting_the_rate_to_zero_leaves_a_held_control_pressed():
    """The finger is still down. Whatever edge the blink last emitted is irrelevant."""
    ov = make_overlay(armed=("A",))
    ov._press("A", "button", 10.0, 10.0)
    ov._turbo_apply("A", False)               # as if the timer had just sent a release
    ov.service.calls.clear()
    ov._set_turbo_hz(0)
    assert ov.service.had("press", "A"), f"a held control was left released: {ov.service.calls}"
    assert ov._turbo_timers == {}, "still firing after turbo was turned off"
    ov._restore()


def test_a_tap_on_the_knob_arms_turbo_without_resizing_anything():
    """The one gesture that shares the corner with resizing, told apart by travel."""
    ov = make_overlay()
    w = ov.geo["A"][2]
    ov._edit_begin("A", w - 1.0, w - 1.0)
    ov._edit_update("A", 1.0, 1.0)            # inside TAP_SLOP
    ov._edit_end("A")
    assert ov.turbo.is_armed("A"), "a tap on the knob did not arm turbo"
    assert ov.geo["A"][2] == w, f"a tap resized the control to {ov.geo['A'][2]}"

    # and a drag still resizes, without arming
    ov2 = make_overlay()
    ov2._edit_begin("A", w - 1.0, w - 1.0)
    ov2._edit_update("A", TAP_SLOP + 20.0, TAP_SLOP + 20.0)
    ov2._edit_end("A")
    assert not ov2.turbo.is_armed("A"), "a resize drag also armed turbo"
    assert ov2.geo["A"][2] > w, "the resize drag stopped resizing"
    ov._restore(); ov2._restore()


def test_the_armed_set_survives_a_save():
    from glasspad.layer_geom import default_frames, load_turbo, write_frames
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "layout.json")
        frames = default_frames(1707, 1067)
        write_frames(p, frames, 0.85, 4, screen=(1707, 1067), turbo={"A", "RB"})
        assert load_turbo(p) == {"A", "RB"}
        write_frames(p, frames, 0.85, 4, screen=(1707, 1067))
        assert load_turbo(p) == set(), "disarming did not clear the flag"


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
