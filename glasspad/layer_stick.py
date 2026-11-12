"""Stick response: how far the thumb has to travel, and what it gets for it.

Why this exists. The Windows original (Input/StickZone.cs) normalises the thumb
offset by **MaxTravel = 28 px** on a 119 px disc, so full deflection arrives at 47 %
of the way to the rim. This port was normalising by the *radius* (59.5 px), which
meant 2.1x the travel the original asks for -- full deflection only when the thumb
reaches the very edge of the glass. Worse, the nub was drawn clamped to 28 px, so
past that point the picture had stopped moving while the pad was still at 0.47: the
overlay was visibly lying about the input it was sending.

Two fixes, kept separate:

* **Parity** -- the default tuning here saturates at 28/119 of the radius, exactly the
  original, and `tests/test_stick_tuning.py` compares it against the client's formula
  point by point.
* **Consistency** -- the nub is computed *from* the final deflection, so the two can
  no longer disagree by construction rather than by two pieces of arithmetic happening
  to match.

On top of that, three dials, because glass is not a thumbstick: a dead zone (a thumb
resting on glass jitters, and a game with a 0.05 threshold reads that as walking), a
response curve (fine control near the centre versus arriving quickly), and the
saturation point itself. All of it is pure arithmetic on numbers the caller supplies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: The original's geometry, from StickZone.cs. Kept here so the defaults below are
#: derived rather than typed twice.
OUTER = 119.0
INNER = 57.0
MAX_TRAVEL = (OUTER - INNER) / 2.0                    # 28 px
#: Fraction of the disc RADIUS at which the original reaches full deflection.
#: MaxTravel is measured against the diameter in StickZone.cs (28 of 119), and every
#: number in here works against the radius, hence the factor of two -- 28 px of the
#: 59.5 px radius, i.e. 47 % of the way out. Writing MAX_TRAVEL / OUTER here looked
#: plausible and halved the travel; the parity test in tests/test_stick_tuning.py is
#: what keeps the two straight.
APP_SATURATE = MAX_TRAVEL / (OUTER / 2.0)             # 0.4706 of the radius

#: Sensible bounds. A saturation below 0.1 of the radius means the stick is a button;
#: a dead zone past 0.45 means the control has almost no travel left.
MIN_SATURATE, MAX_SATURATE = 0.10, 1.0
MIN_DEADZONE, MAX_DEADZONE = 0.0, 0.45
MIN_CURVE, MAX_CURVE = 0.30, 3.0


@dataclass(frozen=True)
class StickTuning:
    """saturate / deadzone are fractions of the disc radius; curve is an exponent."""
    saturate: float = APP_SATURATE
    deadzone: float = 0.0
    curve: float = 1.0

    def clamp(self) -> "StickTuning":
        """Numbers in range, and a dead zone that can never eat all the travel."""
        sat = min(MAX_SATURATE, max(MIN_SATURATE, _num(self.saturate, APP_SATURATE)))
        dead = min(MAX_DEADZONE, max(MIN_DEADZONE, _num(self.deadzone, 0.0)))
        # a dead zone at or beyond saturation leaves no range at all, and the stick
        # becomes a binary switch that reports nothing between the two
        dead = min(dead, sat * 0.9)
        curve = min(MAX_CURVE, max(MIN_CURVE, _num(self.curve, 1.0)))
        return StickTuning(sat, dead, curve)

    @property
    def is_app_default(self) -> bool:
        return (abs(self.saturate - APP_SATURATE) < 1e-6
                and self.deadzone == 0.0 and self.curve == 1.0)

    def describe(self) -> str:
        return (f"sat {self.saturate * 100:.0f}%  dead {self.deadzone * 100:.0f}%  "
                f"curve {self.curve:.2f}")


def _num(value, fallback: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return fallback
    return v if math.isfinite(v) else fallback


def shape(dist_norm: float, tuning: StickTuning) -> float:
    """Map 0..1 of the disc radius to 0..1 of deflection, under the tuning.

    Continuity at the dead-zone edge is deliberate: the value leaves zero at zero, so
    there is no step for a game's own input smoothing to latch onto. The exponent is
    applied after the dead zone is subtracted, so `curve` changes the feel of the
    travel you actually have rather than the travel you threw away.
    """
    t = tuning.clamp()
    if dist_norm <= t.deadzone:
        return 0.0
    span = max(1e-9, t.saturate - t.deadzone)
    x = (dist_norm - t.deadzone) / span
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x ** t.curve


def deflect(dx: float, dy: float, radius: float,
            tuning: StickTuning | None = None) -> tuple[float, float]:
    """Thumb offset in surface px -> deflection in ZONE space, -1..1.

    The magnitude is shaped and the direction is kept, so a diagonal push stays a
    diagonal push at every tuning: scaling the axes independently would bend
    diagonals toward the octants and feel like a broken stick rather than a curve.
    """
    t = (tuning or StickTuning()).clamp()
    if radius <= 0.0:
        return 0.0, 0.0
    if not (math.isfinite(dx) and math.isfinite(dy)):
        # A NaN reaching the pad would be worse than a stick that does not move: it
        # propagates into the axis conversion and can park an axis permanently.
        return 0.0, 0.0
    dist = math.hypot(dx, dy)
    if dist <= 0.0:
        return 0.0, 0.0
    mag = shape(dist / radius, t)
    gx, gy = dx * (mag / dist), dy * (mag / dist)
    if mag >= 1.0:
        # `dx * (1 / dist)` is not exactly 1.0 for most dx, so a thumb well past the
        # saturation point could report 0.9999999999999999. Harmless to a game with a
        # threshold, not harmless to anything that asks for a full stick, and the
        # device quantises to 257 steps anyway -- so full means full.
        if abs(gy) < 1e-12 and abs(abs(gx) - 1.0) < 1e-12:
            gx = 1.0 if gx > 0 else -1.0
        if abs(gx) < 1e-12 and abs(abs(gy) - 1.0) < 1e-12:
            gy = 1.0 if gy > 0 else -1.0
    return gx, gy


def nub_offset(def_x: float, def_y: float, nub_range: float) -> tuple[float, float]:
    """Where to draw the nub for a deflection that has already been shaped.

    Derived from the deflection rather than from the finger, which is the whole point:
    the port used to clamp the nub to 28 px while the deflection carried on out to the
    rim, so past 28 px the ring was frozen at full travel and the pad was at half.
    One number now feeds both, so they cannot disagree.
    """
    return def_x * nub_range, def_y * nub_range


# ---------------------------------------------------------------------- presets

PRESETS: dict[str, StickTuning] = {
    # Exactly StickZone.cs. Not "the safe option": the option the original author
    # tuned against real play, and the only one with a parity test.
    "app": StickTuning(),
    # A small dead zone for glass (a resting thumb creeps), and a touch more response
    # through the middle without changing where full deflection lands.
    "soft": StickTuning(APP_SATURATE, 0.04, 0.85),
    # Near-instant full deflection: fighting games, twin-stick shooters, anything
    # where "run" or "dodge" is the only question the stick is asked. Saturates at
    # three quarters of the original's travel, so it is quick without being digital.
    "crisp": StickTuning(APP_SATURATE * 0.75, 0.06, 0.62),
    # Deliberate travel before anything tops out: driving, camera control, anything
    # where snapping to full is worse than being slow.
    "wide": StickTuning(0.55, 0.02, 1.25),
}


def preset(name: str) -> tuple[StickTuning, str | None]:
    """Look a preset up. Returns (tuning, warning); unknown names keep the app's."""
    if not name:
        return StickTuning(), None
    key = str(name).strip().lower()
    if key in PRESETS:
        return PRESETS[key].clamp(), None
    return StickTuning(), f"unknown stick preset {name!r}; using the app's own response"


def from_dict(raw) -> StickTuning:
    """Read the persisted form, tolerating anything a hand-edit produced."""
    if not isinstance(raw, dict):
        return StickTuning()
    return StickTuning(_num(raw.get("saturate"), APP_SATURATE),
                       _num(raw.get("deadzone"), 0.0),
                       _num(raw.get("curve"), 1.0)).clamp()


def to_dict(t: StickTuning) -> dict:
    return {"saturate": round(t.saturate, 6), "deadzone": round(t.deadzone, 6),
            "curve": round(t.curve, 4)}
