"""Profiles: named layouts, and how one gets chosen.

A profile is not a new format. Everything a profile needs to express -- the sixteen
rectangles, opacity, d-pad mode and style, press treatment, turbo arming and rate, stick
response, the screen it was authored for -- is already in the layout file, so a profile
is a layout file with a name. What this module adds is naming, a claim file a launcher
can write, and the decision about whether a change can be applied *now*.

Why the launcher and not the compositor. On this machine, verified:
KWin 6.8 advertises no zwlr_foreign_toplevel_manager_v1 (so nothing like kdotool can
work); the legacy JS scripting API accepts a script, returns 0, registers nothing and
logs nothing -- it is a no-op; and XWayland reports an active window with no WM_CLASS and
cannot see a Wayland-native game at all. Every compositor route is either absent or
silently wrong exactly when a game is up. A launcher already knows what it launched, and
telling us costs it one command.

The other half is timing, and it is the half that ruins a game if it is wrong: a profile
change must never land while a control is held. `decide()` is that rule, and the caller
retries it when the last contact is released.
"""

from __future__ import annotations

import json
import os
import re
import time

from glasspad.layer_geom import Frame, atomic_write_json

DEFAULT_NAME = "default"
PROFILE_DIRNAME = "profiles"
CLAIM_FILENAME = "profile.active"
LEGACY_LAYOUT = "layout.json"

#: A profile name ends up in a filename and is typed into a Steam launch option, so it
#: is kept boring: no separators, no leading dot, no length that breaks a terminal.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


# ----------------------------------------------------------------------- names/paths

def safe_name(value) -> str | None:
    """Normalise a name a human typed, or return None if it cannot be used.

    Rejecting is the important path: this string becomes a filename, and "" or ".." or
    "two words" arriving from a launcher should say so rather than write a file nobody
    can select.
    """
    if value is None:
        return None
    name = str(value).strip().lower()
    return name if _NAME_RE.match(name) else None


def profiles_dir(cfg_dir: str) -> str:
    return os.path.join(cfg_dir, PROFILE_DIRNAME)


def profile_path(cfg_dir: str, name: str) -> str:
    return os.path.join(profiles_dir(cfg_dir), f"{name}.json")


def list_profiles(cfg_dir: str) -> list[str]:
    """Profile names that exist, sorted, backups excluded.

    `.bak1` and friends are written by the save path; showing them as profiles would put
    three copies of the same layout in a cycle the user has to step through.
    """
    try:
        entries = os.listdir(profiles_dir(cfg_dir))
    except OSError:
        return []
    out = []
    for fn in entries:
        if not fn.endswith(".json"):
            continue
        stem = fn[:-5]
        if ".bak" in stem:
            continue
        if safe_name(stem) == stem:
            out.append(stem)
    return sorted(out)


def resolve(cfg_dir: str, name) -> tuple[str | None, str | None, str]:
    """(name, path, note) for a requested profile, falling back honestly.

    The fallback chain is requested -> `default` -> the pre-profile `layout.json`. The
    last one matters: an install that has used the overlay for a month has one layout and
    no profiles directory, and the first `glasspad-profile whatever` from a launcher must
    not leave it with nothing on screen.
    """
    name = str(name).strip() if name is not None else None
    want = safe_name(name) if name else None
    if want is None and name:
        return (None, None, f"ignoring unusable profile name {name!r}")
    if want is None:
        want = DEFAULT_NAME
    path = profile_path(cfg_dir, want)
    if os.path.exists(path):
        return want, path, ""
    if want != DEFAULT_NAME:
        dflt = profile_path(cfg_dir, DEFAULT_NAME)
        if os.path.exists(dflt):
            return DEFAULT_NAME, dflt, f"no profile {want!r}; using default"
    legacy = os.path.join(cfg_dir, LEGACY_LAYOUT)
    if os.path.exists(legacy):
        return want, legacy, (f"no profile {want!r} and no default; "
                              f"falling back to {LEGACY_LAYOUT}")
    return want, None, f"nothing to load for profile {want!r}"


def cycle(current: str | None, names: list[str]) -> str | None:
    """The next profile in the hub's cycle. Wraps; a single profile does not move."""
    if not names:
        return None
    if current not in names:
        return names[0]
    return names[(names.index(current) + 1) % len(names)]


# ------------------------------------------------------------------------- the claim

def claim_path(cfg_dir: str) -> str:
    return os.path.join(cfg_dir, CLAIM_FILENAME)


def write_claim(cfg_dir: str, name: str, *, source: str = "cli",
                pid: int | None = None) -> str:
    """Record which profile a launcher wants. Atomic: the overlay reads this file."""
    path = claim_path(cfg_dir)
    os.makedirs(cfg_dir, mode=0o700, exist_ok=True)
    # keep=0: this file changes every time a game starts. Backing it up would push the
    # user's layout backups out of the rotation to protect a value that is cheap to
    # re-type and is nobody's authored work.
    atomic_write_json(path, {"profile": name, "at": int(time.time()),
                             "source": source, "pid": pid}, keep=0)
    return path


def read_claim(cfg_dir: str) -> dict | None:
    """The claim, or None if there isn't a usable one. Never raises.

    Hand-edited and half-written files are expected here: it is a file people will cat
    and sometimes echo into.
    """
    try:
        with open(claim_path(cfg_dir), encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    # A number is not a profile name even when str() of it looks like one: {"profile":
    # 42} is a hand-edit mistake, and reading it as "42" makes the overlay resolve a
    # name nobody typed.
    raw_name = raw.get("profile")
    name = safe_name(raw_name) if isinstance(raw_name, str) else None
    if name is None:
        return None
    out = dict(raw)
    out["profile"] = name
    return out


def claim_expired(claim: dict | None, now: float, timeout: float) -> bool:
    """Whether a claim is old enough to stop honouring.

    A launcher that cannot run a command on exit (Steam launch options have no exit
    hook) leaves its profile claimed after the game is gone. Sticky is the better
    default -- a reverted layout mid-alt-tab is worse -- but the option exists for
    people who would rather go back to `default` on their own.
    """
    if timeout is None or timeout <= 0.0:
        return False
    if not claim:
        return False
    try:
        at = float(claim.get("at", 0.0))
    except (TypeError, ValueError):
        return True                      # a claim we cannot date is a claim we cannot trust
    return (now - at) > timeout


def decide(current: str | None, requested, held: bool,
           timeout: float = 0.0, now: float | None = None,
           claim: dict | None = None) -> tuple[str, str | None]:
    """What to do about a requested profile: ("switch"|"defer"|"none", name).

    `held` is the whole point. A layout that changes while a thumb is on the stick moves
    the control out from under the finger, which is the one thing profiles must never do;
    the caller defers and calls again when nothing is held.
    """
    want = safe_name(requested) if requested else None
    if want is None:
        return "none", None
    if claim_expired(claim, now if now is not None else time.time(), timeout):
        want = DEFAULT_NAME
        if want == current:
            return "none", None
    if want == current:
        return "none", None
    if held:
        return "defer", want
    return "switch", want


# ---------------------------------------------------------------------- seed profiles

def seed_from(cfg_dir: str, source_layout: str,
              names=(DEFAULT_NAME,)) -> list[tuple[str, str]]:
    """Copy an existing layout into named profiles. Returns (name, note) per profile.

    `default` is a byte-for-byte copy. It is the safety net -- what an unclaimed window
    gets, and what you revert to -- so nothing about it may be "improved" on the way in.
    """
    out: list[tuple[str, str]] = []
    try:
        with open(source_layout, "rb") as fh:
            blob = fh.read()
    except OSError as exc:
        return [(n, f"could not read {source_layout}: {exc}") for n in names]
    os.makedirs(profiles_dir(cfg_dir), mode=0o700, exist_ok=True)
    for name in names:
        key = safe_name(name)
        if key is None:
            out.append((name, "unusable name, skipped"))
            continue
        dest = profile_path(cfg_dir, key)
        if os.path.exists(dest):
            out.append((key, "already exists, left alone"))
            continue
        with open(dest, "wb") as fh:
            fh.write(blob)
        out.append((key, "created"))
    return out


#: The fighter preset, as fractions of the authored sizes. A bigger d-pad because 8-way
#: on a 180 px cross is a coin toss between two directions, and smaller sticks because
#: a fighting game does not use them and they are in the way of the thumb anyway.
DPAD_GROW = 4.0 / 3.0
STICK_SHRINK = 3.0 / 4.0


def fighter_variant(frames: list[Frame], sw: int, sh: int) -> tuple[list[Frame], int]:
    """A starting point, not a finished layout: bigger d-pad, sticks tucked away.

    Everything else is left exactly as authored -- including the stick *positions*,
    because someone who has played this long has muscle memory about where the thumbs
    rest, and moving them is a decision for edit mode rather than for a script.
    """
    out: list[Frame] = []
    for f in frames:
        if f.kind == "dpad":
            w, h = f.w * DPAD_GROW, f.h * DPAD_GROW
            # grow to the right and down from the authored corner, then pull back on
            # screen: the d-pad lives in the bottom-left and growing it off the panel is
            # the one way this variant could become unusable
            x, y = f.x, f.y
            x = min(max(0.0, x), max(0.0, sw - w))
            y = min(max(0.0, y), max(0.0, sh - h))
            out.append(Frame(f.name, f.kind, x, y, w, h))
        elif f.kind == "stick":
            w, h = f.w * STICK_SHRINK, f.h * STICK_SHRINK
            # keep the centre where it was, so the thumb lands on the stick it expects
            cx, cy = f.x + f.w / 2.0, f.y + f.h / 2.0
            x = min(max(0.0, cx - w / 2.0), max(0.0, sw - w))
            y = min(max(0.0, cy - h / 2.0), max(0.0, sh - h))
            out.append(Frame(f.name, f.kind, x, y, w, h))
        else:
            out.append(f)
    return out, 8
