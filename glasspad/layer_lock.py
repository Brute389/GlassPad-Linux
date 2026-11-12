"""Lockout: after something releases a contact that is still under a finger, that
contact must not be able to press the control again.

Why this exists. The hold watchdog releases a control that has been held too long --
a stuck contact from an exclusive grab, a blanked screen, a resume from suspend. For
buttons that worked. For sticks it did nothing at all, because GTK keeps sending
`drag-update` for a contact it still owns, and `_drag_step` fed the result straight to
the pad. Measured, on the live overlay, left stick axis 0:

    held 1.0  ->  watchdog releases: 0.5  ->  one more drag-update: 1.0

The release lasted until the next motion event, which is to say not at all. The thumb
stays at full deflection for as long as it is on the glass -- exactly the failure the
watchdog was written to break. The same shape applies to any release we decide on
rather than the player performing: the hide button drops every contact, and without a
lockout a finger still on the glass puts the stick back while the overlay is hidden.

The rule is small: a contact that ends is a release; a contact we release *for* is a
release plus a lockout, and the lockout is cleared by the contact actually ending. So
the control is inert until the finger lifts, and the next touch works normally --
which is what makes this safe to leave on rather than a feature to be afraid of.
"""

from __future__ import annotations


class Lockout:
    """Per-control lockout set, with the two decisions asked of it.

    `active` is passed in rather than owned here: the overlay keeps one dict of what is
    held (kind included) and this object only answers "may this event reach the pad".
    Two copies of "what is pressed" is how a stuck control gets into a file.
    """

    def __init__(self) -> None:
        self._locked: set[str] = set()
        self.dropped = 0            # motion events refused; cheap to report, useful once

    # -- decisions -------------------------------------------------------
    def may_press(self, name: str, active) -> bool:
        """A locked control takes no new press, and one already held takes no second."""
        return name not in self._locked and name not in active

    def may_motion(self, name: str, active) -> bool:
        """Motion only counts while the control is held.

        The `active` half of this is the ghost fix on its own: a drag-update that
        arrives after the release has no business moving the pad. The `locked` half
        covers the case where the release was ours and the finger is still down.
        """
        ok = name in active and name not in self._locked
        if not ok:
            self.dropped += 1
        return ok

    # -- transitions -----------------------------------------------------
    def force(self, name: str) -> bool:
        """A release we initiated: the contact is still out there, so fence it off."""
        if name in self._locked:
            return False
        self._locked.add(name)
        return True

    def clear(self, name: str) -> bool:
        """The contact ended, however it ended -- including cancel, and including
        ending after we had already released it, which is the normal path.

        Returns whether a lock was actually lifted, so a caller can tell the two
        apart without inspecting the set.
        """
        if name not in self._locked:
            return False
        self._locked.discard(name)
        return True

    def clear_all(self) -> int:
        n = len(self._locked)
        self._locked.clear()
        return n

    # -- inspection ------------------------------------------------------
    def is_locked(self, name: str) -> bool:
        return name in self._locked

    def locked(self) -> tuple[str, ...]:
        return tuple(sorted(self._locked))

    def describe(self) -> str:
        return f"locked {len(self._locked)} ({','.join(self.locked()) or 'none'})"
