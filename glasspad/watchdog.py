"""Watchdog: release touch zones that are held longer than they plausibly should be.

Why this exists. The gesture code ends a press on release, on the gesture's own
``cancelled`` signal, when the overlay is hidden, and on quit. Every one of those is
something *we* get told about. The cases that actually break a game session are the
ones where nobody tells us: a game grabs input exclusively mid-press, the screen
blanks with a thumb on the stick, KWin moves a surface out from under the sequence,
or the tablet suspends while a control is down and the release dies with the session.

On a gamepad a stuck control is not cosmetic. A held stick means running in circles;
a held left trigger is aim-down-sights or throttle and the player often cannot see
it, because the overlay is translucent by design and their eyes are on the game. So
the cost of a false release (one press cut short) is far lower than the cost of a
missed one, which is why the default limit is generous but not infinite.

Kept free of GTK and of the pad: a deadline is computed from a number the caller
supplies, so all of it is testable without a compositor or a clock to wait on.
"""

from __future__ import annotations

#: Seconds a control may stay held before it is released as suspect. Comfortably
#: longer than any real tap, shorter than the time it takes to notice and blame the
#: game. 0 disables the watchdog entirely.
DEFAULT_HOLD_LIMIT = 8.0

#: The shortest limit honoured. Below about half a second this stops being a safety
#: net and starts cutting off legitimate holds (charge shots, door-holding, menus).
MIN_HOLD_LIMIT = 0.25


def clamp_limit(seconds) -> float:
    """0 turns the watchdog off; anything else lands in a usable range.

    A float is returned so ``None`` never reaches an arithmetic comparison later.
    """
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return DEFAULT_HOLD_LIMIT
    if value <= 0.0:
        return 0.0
    return max(MIN_HOLD_LIMIT, value)


class Watchdog:
    """Deadlines for controls that are currently being held."""

    def __init__(self, limit: float = DEFAULT_HOLD_LIMIT) -> None:
        self.limit = clamp_limit(limit)
        self._due: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return self.limit > 0.0

    def arm(self, name: str, now: float) -> float | None:
        """Start counting on this control. Returns the deadline, or None if off.

        Re-arming refreshes the deadline: a control still under a finger past its
        first deadline has already been released once, and the caller decides
        whether to re-arm or leave it alone.
        """
        if not self.enabled:
            return None
        due = now + self.limit
        self._due[name] = due
        return due

    def disarm(self, name: str) -> None:
        self._due.pop(name, None)

    def clear(self) -> None:
        self._due.clear()

    def holding(self) -> list[str]:
        return sorted(self._due)

    def deadline(self, name: str) -> float | None:
        return self._due.get(name)

    def due(self, now: float) -> list[str]:
        """Controls whose deadline has passed, oldest first."""
        if not self.enabled:
            return []
        return sorted((n for n, t in self._due.items() if now >= t),
                      key=lambda n: self._due[n])

    def next_deadline(self, now: float) -> float | None:
        """The soonest deadline still in the future, for one timer per overlay.

        One timeout per control would mean a timer per finger; the overlay keeps a
        single one and re-arms it from here, so sixteen held controls cost one timer
        and a released control costs nothing beyond a dict pop.
        """
        if not self.enabled:
            return None
        future = [t for t in self._due.values() if t > now]
        return min(future) if future else None
