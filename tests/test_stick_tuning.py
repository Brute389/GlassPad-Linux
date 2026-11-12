#!/usr/bin/env python3
"""Stick response: parity with the original, and the properties any tuning must keep.

The parity test is the reason this file exists. The port normalised thumb offset by the
disc **radius** (59.5 px) where StickZone.cs normalises by **MaxTravel**; full
deflection therefore needed 1.9x the travel the original asks for, and the nub -- clamped
separately -- stopped moving long before that. The mapping is now the client's, with the
tuning layered on top, so "we changed nothing that matters" is a claim with a test
behind it rather than a hope.

Note on MaxTravel: StickZone.cs writes `(Outer - Inner) / 2` with a comment saying
"28 px". The arithmetic says 31 and the compiled value is 31. Every number below comes
from evaluating the expression, not from quoting the comment -- which is the mistake
this test also guards against.

Run directly: python3 tests/test_stick_tuning.py
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad.layer_geom import (  # noqa: E402
    default_frames, load_stick_tuning, write_frames,
)
from glasspad.layer_stick import (  # noqa: E402
    APP_SATURATE,
    INNER,
    MAX_TRAVEL,
    OUTER,
    PRESETS,
    StickTuning,
    deflect,
    from_dict,
    nub_offset,
    preset,
    shape,
    to_dict,
)

R = OUTER / 2.0


def client(px, py):
    """StickZone.Apply(), transcribed: clamp the offset to MaxTravel, divide by it."""
    d = math.hypot(px, py)
    if d > MAX_TRAVEL:
        px, py = px / d * MAX_TRAVEL, py / d * MAX_TRAVEL
    return px / MAX_TRAVEL, py / MAX_TRAVEL


# ------------------------------------------------------------------- parity with the client

def test_the_default_tuning_is_the_original_mapping_exactly():
    pts = [(0, 0), (5, 0), (10, 0), (0, 15), (-20, 0), (0, -25),
           (10, 10), (-20, 15), (31, 0), (40, 12), (-59, -2), (50, 50), (120, 0)]
    for px, py in pts:
        gx, gy = deflect(px, py, R)                    # default tuning
        cx, cy = client(px, py)
        assert abs(gx - cx) < 1e-12 and abs(gy - cy) < 1e-12, f"{px},{py}: ours ({gx:.4f},{gy:.4f}) client ({cx:.4f},{cy:.4f})"


def test_full_deflection_arrives_at_max_travel_not_at_the_rim():
    """The regression this whole module exists to prevent.

    Normalising by the radius is one character of difference and it costs a factor of
    two in travel, which on glass is the difference between a game registering "run"
    and never registering it.
    """
    x, _y = deflect(MAX_TRAVEL, 0.0, R)
    assert abs(x - 1.0) < 1e-12, f"full deflection not reached at {MAX_TRAVEL}px: {x}"
    # isclose, not ==: APP_SATURATE is a quotient, and 31/59.5*59.5 is not 31
    assert math.isclose(APP_SATURATE * R, MAX_TRAVEL, rel_tol=1e-12)
    assert MAX_TRAVEL == (OUTER - INNER) / 2.0, "MaxTravel must be evaluated, not quoted"


def test_the_port_needs_less_travel_than_the_rim_at_every_tuning():
    for name, t in PRESETS.items():
        # walk outward until full deflection; it must happen inside the disc
        hit = next((px for px in range(1, int(R) + 1)
                    if math.hypot(*deflect(px, 0.0, R)) > 0.999), None)
        assert hit is not None, f"{name}: full deflection unreachable inside the disc"
        assert hit <= R, f"{name}: saturates at {hit}px, outside the {R}px disc"


# --------------------------------------------------------------------- invariants

def test_more_travel_never_gives_less_deflection():
    for t in list(PRESETS.values()) + [StickTuning(0.9, 0.4, 2.5)]:
        prev = -1.0
        for px in range(0, 80):
            mag = math.hypot(*deflect(px, 0.0, R, t))
            assert mag >= prev - 1e-12, f"{t.describe()}: not monotonic at {px}px"
            prev = mag


def test_direction_is_kept_at_every_angle():
    """Scaling the axes independently would bend diagonals toward the octants."""
    t = StickTuning(0.6, 0.1, 0.7)
    for deg in range(0, 360, 5):
        a = math.radians(deg)
        px, py = 25 * math.cos(a), 25 * math.sin(a)
        gx, gy = deflect(px, py, R, t)
        if abs(px) > 1e-6 and abs(py) > 1e-6:
            assert abs(gx / gy - px / py) < 1e-9, f"{deg} deg: direction bent"


def test_magnitude_never_exceeds_one_and_starts_at_zero():
    t = StickTuning(0.2, 0.0, 0.4)          # saturates early, aggressive curve
    for px in range(0, 120):
        for py in range(-40, 41, 7):
            assert math.hypot(*deflect(px, py, R, t)) <= 1.0 + 1e-12
    assert deflect(0, 0, R, t) == (0.0, 0.0)


def test_the_dead_zone_is_continuous_and_eats_nothing_at_the_edge():
    t = StickTuning(0.6, 0.1, 1.0)
    dead_px = 0.1 * R
    assert deflect(dead_px * 0.99, 0.0, R, t) == (0.0, 0.0), "leaks below the dead zone"
    just = deflect(dead_px * 1.01, 0.0, R, t)[0]
    assert 0.0 < just < 0.05, f"steps to {just} at the dead-zone edge: discontinuity"


def test_curve_orders_sensibly_between_the_ends():
    lo = StickTuning(APP_SATURATE, 0.0, 0.5)      # responds sooner
    mid = StickTuning(APP_SATURATE, 0.0, 1.0)
    hi = StickTuning(APP_SATURATE, 0.0, 2.0)      # finer near the centre
    half = MAX_TRAVEL / 2.0
    a = math.hypot(*deflect(half, 0.0, R, lo))
    b = math.hypot(*deflect(half, 0.0, R, mid))
    c = math.hypot(*deflect(half, 0.0, R, hi))
    assert a > b > c, f"curve did not order: {a:.2f} {b:.2f} {c:.2f}"
    for t in (lo, mid, hi):
        assert math.hypot(*deflect(0, 0, R, t)) == 0.0
        assert abs(math.hypot(*deflect(MAX_TRAVEL, 0, R, t)) - 1.0) < 1e-12


def test_the_nub_is_a_pure_function_of_the_deflection():
    """The invariant that stops the picture lying about the input.

    Two different tunings that produce the same deflection must draw the same nub, and
    the nub must reach its limit exactly where the deflection does.
    """
    rng = MAX_TRAVEL                      # the 119 px stick's nub range
    # Half the original's travel with a saturation point half as far out gives the
    # same deflection as full travel on the original curve -- both are at 1.0.
    a = deflect(MAX_TRAVEL * 0.5, 0.0, R, StickTuning(APP_SATURATE * 0.5, 0.0, 1.0))
    b = deflect(MAX_TRAVEL, 0.0, R, StickTuning(APP_SATURATE, 0.0, 1.0))
    assert abs(a[0] - b[0]) < 1e-9, f"the two tunings disagree: {a} vs {b}"
    assert nub_offset(*a, rng) == nub_offset(*b, rng), "same input, different picture"
    full = deflect(MAX_TRAVEL, 0.0, R)
    assert abs(nub_offset(*full, rng)[0] - rng) < 1e-9, "nub limit != full deflection"


def test_full_deflection_is_exactly_full_past_the_saturation_point():
    """A thumb well past the saturation point must report a full stick, not 1 ulp under.

    `dx * (1 / dist)` is not exactly 1.0 for most dx, and the first version reported
    0.9999999999999999 for a thumb at 27.89 px on a curve that saturates at 23.25. Only
    the boundary itself is allowed to be a rounding error wide.
    """
    for t in PRESETS.values():
        for mult in (1.05, 1.5, 3.0):
            x, y = deflect(t.saturate * R * mult, 0.0, R, t)
            assert x == 1.0, f"{t.describe()} at {mult}x: {x!r}"
            assert y == 0.0
            x, y = deflect(0.0, -t.saturate * R * mult, R, t)
            assert y == -1.0 and x == 0.0, f"{t.describe()}: {x!r},{y!r}"


def test_degenerate_input_does_not_crash_or_move():
    assert deflect(10, 10, 0.0) == (0.0, 0.0)
    assert deflect(0, 0, R) == (0.0, 0.0)
    assert deflect(float("nan"), 0.0, R) == (0.0, 0.0), "NaN reached the stick"
    assert deflect(float("inf"), float("inf"), R) == (0.0, 0.0)


# --------------------------------------------------------------------- presets/persist

def test_clamping_keeps_the_stick_usable():
    t = StickTuning(0.001, 0.9, 50.0).clamp()
    assert t.saturate >= 0.10 and t.curve <= 3.0
    assert t.deadzone < t.saturate, "the dead zone swallowed all the travel"
    # and a stick with that tuning still reaches full deflection
    assert math.hypot(*deflect(MAX_TRAVEL * 2, 0.0, R, t)) > 0.9


def test_unknown_preset_says_so_and_keeps_the_original():
    t, warn = preset("bananas")
    assert warn, "an unknown preset was accepted silently"
    assert t.is_app_default
    t2, warn2 = preset("CRISP")
    assert warn2 is None and t2 == PRESETS["crisp"], "names are case sensitive?"
    assert all(v == v.clamp() for v in PRESETS.values()), "a preset ships unclamped"


def test_the_tuning_round_trips_through_the_layout():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "layout.json")
        t = StickTuning(0.61, 0.05, 0.75)
        write_frames(p, default_frames(1707, 1067), 0.85, 4,
                     screen=(1707, 1067), stick=t)
        assert load_stick_tuning(p) == t
        # and a file that never had one gets the original's curve, not zeros
        write_frames(p, default_frames(1707, 1067), 0.85, 4, screen=(1707, 1067))
        assert load_stick_tuning(p).is_app_default


def test_hand_edited_nonsense_falls_back_rather_than_crashing():
    for bad in (None, {}, [], "wide", {"saturate": "lots"},
                {"saturate": float("nan"), "curve": None}):
        t = from_dict(bad)
        assert isinstance(t, StickTuning) and t == t.clamp()
    assert from_dict({"saturate": 0.61, "deadzone": 0.05, "curve": 0.75}) ==  StickTuning(0.61, 0.05, 0.75)
    assert to_dict(StickTuning(0.6123456789, 0.0, 1.0))["saturate"] == 0.612346


def test_shape_is_a_unit_interval_for_every_reasonable_input():
    for t in list(PRESETS.values()) + [StickTuning(0.15, 0.14, 3.0)]:
        for x in (0.0, 0.01, 0.1, 0.3, 0.5, 0.9, 1.0, 2.0, 10.0):
            v = shape(x, t)
            assert 0.0 <= v <= 1.0, f"shape({x}, {t.describe()}) = {v}"


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
