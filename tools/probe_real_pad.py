#!/usr/bin/env python3
"""Create the REAL virtual pad and read it back from the kernel.

    python3 tools/probe_real_pad.py            # create, inspect, destroy
    python3 tools/probe_real_pad.py --hold 30  # keep it open 30s to poke at

Every other check in this repo proves logic. This one proves the device: that
/dev/uinput accepts our declaration, that the kernel registers it with the
identity userspace looks for, that the axis ranges survived, and that destroying
it leaves nothing behind. It is only meaningful on a machine where /dev/uinput
is writable.

Verified on real hardware (ROG Flow Z13, 2026-11-06): registers with
PRODUCT=3/45e/28e/110, `B: EV=b`, `B: ABS=3003f` and the 15-bit KEY mask that
AXIS_SPECS/BUTTON_CODES describe -- exact, with no ABS_MT bits -- and all eight
EVIOCGABS ranges come back as declared. Note that nothing binds this device in
kernel space: xpad is a USB driver matching on interface descriptors, and a
uinput device has no USB parent, so `H: Handlers=` lists evdev plus the joydev
js node and that is the correct and complete answer.

Needs no pytest and no evdev -- just the standard library.

Design notes that matter:

  * A uinput device exists only while its file descriptor is open, so the
    inspection happens with the pad live and a --hold window lets you look from
    another shell (sdl2-joystick-from, jstest-gtk, evhz, /proc/bus/input).
  * Destroying is explicit (UI_DEV_DESTROY via backend.close()) in a finally
    block, and SIGINT is handled, so Ctrl-C cannot leave a phantom controller
    behind. If the process is SIGKILLed the kernel still reaps the device when
    the fd closes, which is the backstop.
  * Nothing here trusts the object we just built: identity and ranges are read
    back from sysfs and from EVIOCGABS on the event node, i.e. from the kernel.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import os
import pathlib
import signal
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from glasspad import xd360 as X  # noqa: E402
from glasspad.pad import UinputBackend  # noqa: E402

SYS_INPUT = pathlib.Path("/sys/class/input")
DEV_INPUT = pathlib.Path("/dev/input")
PROC_DEVICES = pathlib.Path("/proc/bus/input/devices")

# EVIOCGABS(abs) = _IOR('E', 0x40 + abs, struct input_absinfo); with
# _IOC_READ=2 and sizeof(input_absinfo)==24 that is 0x80184540 + abs.
EVIOCGABS_BASE = (2 << 30) | (24 << 16) | (ord("E") << 8) | 0x40


class AbsInfo(ctypes.Structure):
    _fields_ = [
        ("value", ctypes.c_int32), ("minimum", ctypes.c_int32),
        ("maximum", ctypes.c_int32), ("fuzz", ctypes.c_int32),
        ("flat", ctypes.c_int32), ("resolution", ctypes.c_int32),
    ]


def find_pads(sys_base: pathlib.Path | None = None,
              phys: str = UinputBackend.PHYS,
              name: str = UinputBackend.DEVICE_NAME) -> list[pathlib.Path]:
    """inputN dirs whose phys or name matches our pad.

    phys is the strong signal (no other driver can emit "glasspad/virtual0");
    name catches the case where UI_SET_PHYS was refused, which the backend
    treats as cosmetic. Both attributes live on inputN itself -- reading
    inputN/device/name yields nothing and would falsely report "no device".
    """
    # Resolve at CALL time, not import time. A default of `sys_base=SYS_INPUT`
    # freezes the path into the signature at first import, so a harness that
    # reassigns SYS_INPUT still queries the real /sys and finds nothing -- which
    # reads as "the pad was never created" instead of "wrong directory".
    if sys_base is None:
        sys_base = SYS_INPUT
    hits: list[pathlib.Path] = []
    if not sys_base.is_dir():
        return hits
    for entry in sorted(sys_base.glob("input*")):
        try:
            got_phys = (entry / "phys").read_text().strip()
            got_name = (entry / "name").read_text().strip()
        except OSError:
            continue
        if got_phys == phys or got_name == name:
            hits.append(entry)
    return hits


def event_node(input_dir: pathlib.Path,
               dev_input: pathlib.Path | None = None) -> pathlib.Path | None:
    """The /dev/input/eventN node for an inputN sysfs dir.

    sysfs lists the event child as a SYMLINK named eventN, so its .name is what
    identifies it and its own path is NOT the character device. Returning
    /sys/class/input/input0/event0 would hand os.open() a sysfs symlink, every
    EVIOCGABS ioctl would fail, and the report would read "the axis ranges are
    wrong" when the ranges are fine. Take the name, re-anchor it under
    /dev/input.
    """
    if dev_input is None:
        dev_input = DEV_INPUT
    for child in sorted(input_dir.glob("event*")):
        node = dev_input / child.name
        if node.exists():
            return node
    # No nested event entry: derive it from the id number (inputN -> eventN is
    # the usual pairing, worth attempting before giving up on verification).
    digits = "".join(c for c in input_dir.name if c.isdigit())
    node = dev_input / f"event{digits}"
    return node if node.exists() else None


def read_abs(fd: int, code: int) -> tuple[int, int, int] | None:
    """(minimum, maximum, value) the kernel actually holds for one axis."""
    buf = AbsInfo()
    try:
        fcntl.ioctl(fd, EVIOCGABS_BASE + code, buf)
    except OSError:
        return None
    return buf.minimum, buf.maximum, buf.value


def identity_from_proc(name: str, phys: str,
                       proc_path: pathlib.Path | None = None) -> list[str]:
    """The full /proc/bus/input/devices stanza for our pad.

    Two things this format demands, both found by testing against a real
    stanza rather than from memory:

      * the field is 'N: Name="..."' -- name EQUALS quoted value. Writing it as
        'name:"..."' (colon, no equals) matches nothing, and a check that cannot
        fire is worse than no check: it reads as "we looked, it was fine";
      * the phys line is 'P: Phys=glasspad/virtual0' with a capital P, so the
        comparison must be case-folded.
    """
    if proc_path is None:
        proc_path = PROC_DEVICES
    try:
        text = proc_path.read_text()
    except OSError:
        return []
    want_name = f'name="{name}"'.lower()
    want_phys = phys.lower()
    out: list[str] = []
    for block in text.split("\n\n"):
        low = block.lower()
        if want_name in low and want_phys in low:
            out.extend(line.strip() for line in block.splitlines() if line.strip())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hold", type=float, default=6.0,
                    help="seconds to keep the pad alive (default 6)")
    ap.add_argument("--path", default="/dev/uinput")
    args = ap.parse_args(argv)

    if not os.access(args.path, os.W_OK):
        print(f"FAIL  {args.path} is not writable by you")
        print("      run: sudo ./install.sh, then log out and back in")
        # One gr_name lookup failure (an id with no /etc/group entry, common in
        # containers) must not sink the diagnostic this line exists to print.
        try:
            import grp
            names = " ".join(grp.getgrgid(g).gr_name for g in os.getgroups())
        except Exception:  # noqa: BLE001
            names = " ".join(str(g) for g in os.getgroups())
        print(f"      your groups: {names}")
        return 2

    fails = 0
    rc = 0          # success unless a handler below overrides it
    print(f"creating pad on {args.path} ...")
    try:
        backend = UinputBackend(args.path)
    except OSError as exc:
        reason = {errno.EACCES: "permission denied (udev rule / group?)",
                  errno.ENOENT: "node missing (module not loaded?)",
                  errno.EBUSY: "device name already in use"}.get(exc.errno, str(exc))
        print(f"FAIL  construction raised {exc.__class__.__name__}: {reason}")
        return 1

    try:
        print(f"ok    created via {backend.name}\n")
        time.sleep(0.25)          # let udev finish before sysfs is read

        pads = find_pads(SYS_INPUT)
        if not pads:
            print("FAIL  kernel registered nothing matching our phys/name")
            fails += 1
        else:
            for d in pads:
                try:
                    got_name = (d / "name").read_text().strip()
                    got_phys = (d / "phys").read_text().strip()
                except OSError:
                    got_name = got_phys = ""
                print(f"ok    {d.name}  name={got_name!r}  phys={got_phys!r}")
                # phys deserves its own assertion rather than just being printed.
                # UinputBackend treats a failed UI_SET_PHYS as cosmetic, and
                # find_pads matches on name OR phys so the device is still found
                # -- but uinput_watch names phys "the strong marker, since no
                # other driver can produce it". Without it, a leaked pad from a
                # future crash is invisible to the leak checker while every
                # other tool still reports the pad as ours. So: found, but flag
                # the lost marker.
                if got_phys != UinputBackend.PHYS:
                    fails += 1
                    print("      FAIL phys is not "
                          f"{UinputBackend.PHYS!r}: UI_SET_PHYS was refused, so "
                          "tools/uinput_watch.py cannot detect this device as a "
                          "GlassPad leak (its name-only fallback also matches "
                          "real pads, so leaks would be reported as 'clean')")
                # PRODUCT is on inputN/uevent. inputN/device/uevent holds only
                # DRIVER/MODALIAS, so reading it there yields no PRODUCT key and
                # the check reports FAIL against a perfectly good device.
                ue = d / "uevent"
                try:
                    # Values are quoted in sysfs (NAME="..."), so strip the
                    # quotes from the VALUE only -- split() gives a list, which
                    # has no .strip().
                    kv = {}
                    for line in ue.read_text().splitlines():
                        if "=" not in line:
                            continue
                        key, _, val = line.partition("=")
                        kv[key.strip()] = val.strip().strip('"')
                except OSError:
                    kv = {}
                want = "3/45e/28e/110"
                got_prod = kv.get("PRODUCT", "")
                good = got_prod == want
                if not good:
                    fails += 1
                print(f"{'ok  ' if good else 'FAIL'}  uevent PRODUCT={got_prod!r} "
                      f"(want {want!r} = bustype 3 USB, Microsoft 045e, "
                      f"360 wired 028e, version 0110)")
                if not good and got_prod:
                    print("      the identity written by UinputBackend._create_device "
                          "is not what reached the kernel")

        proc = identity_from_proc(UinputBackend.DEVICE_NAME,
                                  UinputBackend.PHYS, PROC_DEVICES)
        for line in proc:
            print(f"      proc  {line}")

        # Ranges are the part that can silently be wrong: only the legacy
        # uinput_user_dev write() carries absinfo. Without it the absinfo array
        # stays kzalloc'd, so EVIOCGABS reports 0..0 on a declared axis -- the
        # stick is dead to anything normalising by the range, rather than
        # travelling too far (the kernel clamps injected values against
        # nothing).
        #
        # The labels here are the ONLY thing tying a code to a body part, so
        # they are cross-checked: a wrong one would print a confident wrong
        # reading of the pad while every value still matched AXIS_SPECS.
        node = event_node(pads[0], DEV_INPUT) if pads else None
        if node is None:
            print("warn  no /dev/input node readable; cannot verify axis ranges")
        else:
            try:
                fd = os.open(str(node), os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                print(f"warn  cannot open {node} ({exc.strerror}); "
                      f"axis ranges unverified")
                fd = None
            if fd is not None:
                try:
                    print(f"\naxis ranges as the kernel reports them via {node}:")
                    # Physical labels must follow xpad's own assignment, not the
                    # order the codes happen to be declared in: sticks are
                    # ABS_X/Y + ABS_RX/RY, triggers ABS_Z/RZ, hat ABS_HAT0X/Y.
                    for code, label in ((X.ABS_X, "left stick X"),
                                        (X.ABS_Y, "left stick Y"),
                                        (X.ABS_RX, "right stick X"),
                                        (X.ABS_RY, "right stick Y"),
                                        (X.ABS_Z, "left trigger"),
                                        (X.ABS_RZ, "right trigger"),
                                        (X.ABS_HAT0X, "dpad X"),
                                        (X.ABS_HAT0Y, "dpad Y")):
                        got = read_abs(fd, code)
                        if got is None:
                            print(f"  FAIL  {label:<14} EVIOCGABS refused")
                            fails += 1
                            continue
                        lo, hi, val = got
                        spec = X.AXIS_SPECS.get(code)
                        if spec is not None:
                            good = (lo, hi) == (spec.minimum, spec.maximum)
                        else:
                            good = (lo, hi) == (-1, 1)
                        if not good:
                            fails += 1
                        print(f"  {'ok  ' if good else 'FAIL'} {label:<14} "
                              f"{lo:>6}..{hi:<6} value={val:>6}")
                finally:
                    os.close(fd)

        if args.hold > 0:
            print(f"\npad is live for {args.hold:g}s -- from another shell try:")
            print("  grep -A4 'X-Box 360' /proc/bus/input/devices")
            print("  cat /sys/class/input/input*/phys | grep glasspad")
            print("  (interrupting early is fine: the destroy and the verdict "
                  "still run)")
            time.sleep(args.hold)
    except KeyboardInterrupt:
        print("\ninterrupted -- destroying the pad before exiting")
        rc = 130
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  unexpected {exc.__class__.__name__}: {exc}")
        rc = 1

    # Teardown and verdict must run on every path -- including an interrupted
    # --hold. An earlier draft returned 130 straight from the KeyboardInterrupt
    # arm, so that run skipped the clean-destroy check AND printed no RESULT
    # line at all, which reads as a destruction failure when nothing was
    # checked. This is deliberately NOT a `finally` block: `return` inside
    # `finally` is a SyntaxWarning in Python 3.14 and slated to become a
    # SyntaxError, and it also silently swallows in-flight exceptions. Falling
    # through from the handlers covers every path that can reach here, and a
    # BaseException that is neither (SystemExit from a dependency) still cannot
    # leak a pad: uinput_release() destroys the device when the fd closes.
    try:
        backend.close()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  close() raised {exc.__class__.__name__}: {exc} -- "
              "the kernel reaps the device when the fd closes, but the "
              "explicit UI_DEV_DESTROY did not happen")
        fails += 1
    time.sleep(0.25)
    left = find_pads(SYS_INPUT)
    if left:
        print(f"FAIL  pad still present after close(): {[p.name for p in left]}")
        fails += 1
    else:
        print("\nok    destroyed cleanly: no glasspad device remains")

    print()
    print("RESULT: PASS -- the virtual pad is real, correct, and gone"
          if fails == 0 else f"RESULT: {fails} FAILURE(S)")
    # rc wins when a handler set it: an interrupted run reports 130 and an
    # unexpected exception reports 1 even if it fired before any check had
    # incremented `fails`, which would otherwise return 0 and read as a PASS.
    return rc if rc else (0 if fails == 0 else 1)


if __name__ == "__main__":
    # SIGTERM must not skip the destroy either; the kernel reaps the fd, but an
    # explicit UI_DEV_DESTROY is what keeps the name free for the next run.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    raise SystemExit(main())
