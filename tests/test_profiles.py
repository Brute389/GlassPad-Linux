#!/usr/bin/env python3
"""Profile naming, resolution, the claim file, and when a switch is allowed.

Run directly: python3 tests/test_profiles.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad.layer_geom import default_frames, load_frames  # noqa: E402
from glasspad.layer_profiles import (  # noqa: E402
    DEFAULT_NAME, claim_expired, claim_path, cycle, decide, fighter_variant,
    list_profiles, profile_path, read_claim, resolve, safe_name, seed_from,
    write_claim,
)

SW, SH = 1707, 1067


# ------------------------------------------------------------------------- names

def test_names_are_normalised_or_refused():
    assert safe_name("Fighter") == "fighter"
    assert safe_name("  hollow_knight  ") == "hollow_knight"
    assert safe_name("ok.name-1_2") == "ok.name-1_2"
    for bad in ("", "   ", None, "../evil", "a/b", "two words", ".hidden",
                "-leading", "x" * 40, "\xfcran\xe9"):
        assert safe_name(bad) is None, f"{bad!r} should not be a filename"


def test_profiles_are_listed_sorted_and_backups_are_not():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "profiles"))
        for fn in ("fighter.json", "default.json", "racing.json.bak1",
                   "default.json.bak", "notes.txt", "..json", "ok-2.json"):
            open(os.path.join(d, "profiles", fn), "w").write("{}")
        assert list_profiles(d) == ["default", "fighter", "ok-2"]


def test_a_missing_directory_is_no_profiles_not_an_error():
    with tempfile.TemporaryDirectory() as d:
        assert list_profiles(d) == []


# ----------------------------------------------------------------------- resolution

def test_resolve_falls_back_in_the_right_order():
    with tempfile.TemporaryDirectory() as d:
        # nothing at all yet
        name, path, note = resolve(d, "fighter")
        assert path is None and "nothing to load" in note
        # the pre-profile install: one layout.json, no profiles directory
        legacy = os.path.join(d, "layout.json")
        open(legacy, "w").write("{}")
        name, path, note = resolve(d, "fighter")
        assert path == legacy and "layout.json" in note
        # a default appears -> it wins over the legacy file
        os.makedirs(os.path.join(d, "profiles"))
        open(os.path.join(d, "profiles", "default.json"), "w").write("{}")
        name, path, note = resolve(d, "fighter")
        assert name == DEFAULT_NAME and path.endswith("default.json")
        assert "no profile" in note
        # and the requested one wins when it exists
        open(os.path.join(d, "profiles", "fighter.json"), "w").write("{}")
        name, path, _n = resolve(d, "fighter")
        assert name == "fighter"


def test_an_unusable_name_is_said_out_loud_rather_than_becoming_default():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "profiles", "default.json"), "w") if os.makedirs(
            os.path.join(d, "profiles")) else None
        name, path, note = resolve(d, "../etc/passwd")
        assert name is None and path is None and "unusable" in note


def test_no_request_means_default():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "profiles"))
        open(os.path.join(d, "profiles", "default.json"), "w").write("{}")
        for none in (None, "", "   "):
            name, path, _note = resolve(d, none)
            assert name == DEFAULT_NAME, f"{none!r} did not mean default"


# -------------------------------------------------------------------------- cycling

def test_cycling_wraps_and_does_not_move_a_solo_profile():
    names = ["default", "fighter", "racing"]
    assert cycle("default", names) == "fighter"
    assert cycle("racing", names) == "default", "the cycle must wrap"
    assert cycle("fighter", names) == "racing"
    assert cycle("vanished", names) == "default", "unknown current should land somewhere"
    assert cycle("solo", ["solo"]) == "solo"
    assert cycle("default", []) is None


# ---------------------------------------------------------------------- claim file

def test_a_claim_round_trips():
    with tempfile.TemporaryDirectory() as d:
        write_claim(d, "fighter", source="steam", pid=4242)
        got = read_claim(d)
        assert got and got["profile"] == "fighter" and got["source"] == "steam"
        assert got["pid"] == 4242 and isinstance(got["at"], int)
        assert os.path.exists(claim_path(d))


def test_a_junk_claim_is_no_claim_rather_than_a_crash():
    for body in ("", "{", "not json", "[]", "{}", '{"profile": "../x"}',
                 '{"profile": 42}', '{"profile": ""}'):
        with tempfile.TemporaryDirectory() as d:
            open(claim_path(d), "w").write(body)
            assert read_claim(d) is None, f"{body!r} was accepted as a claim"


def test_a_claim_never_leaves_a_temp_file_behind():
    with tempfile.TemporaryDirectory() as d:
        for n in ("a", "b", "c"):
            write_claim(d, n)
        assert sorted(os.listdir(d)) == ["profile.active"],  f"stray files: {sorted(os.listdir(d))}"


def test_expiry_is_off_unless_asked_for():
    claim = {"profile": "fighter", "at": 1000}
    assert not claim_expired(claim, 10 ** 9, 0)
    assert not claim_expired(claim, 1001, 60)
    assert claim_expired(claim, 1061, 60)
    assert claim_expired({"profile": "x"}, 100, 60), "an undateable claim was trusted"
    assert not claim_expired(None, 10 ** 9, 60)


# ------------------------------------------------------------------------ the decision

def test_a_held_control_defers_the_switch():
    action, name = decide("default", "fighter", held=True)
    assert action == "defer" and name == "fighter"
    action, name = decide("default", "fighter", held=False)
    assert action == "switch" and name == "fighter"


def test_the_same_profile_is_not_a_switch_at_all():
    assert decide("fighter", "fighter", held=False) == ("none", None)
    assert decide("fighter", "FIGHTER", held=True) == ("none", None),  "case difference re-applied the layout you are already using"


def test_nonsense_and_empty_requests_do_nothing():
    for bad in ("", None, "two words", "../x"):
        assert decide("default", bad, held=False) == ("none", None)


def test_an_expired_claim_goes_home_to_default():
    claim = {"profile": "fighter", "at": 1000}
    # current is the claimed profile and the claim has gone stale: go home to default
    action, name = decide("fighter", "fighter", held=False, timeout=60, now=2000,
                          claim=claim)
    assert (action, name) == ("switch", DEFAULT_NAME), "a stale claim was kept"
    # and once we are home, an expired claim asking for the same thing is no action
    action, name = decide(DEFAULT_NAME, "fighter", held=False, timeout=60, now=2000,
                          claim=claim)
    assert (action, name) == ("none", None), "re-applied the profile we are already on"
    action, name = decide("racing", "racing", held=False, timeout=60, now=2000,
                          claim=claim)
    assert (action, name) == ("switch", DEFAULT_NAME), "a stale claim never let go"
    # still sticky when the timeout is off, which is the default
    assert decide("racing", "racing", held=False, timeout=0, now=2000,
                  claim=claim)[0] == "none"


# -------------------------------------------------------------------------- seeding

def test_default_profile_is_the_current_layout_byte_for_byte():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "layout.json")
        blob = open(os.path.expanduser("~/.config/GlassPad/layout.json"),
                    "rb").read() if os.path.exists(
            os.path.expanduser("~/.config/GlassPad/layout.json")) else b'{"a":1}'
        open(src, "wb").write(blob)
        done = seed_from(d, src, [DEFAULT_NAME, "fighter"])
        assert [n for n, _s in done] == ["default", "fighter"]
        assert open(profile_path(d, DEFAULT_NAME), "rb").read() == blob
        assert all(s == "created" for _n, s in done)


def test_seeding_never_clobbers_and_says_what_it_did():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "layout.json")
        open(src, "w").write('{"v":1}')
        seed_from(d, src, ["default"])
        open(profile_path(d, "default"), "w").write('{"edited":true}')
        done = dict(seed_from(d, src, ["default"]))
        assert done["default"] == "already exists, left alone"
        assert json.load(open(profile_path(d, "default"))) == {"edited": True}
        # a source that is not there is reported, not raised
        gone = dict(seed_from(d, os.path.join(d, "nope.json"), ["x"]))
        assert "could not read" in gone["x"]


# ------------------------------------------------------------------- the fighter seed

def test_the_fighter_variant_makes_the_pad_bigger_and_the_sticks_smaller():
    fr = default_frames(SW, SH)
    out, mode = fighter_variant(fr, SW, SH)
    assert mode == 8
    before = {f.name: f for f in fr}
    after = {f.name: f for f in out}
    assert after["dpad"].w > before["dpad"].w
    for n in ("LS", "RS"):
        assert after[n].w < before[n].w, "sticks must not grow"
        # same centre: the thumb lands on the stick it expects
        assert abs((after[n].x + after[n].w / 2) - (before[n].x + before[n].w / 2)) < 1e-6
        assert abs((after[n].y + after[n].h / 2) - (before[n].y + before[n].h / 2)) < 1e-6
    for name in ("A", "B", "X", "Y", "LT", "RT", "LB", "RB"):
        assert after[name] == before[name], f"{name} changed for no reason"


def test_the_variant_keeps_everything_on_the_screen():
    """Through the pipeline the overlay actually uses: fit to the screen, then vary.

    Authoring is landscape; a portrait panel gets the frames fitted first, and it is the
    combination that must stay on screen -- checking the raw variant against a portrait
    size only proves that landscape frames do not fit portrait, which we already know.
    """
    from glasspad.layer_geom import fit_frames
    for sw, sh in ((SW, SH), (1067, 1707), (800, 1280)):
        fr = fit_frames(default_frames(SW, SH), SW, SH, sw, sh)
        out, _m = fighter_variant(fr, sw, sh)
        for f in out:
            assert f.x >= -1e-9 and f.y >= -1e-9, f"{f.name} off the top/left"
            assert f.x + f.w <= sw + 1e-6, f"{sw}x{sh}: {f.name} past the right edge"
            assert f.y + f.h <= sh + 1e-6, f"{sw}x{sh}: {f.name} past the bottom"


def test_the_variant_is_a_valid_layout_file():
    """If the seed cannot be read back by the real loader it is not a profile."""
    from glasspad.layer_geom import write_frames
    with tempfile.TemporaryDirectory() as d:
        fr = default_frames(SW, SH)
        out, mode = fighter_variant(fr, SW, SH)
        p = os.path.join(d, "fighter.json")
        write_frames(p, out, 0.9, mode, screen=(SW, SH))
        frames, opacity, dpad_mode, note = load_frames(p, SW, SH)
        # The loader returns a note on success too; what matters is that it did not
        # REFUSE the file, which is the path where it hands back defaults and says so.
        assert "refus" not in note.lower(), f"loader refused it: {note}"
        assert dpad_mode == 8 and abs(opacity - 0.9) < 1e-9
        assert len(frames) == 16


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
