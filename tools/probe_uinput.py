"""Run the suite with opening a real /dev/uinput pad made impossible, and count
whether anything tried.

    python3 tools/probe_uinput.py

Worth running on a machine where /dev/uinput IS writable, because that is the
only case where the tests could create a device at all. A green pytest run does
not prove it did not happen: PadService swallows backend errors into a status
line, so the three window tests that used to reach UinputBackend() passed either
way. This counts the attempts directly.

Two things make it stronger than a sysfs before/after diff:

  - it fires on construction, so a pad opened and closed a microsecond later is
    still counted;
  - it does not depend on the device surviving long enough to appear in sysfs.

Only __init__ is wrapped, not the class. Tests that exercise the event encoder
via UinputBackend.__new__() never enter __init__, so they keep working; wrapping
the class would turn those into false failures.

uinput_available() is forced True to model a real desktop. Exit status is 1 if
any attempt was made, so this can gate CI.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import glasspad.pad as pad  # noqa: E402

ATTEMPTS: list[str] = []
_real_init = pad.UinputBackend.__init__


def guarded_init(self, path="/dev/uinput", *args, **kwargs):
    ATTEMPTS.append(path)
    # Never call through: on this machine that would create the device we are
    # here to rule out.
    raise OSError(13, "probe: real /dev/uinput access is blocked")


pad.UinputBackend.__init__ = guarded_init
pad.uinput_available = lambda path="/dev/uinput": True

# Verify the probe is live without constructing anything. Actually calling
# UinputBackend() to test the trap would open /dev/uinput for real in the case
# where the trap silently failed to install -- the precise outcome being checked
# for -- so compare the installed function instead.
if pad.UinputBackend.__init__ is not guarded_init:
    print("probe did not install; results below would be meaningless",
          file=sys.stderr)
    sys.exit(2)

try:
    import pytest  # noqa: E402
    code = pytest.main(["-q", str(ROOT / "tests")])
except ImportError:
    # No pytest: run the same suite through the bundled runner, which supplies
    # its own stand-in for the handful of pytest APIs the tests use. Without
    # this fallback the strongest of the two leak checks would be the only one
    # that needs a venv, which is backwards.
    print("pytest not installed; using tools/run_tests.py", flush=True)
    import run_tests
    code = run_tests.main([])

print(f"\nreal /dev/uinput construction attempts: {len(ATTEMPTS)}")
if ATTEMPTS:
    for path in ATTEMPTS:
        print(f"  attempted to open {path}")
sys.exit(code or (1 if ATTEMPTS else 0))
