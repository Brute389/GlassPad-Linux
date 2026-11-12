"""Edge rejection: a contact right against the bezel is usually not a deliberate press.

Scope, stated honestly. This is the *small* half of what people call palm rejection.
The tablet gives GTK no contact size and no pressure through a layer surface, so a
palm and a fingertip arrive as indistinguishable points; anything that claims to tell
them apart on this hardware is guessing. What is real: contacts in the last few pixels
of the panel are disproportionately the thumb root or a heel of the hand, and controls
parked against a bezel -- which is easy to do in edit mode, and what people do with
LT/RT on a tablet -- take those contacts as presses.

Two rules keep this from becoming a feature that eats legitimate input:

* A band is only applied where it overlaps the panel's outer `margin` pixels.
* A band never takes more than MAX_FRACTION of a control's own face on that axis, so
  a small control sitting flush against the edge stays usable rather than being
  quietly disabled. A rejection rule that swallows a button is worse than no rule.

With the app's default layout -- and with this machine's saved layout, and with the
app layout after a rotation -- no control comes within the default margin of any edge,
so the gate refuses nothing at all. That is asserted in tests/test_edge.py against the
real frames rather than claimed, because the failure mode here is silent.
"""

from __future__ import annotations

import math

#: Logical pixels of panel that count as "against the edge". Small on purpose: it is
#: meant to catch a heel of the hand, not to carve up the play area.
DEFAULT_MARGIN = 8.0
MIN_MARGIN, MAX_MARGIN = 0.0, 64.0

#: Most of a control's face any one band may take.
MAX_FRACTION = 0.25


def clamp_margin(value) -> float:
    """A margin in logical pixels. Junk (or nothing) means the default; 0 means off."""
    if value is None:
        return DEFAULT_MARGIN
    try:
        v = float(value)
    except (TypeError, ValueError):
        return DEFAULT_MARGIN
    if not math.isfinite(v):
        return DEFAULT_MARGIN
    return max(MIN_MARGIN, min(MAX_MARGIN, v))


def bands(frame, screen, margin: float) -> tuple[float, float, float, float]:
    """Applied band width per side: (left, right, top, bottom).

    `frame` is the control in screen coordinates, `screen` is (w, h). Each value is how
    many pixels of *that control's* face are refused on that side -- 0 for the usual
    case of a control that is nowhere near an edge.
    """
    m = clamp_margin(margin)
    if m <= 0.0:
        return 0.0, 0.0, 0.0, 0.0
    fx, fy, fw, fh = frame
    sw, sh = screen
    if fw <= 0.0 or fh <= 0.0:
        return 0.0, 0.0, 0.0, 0.0
    cap_w, cap_h = fw * MAX_FRACTION, fh * MAX_FRACTION
    left = min(max(0.0, m - fx), cap_w) if fx < m else 0.0
    right = min(max(0.0, (fx + fw) - (sw - m)), cap_w) if fx + fw > sw - m else 0.0
    top = min(max(0.0, m - fy), cap_h) if fy < m else 0.0
    bottom = min(max(0.0, (fy + fh) - (sh - m)), cap_h) if fy + fh > sh - m else 0.0
    return left, right, top, bottom


def refuses(local_x: float, local_y: float, frame, screen,
            margin: float) -> str | None:
    """Which edge band this contact falls in, or None if the press is allowed.

    `local_x/y` is the position inside the control, as the gesture reports it.
    """
    fx, fy, fw, fh = frame
    left, right, top, bottom = bands(frame, screen, margin)
    sx, sy = fx + local_x, fy + local_y
    # Deterministic order, so a corner contact reports the same reason every time and a
    # test can assert on it instead of on whichever branch the compiler liked.
    if left and sx < fx + left:
        return "left"
    if right and sx > fx + fw - right:
        return "right"
    if top and sy < fy + top:
        return "top"
    if bottom and sy > fy + fh - bottom:
        return "bottom"
    return None


def _frame_of(f) -> tuple[str, tuple[float, float, float, float]]:
    """Accept a Frame or a (name, x, y, w, h) tuple; both are in use in this repo."""
    if hasattr(f, "x"):
        return str(f.name), (float(f.x), float(f.y), float(f.w), float(f.h))
    return str(f[0]), (float(f[1]), float(f[2]), float(f[3]), float(f[4]))


def applied(frames, screen, margin: float) -> dict[str, tuple[float, float, float, float]]:
    """Every control that has a band at all -- empty means the gate changes nothing.

    Used by the startup line and by the tests that assert the default layout is
    untouched. Anything that silently alters presses on all sixteen controls deserves to
    be visible before the first game rather than discovered in one.
    """
    out = {}
    for f in frames:
        name, frame = _frame_of(f)
        b = bands(frame, screen, margin)
        if any(v > 0.0 for v in b):
            out[name] = b
    return out


def describe(frames, screen, margin: float) -> str:
    m = clamp_margin(margin)
    if m <= 0.0:
        return "edge off"
    touched = applied(frames, screen, m)
    if not touched:
        return f"edge {m:.0f}px (no control sits that close to an edge)"
    return (f"edge {m:.0f}px on {len(touched)} control(s): "
            + ", ".join(f"{k}+{max(v):.0f}" for k, v in sorted(touched.items())))
