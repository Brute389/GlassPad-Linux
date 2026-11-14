"""Snapshot the kernel's input devices and report any GlassPad pad that appeared.

    python3 tools/uinput_watch.py before
    python3 tools/uinput_watch.py check          # after running the tests

Or let it run the command itself, so nothing can be missed between the steps:

    python3 tools/uinput_watch.py run -- python3 tools/run_tests.py

Why this exists: a green test suite does not prove no virtual gamepad was
created. PadService turns backend errors into a status line, so a test that
opens /dev/uinput and fails just as happily reports success as one that never
tries. This asks the kernel directly instead of trusting the code under test.

Reads only /sys/class/input, so it needs no root and changes nothing. Markers
are the phys the backend registers ("glasspad/virtual0") and the device name
("Microsoft X-Box 360 pad"); phys is the strong one, since no other driver can
produce it.

Limitation worth knowing: the device only appears in sysfs for as long as its
file descriptor is open. The race this guards closed opens and closes the pad
within microseconds, so a before/after diff can legitimately report clean while
the device briefly existed (the runner's uinput-leg check in tools/run_tests.py counts those constructions)
instead; run both.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

BASE = pathlib.Path("/sys/class/input")
MARKERS = ("glasspad", "X-Box 360")
STATE = pathlib.Path("/tmp/glasspad-input-snapshot.txt")


def devices() -> set[str]:
    """Every input device as 'N <name>' / 'P <phys>' lines.

    name and phys live on inputN itself, not on inputN/device/ -- reading the
    wrong level yields an empty snapshot that looks perfectly clean, which is
    the worst possible false result.
    """
    found: set[str] = set()
    for entry in BASE.glob("input*"):
        for kind, attr in (("N", "name"), ("P", "phys")):
            try:
                text = (entry / attr).read_text(errors="replace").strip()
            except OSError:
                continue
            if text:
                found.add(f"{kind} {text}")
    return found


def matched(lines) -> list[str]:
    return [l for l in lines
            if any(m.lower() in l.lower() for m in MARKERS)]


def count() -> int:
    n = len([d for d in BASE.glob("input*") if (d / "name").exists()])
    return n


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "check"

    if mode == "run":
        cmd = argv[2:]
        # The documented form is `run -- <command>`. That -- separates this
        # script's own arguments from the child's; it is not part of the child's
        # argv. Passing it through made exec look for a program literally named
        # "--", so the headline usage in the docstring died with
        # FileNotFoundError before the command ever ran.
        if cmd[:1] == ["--"]:
            cmd = cmd[1:]
        if not cmd:
            print("run needs a command (after --)", file=sys.stderr)
            return 2
        before = devices()
        print(f"before: {count()} input devices", flush=True)
        rc = subprocess.call(cmd)
        print(f"command exit: {rc}", flush=True)
        return verdict(before, rc)

    if mode == "before":
        snap = devices()
        STATE.write_text("\n".join(sorted(snap)) + "\n")
        print(f"before: {count()} input devices; baseline saved to {STATE}")
        return 0

    if not STATE.exists():
        print("no baseline: run 'python3 tools/uinput_watch.py before' first",
              file=sys.stderr)
        return 2
    before = set(STATE.read_text().splitlines())
    return verdict(before, 0)


def verdict(before, run_rc: int) -> int:
    new = sorted(devices() - before)
    leaks = matched(new)
    if leaks:
        print("\nLEAK: a GlassPad device reached the input layer:")
        for line in leaks:
            print(f"  + {line}")
        return 1
    extra = [l for l in new if l.startswith("N ")]
    print(f"\nclean: no GlassPad device among {len(new)} new entries "
          f"({count()} devices now)")
    if extra:
        print("  other new devices (not GlassPad):")
        for line in extra[:10]:
            print(f"    {line}")
    return run_rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
