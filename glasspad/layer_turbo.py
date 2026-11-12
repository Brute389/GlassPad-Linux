"""Turbo / autofire: hold a control and it fires repeatedly.

The shape of it is deliberately dull. One global rate, per-control arming, and a
square wave: a control that is held down is pressed and released on the half-period
until the finger lifts. Every overlay controller in existence does this, and games
expect exactly this -- a shaped or duty-cycle-tweaked version reads as missed shots.

Two things in here are not obvious and are the reason this is a module rather than a
few lines in the press handler:

* **The watchdog must stand down for armed controls.** A held turbo button is a
  control being held for minutes on purpose, which is precisely the pattern the hold
  watchdog exists to break up. Without the exception the safety net would quietly
  disable the feature after eight seconds.
* **Turbo must never leave the pad pressed.** The last thing a held turbo control did
  may be a *press*, and the finger lifting has to end it in the released state, or
  the game sees a button held for the rest of the session -- the exact failure the
  watchdog was written for, introduced by the feature meant to help.

Pure: no GTK, no clock of its own. Callers pass a monotonic time in and get the state
they should be showing the pad, which is what makes a 20 Hz signal testable in
constant time.
"""

from __future__ import annotations

#: Button presses per second while a turbo control is held. 20 Hz is the rate the
#: USB controllers shipped with console games -- fast enough that no game polls it
#: slower, slow enough that a game with a one-frame cooldown still registers both
#: edges. 30 Hz reads as a smear on anything with an input cooldown.
DEFAULT_TURBO_HZ = 20.0

MIN_TURBO_HZ = 2.0
MAX_TURBO_HZ = 60.0

#: Arming lives in edit mode, where a tap on the sizing knob means "this one". The
#: drag has to be under this far for the touch to count as a tap rather than a resize.
TAP_SLOP = 4.0


def clamp_hz(value) -> float:
    """0 means off for *this session*; anything else lands in a usable rate.

    Clamping rather than rejecting: this value comes from a CLI flag and a JSON file,
    and a layout that says 500 Hz should fire fast, not refuse to start.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TURBO_HZ
    if v <= 0.0:
        return 0.0
    return min(MAX_TURBO_HZ, max(MIN_TURBO_HZ, v))


def half_period_ms(hz: float) -> int:
    """Timer interval between edges. Never below 4 ms: a 2 ms GLib timeout under a
    busy compositor drifts more than it fires, and the signal stops being a square."""
    if hz <= 0.0:
        return 0
    return max(4, int(round(500.0 / hz)))


def phase(now: float, hz: float) -> bool:
    """True while the control should be *pressed* at this instant.

    ``hz`` means presses per second, so a full cycle takes 1/hz and the parity has to
    flip every *half* cycle: ``int(now * 2 * hz)``. Getting that factor of two wrong
    is what this function's first test caught -- ``int(now * hz) % 2`` is a perfectly
    reasonable-looking square wave that fires at half the requested rate, while
    half_period_ms() was already scheduling one edge per 1/(2*hz). The two must agree,
    and the agreement is asserted directly in tests/test_turbo.py.

    It is a pure function of the clock rather than a counter, so a dropped timer tick
    cannot make the signal lose its rhythm permanently: it skips an edge and comes
    back in step, instead of drifting by half a cycle forever.
    """
    if hz <= 0.0:
        return True
    return int(now * 2.0 * hz) % 2 == 0


class Turbo:
    """Which controls fire automatically, and what the pad should see right now."""

    def __init__(self, hz: float = DEFAULT_TURBO_HZ, armed=None) -> None:
        self.hz = clamp_hz(hz)
        self.armed: set[str] = set(armed or ())

    @property
    def enabled(self) -> bool:
        return self.hz > 0.0

    def is_armed(self, name: str) -> bool:
        return name in self.armed

    def toggle(self, name: str) -> bool:
        """Flip one control. Returns the new state, for the message that follows."""
        if name in self.armed:
            self.armed.discard(name)
            return False
        self.armed.add(name)
        return True

    def active(self, name: str) -> bool:
        """Should this control be blinking right now (armed, and a rate is set)?"""
        return self.enabled and name in self.armed

    def state(self, name: str, now: float) -> bool:
        """Pad state for one control at this instant: True = pressed."""
        if not self.active(name):
            return True                     # not turboing: whatever the finger says
        return phase(now, self.hz)

    def describe(self) -> str:
        if not self.enabled:
            return "turbo off"
        return f"turbo {self.hz:g} Hz"
