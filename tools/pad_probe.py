#!/usr/bin/env python3
"""Read the virtual Xbox 360 pad straight from evdev -- stdlib only.

python-evdev is not installed, but the user is in the `input` group, so
/dev/input/eventN is readable. This exists so overlay input can be confirmed at
the kernel boundary instead of by eye: if a touch produces the right EV_KEY /
EV_ABS pair here, the game will see it too.

Usage:
    python3 tools/pad_probe.py              # print events until Ctrl-C
    python3 tools/pad_probe.py --summary    # count per code, print on exit
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

# struct input_event: struct timeval (2 x long) + __u16 type, code + __s32 value
EVENT = struct.Struct("@llHHi")

EV_KEY, EV_ABS, EV_SYN = 0x01, 0x03, 0x00

#: Names come from the device definition itself. A hand-copied table here called
#: 306 "Y", which is the BTN_NORTH convention -- but xd360 deliberately uses the
#: codes xpad reports (BTN_A/B/X/Y = 304/305/307/308), so the probe disagreed
#: with the very device it was auditing. Single source of truth, no drift.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from glasspad import xd360 as _xd360
    KEY_NAMES = {code: name for name, code in _xd360.BUTTON_CODES.items()}
except Exception as _exc:                                  # pragma: no cover
    print(f"  (falling back to numeric labels: {_exc})")
    KEY_NAMES = {}
ABS_NAMES = {
    0: "ABS_X (LS x)", 1: "ABS_Y (LS y)", 2: "ABS_Z (LT)",
    3: "ABS_RX (RS x)", 4: "ABS_RY (RS y)", 5: "ABS_RZ (RT)",
    16: "ABS_HAT0Y", 17: "ABS_HAT0X",   # 0x10 / 0x11, not 9/10
}


def find_pad(name_fragment: str) -> tuple[str | None, str]:
    """Return (event device path, name) for the first match under /sys."""
    for entry in sorted(os.listdir("/sys/class/input")):
        if not entry.startswith("event"):
            continue
        path = f"/sys/class/input/{entry}/device/name"
        try:
            with open(path, encoding="utf-8") as fh:
                name = fh.read().strip()
        except OSError:
            continue
        if name_fragment.lower() in name.lower():
            return f"/dev/input/{entry}", name
    return None, ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--name", default="X-Box 360 pad")
    ap.add_argument("--summary", action="store_true", help="aggregate per code")
    args = ap.parse_args(argv)

    dev, name = find_pad(args.name)
    if not dev:
        print(f"  no input device matching {args.name!r}; start the overlay first")
        return 3
    print(f"  listening on {dev}  ({name!r})  Ctrl-C to stop")

    counts: dict[tuple[int, int], int] = {}
    last_value: dict[tuple[int, int], int] = {}
    try:
        fd = os.open(dev, os.O_RDONLY)
    except OSError as exc:
        print(f"  cannot open {dev}: {exc}  (need the input group)")
        return 4
    try:
        while True:
            buf = os.read(fd, EVENT.size * 64)
            if not buf:
                break
            for off in range(0, len(buf) - len(buf) % EVENT.size, EVENT.size):
                _sec, _usec, ev_type, code, value = EVENT.unpack_from(buf, off)
                if ev_type == EV_SYN:
                    continue
                key = (ev_type, code)
                counts[key] = counts.get(key, 0) + 1
                if value == last_value.get(key):
                    continue                      # suppress repeat spam
                last_value[key] = value
                kind = "KEY" if ev_type == EV_KEY else "ABS" if ev_type == EV_ABS else f"0x{ev_type:02x}"
                label = KEY_NAMES.get(code, f"code{code}") if ev_type == EV_KEY else ABS_NAMES.get(code, f"code{code}")
                print(f"  {kind:<4} {label:<16} = {value}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        os.close(fd)

    print("\n  summary (events per code):")
    for (ev_type, code), n in sorted(counts.items()):
        label = KEY_NAMES.get(code, f"code{code}") if ev_type == EV_KEY else ABS_NAMES.get(code, f"code{code}")
        kind = "KEY" if ev_type == EV_KEY else "ABS" if ev_type == EV_ABS else f"0x{ev_type:02x}"
        print(f"    {kind:<4} {label:<16} {n:>6}")
    print("\n  (names come from xd360.BUTTON_CODES; stick ranges are as declared in AXIS_SPECS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
