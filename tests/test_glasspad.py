"""Tests for the GlassPad Linux port.

Run: python3 tools/run_tests.py        (no pytest installed; it carries its own)

These assert the behaviour the Windows original has, derived from the C# source
in GlassPad-1.0.2, not from this port's implementation. Where the port deviates
on purpose the test says so.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import pathlib
import re
import socket
import ast
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



from glasspad import xd360  # noqa: E402
from glasspad.pad import (  # noqa: E402
    HAT_BUTTONS,
    HAT_VECTORS,
    NullBackend,
    PadService,
    to_hm,
)


# ---------------------------------------------------------------------------
# uinput ABI
# ---------------------------------------------------------------------------
class TestXbox360Abi:
    def test_struct_sizes_match_kernel(self):
        import ctypes

        assert ctypes.sizeof(xd360.InputAbsinfo) == 24
        # uinput_setup = input_id(8) + name[80] + u32 -> 92, and it carries NO
        # absinfo, which is why the backend must write uinput_user_dev.
        assert ctypes.sizeof(xd360.UinputSetup) == 92
        # uinput_user_dev = name[80] + id(8) + u32 + 4 * (64 * i32)
        assert ctypes.sizeof(xd360.UinputUserDev) == 80 + 8 + 4 + 4 * 64 * 4
        assert xd360.INPUT_EVENT_SIZE == 24
        assert xd360.ABS_CNT == 64

    def test_ioctl_numbers(self):
        # _IO('U', 1) / _IO('U', 2)
        assert xd360.UI_DEV_CREATE == 0x5501
        assert xd360.UI_DEV_DESTROY == 0x5502
        # _IOW('U', 100, int) etc.
        assert xd360.UI_SET_EVBIT == 0x40045564
        assert xd360.UI_SET_KEYBIT == 0x40045565
        assert xd360.UI_SET_ABSBIT == 0x40045567
        # _IOW('U', 3, struct uinput_setup /* 92 bytes */)
        assert xd360.UI_DEV_SETUP == 0x405C5503

    #: Authoritative values, from torvalds/linux include/uapi/linux/
    #: input-event-codes.h (BTN_GAMEPAD/BTN_SOUTH block at 0x130, the BTN_DPAD_*
    #: block at 0x220, ABS_HAT0X/0Y at 0x10/0x11). Written out independently of
    #: xd360.py on purpose: the original list was hand-typed and slid by one
    #: through 0x138..0x13b, and a test that re-reads the module's own constants
    #: cannot see that.
    KERNEL_CODES = {
        "BTN_A": 0x130, "BTN_B": 0x131, "BTN_X": 0x133, "BTN_Y": 0x134,
        "BTN_TL": 0x136, "BTN_TR": 0x137,
        "BTN_SELECT": 0x13A, "BTN_START": 0x13B,
        "BTN_MODE": 0x13C, "BTN_THUMBL": 0x13D, "BTN_THUMBR": 0x13E,
        "BTN_DPAD_UP": 0x220, "BTN_DPAD_DOWN": 0x221,
        "BTN_DPAD_LEFT": 0x222, "BTN_DPAD_RIGHT": 0x223,
        "ABS_HAT0X": 0x10, "ABS_HAT0Y": 0x11,
    }

    def test_button_codes_are_what_xpad_reports(self):
        assert xd360.BUTTON_CODES["A"] == xd360.BTN_A == 0x130
        # The 360 Guide/X button is BTN_MODE. 0x100 is BTN_0 and 0x120 is
        # BTN_TRIGGER; neither is what xpad reports for the Guide button.
        assert xd360.BUTTON_CODES["GUIDE"] == xd360.BTN_MODE == 0x13C
        assert xd360.BUTTON_CODES["LB"] == xd360.BTN_TL
        assert xd360.BUTTON_CODES["L3"] == xd360.BTN_THUMBL
        assert xd360.BUTTON_CODES["BACK"] == xd360.BTN_SELECT == 0x13A
        assert xd360.BUTTON_CODES["START"] == xd360.BTN_START == 0x13B

    @pytest.mark.parametrize("name", sorted(KERNEL_CODES))
    def test_codes_match_kernel_headers(self, name):
        assert getattr(xd360, name) == self.KERNEL_CODES[name]

    def test_codes_match_evdev_when_available(self):
        """Second oracle: python-evdev's table is generated from the uapi headers."""
        try:
            from evdev import ecodes as ev
        except ImportError:
            pytest.skip("python-evdev not installed (pip install evdev)")
        for name, want in self.KERNEL_CODES.items():
            assert getattr(xd360, name) == getattr(ev, name) == want, name

    def test_axis_roles_match_xpad(self):
        """Which body part sits on which axis, per xpad.c.

        xpad_abs[] = ABS_X, ABS_Y, ABS_RX, ABS_RY (sticks).
        xpad_abs_triggers[] = ABS_Z, ABS_RZ (triggers).
        This was inverted, and because AXIS_SPECS drives both the declaration and
        the normalisation, an inverted table is self-consistent: it declares the
        right stick with the trigger range and then obediently reports ok.
        """
        stick = {c for c, s in xd360.AXIS_SPECS.items() if s is xd360.STICK}
        trigger = {c for c, s in xd360.AXIS_SPECS.items() if s is xd360.TRIGGER}
        assert stick == {xd360.ABS_X, xd360.ABS_Y, xd360.ABS_RX, xd360.ABS_RY}
        assert trigger == {xd360.ABS_Z, xd360.ABS_RZ}
        assert xd360.STICK.minimum < 0 <= xd360.STICK.maximum
        assert (xd360.TRIGGER.minimum, xd360.TRIGGER.maximum) == (0, 255)

    def test_no_multitouch_axes_are_declared(self):
        """A gamepad must not advertise ABS_MT_*.

        ABS_HAT0X/0Y were once declared as 0x39/0x3a, which are
        ABS_MT_TRACKING_ID / ABS_MT_PRESSURE. The device then looked like a
        touchscreen to anything classifying by capability, while the real hat
        axes stayed undeclared and read back 0..0 through EVIOCGABS (the
        absinfo array is kzalloc'd, so an axis that was never described has a
        zero-width range rather than a wide one).
        """
        declared = set(xd360.AXIS_SPECS) | {xd360.ABS_HAT0X, xd360.ABS_HAT0Y}
        mt = {c for c in declared if 0x2F <= c <= 0x3F}
        assert mt == set(), "multitouch axes declared on a gamepad: %s" % sorted(mt)
        assert 0x39 not in declared and 0x3A not in declared

    @pytest.mark.parametrize("value,expected", [
        (0.5, 0), (1.0, 320), (0.0, -320), (0.75, 160),
    ])
    def test_stick_normalisation(self, value, expected):
        assert xd360.normalise(xd360.STICK, value) == expected

    @pytest.mark.parametrize("value,expected", [
        (0.0, 0), (1.0, 255), (0.5, 128),
    ])
    def test_trigger_normalisation(self, value, expected):
        assert xd360.normalise(xd360.TRIGGER, value) == expected

    def test_values_are_clamped(self):
        assert xd360.normalise(xd360.STICK, 9.0) == 320
        assert xd360.normalise(xd360.STICK, -9.0) == -320
        assert xd360.normalise(xd360.TRIGGER, 2.0) == 255

    def test_nan_rests_at_centre(self):
        # HIDMaestro rule: a NaN writes the field's resting value.
        assert xd360.normalise(xd360.STICK, float("nan")) == 0
        assert xd360.normalise(xd360.TRIGGER, float("nan")) == 128


# ---------------------------------------------------------------------------
# PadService
# ---------------------------------------------------------------------------
    def test_the_wrong_button_codes_are_pinned_too(self):
        """BTN_0 and BTN_TRIGGER exist as a fence, so their numbers must be asserted.

        They map no key. They are defined because an earlier revision of this port used
        0x100 / 0x120 for the Guide button, and a name that does not exist gets defined
        from memory the next time someone needs it. A fence is only worth having if
        something checks it is still standing: the values are the kernel's, from
        input-event-codes.h, and they are asserted here rather than trusted.
        """
        import glasspad.xd360 as X

        assert X.BTN_0 == 0x100, hex(X.BTN_0)
        assert X.BTN_TRIGGER == 0x120, hex(X.BTN_TRIGGER)
        # and the Guide button is BTN_MODE (xpad360_btn[] in xpad.c), which is the
        # whole point of the fence: an earlier revision mapped Guide to one of these.
        assert X.BTN_MODE == 0x13C, hex(X.BTN_MODE)
        assert X.BTN_MODE not in (X.BTN_0, X.BTN_TRIGGER)

    def test_about_box_quotes_the_abi_it_builds(self):
        """The number a user pastes into a bug report must be the number we send.

        The About box used to carry "ABI v5" as prose next to a struct built from
        UINPUT_VERSION = 5. Two sources, one of which is invisible to the compiler --
        and this port's whole bug class is an ABI constant whose name is right and
        whose number is off by one. Interpolated now, and asserted here, because the
        failure this prevents is a field report about a kernel ABI that was never the
        one in the report.
        """
        import pathlib
        import glasspad.xd360 as X

        hub = pathlib.Path(X.__file__).with_name("overlay_layer.py").read_text()
        assert "ABI v{xd360.UINPUT_VERSION}" in hub, (
            "the About box stopped interpolating the ABI constant; if it has a literal "
            "again, say so here and keep them in step by hand")
        assert 'ABI v5' not in hub, "a hardcoded ABI version is back in the hub"
        assert X.UINPUT_VERSION == 5, (
            "the ABI number changed -- that is a kernel-facing change, not a string edit; "
            "re-read uinput.h and the EVIOCREVOKE/struct uinput_setup layout before "
            "letting this pass")


class TestPadService:
    def _pad(self):
        backend = NullBackend()
        pad = PadService(backend)
        pad.init(lambda _s: None, synchronous=True)
        return pad, backend

    def test_resting_state_matches_original(self):
        # C# fields: _lsX=_lsY=_rsX=_rsY=0.5f, _lt=_rt=0f. The zero-valued
        # entries are the TRIGGERS, which on a real 360 pad are ABS_Z/ABS_RZ
        # (xpad's xpad_abs_triggers[]), not ABS_RX/RY.
        _, backend = self._pad()
        assert backend.last_axes[xd360.ABS_X] == 0.5
        assert backend.last_axes[xd360.ABS_Z] == 0.0
        assert backend.last_axes[xd360.ABS_RZ] == 0.0

    def test_to_hm_mapping(self):
        # PadService.ToHM(v) == (v + 1) / 2
        assert to_hm(-1) == 0.0
        assert to_hm(0) == 0.5
        assert to_hm(1) == 1.0

    def test_press_and_release(self):
        pad, backend = self._pad()
        pad.press_button("A")
        assert backend.last_buttons == {"A"}
        pad.press_button("B")
        assert backend.last_buttons == {"A", "B"}
        pad.release_button("A")
        assert backend.last_buttons == {"B"}

    def test_all_three_face_buttons_at_once(self):
        # The Windows README's known limitation: simultaneous presses can be
        # lost. The port has no such ceiling.
        pad, backend = self._pad()
        for name in ("A", "B", "X", "Y", "LB", "RB"):
            pad.press_button(name)
        assert backend.last_buttons == {"A", "B", "X", "Y", "LB", "RB"}

    def test_trigger_full_travel(self):
        pad, backend = self._pad()
        pad.set_left_trigger(1.0)
        assert backend.last_axes[xd360.ABS_Z] == 1.0
        pad.set_left_trigger(0.0)
        assert backend.last_axes[xd360.ABS_Z] == 0.0

    def test_stick_goes_through_to_hm(self):
        pad, backend = self._pad()
        pad.set_left_stick(1.0, -1.0)
        assert backend.last_axes[xd360.ABS_X] == 1.0
        assert backend.last_axes[xd360.ABS_Y] == 0.0

    def test_right_stick_uses_right_stick_axes(self):
        # xpad: right stick is ABS_RX/ABS_RY; ABS_Z/ABS_RZ are the triggers.
        pad, backend = self._pad()
        pad.set_right_stick(1.0, 0.0)
        assert backend.last_axes[xd360.ABS_RX] == 1.0
        assert backend.last_axes[xd360.ABS_RY] == 0.5

    def test_reset_clears_everything(self):
        pad, backend = self._pad()
        pad.press_button("A")
        pad.set_dpad("N")
        pad.set_left_trigger(1.0)
        pad.set_left_stick(1.0, 1.0)
        pad.reset()
        assert backend.last_buttons == set()
        assert backend.last_hat == "None"
        assert backend.last_axes[xd360.ABS_Z] == 0.0
        assert backend.last_axes[xd360.ABS_X] == 0.5

    def test_dpad_hat_is_forwarded(self):
        pad, backend = self._pad()
        pad.set_dpad("SE")
        assert backend.last_hat == "SE"

    def test_dispose_is_idempotent_and_blocks_submit(self):
        pad, backend = self._pad()
        pad.dispose()
        before = backend.submits
        pad.press_button("A")  # must not raise after disposal
        assert backend.submits == before
        pad.dispose()

    def test_init_is_idempotent(self):
        backend = NullBackend()
        pad = PadService(backend)
        pad.init(lambda _s: None, synchronous=True)
        pad.init(lambda _s: None, synchronous=True)
        assert pad.backend is backend

    def test_error_status_is_reported(self, monkeypatch):
        # A backend that fails at construction must surface a status string and
        # must not leave a half-published pad behind.
        import glasspad.pad as pad_mod

        class Boom(NullBackend):
            def __init__(self):
                raise OSError(13, "Permission denied")

        monkeypatch.setattr(pad_mod, "uinput_available", lambda *a: True)
        monkeypatch.setattr(pad_mod, "UinputBackend", Boom)
        messages = []
        pad = PadService()
        pad.init(messages.append, synchronous=True)
        assert any("udev" in m for m in messages), messages
        assert pad.backend is None
        pad.dispose()

    def test_missing_uinput_reports_module_hint(self, monkeypatch):
        import glasspad.pad as pad_mod

        monkeypatch.setattr(pad_mod, "uinput_available", lambda *a: False)
        messages = []
        pad = PadService()
        pad.init(messages.append, synchronous=True)
        # No uinput -> falls back to the null backend so the overlay still runs.
        assert pad.backend is not None
        pad.dispose()

    def test_disposed_service_never_creates_a_real_device(self, monkeypatch):
        """A worker still starting up must not open /dev/uinput after dispose().

        init() runs on a thread and read self._backend without the lock while
        dispose() clears it under that lock, so closing the overlay during
        startup left it None and the worker fell through to opening a real
        /dev/uinput pad for a window that had already gone away. The finally
        block does close it, so no controller is left behind -- but the device
        still appears in the input layer for a moment and any game enumerating
        pads sees it come and go, and reaching for a privileged node on a shutdown
        path is wrong regardless.

        This went unseen because the failure is swallowed into a status line and
        because a machine without a writable /dev/uinput cannot open one at all:
        three window tests tripped it while still reporting green.
        """
        import glasspad.pad as pad_mod

        created = []
        started = threading.Event()

        class Forbidden:
            # Must be a class: run() calls UinputBackend() with no arguments, so
            # a plain function here raises TypeError before recording anything,
            # the broad except turns that into a status line, and this test
            # would pass without ever proving the device was not opened.
            def __init__(self, path="/dev/uinput", *a, **k):
                created.append(path)
                raise AssertionError(f"opened real device {path!r}")

        monkeypatch.setattr(pad_mod, "UinputBackend", Forbidden)
        monkeypatch.setattr(pad_mod, "uinput_available", lambda *a: True)

        pad = PadService(NullBackend())

        # on_status is invoked first inside the worker thread, so disposing from
        # there lands deterministically between the worker starting and its read
        # of self._backend -- no sleep-based guessing. If the worker never runs
        # the assertion below would pass vacuously, so wait for it explicitly.
        def status(message):
            if not started.is_set() and "Checking" in str(message):
                started.set()
                pad.dispose()

        pad.init(status)                       # async on purpose
        assert started.wait(5), "worker never started"
        for _ in range(100):
            time.sleep(0.01)
            if created:
                break
        assert not created, f"real device created after dispose: {created}"

    def test_hat_vectors(self):
        assert HAT_VECTORS["N"] == (0, -1)
        assert HAT_VECTORS["S"] == (0, 1)
        assert HAT_VECTORS["NE"] == (1, -1)
        assert HAT_VECTORS["None"] == (0, 0)

    def test_hat_also_asserts_button_keys(self):
        assert set(HAT_BUTTONS["NW"]) == {"DPAD_UP", "DPAD_LEFT"}
        assert HAT_BUTTONS["None"] == ()


class TestBackendEvents:
    """The uinput encoder, exercised without /dev/uinput."""

    def test_submit_frame_shape(self):
        from glasspad.pad import UinputBackend

        written = []
        backend = UinputBackend.__new__(UinputBackend)
        backend._fd = None
        backend._send = lambda t, c, v: written.append((t, c, v))
        backend.submit({"A"}, "N", {xd360.ABS_X: 0.5, xd360.ABS_Y: 0.5,
                                    xd360.ABS_RX: 0.5, xd360.ABS_RY: 0.5,
                                    xd360.ABS_Z: 0.0, xd360.ABS_RZ: 0.0})

        # Every button is written each frame, so releases cannot be lost.
        keys = {c for t, c, _ in written if t == xd360.EV_KEY}
        assert xd360.BTN_A in keys and xd360.BTN_B in keys
        assert written[-1] == (xd360.EV_SYN, xd360.SYN_REPORT, 0)

        values = {(t, c): v for t, c, v in written}
        assert values[(xd360.EV_KEY, xd360.BTN_A)] == 1
        assert values[(xd360.EV_KEY, xd360.BTN_B)] == 0
        # Hat drives both ABS_HAT0x and the BTN_DPAD_* keys, as xpad does.
        assert values[(xd360.EV_ABS, xd360.ABS_HAT0Y)] == -1
        assert values[(xd360.EV_KEY, xd360.BTN_DPAD_UP)] == 1
        assert values[(xd360.EV_ABS, xd360.ABS_X)] == 0
        assert values[(xd360.EV_ABS, xd360.ABS_Z)] == 0

    def test_release_reaches_device(self):
        from glasspad.pad import UinputBackend

        written = []
        backend = UinputBackend.__new__(UinputBackend)
        backend._fd = None
        backend._send = lambda t, c, v: written.append((t, c, v))
        backend.submit(set(), "None", {})
        values = {(t, c): v for t, c, v in written}
        assert values[(xd360.EV_KEY, xd360.BTN_A)] == 0
        assert values[(xd360.EV_KEY, xd360.BTN_DPAD_UP)] == 0
        assert values[(xd360.EV_ABS, xd360.ABS_HAT0X)] == 0


class TestPadInitRace:
    """dispose() landing while the startup thread is still building the backend.

    PadService.init() constructs the backend on a daemon thread, so the overlay
    can close mid-startup. dispose() clears and closes self._backend -- and while
    a construction is in flight that is still None, so it closes nothing. If the
    thread then installs what it built, the process ends holding a live
    controller with nobody left to dispose it: on a machine with a writable
    /dev/uinput that is a phantom pad, visible in /proc/bus/input/devices and
    removable only by a reboot.

    Ordering is forced with events, never sleeps. In the second test the fake
    backend calls dispose() from inside its own constructor, which is the worst
    case and the one no timing could be trusted to hit: the device exists,
    dispose() has already run and closed nothing, and only the _disposed re-check
    plus the finally that releases the un-installed probe stands between that and
    a leak.

    The init thread is captured from inside the backend constructor, where
    current_thread() is provably the thread under test. Finding it by scanning
    threading.enumerate() for its name loses a race instead: a backend that is
    not slow finishes before the scan ever sees it, and the test reports a hang
    that never happened.

    Neither ordering was covered before, because losing needs a slow backend and
    every existing test injects NullBackend, whose constructor cannot lose.
    """

    class _TrackedBackend(NullBackend):
        """NullBackend plus a close() the test can observe.

        Subclassing keeps submit() real: run() calls it under the lock, so a fake
        that left it out would fail for the wrong reason.
        """

        def __init__(self):
            super().__init__()
            self.closed = False

        def close(self):
            self.closed = True

    def test_dispose_before_construction_returns_closes_the_backend(
            self, monkeypatch):
        """The overlay closes while the backend is still being built."""
        import glasspad.pad as pad_module

        entered = threading.Event()
        proceed = threading.Event()
        built, threads = [], []

        class SlowBackend(TestPadInitRace._TrackedBackend):
            def __init__(self):
                threads.append(threading.current_thread())
                entered.set()
                assert proceed.wait(5.0), "test never released construction"
                super().__init__()
                built.append(self)

        monkeypatch.setattr(pad_module, "uinput_available", lambda: True)
        monkeypatch.setattr(pad_module, "UinputBackend", SlowBackend)

        pad = PadService()
        pad.init(lambda _s: None)
        assert entered.wait(5.0), "init thread never reached the backend"
        pad.dispose()                 # self._backend is still None here
        proceed.set()
        threads[0].join(5.0)
        assert not threads[0].is_alive(), "startup thread hung after dispose()"

        assert built, "backend was never constructed, so nothing was tested"
        assert built[0].closed is True, (
            "startup built a backend after dispose() had already run and nothing "
            "closed it: the overlay exits holding a live virtual pad")

    def test_dispose_from_inside_construction_closes_the_backend(
            self, monkeypatch):
        """The deterministic worst case: dispose() runs mid-constructor.

        A pad created at this instant is invisible to dispose(), which has
        already returned, so this pins the re-check under the lock and the
        finally that releases the never-installed probe. Remove either and the
        device stays open.
        """
        import glasspad.pad as pad_module

        built, threads, done = [], [], threading.Event()

        class SelfDisposing(TestPadInitRace._TrackedBackend):
            def __init__(self):
                threads.append(threading.current_thread())
                super().__init__()
                built.append(self)
                pad.dispose()         # window closed at this exact moment
                done.set()

        monkeypatch.setattr(pad_module, "uinput_available", lambda: True)
        monkeypatch.setattr(pad_module, "UinputBackend", SelfDisposing)

        pad = PadService()
        pad.init(lambda _s: None)
        assert done.wait(5.0), "constructor never ran, so nothing was tested"
        threads[0].join(5.0)
        assert not threads[0].is_alive(), "startup thread hung after dispose()"

        assert built[0].closed is True, (
            "dispose() ran while the backend was being built and closed nothing; "
            "the re-check under the lock is what releases it, and a leak here is "
            "a phantom controller the user cannot remove without a reboot")
        # An abandoned device must not also become the live one, or a later
        # submit() would write to a closed fd.
        assert pad._backend is None, (
            "an abandoned backend was installed as the live one after dispose()")

class TestBackendDeclaration:
    """What _create_device() actually writes into uinput_user_dev.

    This is the last untested step before the kernel sees the device, and the
    kernel rule it has to satisfy is easy to get wrong: an axis declared with
    UI_SET_ABSBIT but left out of the write() keeps kzalloc'd absinfo, so
    EVIOCGABS reports min == max == 0. Nothing clamps that and nothing errors --
    SDL and Steam Input divide by (max - min), so the stick is simply dead.
    A test of the encoders alone cannot see this, which is the same class of gap
    that let the button codes stay off by one.
    """

    @staticmethod
    def _capture(monkeypatch):
        """Run the real _declare() with no /dev/uinput, return (dev, ioctls)."""
        from glasspad import pad as pad_mod

        backend = pad_mod.UinputBackend.__new__(pad_mod.UinputBackend)
        backend._fd = -1
        written = {}
        ioctls = []

        # fcntl is imported inside _set_bit/_create_device, so it resolves
        # through sys.modules rather than the pad module's namespace: patch the
        # fcntl module itself, not pad.fcntl (which does not exist).
        monkeypatch.setattr(pad_mod.os, "write",
                            lambda fd, data: written.__setitem__("dev", data))
        monkeypatch.setattr(fcntl, "ioctl",
                            lambda fd, req, *a: ioctls.append((req, a)))
        backend._declare()
        assert "dev" in written, "_create_device never wrote the description"
        # Re-parse through the struct so the assertion is on the bytes that would
        # have gone to the kernel, not on the Python object that produced them.
        dev = pad_mod.X.UinputUserDev.from_buffer_copy(written["dev"])
        return dev, ioctls

    def test_every_declared_axis_gets_a_nonzero_range(self, monkeypatch):
        dev, _ = self._capture(monkeypatch)
        declared = set(xd360.AXIS_SPECS) | {xd360.ABS_HAT0X, xd360.ABS_HAT0Y}
        dead = [c for c in sorted(declared)
                if dev.absmax[c] == dev.absmin[c]]
        assert not dead, \
            "axes declared but never described read back 0..0 (dead stick): %s" \
            % [hex(c) for c in dead]

    def test_written_ranges_are_the_specs(self, monkeypatch):
        dev, _ = self._capture(monkeypatch)
        for code, spec in xd360.AXIS_SPECS.items():
            assert (dev.absmin[code], dev.absmax[code]) == \
                   (spec.minimum, spec.maximum), hex(code)
            assert (dev.absfuzz[code], dev.absflat[code]) == \
                   (spec.fuzz, spec.flat), hex(code)
        # Hat is a unit tripole, not an AXIS_SPECS entry: -1..1 exactly as xpad
        # reports a d-pad mapped to axes.
        for code in (xd360.ABS_HAT0X, xd360.ABS_HAT0Y):
            assert (dev.absmin[code], dev.absmax[code]) == (-1, 1), hex(code)

    def test_identity_is_a_microsoft_360_pad(self, monkeypatch):
        """xpad and SDL match on these; a wrong pair registers the device but
        leaves it unclaimed by the game-facing layers."""
        dev, ioctls = self._capture(monkeypatch)
        assert dev.name == b"Microsoft X-Box 360 pad"
        assert dev.id.bustype == xd360.BUS_USB
        assert dev.id.vendor == 0x045E, "0x045e is Microsoft"
        assert dev.id.product == 0x028E, "0x028e is the wired 360 pad"
        assert dev.ff_effects_max == 0, \
            "non-zero ff_effects_max makes the kernel build an FF device we never use"
        assert (xd360.UI_DEV_CREATE, ()) in ioctls, "device is never created"

    def test_no_multitouch_bit_is_declared(self, monkeypatch):
        """Same defect as the hat codes, caught at the ioctl layer: a UI_SET_ABSBIT
        for an ABS_MT_* code is what made this pad look like a touchscreen."""
        _, ioctls = self._capture(monkeypatch)
        abs_bits = {a[0] for req, a in ioctls if req == xd360.UI_SET_ABSBIT}
        mt = sorted(c for c in abs_bits if 0x2F <= c <= 0x3F)
        assert not mt, "multitouch axes declared: %s" % [hex(c) for c in mt]
        assert abs_bits == set(xd360.AXIS_SPECS) | {
            xd360.ABS_HAT0X, xd360.ABS_HAT0Y}


_REAL_X_SOCKET_DIR = "/tmp/.X11-unix"


# ---------------------------------------------------------------------------
# Installer and verification tools
#
# The port's own tooling has shipped two bugs that only a real desktop exposes:
# an installer that aborted after doing everything right, and a watcher whose
# documented invocation could not start. Both are reproduced here rather than
# merely reasoned about, because neither shows up in --status or a unit test.
# ---------------------------------------------------------------------------
class TestInstallerRobustness:
    ROOT = pathlib.Path(__file__).resolve().parent.parent

    @staticmethod
    def _functions(workdir: pathlib.Path) -> pathlib.Path:
        """install.sh up to its dispatch `case`, so functions can be sourced.

        Extracted rather than re-implemented: a test of a hand-copied function
        would keep passing while the shipped one stayed fatal.
        """
        text = (TestInstallerRobustness.ROOT / "install.sh").read_text()
        marker = 'case "$MODE" in'
        head = text.split(marker, 1)[0]
        assert "refresh_caches()" in head, "install.sh changed shape; update me"
        out = workdir / "functions.sh"
        out.write_text(head)
        return out

    @staticmethod
    def _stub_icon_cache(workdir: pathlib.Path, always_fail: bool = False
                         ) -> pathlib.Path:
        """A gtk-update-icon-cache behaving like the real one.

        Default reproduces what the real tool prints for a hicolor directory
        without index.theme -- "No theme index file." and exit 1 -- which is the
        state a first install into /usr/local creates. With a valid index.theme
        it succeeds, so the "still refreshed" test proves real work happens.

        always_fail instead fails unconditionally, for a reason the index.theme
        guard cannot foresee (a read-only or corrupt cache, say), even when the
        theme is perfectly good. Only the non-fatal run_soft call can cover that.
        """
        bindir = workdir / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        stub = bindir / "gtk-update-icon-cache"
        if always_fail:
            script = (
                '#!/bin/sh\n'
                'echo "stub: cannot write cache" >&2\n'
                'exit 1\n'
            )
        else:
            script = (
                '#!/bin/sh\n'
                '[ "$1" = "-q" ] && shift\n'
                'target="$1"\n'
                'if [ ! -f "$target/index.theme" ]; then\n'
                '  echo "gtk-update-icon-cache: No theme index file." >&2\n'
                '  exit 1\n'
                'fi\n'
                'echo "stub: updated $target"\n'
            )
        stub.write_text(script)
        stub.chmod(0o755)
        return bindir

    def _drive_refresh(self, tmp_path, index_theme: bool, always_fail: bool = False):
        work = tmp_path / "work"
        work.mkdir()
        prefix = work / "prefix"
        (prefix / "share" / "applications").mkdir(parents=True)
        # Names chosen so the levels cannot be confused: `theme` is the hicolor
        # directory the refresher is pointed at, `sizes` is a subdir inside it.
        theme = prefix / "share" / "icons" / "hicolor"
        sizes = theme / "256x256" / "apps"
        sizes.mkdir(parents=True)
        if index_theme:
            # index.theme belongs to hicolor itself. Anywhere else leaves the
            # theme bare, and the "still refreshed" assertion below would then
            # pass for the wrong reason -- or, before the fix, fail the install.
            (theme / "index.theme").write_text(
                "[Icon Theme]\nDirectories=256x256/apps\n")
        funcs = self._functions(work)
        driver = work / "drive.sh"
        driver.write_text(
            "#!/bin/sh\nset -eu\n"
            f"DRY=0\n. {funcs}\nrefresh_caches \"$TARGET\"\nsay REACHED_END\n")
        bindir = self._stub_icon_cache(work, always_fail=always_fail)
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
                   TARGET=str(prefix))
        return subprocess.run(["/bin/sh", str(driver)], env=env,
                              capture_output=True, text=True)

    def test_icon_cache_failure_does_not_abort_the_install(self, tmp_path):
        """Regression: the first install into /usr/local died after finishing.

        Under `set -eu` a non-zero gtk-update-icon-cache killed the installer
        between writing the files and reporting them, so a fully installed system
        exited 1, never printed "wrote ... glasspad.desktop" or "done.", and
        `sudo ./install.sh && ./install.sh --check` stopped before the check.
        """
        result = self._drive_refresh(tmp_path, index_theme=False)
        assert result.returncode == 0, (
            "refresh_caches aborted the run; install.sh would exit mid-install\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}")
        assert "REACHED_END" in result.stdout

    def test_a_bare_theme_is_skipped_without_printing_an_error(self, tmp_path):
        """A first install must not print something that reads like a failure.

        "gtk-update-icon-cache: No theme index file." on stderr is what made a
        completed install look broken, and skipping rather than attempting-and-
        tolerating is what removes it: hicolor is the mandated fallback theme and
        is searched as-is with no cache, so the attempt had no upside to trade off.
        """
        result = self._drive_refresh(tmp_path, index_theme=False)
        assert result.returncode == 0
        assert "No theme index file" not in result.stderr, (
            "the refresher was attempted against a theme that cannot succeed, "
            "so its error still reaches the user\n"
            f"stderr: {result.stderr}")

    def test_a_theme_with_an_index_is_still_refreshed(self, tmp_path):
        """The fix must skip the bare theme, not all refreshes."""
        result = self._drive_refresh(tmp_path, index_theme=True)
        assert result.returncode == 0, result.stderr
        assert "stub: updated" in result.stdout, \
            "gtk-update-icon-cache was never invoked where it would work"
        assert "REACHED_END" in result.stdout

    def test_any_refresher_failure_stays_advisory(self, tmp_path):
        """The guard only foresees a missing index.theme; failures are not bounded.

        A read-only or corrupt cache fails against a perfectly good theme, so the
        skip guard cannot help and only the non-fatal call can. This pins that
        half of the fix independently: without it, install.sh regresses to dying
        on the first advisory command that disagrees, for reasons nobody predicted.
        """
        result = self._drive_refresh(tmp_path, index_theme=True, always_fail=True)
        assert result.returncode == 0, (
            "an advisory cache refresh was fatal to the install\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}")
        assert "REACHED_END" in result.stdout
        assert "WARNING" in result.stdout, \
            "the failure was swallowed silently instead of reported"

    def _drive_remove_prefix(self, tmp_path):
        """Run the real remove_prefix() against a throwaway install root.

        Scoped to one prefix on purpose: uninstall() itself reaches /etc and the
        real home directory, which a test has no business touching.
        """
        work = tmp_path / "work"
        work.mkdir()
        prefix = work / "prefix"
        for sub in ("share/applications", "share/icons/hicolor/256x256/apps",
                    "share/icons/hicolor/512x512/apps"):
            (prefix / sub).mkdir(parents=True)
        # A valid index.theme, so the guard lets the refresher run and the
        # always-failing stub is actually reached. Without it the guard skips the
        # call, nothing fails, and this test would pass even with the fatal bug
        # restored -- a assertion that cannot fail is worse than no assertion.
        (prefix / "share" / "icons" / "hicolor" / "index.theme").write_text(
            "[Icon Theme]\nDirectories=256x256/apps,512x512/apps\n")
        funcs = self._functions(work)
        driver = work / "drive.sh"
        driver.write_text(
            "#!/bin/sh\nset -eu\n"
            f"DRY=0\n. {funcs}\nremove_prefix \"$TARGET\"\nsay REACHED_END\n")
        bindir = self._stub_icon_cache(work, always_fail=True)
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
                   TARGET=str(prefix))
        return subprocess.run(["/bin/sh", str(driver)], env=env,
                              capture_output=True, text=True)

    def test_uninstall_is_not_derailed_by_a_cache_refresher(self, tmp_path):
        """remove_prefix is where uninstall called refresh_caches, fatally.

        This is the dangerous twin of the install bug. remove_prefix runs before
        the udevadm reload at the end of uninstall(), so an abort there left the
        autostart entry, the saved layout and the deleted udev rule's permissions
        on the live /dev/uinput -- precisely the half-removed state run_ok() was
        written to prevent, reintroduced through a path that bypassed it.
        """
        result = self._drive_remove_prefix(tmp_path)
        assert result.returncode == 0, (
            "remove_prefix aborted; uninstall() would stop before reverting "
            "udev\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}")
        assert "REACHED_END" in result.stdout

    def test_installer_still_parses_and_documents_itself(self):
        """The -h text is sliced from the header by line number; keep it whole."""
        result = subprocess.run(["/bin/sh", str(self.ROOT / "install.sh"), "-h"],
                                capture_output=True, text=True)
        assert result.returncode == 0
        assert "--uninstall" in result.stdout
        assert result.stdout.rstrip().endswith("skips the /etc work entirely."), \
            "usage text is truncated; the header grew past the sed range"


class TestUinputWatcher:
    """tools/uinput_watch.py is the only check that no virtual pad leaked.

    Worth testing on its own: a broken watcher silently stops guarding, which is
    worse than not having one.
    """

    @staticmethod
    def _load():
        import importlib.util
        path = (pathlib.Path(__file__).resolve().parent.parent
                / "tools" / "uinput_watch.py")
        spec = importlib.util.spec_from_file_location("uinput_watch", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_run_accepts_the_documented_double_dash(self, tmp_path):
        """`run -- <command>` is what the docstring tells people to type.

        The -- belongs to this script, not to the child, so passing it through
        made exec search for a program literally named "--" and the headline
        usage died with FileNotFoundError before the suite ran.
        """
        marker = tmp_path / "ran"
        rc = self._load().main([
            "uinput_watch.py", "run", "--",
            sys.executable, "-c",
            f"open({str(marker)!r},'w').write('ok')",
        ])
        assert marker.exists(), \
            "the command after -- never executed (the '--' was passed to exec)"
        assert rc == 0

    def test_run_works_without_the_double_dash_too(self, tmp_path):
        marker = tmp_path / "bare"
        rc = self._load().main([
            "uinput_watch.py", "run",
            sys.executable, "-c", f"open({str(marker)!r},'w').write('ok')",
        ])
        assert marker.exists()
        assert rc == 0

    def test_run_without_a_command_is_a_clean_usage_error(self):
        assert self._load().main(["uinput_watch.py", "run"]) == 2
        assert self._load().main(["uinput_watch.py", "run", "--"]) == 2

    def test_run_propagates_the_child_exit_status(self, tmp_path):
        """A failing suite must not be reported as clean."""
        rc = self._load().main([
            "uinput_watch.py", "run", "--", sys.executable, "-c", "raise SystemExit(3)",
        ])
        assert rc == 3


# ---------------------------------------------------------------------------
# Documentation that ships with the code
# ---------------------------------------------------------------------------
class TestDocPointers:
    """A docstring promise must name a file that exists.

    This port grew up next to the Windows tree, and its comments inherited the
    habit of pointing at README.md / NOTES.md for the "why" -- neither of which
    was ever written here. A user following `--status` output told to "See
    README" gets nothing, and a reader chasing a NOTES.md deviation note learns
    nothing. Rather than trust memory, resolve every filename a shipped file
    mentions against the tree.
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent
    SHIPPED = (sorted((ROOT / "glasspad").glob("*.py"))
               + [ROOT / "install.sh", ROOT / "desktop" / "glasspad.desktop.in"])
    # A bare filename resolves relative to the package or the repo root, which
    # is where install.sh and the license texts actually live. tests/ belongs here
    # too: shipped comments cite the suite that enforces an invariant ("which is
    # what test_orphan_constants.py caught"), and a pointer to the check that will
    # fire is the most useful kind of pointer there is. It should resolve, not be
    # exempted.
    LOOKUP = (ROOT / "glasspad", ROOT / "desktop", ROOT / "licenses",
              ROOT / "tests", ROOT)
    # Written at runtime in the user's config dir, never shipped.
    RUNTIME = {"layout.json"}
    # Removed on purpose, and still cited by name in the code that replaced them:
    # "parity with window.py's _toggle_edit" is telling you WHICH behaviour was copied,
    # which is the useful half of the sentence. Rewriting 18 such comments to say
    # "see git history" would trade a real explanation for a vague one. So a name on
    # this list is provenance, not a link -- and it is the same list the artefact guard
    # reads, so "the Qt backend was deleted" stays one fact stored in one place.
    HISTORICAL = frozenset()   # filled below, from glasspad.bundle
    NAMED = re.compile(r"(?<![\w/.-])([A-Za-z0-9_-]+\.(?:md|json|txt|sh|py|"
                       r"png|desktop))\b")

    def test_shipped_files_are_present_to_read(self):
        missing = [str(p) for p in self.SHIPPED if not p.is_file()]
        assert not missing, f"test itself is stale: {missing}"

    @staticmethod
    def _prose(path):
        """Comment and docstring text only.

        Scanning raw source produced nothing but false positives: `self.sh` and `ov.sh`
        look exactly like shell filenames to a regex, and every runtime path we build --
        profiles/default.json, the selftest scratch file, the LICENSE-name search list --
        is a string literal in code, not a pointer a human follows. The thing worth
        checking is prose that sends a reader to a file, so that is all that is read.
        """
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            import io
            import tokenize

            out = []
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(node, clean=False)
                    if doc:
                        out.append(doc)
            try:
                for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                    if tok.type == tokenize.COMMENT:
                        out.append(tok.string)
            except (tokenize.TokenError, IndentationError):
                pass
            return "\n".join(out)
        # shell and desktop templates: '#' comment lines, and nothing else
        return "\n".join(ln for ln in text.splitlines()
                          if ln.strip().startswith("#"))

    def test_no_dangling_file_references(self):
        dangling = []
        for path in self.SHIPPED:
            for name in sorted(set(self.NAMED.findall(self._prose(path)))):
                if name in self.RUNTIME or name in self.HISTORICAL:
                    continue
                # A qualified mention ("tools/check_all.py") never reaches here: the
                # lookbehind on NAMED refuses anything preceded by a slash, which is the
                # right outcome -- a path that names its directory is already precise.
                if not any((base / name).is_file() for base in self.LOOKUP):
                    dangling.append(f"{path.name} mentions {name}")
        assert not dangling, dangling

    @classmethod
    def _resolves(cls, name):
        return (name in cls.RUNTIME
                or any((base / name).is_file() for base in cls.LOOKUP))

    @classmethod
    def _citable(cls, name):
        """Exists, or is a deliberately removed file being cited as provenance."""
        return cls._resolves(name) or name in cls.HISTORICAL

    def test_the_checker_is_not_vacuous(self):
        # A scan that reads nothing would pass forever, so exercise the pattern
        # and the lookup on samples instead of on whichever mentions happen to
        # survive in the tree today.
        sample = ("see README.md and NOTES.md, run install.sh, load "
                  "layout.json, uses layer_geom.py, not .md alone")
        assert set(self.NAMED.findall(sample)) == {
            "README.md", "NOTES.md", "install.sh", "layout.json",
            "layer_geom.py"}
        # The lookup is what separates a real pointer from a dead one.
        assert self._resolves("layer_geom.py")
        assert self._resolves("layout.json")   # runtime, deliberately exempt
        assert not self._resolves("no-such-file-here.md")
        # ...and the exemption is for removed files only, not a general escape hatch:
        # the Qt modules are gone, so they resolve False but stay citable.
        assert not self._resolves("touch_router.py")
        assert self._citable("touch_router.py")
        assert not self._citable("no-such-file-here.md")

    def test_the_scanned_files_are_actually_readable(self):
        # Same reason: if the glob ever matches nothing, every other assertion
        # here is silently satisfied by an empty scan.
        assert len(self.SHIPPED) >= 10, self.SHIPPED
        for path in self.SHIPPED:
            assert path.read_text(encoding="utf-8"), path


class TestRealPadProbe:
    """tools/probe_real_pad.py is the only check that the pad is REAL.

    Everything else in this suite proves logic; this proves the kernel accepted
    our declaration and registered the identity xpad/SDL look for. It can only
    run for real on a host with a writable /dev/uinput, so these tests drive
    main() against a fake sysfs and assert the REPORT is right -- including that
    it can fail. A checker that always prints PASS is worse than none, so each
    injected fault must be caught.

    main() takes its paths from the module constants SYS_INPUT / DEV_INPUT /
    PROC_DEVICES at call time, which is what lets these tests redirect it into a
    sandbox; test_main_honours_its_path_constants pins that down, because a
    hardcoded "/sys/class/input" would leave the whole class reading the real
    (empty here) sysfs and passing vacuously.
    """

    PROC_STANZA = (
        "I: Bus=0019 Vendor=0001 Product=0001 Version=0100\n"
        'N: Name="Lid Switch"\nP: Phys=PNP0C0D/button/input0\n'
        "H: Handlers=kbd event0\n\n"
        "I: Bus=0003 Vendor=045e Product=028e Version=0110\n"
        'N: Name="Microsoft X-Box 360 pad"\nP: Phys=glasspad/virtual0\n'
        "H: Handlers=event77\nB: EV=17\nB: ABS=3001f\n"
    )

    @staticmethod
    def _load():
        import importlib.util
        path = (pathlib.Path(__file__).resolve().parent.parent
                / "tools" / "probe_real_pad.py")
        spec = importlib.util.spec_from_file_location("probe_real_pad", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _drive(self, tmp_path, *, phys_ok=True, product="3/45e/28e/110",
               destroy=True, create=True, ranges=None, interrupt=False,
               monkeypatch=None):
        """Run probe main() against a fake sysfs; return (verdict, output).

        interrupt=True simulates Ctrl-C during the --hold window: main() is
        entered with --hold 30 and time.sleep raises once the hold starts, so
        the 0.25s settle sleeps still run normally.
        """
        import io
        import contextlib
        import shutil

        prp = self._load()
        sysi = tmp_path / "sys/class/input"
        devi = tmp_path / "dev/input"
        sysi.mkdir(parents=True)
        devi.mkdir(parents=True)
        proc = tmp_path / "devices"
        proc.write_text(self.PROC_STANZA)

        # What a healthy kernel would report, which the probe must agree with.
        expected = {code: (s.minimum, s.maximum)
                    for code, s in xd360.AXIS_SPECS.items()}
        expected[xd360.ABS_HAT0X] = expected[xd360.ABS_HAT0Y] = (-1, 1)
        if ranges:
            expected.update(ranges)

        dn = prp.UinputBackend.DEVICE_NAME
        ph = prp.UinputBackend.PHYS

        class FakeBackend:
            name = "xbox-360-wired"
            DEVICE_NAME = dn
            PHYS = ph

            def __init__(self, path="/dev/uinput"):
                if not create:
                    return
                d = sysi / "input77"
                d.mkdir()
                (d / "name").write_text(dn + "\n")
                (d / "phys").write_text(
                    (ph if phys_ok else "usb-0000:00:14.0-1/input0") + "\n")
                # sysfs nests the event node as a symlink of that name. The
                # target must exist: Path.exists() follows symlinks, so a
                # dangling one skips the primary branch and the test would only
                # be exercising the number-matching fallback.
                (sysi / "devices" / "input77").mkdir(parents=True,
                                                     exist_ok=True)
                (sysi / "devices" / "input77" / "event77").write_text("")
                (d / "event77").symlink_to("../devices/input77/event77")
                (d / "uevent").write_text(f'PRODUCT={product}\nNAME="x"\n')
                (devi / "event77").write_text("")

            def close(self):
                if destroy and (sysi / "input77").exists():
                    shutil.rmtree(sysi / "input77")

        def fake_read_abs(fd, code):
            got = expected.get(code)
            return (got[0], got[1], 0) if got else None

        prp.SYS_INPUT, prp.DEV_INPUT = sysi, devi
        prp.PROC_DEVICES = proc
        prp.os.access = lambda path, mode: True
        prp.UinputBackend = FakeBackend
        prp.read_abs = fake_read_abs

        real_sleep = prp.time.sleep
        if interrupt:
            if monkeypatch is None:
                raise AssertionError("interrupt=True needs a monkeypatch")

            def sleep(seconds):
                if seconds > 1:
                    raise KeyboardInterrupt
                real_sleep(0)          # keep the settle checks cheap
            monkeypatch.setattr(prp.time, "sleep", sleep)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = prp.main(["--hold", "30"] if interrupt else ["--hold", "0"])
        out = buf.getvalue()
        return ("PASS" if "RESULT: PASS" in out else "FAIL"), rc, out

    def test_a_healthy_pad_reports_pass(self, tmp_path):
        verdict, rc, out = self._drive(tmp_path)
        assert verdict == "PASS", out
        assert rc == 0, out

    def test_no_device_registered_is_a_failure(self, tmp_path):
        verdict, rc, _ = self._drive(tmp_path, create=False)
        assert verdict == "FAIL"

    def test_lost_phys_is_a_failure(self, tmp_path):
        """UI_SET_PHYS is treated as cosmetic by the backend, but it is the
        strong marker in uinput_watch. If it is missing, a future leaked pad
        would be invisible to the leak checker, so the probe must say so."""
        verdict, _, out = self._drive(tmp_path, phys_ok=False)
        assert verdict == "FAIL"
        assert "uinput_watch" in out, "must explain why a lost phys matters"

    def test_wrong_product_id_is_a_failure(self, tmp_path):
        verdict, _, _ = self._drive(tmp_path, product="3/1234/5678/1")
        assert verdict == "FAIL"

    def test_unapplied_absinfo_is_a_failure(self, tmp_path):
        """The classic uinput mistake: UI_SET_ABSBIT without the legacy
        uinput_user_dev write.

        The injected range is 0..0 because that is what the kernel actually
        reports in that state, not a stand-in for 'wrong': input_alloc_absinfo()
        kzallocs the array and evdev returns EVIOCGABS verbatim, so a declared
        axis that never received absinfo reads back min == max == 0. The failure
        mode is therefore a *dead* stick -- SDL and Steam Input divide by
        (max - min) to normalise -- rather than a stick travelling too far, as
        the kernel applies no clamping to injected values."""
        verdict, _, out = self._drive(tmp_path, ranges={xd360.ABS_X: (0, 0)})
        assert verdict == "FAIL"
        assert "left stick X" in out

    def test_hat_range_is_checked_too(self, tmp_path):
        verdict, _, _ = self._drive(tmp_path, ranges={xd360.ABS_HAT0X: (0, 1)})
        assert verdict == "FAIL"

    def test_interrupted_hold_still_verifies_destruction(self, tmp_path,
                                                         monkeypatch):
        """Ctrl-C during --hold must still destroy, still check, still print.

        Found on real hardware: `probe_real_pad.py --hold 30` interrupted with
        Ctrl-C returned 130 straight from the except arm, so the clean-destroy
        check never ran and no RESULT line was printed. From a second shell the
        pad was still visible in /proc at that moment, which looked exactly like
        a destroyed-device failure while in truth nothing had been checked.
        """
        verdict, rc, out = self._drive(tmp_path, interrupt=True,
                                       monkeypatch=monkeypatch)
        assert "interrupted" in out, out
        assert "destroyed cleanly" in out, \
            "the clean-destroy check was skipped, so nothing was verified: " + out
        assert verdict == "PASS", out
        assert rc == 130, "an interrupted run must still signal 130"

    def test_interrupted_hold_still_reports_a_leaked_pad(self, tmp_path,
                                                         monkeypatch):
        """The interrupted path is not a free pass: a pad left behind is still
        a FAIL, otherwise the finally-block would be decorative."""
        verdict, rc, out = self._drive(tmp_path, interrupt=True, destroy=False,
                                       monkeypatch=monkeypatch)
        assert verdict == "FAIL", out
        assert "still present" in out, out
        assert rc == 130, rc

    def test_a_pad_left_behind_is_a_failure(self, tmp_path):
        verdict, _, _ = self._drive(tmp_path, destroy=False)
        assert verdict == "FAIL"

    def test_proc_stanza_matching_is_not_vacuous(self, tmp_path):
        """'N: Name=\"...\"' and 'P: Phys=...' are the real field spellings.
        A pattern written as 'Name:\"' or matched case-sensitively matches
        nothing, which reads as 'looked, was fine'."""
        prp = self._load()
        good = tmp_path / "good"
        good.write_text(self.PROC_STANZA)
        lines = prp.identity_from_proc(
            prp.UinputBackend.DEVICE_NAME, prp.UinputBackend.PHYS, good)
        assert any(line.startswith("B: ABS=") for line in lines), lines
        assert any("Handlers=event77" in line for line in lines), lines
        # negative controls: the stanza must not match on name alone, nor on
        # phys alone, nor in a file that has neither.
        wrong_phys = tmp_path / "wrong"
        wrong_phys.write_text(self.PROC_STANZA.replace("glasspad/virtual0",
                                                       "isa0060/set1/input0"))
        assert prp.identity_from_proc(prp.UinputBackend.DEVICE_NAME,
                                      prp.UinputBackend.PHYS,
                                      wrong_phys) == []
        no_phys = tmp_path / "nophys"
        no_phys.write_text('N: Name="Microsoft X-Box 360 pad"\n')
        assert prp.identity_from_proc(prp.UinputBackend.DEVICE_NAME,
                                      prp.UinputBackend.PHYS, no_phys) == []

    def test_event_node_never_returns_the_sysfs_symlink(self, tmp_path):
        """Opening /sys/class/input/inputN/eventN instead of /dev/input/eventN
        makes every EVIOCGABS fail, which would be reported as bad axis ranges
        on a perfectly good device."""
        prp = self._load()
        sysi = tmp_path / "sys"
        devi = tmp_path / "dev"
        sysi.mkdir()
        devi.mkdir()
        d = sysi / "input77"
        d.mkdir()
        target = sysi / "real-event77"
        target.write_text("")
        (d / "event77").symlink_to(target)     # resolves, as in real sysfs
        (devi / "event77").write_text("")
        node = prp.event_node(d, devi)
        assert node == devi / "event77", node
        assert "/sys" not in str(node)
        # and a missing /dev/input must yield None rather than a dead path
        assert prp.event_node(d, tmp_path / "nothing") is None
        # Omitting the argument must fall back to the module constant, not to a
        # literal "/dev/input" -- otherwise a redirected run would look at the
        # real /dev and this class would not notice.
        prp.DEV_INPUT = devi
        assert prp.event_node(d) == devi / "event77"
        prp.DEV_INPUT = tmp_path / "nothing"
        assert prp.event_node(d) is None

    def test_find_pads_ignores_unrelated_devices(self, tmp_path):
        prp = self._load()
        for n, name, ph in ((0, "Lid Switch", "PNP0C0D/button/input0"),
                            (1, "Microsoft X-Box 360 pad", "usb-1/input0"),
                            (2, "Microsoft X-Box 360 pad",
                             prp.UinputBackend.PHYS)):
            d = tmp_path / f"input{n}"
            d.mkdir()
            (d / "name").write_text(name + "\n")
            (d / "phys").write_text(ph + "\n")
        found = prp.find_pads(tmp_path)
        # name alone qualifies the device as ours; a missing sysfs is empty.
        assert [p.name for p in found] == ["input1", "input2"], found
        assert prp.find_pads(tmp_path / "absent") == []

    def test_main_honours_its_path_constants(self, tmp_path):
        """If main() ever hardcodes /sys/class/input, every test in this class
        keeps passing while reading the real (padless) sysfs here -- a class
        that is green forever and checks nothing. Redirect the constants and
        require main to have looked where we redirected them."""
        verdict, rc, out = self._drive(tmp_path)
        assert verdict == "PASS", out
        # The sandbox is the only place a pad was ever created, so a PASS can
        # only come from having read it. Assert on strings that exist ONLY in
        # the redirected files: a run against the real /sys or /proc cannot
        # produce them, which is what catches main() hardcoding a path.
        assert "input77" in out, out          # from SYS_INPUT
        assert "Handlers=event77" in out, out  # from PROC_DEVICES
        assert "Lid Switch" not in out         # only our stanza may be printed


def test_about_box_credits_the_upstream_it_ports():
    """The credit line is the one place the port diverges from the client on purpose.

    Recorded as a test because it is a choice about attribution, not a styling detail,
    and "just restore parity with the original" is the kind of well-meaning edit that
    would quietly put someone else's name on this build. If this fails, it should be
    because someone decided to change the wording -- in which case they are changing a
    credit, and should say so in the commit message.
    """
    import glasspad
    import pathlib

    hub = pathlib.Path(glasspad.__file__).with_name("overlay_layer.py").read_text()
    assert '"Linux port of:"' in hub, "the port no longer says what it is"
    assert "github.com/dansunmi/GlassPad" in hub, (
        "and no longer names the project it ports -- the line above points at nothing")
    # Checked on the caption call, not the whole file: prose is allowed to *discuss*
    # the old wording -- the method this replaces is explained by quoting it. What must
    # not come back is the line being drawn.
    assert '_caption("by Dansunmi"' not in hub, (
        "the client's own authorship line is being rendered again, on a build that is "
        "not the client")
    # the MIT notice is what actually carries the upstream copyright, so it had better
    # exist next to the code that cites it
    lic = pathlib.Path(glasspad.__file__).resolve().parent.parent / "LICENSE"
    assert lic.is_file() and "MIT" in lic.read_text(), (
        "the About box leans on LICENSE for attribution and there is no LICENSE")


def test_version_strings_agree():
    """One release, one version number, wherever it is read from.

    The port's own version lived in three places: the package, `--version`, and
    the About box. Nothing kept them equal, so a bump could leave `--version` and
    the About box quoting different releases -- exactly what a user repeats when
    reporting a bug, and enough to make "did you get the fix?" unanswerable.

    Asserting all three equal to each other is not enough, because that still
    passes when every one of them was forgotten. So the expected number is pinned
    literally here as well: bump the package and not the CLI and this fails, and
    bump everything without updating this test and it fails too. That is the
    intent -- a release bump is supposed to touch this file.

    The Windows original's version is deliberately NOT asserted. Strings like
    "ported from GlassPad 1.0.2 (MainWindow.xaml)" name the source release and
    stay correct whatever this port is numbered.
    """
    import glasspad
    from glasspad import __main__ as entry

    declared = "1.1.0"  # bump alongside glasspad/__init__.py and CHANGELOG.md
    assert glasspad.__version__ == declared, (
        "glasspad/__init__.py says %s but this test expects %s -- a release "
        "bump is supposed to update all three"
        % (glasspad.__version__, declared))
    assert entry.__version__ == glasspad.__version__, (
        "`--version` reports %s while the package says %s"
        % (entry.__version__, glasspad.__version__))
    # The About box used to be a Qt window holding its own APP_VERSION literal -- which
    # is precisely how two places come to quote two releases. The layer hub interpolates
    # the package constant, so there is nothing left to keep in step. Assert that,
    # because the day someone types a literal into the caption is the day this bug
    # returns, and it returns quietly.
    hub = pathlib.Path(glasspad.__file__).with_name("overlay_layer.py").read_text()
    assert "from glasspad import __version__" in hub, (
        "the hub no longer imports the package version -- where does the About box get "
        "its number from now?")
    assert "GlassPad v{__version__}" in hub, (
        "the About box caption no longer interpolates __version__; if it has its own "
        "literal, this test has to compare them by hand instead")


# Filled here rather than in the class body so a broken import cannot take the whole
# module down: the guard is worth keeping even if bundle.py ever moves.
try:
    from glasspad.bundle import REMOVED_QT_MODULES as _GONE
except Exception:                                       # noqa: BLE001
    _GONE = ()
# Deleted in the lean-folder pass, when the tree was trimmed to what is needed to
# build and install the AppImage. Prose in glasspad/*.py still cites them as the
# origin of measurements -- render_style.py is where the pressed-button bevel was
# derived, resize_probe.py where the grip-zone size was measured -- and a
# provenance comment that names a file which no longer exists is a truer record
# than one edited to pretend the measurement came from nowhere. Citable, not
# resolvable: exactly the distinction the Qt modules above established.
TRIMMED_TOOLS = frozenset({
    "hit_test.py", "layer_probe.py", "layer_probe.qml", "pad_probe.py",
    "render_dpad.py", "render_resize_previews.py", "render_stick_curve.py",
    "render_style.py", "resize_probe.py", "verify_screen.py",
    # and the second bundle builder, retired when the project went AppImage-only;
    # its install-desktop subcommand now lives in glasspad/desktop_entry.py
    "make_bundle.py",
    # kept: uinput_watch.py and probe_real_pad.py, because this file tests them.
    # Dropping them would have dropped the only checks that a pad is real and
    # that no virtual device leaks behind the suite -- coverage, not clutter.
})
# Dropped as documentation-adjacent bulk; tests/test_glasspad.py still mentions it
# in a comment about where to bump the version alongside.
TRIMMED_DOCS = frozenset({"CHANGELOG.md"})

TestDocPointers.HISTORICAL = frozenset(_GONE) | TRIMMED_TOOLS | TRIMMED_DOCS
