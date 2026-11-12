"""Tests for the GlassPad Linux port.

Run: QT_QPA_PLATFORM=offscreen pytest tests -q

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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import (  # noqa: E402
    QEventPoint,
    QPointingDeviceUniqueId,
    QTouchEvent,
)
from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from glasspad import passthrough, xd360  # noqa: E402
from glasspad.editor import HANDLE_SIZE, MIN_SIZE, EditOverlay  # noqa: E402
from glasspad.layout import TAG_TO_BUTTON, GamepadLayout  # noqa: E402
from glasspad.pad import (  # noqa: E402
    HAT_BUTTONS,
    HAT_VECTORS,
    NullBackend,
    PadService,
    to_hm,
)
from glasspad.widgets import (  # noqa: E402
    TOUCH_PRESSED,
    TOUCH_RELEASED,
    CircleButton,
    FabButton,
    Pill,
    shape_hit,
)
from glasspad.touch_router import TouchRouter  # noqa: E402
from glasspad.zones import DpadZone, StickZone, to_hat4, to_hat8  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def require_offscreen(qapp) -> None:
    """Skip unless Qt is really running the offscreen plugin.

    These assertions are about what happens when there is *no* native window --
    winId() is a fake small integer, and shaping it is what used to make libX11's
    default error handler call exit(). Run under QT_QPA_PLATFORM=xcb against a
    live server the premise is gone and shaping legitimately succeeds, so
    reporting a failure would blame the code for the platform it was started on.
    The suite's documented target stays offscreen; this just keeps a second run
    against a real X server showing only real failures. pytest.skip() is the
    right call under both runners, because tools/run_tests.py installs its own
    `pytest` stand-in whose skip() raises the Skipped type that runner catches.
    """
    if qapp.platformName() != "offscreen":
        pytest.skip("needs the offscreen plugin, this run uses "
                    f"{qapp.platformName()!r}")


def router_local(window, widget) -> QPointF:
    """Centre of `widget` in the overlay's window coordinates.

    The router hit-tests in window space, so every test that asks "who owns this
    pixel" has to convert from widget-local first -- mapTo(self) is a no-op for a
    widget whose parent is not the window itself.
    """
    origin = widget.mapTo(window, QPoint(0, 0))
    return QPointF(origin.x() + widget.width() / 2,
                   origin.y() + widget.height() / 2)


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


# ---------------------------------------------------------------------------
# D-pad hat maths (DpadZone.cs)
# ---------------------------------------------------------------------------
class TestHatMath:
    @pytest.mark.parametrize("angle,expected", [
        (-90, "N"), (0, "E"), (90, "S"), (180, "W"), (-180, "W"),
    ])
    def test_cardinals_4way(self, angle, expected):
        assert to_hat4(angle) == expected

    def test_4way_boundaries_are_half_open(self):
        # C#: a > -135 && a <= -45 -> N, so -45 is N and -45+ is E.
        assert to_hat4(-45) == "N"
        assert to_hat4(-44.999) == "E"
        assert to_hat4(45) == "E"
        assert to_hat4(45.001) == "S"
        assert to_hat4(135) == "S"
        assert to_hat4(135.001) == "W"
        assert to_hat4(-135) == "W"
        assert to_hat4(-134.999) == "N"

    @pytest.mark.parametrize("angle,expected", [
        (0, "E"), (45, "SE"), (90, "S"), (135, "SW"),
        (180, "W"), (-135, "NW"), (-90, "N"), (-45, "NE"),
    ])
    def test_eight_way_sector_centres(self, angle, expected):
        assert to_hat8(angle) == expected

    def test_8way_sector_edges(self):
        # Sector boundaries at +/-22.5 from each axis.
        assert to_hat8(22.4) == "E"
        assert to_hat8(22.6) == "SE"
        assert to_hat8(-22.4) == "E"
        assert to_hat8(-22.6) == "NE"

    def test_8way_wraps_negative_angles(self):
        assert to_hat8(-360) == to_hat8(0) == "E"
        assert to_hat8(370) == to_hat8(10)


class TestDpadZoneLogic:
    def test_centre_deadzone(self, qapp):
        zone = DpadZone()
        zone.resize(180, 180)
        hats = []
        zone.hat_changed.connect(hats.append)
        zone.control_press(QPointF(90, 90))
        assert hats == ["None"]

    @pytest.mark.parametrize("dx,dy,expected", [
        (0, -50, "N"), (0, 50, "S"), (50, 0, "E"), (-50, 0, "W"),
        (40, -40, "NE"), (40, 40, "SE"), (-40, 40, "SW"), (-40, -40, "NW"),
    ])
    def test_directions_8way(self, qapp, dx, dy, expected):
        zone = DpadZone()
        zone.resize(180, 180)
        zone.mode = DpadZone.EIGHT
        hats = []
        zone.hat_changed.connect(hats.append)
        zone.control_press(QPointF(90 + dx, 90 + dy))
        assert hats[-1] == expected

    def test_release_reports_none(self, qapp):
        zone = DpadZone()
        zone.resize(180, 180)
        hats = []
        zone.hat_changed.connect(hats.append)
        zone.control_press(QPointF(90, 40))
        zone.control_release()
        assert hats[-1] == "None"
        assert zone.is_pressed is False

    def test_geometry_constants(self):
        assert DpadZone.BASE == 180
        assert DpadZone.BASE_ARM == 60
        assert DpadZone.RING_SIZE == 200
        assert DpadZone.DEAD_ZONE == 12

    def test_cross_polygon_has_12_points(self):
        assert len(DpadZone.cross_polygon(180, 60)) == 12

    def test_arm_rects_match_addarmhighlight(self):
        zone = DpadZone()
        # c = (180-60)/2 = 60
        assert (zone.arm_rect("N").x(), zone.arm_rect("N").y()) == (60, 0)
        assert (zone.arm_rect("S").x(), zone.arm_rect("S").y()) == (60, 120)
        assert (zone.arm_rect("W").x(), zone.arm_rect("W").y()) == (0, 60)
        assert (zone.arm_rect("E").x(), zone.arm_rect("E").y()) == (120, 60)

    def test_uniform_scale_at_default_size(self, qapp):
        zone = DpadZone()
        zone.resize(180, 180)
        s, ox, oy = zone._scale()
        assert (s, ox, oy) == (1.0, 0.0, 0.0)

    def test_viewbox_scaling_maps_touch_back_to_base(self, qapp):
        # A resized (scaled) dpad must still resolve direction correctly.
        zone = DpadZone()
        zone.resize(90, 90)
        zone.mode = DpadZone.FOUR
        hats = []
        zone.hat_changed.connect(hats.append)
        zone.control_press(QPointF(45, 10))  # straight up from centre
        assert hats[-1] == "N"


# ---------------------------------------------------------------------------
# Stick zone
# ---------------------------------------------------------------------------
class TestStickZone:
    def test_travel_constant(self):
        # The C# comment claims 28px; the expression evaluates to 31.
        assert StickZone.MAX_TRAVEL == 31.0
        assert (StickZone.OUTER - StickZone.INNER) / 2 == 31.0

    def test_centre_is_zero(self, qapp):
        zone = StickZone("LS")
        zone.resize(119, 119)
        zone.control_press(QPointF(59.5, 59.5))
        assert zone.X == pytest.approx(0.0)
        assert zone.Y == pytest.approx(0.0)

    def test_full_deflection_clamps(self, qapp):
        zone = StickZone("LS")
        zone.resize(119, 119)
        zone.control_press(QPointF(119, 59.5))
        assert zone.X == pytest.approx(1.0)
        assert zone.Y == pytest.approx(0.0)

    def test_partial_deflection(self, qapp):
        zone = StickZone("LS")
        zone.resize(119, 119)
        zone.control_press(QPointF(59.5 + 15.5, 59.5))
        assert zone.X == pytest.approx(0.5)

    def test_circle_not_corner(self, qapp):
        # Corner (45 deg) is clamped to the travel radius, so both axes land at
        # 1/sqrt(2), never 1.0/1.0.
        zone = StickZone("LS")
        zone.resize(119, 119)
        zone.control_press(QPointF(119, 119))
        assert zone.X == pytest.approx(math.sqrt(0.5))
        assert zone.Y == pytest.approx(math.sqrt(0.5))

    def test_release_returns_to_centre_and_emits(self, qapp):
        zone = StickZone("LS")
        zone.resize(119, 119)
        events = []
        zone.changed.connect(lambda z: events.append((z.X, z.Y)))
        zone.control_press(QPointF(119, 59.5))
        zone.control_release()
        assert events[-1] == (0.0, 0.0)
        assert zone.X == 0.0 and zone.Y == 0.0
        assert zone.is_pressed is False

    def test_move_updates(self, qapp):
        zone = StickZone("LS")
        zone.resize(119, 119)
        zone.control_press(QPointF(59.5, 59.5))
        zone.control_move(QPointF(59.5 - 31, 59.5))
        assert zone.X == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# Layout geometry and persistence
# ---------------------------------------------------------------------------
class TestLayoutGeometry:
    @pytest.fixture()
    def layout(self, qapp):
        canvas = QWidget()
        canvas.resize(1920, 1200)
        layout = GamepadLayout(canvas)
        layout.build(1920, 1200)
        return layout

    def test_button_count_and_order(self, layout):
        tags = [tag for _, tag in layout.buttons]
        assert tags == ["LT", "RT", "LB", "RB", "Y", "X", "B", "A",
                        "L3", "R3", "\u229f", "\u2299", "\u229e"]

    def test_all_editable_order_matches_windows(self, layout):
        # Windows: 13 buttons, then dpad, LS, RS -> indices 13/14/15.
        editable = layout.all_editable
        assert len(editable) == 16
        assert isinstance(editable[13], DpadZone)
        assert isinstance(editable[14], StickZone) and editable[14].name == "LS"
        assert isinstance(editable[15], StickZone) and editable[15].name == "RS"

    def test_dpad_and_stick_index_order(self, layout):
        # dpad is added before LS, but all_editable is buttons + elements, so
        # element 4 is dpad and 5 is LS in the *element* list.
        assert layout.defaults[13].element is layout.dpad

    @pytest.mark.parametrize("tag,x,y,w,h", [
        ("LT", 40, 35, 110, 55),
        ("RT", 1920 - 150, 35, 110, 55),
        ("LB", 40, 100, 120, 48),
        ("RB", 1920 - 160, 100, 120, 48),
    ])
    def test_pills(self, layout, tag, x, y, w, h):
        el = next(e for e, t in layout.buttons if t == tag)
        assert (el.x(), el.y(), el.width(), el.height()) == (x, y, w, h)

    @pytest.mark.parametrize("tag,cx,cy,d", [
        ("Y", 1920 - 175, 1200 * 0.52 - 83, 75),
        ("X", 1920 - 175 - 83, 1200 * 0.52, 75),
        ("B", 1920 - 175 + 83, 1200 * 0.52, 75),
        ("A", 1920 - 175, 1200 * 0.52 + 83, 75),
        ("L3", 190, 124, 44),
        ("R3", 1920 - 190, 124, 44),
        ("\u229f", 960 - 115, 1200 * 0.90, 52),
        ("\u2299", 960, 1200 * 0.88, 62),
        ("\u229e", 960 + 115, 1200 * 0.90, 52),
    ])
    def test_circles_are_centred_on_their_point(self, layout, tag, cx, cy, d):
        el = next(e for e, t in layout.buttons if t == tag)
        assert el.width() == el.height() == d
        assert el.x() == round(cx - d / 2)
        assert el.y() == round(cy - d / 2)

    def test_dpad_position(self, layout):
        dpad = layout.dpad
        assert dpad.width() == dpad.height() == 180
        assert dpad.x() == round(175 - 90)
        assert dpad.y() == round(1200 * 0.52 - 90)

    def test_stick_positions(self, layout):
        ls, rs = layout.sticks
        assert (ls.width(), ls.height()) == (119, 119)
        assert ls.x() == round(260 - 59.5)
        assert ls.y() == round(1200 * 0.78 - 59.5)
        assert rs.x() == round(1920 - 300 - 59.5)

    def test_every_tag_maps_to_a_button_or_trigger(self, layout):
        from glasspad.layout import TAG_TO_TRIGGER

        for _el, tag in layout.buttons:
            assert tag in TAG_TO_BUTTON or tag in TAG_TO_TRIGGER, tag

    def test_guide_button_mapping(self):
        assert TAG_TO_BUTTON["\u2299"] == "GUIDE"
        assert TAG_TO_BUTTON["\u229f"] == "BACK"
        assert TAG_TO_BUTTON["\u229e"] == "START"


class TestLayoutPersistence:
    @pytest.fixture()
    def layout(self, qapp):
        canvas = QWidget()
        canvas.resize(1920, 1200)
        layout = GamepadLayout(canvas)
        layout.build(1920, 1200)
        return layout

    def test_save_then_load_roundtrip(self, layout, tmp_path):
        path = str(tmp_path / "layout.json")
        layout.all_editable[0].setGeometry(123, 456, 111, 55)  # default is 40,35
        layout.save_layout(path, 0.65, 8)

        canvas2 = QWidget()
        canvas2.resize(1920, 1200)
        layout2 = GamepadLayout(canvas2)
        layout2.build(1920, 1200)
        opacity, mode = layout2.load_layout(path)

        assert opacity == pytest.approx(0.65)
        assert mode == 8
        assert layout2.all_editable[0].x() == 123
        assert layout2.all_editable[0].y() == 456
        assert layout2.all_editable[0].width() == 111

    def test_json_shape_matches_windows(self, layout, tmp_path):
        path = str(tmp_path / "layout.json")
        layout.save_layout(path, 1.0, 4)
        with open(path) as handle:
            root = json.load(handle)
        assert set(root) == {"opacity", "dpadMode", "elements"}
        assert len(root["elements"]) == 16
        assert set(root["elements"][0]) == {"left", "top", "width", "height"}

    def test_loads_file_written_by_windows(self, layout, tmp_path):
        # Hand-written in exactly the format the C# app emits (indent 2, same
        # keys) with the stick at index 14 moved.
        elements = [
            {"left": 0, "top": 0, "width": 100, "height": 50}
            for _ in range(16)
        ]
        elements[14] = {"left": 500, "top": 700, "width": 200, "height": 200}
        path = tmp_path / "layout.json"
        path.write_text(json.dumps(
            {"opacity": 0.5, "dpadMode": 8, "elements": elements}, indent=2))

        opacity, mode = layout.load_layout(str(path))
        assert (opacity, mode) == (0.5, 8)
        ls = layout.sticks[0]
        assert (ls.x(), ls.y(), ls.width(), ls.height()) == (500, 700, 200, 200)

    def test_missing_file_returns_defaults(self, layout, tmp_path):
        assert layout.load_layout(str(tmp_path / "nope.json")) == (1.0, 4)

    def test_corrupt_file_is_ignored(self, layout, tmp_path):
        path = tmp_path / "layout.json"
        path.write_text("{not json")
        assert layout.load_layout(str(path)) == (1.0, 4)

    def test_short_element_array_is_not_fatal(self, layout, tmp_path):
        path = tmp_path / "layout.json"
        path.write_text(json.dumps({"opacity": 1.0, "dpadMode": 4,
                                    "elements": [{"left": 5, "top": 5,
                                                 "width": 50, "height": 50}]}))
        layout.load_layout(str(path))
        assert layout.all_editable[0].x() == 5
        assert layout.all_editable[1].x() != 5  # untouched

    def test_reset_restores_defaults(self, layout):
        target = layout.all_editable[0]
        original = (target.x(), target.y(), target.width(), target.height())
        target.setGeometry(1, 2, 300, 400)
        layout.reset()
        assert (target.x(), target.y(), target.width(), target.height()) == original

    def test_reset_shows_hidden_elements(self, layout):
        # Hold-to-disable hides elements; Reset must bring them back. isHidden()
        # is the explicit flag and stays meaningful without a shown parent.
        target = layout.all_editable[0]
        target.setVisible(False)
        assert target.isHidden()
        layout.reset()
        assert not target.isHidden()


# ---------------------------------------------------------------------------
# Edit overlay
# ---------------------------------------------------------------------------
class TestEditOverlay:
    @pytest.fixture()
    def setup(self, qapp):
        root = QWidget()
        root.resize(1000, 800)
        button = CircleButton("A", Qt.transparent if False else __import__(
            "PyQt6.QtGui", fromlist=["QColor"]).QColor("#B3108040"), root)
        button.setGeometry(400, 300, 80, 80)
        root.show()
        button.show()
        overlay = EditOverlay(root)
        overlay.set_elements([button])
        return root, button, overlay

    def test_hit_test_drag_area(self, setup):
        _root, button, overlay = setup
        kind, el = overlay.hit_test(QPointF(440, 340))
        assert kind == "drag" and el is button

    def test_hit_test_outside(self, setup):
        _root, _button, overlay = setup
        kind, el = overlay.hit_test(QPointF(10, 10))
        assert kind == "" and el is None

    def test_resize_handle_wins_over_drag(self, setup):
        _root, button, overlay = setup
        hx, hy, hw, hh = overlay.handle_rect(button)
        kind, el = overlay.hit_test(QPointF(hx + hw / 2, hy + hh / 2))
        assert kind == "resize" and el is button

    def test_drag_moves_element(self, setup):
        _root, button, overlay = setup
        overlay.control_press(QPointF(440, 340))
        overlay.control_move(QPointF(540, 440))
        overlay.control_release()
        assert (button.x(), button.y()) == (500, 400)

    def test_resize_grows(self, setup):
        _root, button, overlay = setup
        hx, hy, hw, hh = overlay.handle_rect(button)
        overlay.control_press(QPointF(hx + hw / 2, hy + hh / 2))
        overlay.control_move(QPointF(hx + hw / 2 + 40, hy + hh / 2 + 20))
        overlay.control_release()
        assert button.width() == 120
        assert button.height() == 100

    def test_resize_minimum_is_44(self, setup):
        _root, button, overlay = setup
        hx, hy, hw, hh = overlay.handle_rect(button)
        overlay.control_press(QPointF(hx + hw / 2, hy + hh / 2))
        overlay.control_move(QPointF(0, 0))
        assert button.width() == MIN_SIZE
        assert button.height() == MIN_SIZE

    def test_release_stops_dragging(self, setup):
        _root, button, overlay = setup
        overlay.control_press(QPointF(440, 340))
        overlay.control_release()
        overlay.control_move(QPointF(900, 700))
        assert (button.x(), button.y()) == (400, 300)

    def test_toggle_shows_and_hides(self, setup):
        _root, _button, overlay = setup
        overlay.toggle()
        assert overlay.active and overlay.isVisible()
        overlay.toggle()
        assert not overlay.active and not overlay.isVisible()

    def test_exit_forces_inactive(self, setup):
        _root, _button, overlay = setup
        overlay.toggle()
        overlay.exit()
        assert not overlay.active

    def test_handle_size_constant(self):
        assert HANDLE_SIZE == 26


# ---------------------------------------------------------------------------
# Shape hit-testing / input region parity
# ---------------------------------------------------------------------------
class TestShapeParity:
    def test_circle_corners_are_not_hits(self, qapp):
        btn = CircleButton("A", QColor("red"))
        btn.resize(100, 100)
        assert shape_hit(btn, 50, 50) is True
        assert shape_hit(btn, 2, 2) is False  # corner, outside the circle
        assert shape_hit(btn, 50, 4) is True  # top of the circle

    def test_pill_caps(self, qapp):
        pill = Pill("LT", QColor("red"), 55)
        pill.resize(110, 55)
        assert shape_hit(pill, 55, 27) is True
        assert shape_hit(pill, 2, 2) is False
        assert shape_hit(pill, 55, 2) is True

    def test_row_span_agrees_with_shape_hit(self, qapp):
        """The XShape region must not claim pixels the hit test rejects."""
        circle = CircleButton("A", QColor("red"))
        pill = Pill("LT", QColor("red"), 55)
        for widget in (circle, pill):
            widget.resize(100, 60)
            for y in range(0, 60):
                span = passthrough.row_span(widget, y + 0.5)
                if span is None:
                    assert not any(shape_hit(widget, x, y + 0.5)
                                   for x in range(100)), f"y={y} missed a hit"
                    continue
                lo, hi = span
                for x in range(100):
                    inside = lo <= x < hi
                    assert inside == shape_hit(widget, x, y + 0.5), \
                        f"{type(widget).__name__} disagrees at ({x},{y})"

    def test_scanline_region_accepts_plain_rects(self, qapp):
        root = QWidget()
        root.resize(400, 300)
        rects = passthrough.scanline_region([(0, 0, 400, 300)], 400, 300)
        # Coalesced rows mean fewer rectangles than the window is tall.
        assert rects and len(rects) < 300
        assert sum((e - s) * h for s, y, w, h in [(x, y, w, h) for x, y, w, h in rects]
                   for e in [s + w]) > 0

    def test_scanline_region_is_bounded_by_widgets(self, qapp):
        root = QWidget()
        root.resize(800, 600)
        btn = CircleButton("A", QColor("red"), root)
        btn.setGeometry(100, 100, 80, 80)
        root.show()
        btn.show()
        rects = passthrough.scanline_region([btn], 800, 600)
        assert rects
        for x, y, w, h in rects:
            assert 100 <= x and x + w <= 180
            assert 100 <= y and y + h <= 180

    def test_hidden_and_faded_widgets_are_dropped(self, qapp):
        """Hold-to-disable and opacity 0 must both remove a control's region.

        This is the WPF rule: alpha 0 is neither drawn nor hit-tested.
        """
        from PyQt6.QtWidgets import QGraphicsOpacityEffect

        root = QWidget()
        root.resize(400, 400)
        btn = CircleButton("A", QColor("red"), root)
        btn.setGeometry(10, 10, 50, 50)
        root.show()
        btn.show()

        shapes = passthrough._shapes([btn, None])
        assert len(shapes) == 1
        widget, x, y, w, h = shapes[0]
        assert widget is btn and (x, y, w, h) == (10, 10, 50, 50)

        btn.setVisible(False)
        assert passthrough._shapes([btn]) == []

        btn.setVisible(True)
        effect = QGraphicsOpacityEffect(btn)
        effect.setOpacity(0.0)
        btn.setGraphicsEffect(effect)
        assert passthrough._shapes([btn]) == []

        effect.setOpacity(0.4)
        assert len(passthrough._shapes([btn])) == 1

    def test_plain_rect_blockers_are_accepted(self, qapp):
        shapes = passthrough._shapes([(5, 6, 70, 80), (0, 0, 0, 0)])
        assert shapes == [(None, 5, 6, 70, 80)]

# ---------------------------------------------------------------------------
# Session detection
# ---------------------------------------------------------------------------
class TestSessionDetection:
    def test_offscreen_without_env(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        info = passthrough.detect_session()
        assert info.server == "offscreen"

    def test_wayland_only(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert passthrough.detect_session().server == "wayland"

    def test_xwayland_without_shape_falls_back(self, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        # No reachable X server in CI, so the XShape probe fails and we report
        # wayland rather than promising pass-through we cannot deliver.
        info = passthrough.detect_session()
        assert info.server in ("x11", "wayland")

    def test_x11_env_probes_shape(self, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":99")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        info = passthrough.detect_session()
        assert info.server == "x11"

    def test_strategy_string_is_informative(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        assert "no display server" in passthrough.detect_session().strategy

    def test_offscreen_detail_does_not_deny_a_present_server(self, monkeypatch):
        """A forced headless Qt used to report "no DISPLAY and no
        WAYLAND_DISPLAY" with WAYLAND_DISPLAY set.

        The verdict (no shaping) was right; the reason printed under it was
        wrong, and sent the user to export a variable they had already exported
        while the actual cause -- QT_QPA_PLATFORM=offscreen -- went unmentioned.
        """
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        info = passthrough.detect_session()
        assert info.server == "offscreen"
        assert "no DISPLAY and no WAYLAND_DISPLAY" not in info.detail
        assert "offscreen" in info.detail

    def test_no_server_at_all_still_says_so(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        assert "no DISPLAY and no WAYLAND_DISPLAY" in \
            passthrough.detect_session().detail

    def test_wayland_strategy_promises_nothing_unbuilt(self, monkeypatch):
        """Native Wayland once promised "one top-level per control", a mode
        that was never written, then promised nothing at all once click-through
        actually landed on Wayland.

        Whichever way the capability goes, the strategy string has to follow it:
        claiming pass-through the session cannot deliver is as much a lie as
        reporting a working feature as missing, because both send the user off to
        debug something that is not broken.
        """
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        info = passthrough.detect_session()
        assert info.server == "wayland"
        strategy = info.strategy.lower()
        assert "top-level per control" not in strategy
        available = passthrough.wayland_input_region_available()
        assert info.pass_through is available
        if available:
            assert "not passed through" not in strategy
            assert "input region" in strategy
        else:
            assert "not passed through" in strategy

    def test_unprobed_xwayland_does_not_borrow_wayland_capability(
            self, qapp, monkeypatch):
        """Regression: `pass-through: True | XWayland client without XShape`.

        Both halves of that --status line came out of the same branch. server
        was "wayland" because the XShape probe could not run -- $DISPLAY named
        nothing, and Qt on a non-xcb plugin refuses the connection outright --
        while pass_through reported the QWindow.setMask capability of the
        *Wayland* plugin, an API a process Qt is drawing through Xwayland is not
        using. The verdict contradicted the reason printed beside it, and the
        reason blamed a missing X extension when the real cause was a stale
        $DISPLAY: the same class of defect as the offscreen detail, right
        verdict, wrong reason, user sent off to debug something not broken.
        """
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        monkeypatch.setenv("DISPLAY", ":77")     # nothing listens here
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        passthrough._X.reset()
        info = passthrough.detect_session()
        available = passthrough.wayland_input_region_available()
        assert info.server == "wayland", (
            f"expected the wayland verdict on a session whose $DISPLAY answers "
            f"nothing, got {info.server!r} ({info.detail})")
        assert "without XShape" not in info.detail, (
            f"detail blames a missing X extension that was never probed: "
            f"{info.detail!r}")
        # Flipping the boolean would silence the report while lying the same
        # way, so the assertion names the capability instead of a direction.
        assert info.pass_through is available, (
            f"pass_through={info.pass_through!r} but the Wayland plugin's "
            f"setMask capability is {available!r}; the flag and its reason "
            f"({info.detail!r}) must be derived from the same evidence")
        assert "QWindow.setMask" in info.detail, (
            f"detail does not name the API actually in use: {info.detail!r}")
        assert "without XShape" not in info.strategy, (
            f"strategy contradicts pass_through={info.pass_through!r}: "
            f"{info.strategy!r}")

    def test_wayland_verdict_via_a_live_x_connection_denies_pass_through(self):
        """The genuine "no XShape under XWayland" case must still say no.

        Fixing the case above by making server "wayland" always mean "the
        Wayland plugin owns this surface" would be just as wrong the other way:
        once a Display* has been opened the xcb plugin owns the window, and
        QWindow.setMask cannot reach the compositor through it. Both routes are
        reported as server "wayland", so which API owns the surface has to be
        carried explicitly rather than inferred from the verdict.
        """
        info = passthrough.SessionInfo("wayland", False,
                                       "XWayland client without XShape",
                                       via_x11=True)
        assert info.pass_through is False, (
            "a wayland verdict reached through a live X connection reported "
            "pass-through from QWindow.setMask, an API the xcb plugin cannot "
            "route to the compositor")
        assert "not passed through" in info.strategy, (
            f"strategy promises a region this session cannot cut: "
            f"{info.strategy!r}")

    def test_wayland_without_x_display_borrows_nothing_yet_denies_nothing(
            self, monkeypatch):
        """Keeps the fix from over-reaching.

        With $DISPLAY unset there was no X option to probe, so the Wayland
        plugin does own the surface and pass_through stays whatever setMask can
        do -- exactly the session the detail now describes.
        """
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        passthrough._X.reset()
        info = passthrough.detect_session()
        assert info.server == "wayland"
        assert info.via_x11 is False
        assert info.pass_through is \
            passthrough.wayland_input_region_available()

_REAL_X_SOCKET_DIR = "/tmp/.X11-unix"


class TestVerifyToolTeardown:
    """The verify tools must tear the overlay down the way the app does.

    Both mapped a full-screen click-through overlay, printed their verdict and
    called sys.exit() with the window still alive. closeEvent is what runs
    pad.dispose() and router.shutdown(), so neither ran: the event filter stayed
    installed on the QApplication, and a live X window was left for the
    interpreter to unwind around -- the one place a teardown fault surfaces as a
    core dump printed immediately under the tool's own "ALL PASS", which reads as
    though a certified path had crashed.

    The app cannot show this on the signal path, and was measured not to: on
    SIGINT/SIGTERM app.quit() closes windows on the way out, and pad.dispose()
    then router.shutdown() ran with exit 0 across three runs (xcb).

    That result was over-extended in an earlier revision of this docstring to
    claim the hub's Quit button was sound because it also calls close(). It is
    not, and TestQuitOnClose owns that: close() did dispose the pad, but for a
    Qt::Tool window it does not end the event loop, so the process survived with
    no window. Teardown-logic correctness and loop-exit are two different
    assertions -- proving one says nothing about the other.

    Parsed as an AST rather than searched as text so a mention in prose cannot
    satisfy it. What is being asserted is that the file attempts the teardown;
    whether close() actually ran is visible in the tool's own transcript, which
    now prints a teardown marker before its verdict.
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    @pytest.mark.parametrize("tool_name",
                             ["verify_x11.py", "verify_wayland.py"])
    def test_tool_closes_its_window_before_exiting(self, tool_name):
        tool = self.ROOT / "tools" / tool_name
        assert tool.exists(), f"{tool} is gone; update me"
        tree = ast.parse(tool.read_text())
        closes = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "close"
                  and not node.args]
        assert closes, (
            f"{tool_name} exits with its window still mapped, so closeEvent "
            "never runs: pad.dispose() and router.shutdown() are skipped and a "
            "teardown fault would surface as a core dump under its own ALL PASS")


class TestQuitOnClose:
    """The hub's Quit button must end the process, not just hide the overlay.

    Reported from hardware: clicking Quit made the overlay disappear, and the
    program kept running in konsole until the user pressed Ctrl-C. Nothing here
    was wrong with closeEvent -- the hub's button is wired to self.close(), the
    window did close, and dispose/shutdown ran. The fault is that MainWindow is
    a Qt.Tool window, and QWidget::quitOnClose -- the attribute Qt's
    quitOnLastWindowClosed rule actually consults -- is FALSE for a window that
    is not a window with a titlebar. Qt::Tool windows are excluded, exactly like
    Qt::ToolTip and Qt::Popup, so closing the only window left zero visible
    windows and app.exec() kept running with no window, no pad input and no way
    out from the desktop.

    Measured rather than assumed: a plain QWidget whose close() is the only
    action returns from exec() on its own; the same widget with the overlay's
    exact flags (FramelessWindowHint | WindowStaysOnTopHint | Tool) does not,
    under offscreen AND xcb AND with no DISPLAY at all. Setting
    WA_QuitOnClose on the overlay restores the exit in all three.

    No test caught this because 256 tests asserted that close() disposes the pad
    -- which it always did -- and none asked whether the event loop ended.
    """

    @pytest.fixture
    def overlay(self, qapp, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from glasspad.window import MainWindow

        win = MainWindow(pad=PadService(NullBackend()))
        win.resize(1920, 1200)
        win.show()
        qapp.processEvents()
        yield win
        if win.isVisible():
            win.close()

    def test_the_overlay_is_excluded_by_qt_default(self, overlay):
        """Why the bug was possible at all: Tool windows are not quit-on-close.

        Asserts the premise, so that if a Qt change ever makes Tool windows
        count by default this test fails loudly instead of leaving the other one
        passing for a reason nobody verified.
        """
        assert overlay.windowFlags() & Qt.WindowType.Tool
        # Default for a Tool window is False; the overlay must override it.
        assert overlay.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose) is True

    #: Child watchdog. Long enough that a real start-up cannot lose the race, so
    #: "still running" means the event loop never ended, not "not yet".
    WATCHDOG_MS = 3000

    CHILD = '''
import os, sys, tempfile
root = sys.argv[1]
sys.path.insert(0, root)
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from glasspad.pad import NullBackend, PadService
from glasspad.window import MainWindow
win = MainWindow(pad=PadService(NullBackend()))
win.resize(1920, 1200)
win.show()

def close_overlay():
    win.close()                      # exactly the hub Quit button

def close_dialog():
    from glasspad.license_window import LicenseWindow
    win._open_licenses()
    dlg = [w for w in app.topLevelWidgets()
           if type(w).__name__ == "LicenseWindow" and w.isVisible()]
    if not dlg:
        print("NO-DIALOG"); app.quit(); return
    dlg[0].close()                   # close ONLY the panel

mode = sys.argv[2]
QTimer.singleShot(300, close_overlay if mode == "overlay" else close_dialog)
def watchdog():
    # sys.exit would unwind Qt frames that never got a paint turn; os._exit is
    # the blunt instrument, so the report must be flushed by hand -- with stdout
    # a pipe it is block-buffered and os._exit skips the buffer entirely, which
    # showed up as an empty stdout and no clue which branch ran.
    print("STILL-RUNNING overlay_visible=%s" % win.isVisible(), flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(97)

QTimer.singleShot(int(sys.argv[3]), watchdog)
rc = app.exec()
print("EXEC-RETURNED rc=%s" % rc)
'''

    def _run_child(self, tmp_path, mode):
        """Start the real app in its own process and report how it ended.

        Deliberately out-of-process. Inside the suite there is no running
        event loop, and Qt's exit()/quit() with no loop sets a quitLocked flag
        that makes the NEXT exec() return at once -- so observing the quit
        decision in-process would leave later tests running against an event
        loop that silently cannot block. A subprocess also asserts the property
        the user actually reported ("the process ends") instead of a proxy for
        it.
        """
        script = tmp_path / "child.py"
        script.write_text(self.CHILD)
        proc = subprocess.run(
            [sys.executable, str(script), str(pathlib.Path(__file__).resolve()
                                   .parent.parent), mode, str(self.WATCHDOG_MS)],
            capture_output=True, text=True,
            env={**os.environ, "XDG_CONFIG_HOME": str(tmp_path)},
            timeout=self.WATCHDOG_MS / 1000 + 30)
        return proc

    def test_closing_the_overlay_ends_the_process(self, tmp_path):
        """The hub's Quit button must end the program, not just hide the overlay.

        This is the user-reported symptom: the overlay vanished and konsole kept
        running until Ctrl-C.
        """
        proc = self._run_child(tmp_path, "overlay")
        assert "EXEC-RETURNED" in proc.stdout, (
            "app.exec() never returned after the overlay closed -- the child "
            "was still alive at the watchdog and had to be killed ("
            f"stdout={proc.stdout.strip()!r}). Qt does not quit on the last "
            "closed window for a Qt::Tool window unless WA_QuitOnClose is set, "
            "so the process sits with no window and the only way out is "
            "Ctrl-C from the terminal")
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_closing_a_panel_does_not_end_the_session(self, tmp_path):
        """The fix must not turn closing the licenses dialog into quitting.

        LicenseWindow is a Qt::Dialog and already has quitOnClose True, so it
        was the one window Qt counted all along. With the overlay counted too,
        a panel still has to be closable while the overlay keeps running -- a
        user who opens the license list and shuts it must not lose the pad.

        Here the watchdog firing IS the pass: it means the loop was still
        running when the panel closed.
        """
        proc = self._run_child(tmp_path, "dialog")
        assert "NO-DIALOG" not in proc.stdout, proc.stdout + proc.stderr
        assert "EXEC-RETURNED" not in proc.stdout, (
            "closing the licenses dialog quit the app while the overlay was "
            "still open -- only the overlay's own close may end the session")
        assert "STILL-RUNNING overlay_visible=True" in proc.stdout, (
            "the session ended by some other route; expected to still be "
            "running with the overlay visible "
            f"(stdout={proc.stdout!r} stderr={proc.stderr[-400:]!r})")


class TestForceXcb:
    """Which Qt plugin the overlay starts on.

    This choice decides whether pass-through goes through XShape or
    QWindow.setMask, so a wrong guess is a broken overlay rather than a
    cosmetic one. The live QApplication is already offscreen here, and
    _force_xcb reads the environment plus the X socket directory, so the helpers
    are patched where they are used.
    """

    @pytest.fixture(autouse=True)
    def _never_touch_the_real_socket_dir(self, monkeypatch):
        """Make this class hermetic: the real /tmp/.X11-unix is an error.

        Two ways in, and both are refused. _x_socket_number() lists the socket
        directory, and since the liveness fix it also connects to every candidate
        to ask whether a server is really there. Patching only the listing --
        what earlier revisions of these tests did -- sent the probe to the
        developer's machine, so the class passed or failed depending on whether
        that host happened to be running Xvfb on :99.

        test_no_display_at_all_sets_nothing was the original instance of the
        first kind: it passed only because the container it was written in had
        never had an X socket, and on a desktop with Xwayland running it asserted
        `xcb not in os.environ` against a correct decision and failed.

        Loud failure rather than a default of "no X", so a forgotten patch cannot
        quietly assert the headless answer on a machine that is not headless.
        Anything else still works normally: only the real socket directory is
        off limits, so the tests' own temp directories and every other connect()
        in the suite are untouched.
        """
        real_listdir = os.listdir

        def refusing(path):
            if os.path.abspath(str(path)) == _REAL_X_SOCKET_DIR:
                raise AssertionError(
                    f"{path} was read unpatched; use the x_socket_dir fixture so "
                    "this case behaves identically on every machine")
            return real_listdir(path)

        monkeypatch.setattr(os, "listdir", refusing)

        real_connect = socket.socket.connect

        def refusing_connect(sock, addr):
            name = addr.decode("utf-8", "replace") if isinstance(addr, bytes) \
                else addr
            if isinstance(name, str):
                # An abstract-namespace address is the same text behind a NUL.
                if name.lstrip("\0").startswith(_REAL_X_SOCKET_DIR):
                    raise AssertionError(
                        f"{name} was probed unpatched; use the x_socket_dir "
                        "fixture so this case behaves identically on every machine")
            return real_connect(sock, addr)

        monkeypatch.setattr(socket.socket, "connect", refusing_connect)

    @pytest.fixture()
    def clean_env(self, monkeypatch):
        # _force_xcb writes DISPLAY/QT_QPA_PLATFORM, and the module under test
        # reads them through the global os, so monkeypatch's env undo is not
        # enough on its own: a leaked QT_QPA_PLATFORM=xcb here would make every
        # later session-detection test in the file see a display the fixture
        # deleted. Snapshot and restore around each case.
        saved = {name: os.environ.get(name)
                 for name in ("QT_QPA_PLATFORM", "DISPLAY", "WAYLAND_DISPLAY")}
        for name in saved:
            monkeypatch.delenv(name, raising=False)
        from glasspad import __main__ as entry
        yield entry
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    @pytest.fixture()
    def x_socket_dir(self, clean_env, tmp_path, monkeypatch):
        """A private stand-in socket directory holding sockets this test made.

        Returns make(live=[...], stale=[...], extra=[...]) and points the module
        at the directory it builds. `live` entries are bound and left listening;
        `stale` ones are bound and then closed, which is exactly what a killed
        server leaves behind and precisely what a directory listing cannot tell
        apart from a live one. `extra` adds plain files and odd names, so the
        real listdir output is parsed rather than a hand-written list.

        Real sockets rather than a stubbed listdir, because after the liveness
        fix the probe is part of the behaviour under test: stubbing it would
        assert the mock, and stubbing only the listing is what leaked the
        developer's machine into the result.

        create=False points the module at a directory that does not exist, for
        the Wayland-only case where the scan must find nothing at all.
        """
        listeners = []
        made = []

        def make(live=(), stale=(), extra=(), create=True):
            d = tmp_path / ("X11-unix-%d" % len(made))
            made.append(d)
            if not create:
                monkeypatch.setattr(clean_env, "_X_SOCKET_DIR", str(d))
                return d
            d.mkdir()
            for n in live:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.bind(str(d / ("X%d" % n)))
                s.listen(1)
                listeners.append(s)
            for n in stale:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.bind(str(d / ("X%d" % n)))
                s.close()          # the inode survives the server: a stale socket
            for name in extra:
                (d / name).write_text("")
            monkeypatch.setattr(clean_env, "_X_SOCKET_DIR", str(d))
            return d

        try:
            yield make
        finally:
            for s in listeners:
                s.close()

    def test_explicit_platform_always_wins(self, clean_env, monkeypatch):
        """CI, --status and offscreen runs must survive untouched."""
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
        monkeypatch.setenv("DISPLAY", ":99")
        clean_env._force_xcb()
        assert os.environ["QT_QPA_PLATFORM"] == "offscreen"

    def test_reachable_x_server_selects_xcb(self, clean_env, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":0")
        clean_env._force_xcb()
        assert os.environ["QT_QPA_PLATFORM"] == "xcb"

    def test_wayland_only_with_x_socket_uses_that_socket(self, clean_env,
                                                         monkeypatch,
                                                         x_socket_dir):
        """Xwayland running but DISPLAY not exported -- name the socket."""
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        x_socket_dir(live=[1, 0, 9])
        clean_env._force_xcb()
        assert os.environ["QT_QPA_PLATFORM"] == "xcb"
        assert os.environ["DISPLAY"] == ":0"  # lowest number, not the highest

    def test_only_a_high_socket_is_named_correctly(self, clean_env, monkeypatch,
                                                   x_socket_dir):
        """A real server on :1 must not be reported as :0."""
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        x_socket_dir(live=[1])
        clean_env._force_xcb()
        assert os.environ["DISPLAY"] == ":1"
        assert os.environ["QT_QPA_PLATFORM"] == "xcb"

    def test_wayland_only_without_x_socket_stays_wayland(self, clean_env,
                                                         monkeypatch,
                                                         x_socket_dir):
        """Inventing DISPLAY=:0 here would fail the launch outright.

        A Wayland-only compositor with no X socket is supported now that
        setMask gives click-through natively, so the right move is to leave the
        platform alone and let Qt load the wayland plugin.
        """
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        x_socket_dir(create=False)
        clean_env._force_xcb()
        assert "QT_QPA_PLATFORM" not in os.environ
        assert "DISPLAY" not in os.environ

    def test_empty_socket_directory_is_not_a_display(self, clean_env,
                                                     monkeypatch, x_socket_dir):
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        x_socket_dir()
        clean_env._force_xcb()
        assert "QT_QPA_PLATFORM" not in os.environ

    def test_no_display_at_all_sets_nothing(self, clean_env, monkeypatch,
                                            x_socket_dir):
        """Nothing named and no socket at all: leave Qt alone.

        clean_env only clears DISPLAY/QT_QPA_PLATFORM/WAYLAND_DISPLAY; it cannot
        make /tmp/.X11-unix not exist. x_socket_dir(create=False) does, so this
        asserts the headless answer on every machine rather than on whichever one
        happens to have no X socket. See the autouse fixture for what happened
        when it depended on the host.
        """
        x_socket_dir(create=False)
        clean_env._force_xcb()
        assert "QT_QPA_PLATFORM" not in os.environ
        assert "DISPLAY" not in os.environ

    def test_socket_above_the_legacy_range_is_found(self, clean_env,
                                                    x_socket_dir):
        """Xvfb and VNC put servers on :99 and above; they are not 'no X'.

        _x_socket_number used to test X0..X31, so a real server numbered higher
        than 31 was invisible and the launcher fell through to native Wayland --
        or failed to start -- on a machine that had a perfectly good X server.
        Parse the names instead of probing a fixed window.
        """
        x_socket_dir(live=[99])
        clean_env._force_xcb()
        assert os.environ["QT_QPA_PLATFORM"] == "xcb"
        assert os.environ["DISPLAY"] == ":99"

    def test_highest_number_never_wins_and_junk_is_ignored(self, clean_env,
                                                           x_socket_dir):
        """Lowest wins numerically (X9 not X10); non-socket names are skipped."""
        x_socket_dir(live=[10, 9],
                     extra=[".X99-lock", "Xauthority", "X\u0663"])
        assert clean_env._x_socket_number() == 9
        x_socket_dir(extra=[".X1-lock", "Xauthority", "Xfoo"])
        assert clean_env._x_socket_number() is None

    def test_stale_socket_from_a_dead_server_is_not_a_display(self, clean_env,
                                                              x_socket_dir):
        """A leftover socket file is not a server, and must not become DISPLAY=:7.

        _x_socket_number took the lowest FILE, so the inode a killed server
        leaves behind was reported as a display. That is not a degraded start but
        a dead one: with QT_QPA_PLATFORM=xcb and DISPLAY pointing at a server
        nobody is listening to, the xcb plugin cannot initialise and Qt abort()s,
        so one stale file turned a session that native Wayland would have served
        into a core dump.

        It is the common failure on a Wayland desktop, where Xwayland sockets go
        stale every time a session ends, on exactly the machines that have no X
        fallback to cover for it.
        """
        x_socket_dir(stale=[7])
        assert clean_env._x_socket_number() is None
        clean_env._force_xcb()
        assert "DISPLAY" not in os.environ
        assert "QT_QPA_PLATFORM" not in os.environ

    def test_a_live_server_still_wins_over_a_lower_stale_one(self, clean_env,
                                                             x_socket_dir):
        """:1 dead and :3 alive has to choose :3, not report no X at all."""
        x_socket_dir(stale=[1], live=[3])
        assert clean_env._x_socket_number() == 3

    def test_plain_file_named_like_a_socket_is_not_a_display(self, clean_env,
                                                             x_socket_dir):
        """Something in the socket directory that was never a socket.

        connect() answers ECONNREFUSED for a plain file as it does for a stale
        socket, so this lands on the same branch; it is pinned separately because
        a stray X5 dropped in by other software must not be read as a display.
        """
        x_socket_dir(extra=["X5"])
        assert clean_env._x_socket_number() is None

    def test_liveness_probe_accepts_a_perm_denial_as_alive(self, clean_env,
                                                           x_socket_dir,
                                                           tmp_path,
                                                           monkeypatch):
        """A socket we may not ask about is NOT evidence of absence.

        Someone else's server on a multi-seat or restricted machine answers
        EACCES. Reading that as dead would hide a working display, which is the
        opposite of the fix and the same class of mistake as the xpad check: a
        verdict drawn from a test that cannot pass. Only ECONNREFUSED and ENOENT
        prove nobody is listening, so monkeypatch connect() to raise EACCES the
        way a permission check would.

        Do not "simplify" this to treat every failure as dead. Both halves were
        measured, not assumed: a chmod-000 socket really does return EACCES from
        an AF_UNIX connect, and a server can still be reached through the
        abstract socket "\0/tmp/.X11-unix/XN" when the filesystem path denies or
        no longer has it. So EACCES is reachable code here, unlike ENOTSOCK,
        which is why one is handled and the other is not listed at all.
        """
        d = x_socket_dir(extra=["X4"])   # entry exists; probe result is forced

        def deny(sock, addr):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(socket.socket, "connect", deny)
        assert clean_env._x_socket_number() == 4
        # Sanity: the same forced failure with ECONNREFUSED does veto it, so the
        # assertion above is about the errno and not about a stub that always
        # returns True.
        monkeypatch.setattr(
            socket.socket, "connect",
            lambda sock, addr: (_ for _ in ()).throw(
                OSError(111, "Connection refused")))
        assert clean_env._x_socket_number() is None
        assert clean_env._socket_is_live(4, str(d)) is False

    def test_is_wayland_client_follows_qt_not_the_environment(self, monkeypatch):
        """A Wayland session with QT_QPA_PLATFORM=xcb must use XShape.

        Both variables are set on every Plasma session; only the plugin Qt
        actually loaded decides whether a window has an X resource to shape.
        """
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
        assert passthrough.is_wayland_client(app=None) is False
        monkeypatch.setenv("QT_QPA_PLATFORM", "wayland")
        monkeypatch.delenv("DISPLAY", raising=False)
        assert passthrough.is_wayland_client(app=None) is True
        # With no plugin named, only WAYLAND_DISPLAY and no DISPLAY says Wayland.
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        assert passthrough.is_wayland_client(app=None) is True
        monkeypatch.setenv("DISPLAY", ":0")
        assert passthrough.is_wayland_client(app=None) is False


# ---------------------------------------------------------------------------
# Full window integration
# ---------------------------------------------------------------------------
class TestWindow:
    @pytest.fixture()
    def window(self, qapp, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from glasspad.window import MainWindow

        pad = PadService(NullBackend())
        win = MainWindow(pad=pad)
        win.resize(1920, 1200)
        win.show()
        qapp.processEvents()
        yield win
        win.close()

    def test_hub_geometry(self, window):
        hub = window.hub_buttons
        assert hub["fab"].x() == 121 and hub["fab"].y() == 88
        assert hub["disable"].x() == 65 and hub["disable"].y() == 88
        assert hub["quit"].x() == 121 and hub["quit"].y() == 28
        assert hub["edit"].x() == 181 and hub["edit"].y() == 88
        assert hub["info"].x() == 121 and hub["info"].y() == 148

    def test_only_disable_and_fab_start_visible(self, window):
        hub = window.hub_buttons
        assert hub["disable"].isVisible() and hub["fab"].isVisible()
        assert not hub["quit"].isVisible()
        assert not hub["edit"].isVisible()
        assert not hub["info"].isVisible()

    def test_fab_opens_and_closes(self, window):
        window._toggle_fab()
        assert window._fab_open
        assert window.hub_buttons["quit"].isVisible()
        window._toggle_fab()
        assert not window._fab_open

    def test_pressing_a_face_button_sends_input(self, window):
        backend = window.pad.backend
        el = next(e for e, tag in window.layout.buttons if tag == "A")
        el.control_press(QPointF(5, 5))
        assert "A" in backend.last_buttons
        el.control_release()
        assert "A" not in backend.last_buttons

    def test_trigger_button_maps_to_axis(self, window):
        backend = window.pad.backend
        el = next(e for e, tag in window.layout.buttons if tag == "LT")
        el.control_press(QPointF(5, 5))
        assert backend.last_axes[xd360.ABS_Z] == 1.0
        el.control_release()
        assert backend.last_axes[xd360.ABS_Z] == 0.0

    def test_stick_drag_reaches_pad(self, window):
        backend = window.pad.backend
        ls = window.layout.sticks[0]
        ls.control_press(QPointF(59.5, 59.5))
        ls.control_move(QPointF(119, 59.5))
        assert backend.last_axes[xd360.ABS_X] == pytest.approx(1.0)
        ls.control_release()
        assert backend.last_axes[xd360.ABS_X] == pytest.approx(0.5)

    def test_dpad_reaches_pad(self, window):
        backend = window.pad.backend
        window.layout.dpad.mode = DpadZone.EIGHT
        window.layout.dpad.control_press(QPointF(130, 50))
        assert backend.last_hat == "NE"
        window.layout.dpad.control_release()
        assert backend.last_hat == "None"

    def test_disable_hides_all_and_releases(self, window):
        backend = window.pad.backend
        window.layout.dpad.control_press(QPointF(90, 40))
        window._set_overlay_enabled(False)
        assert backend.last_hat == "None"
        assert all(not el.isVisible() for el in window.layout.all_editable)
        window._set_overlay_enabled(True)
        assert all(el.isVisible() for el in window.layout.all_editable)

    def test_disable_button_latches_across_presses(self, window):
        """Two presses hide then show -- and the state survives in between.

        Driven through Qt's own dispatch, because the momentary original and the
        latching port differ only in what the *release* does, and a test that
        calls _set_overlay_enabled() by hand cannot see the difference at all.
        That is exactly how the one-way disable shipped: release_all() released
        the disable button from inside its own press handler, whose `released`
        signal used to be the re-enable action, so a single press ran disable /
        enable / disable and left no grab for the real release to find.

        A quick click must now leave the overlay hidden with the button still
        lit as the way back -- it is one of only two controls left on screen.
        """
        from PyQt6.QtGui import QMouseEvent

        disable = window.hub_buttons["disable"]
        origin = disable.mapTo(window, QPoint(0, 0))
        g = window.mapToGlobal(QPoint(origin.x() + disable.width() // 2,
                                      origin.y() + disable.height() // 2))
        c = QPointF(g.x(), g.y())

        def press():
            QApplication.sendEvent(window, QMouseEvent(
                QEvent.Type.MouseButtonPress, c, c, Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            QApplication.sendEvent(window, QMouseEvent(
                QEvent.Type.MouseButtonRelease, c, c, Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

        visible = lambda: sum(el.isVisible() for el in window.layout.all_editable)

        press()
        assert visible() == 0, "first press did not hide the overlay"
        assert window._overlay_enabled is False
        press()
        assert visible() == len(window.layout.all_editable), \
            "second press did not bring the overlay back"
        assert window._overlay_enabled is True

    def test_disable_stays_hidden_after_release(self, window):
        """The latch, specifically: letting go must not undo the hide.

        The whole point of deviating from the Windows original. Under the old
        momentary behaviour the overlay returned the instant the finger lifted,
        so a user could never see what they had uncovered or use the overlay
        while holding anything else.
        """
        from PyQt6.QtGui import QMouseEvent

        disable = window.hub_buttons["disable"]
        origin = disable.mapTo(window, QPoint(0, 0))
        g = window.mapToGlobal(QPoint(origin.x() + disable.width() // 2,
                                      origin.y() + disable.height() // 2))
        c = QPointF(g.x(), g.y())
        QApplication.sendEvent(window, QMouseEvent(
            QEvent.Type.MouseButtonPress, c, c, Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        assert not any(el.isVisible() for el in window.layout.all_editable)

        QApplication.sendEvent(window, QMouseEvent(
            QEvent.Type.MouseButtonRelease, c, c, Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        assert not any(el.isVisible() for el in window.layout.all_editable), \
            "releasing the disable button undid the latch"

    def test_disable_ignores_a_release_off_the_button(self, window):
        """Wired to `pressed`, so a slid-off contact cannot toggle it back.

        Measured, not assumed: FabButton.control_move deliberately does not
        un-press (WPF TouchDevice.Capture parity), so a press on the circle that
        slides 300px away still emits released AND clicked on release. A clicked
        handler would let a finger dragging off on the way out silently re-show
        the overlay for a reason the user cannot see.
        """
        from PyQt6.QtGui import QMouseEvent

        disable = window.hub_buttons["disable"]
        origin = disable.mapTo(window, QPoint(0, 0))
        g = window.mapToGlobal(QPoint(origin.x() + disable.width() // 2,
                                      origin.y() + disable.height() // 2))
        c = QPointF(g.x(), g.y())

        def send(kind, at, pressed):
            QApplication.sendEvent(window, QMouseEvent(
                kind, at, at, Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton if pressed else Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier))

        send(QEvent.Type.MouseButtonPress, c, True)
        assert window._overlay_enabled is False
        far = QPointF(c.x() + 300, c.y() + 300)
        send(QEvent.Type.MouseMove, far, True)
        send(QEvent.Type.MouseButtonRelease, far, False)
        assert window._overlay_enabled is False, \
            "a contact that left the button toggled the overlay back"

    def test_disable_button_reports_its_state(self, window):
        """Colour and tooltip follow the latch.

        While the overlay is hidden every gamepad control is gone, so this
        button and the menu are the only things left to explain why. A grey
        button that still reads "Hide the overlay" would look broken.
        """
        from glasspad.window import DISABLE_COLOR, DISABLED_COLOR, TOOLTIP_ENABLED

        assert window.hub_buttons["disable"].color == DISABLE_COLOR
        assert window.hub_buttons["disable"].toolTip() == TOOLTIP_ENABLED
        window._set_overlay_enabled(False)
        assert window.hub_buttons["disable"].color == DISABLED_COLOR
        assert "click" in window.hub_buttons["disable"].toolTip().lower()
        window._set_overlay_enabled(True)
        assert window.hub_buttons["disable"].color == DISABLE_COLOR

    def test_disable_lifts_a_held_button(self, window):
        """Exempting the disable button must not exempt everything else.

        Disabling has to lift whatever the other finger was holding, or the
        virtual pad keeps reporting a pressed button for the rest of the session.

        Uses the router's own press/release entry points because a synthesized
        QEventPoint always reports contact id 0 -- see touch_router._contact_id
        -- so two hand-made touch contacts would be one contact here.
        """
        backend = window.pad.backend
        a = next(e for e, tag in window.layout.buttons if tag == "A")
        disable = window.hub_buttons["disable"]

        def local(widget):
            origin = widget.mapTo(window, QPoint(0, 0))
            return QPointF(origin.x() + widget.width() / 2,
                           origin.y() + widget.height() / 2)

        window._router._press(1, local(a))
        assert "A" in backend.last_buttons

        window._router._press(2, local(disable))
        assert "A" not in backend.last_buttons, \
            "disabling left a gamepad button held down"
        assert window._router.grab_count() == 1, \
            "only the disable button itself may stay grabbed"
        assert disable._down, "disable lost its pressed look mid-press"

    def test_edit_mode_brings_a_hidden_overlay_back(self, window):
        """Entering edit mode must un-latch, and stay consistent afterwards.

        Editing positions you cannot see is not worth supporting -- and the edit
        panel's Reset calls GamepadLayout.reset(), which show()s every element,
        so without this a reset from edit mode would reveal the overlay while
        the disable button still claimed to be off.
        """
        window._set_overlay_enabled(False)
        assert not any(el.isVisible() for el in window.layout.all_editable)

        from glasspad.window import DISABLE_COLOR

        window._toggle_edit()
        assert window._edit_active
        assert window._overlay_enabled, "edit mode opened over a hidden overlay"
        assert all(el.isVisible() for el in window.layout.all_editable)
        assert window.hub_buttons["disable"].color == DISABLE_COLOR

        # and the toggle still works normally afterwards
        window._reset_layout()
        window._toggle_edit()
        assert window._overlay_enabled is True
        window._set_overlay_enabled(False)
        assert not any(el.isVisible() for el in window.layout.all_editable)
        window._set_overlay_enabled(True)
        assert all(el.isVisible() for el in window.layout.all_editable)

    def test_opacity_applies_to_every_element(self, window):
        window._opacity_slider.setValue(50)
        for el in window.layout.all_editable:
            assert el.graphicsEffect().opacity() == pytest.approx(0.5)
        assert window.hub_buttons["fab"].fade == pytest.approx(0.5)

    def test_dpad_mode_switch(self, window):
        window._set_dpad_mode(DpadZone.EIGHT)
        assert window.layout.dpad.mode == DpadZone.EIGHT
        window._set_dpad_mode(DpadZone.FOUR)
        assert window.layout.dpad.mode == DpadZone.FOUR

    def test_save_writes_layout_file(self, window, tmp_path):
        window._opacity_slider.setValue(60)
        window._set_dpad_mode(DpadZone.EIGHT)
        window._save_layout()
        path = tmp_path / "GlassPad" / "layout.json"
        assert path.exists()
        root = json.loads(path.read_text())
        assert root["dpadMode"] == 8
        assert root["opacity"] == pytest.approx(0.6)

    def test_saved_layout_reloads_in_new_window(self, qapp, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from glasspad.window import MainWindow

        first = MainWindow(pad=PadService(NullBackend()))
        first.resize(1920, 1200)
        first.layout.all_editable[0].setGeometry(111, 222, 99, 55)
        first._opacity_slider.setValue(70)
        first._set_dpad_mode(DpadZone.EIGHT)
        first._save_layout()
        first._save_confirm_yes()
        first.close()

        second = MainWindow(pad=PadService(NullBackend()))
        second.resize(1920, 1200)
        target = second.layout.all_editable[0]
        assert (target.x(), target.y(), target.width()) == (111, 222, 99)
        assert second.layout.dpad.mode == DpadZone.EIGHT
        second.close()

    def test_edit_mode_blocks_gamepad_targets(self, window):
        """The tint covers the gamepad, but the hub stays above it.

        The edit overlay fills the window, so the gamepad controls must drop out
        of the claimable set -- that is the full-screen EditCanvas of the
        original. The hub buttons must not: HubCanvas is a *later* sibling of
        EditCanvas in MainWindow.xaml, so the pencil is what a touch there
        reaches, and it is the only route out of edit mode. Listing the overlay
        alone made every press land on the tint and trapped the user in edit mode.
        """
        window._toggle_edit()
        targets = window._claimable_targets()
        assert targets[0] is window.edit_overlay
        assert window.layout.dpad not in targets
        assert window.hub_buttons["edit"] in targets
        assert targets.index(window.hub_buttons["edit"]) > targets.index(
            window.edit_overlay), "hub must out-rank the tint it sits above"
        window._toggle_edit()
        assert window.layout.dpad in window._claimable_targets()

    def test_edit_mode_pencil_exits_edit_mode(self, window, qapp):
        """End-to-end: in edit mode, tap the hub and tap the pencil to leave.

        Regression test for the edit overlay being raised above the hub, which
        handed every press to the tint and made the app un-exitable without
        killing it. The route out is the same one MainWindow.xaml offers: the
        always-visible FAB opens the menu even under the tint, and the pencil
        then closes edit mode.
        """
        router = window._router
        window._toggle_edit()
        assert window._edit_active

        fab = window.hub_buttons["fab"]
        assert router.hit_test(router_local(window, fab)) is fab, \
            "the tint swallowed the hub: edit mode cannot be left"
        router._press(1, router_local(window, fab))
        router._release(1)
        qapp.processEvents()
        pencil = window.hub_buttons["edit"]
        assert pencil.isVisible()

        router._press(2, router_local(window, pencil))
        router._release(2)
        qapp.processEvents()
        assert not window._edit_active

    def test_edit_panel_is_not_claimed_by_the_tint(self, window):
        """Slider and Save live inside the edit panel, above the tint.

        WPF hit-tests the visual tree top-down, so the panel eats the touch and
        the EditCanvas never sees it. The router must return None there and leave
        the pixel to Qt's touch-to-mouse synthesis.
        """
        window._toggle_edit()
        panel = window._edit_panel
        assert panel.isVisible()
        centre = router_local(window, panel)
        assert window._router.hit_test(centre) is None

    def test_save_dialog_blocks_the_tint_too(self, window):
        """Both panels are blockers, not just the edit one."""
        window._toggle_edit()
        window._save_layout()
        for panel in (window._save_confirm, window._info_panel):
            if not panel.isVisible():
                continue
            assert window._router.hit_test(router_local(window, panel)) is None

    def test_edit_overlay_does_not_cover_the_hub_or_the_panels(self, window, qapp):
        """Stacking, not just the router's view of it.

        The router is told what may claim input, but input Qt does *not* claim --
        the panels' QPushButtons and QSlider, reached through touch-to-mouse
        synthesis -- is delivered by real widget stacking. A raised full-window
        overlay swallows those at the Qt level, so _claimable_targets() alone can
        look correct while the edit panel is still dead. MainWindow.xaml orders
        HubCanvas and both dialogs after EditCanvas; childAt is what says so here.
        """
        window._toggle_edit()
        qapp.processEvents()
        for name, widget in (("edit panel", window._edit_panel),
                             ("fab", window.hub_buttons["fab"]),
                             ("disable", window.hub_buttons["disable"])):
            point = widget.mapTo(window, QPoint(widget.width() // 2,
                                                widget.height() // 2))
            top = window.childAt(point)
            node = top
            while node is not None and node is not widget:
                node = node.parentWidget()
            assert node is widget, (
                f"{name} is covered by {type(top).__name__} in edit mode; "
                "its controls would receive nothing")

    def test_panel_z_order_matches_the_xaml_zindex(self, window):
        """Panel.ZIndex 50 (About) < 60 (Save) -> blockers are ordered likewise.

        Qt has no ZIndex, so child creation order is the only thing that decides
        which panel wins a contested pixel. _input_blockers() is the port's
        record of that order, and both panels must be listed above the edit
        panel, which sits lowest of the three in the XAML tree.
        """
        blockers = window._input_blockers()
        assert blockers == [window._edit_panel, window._info_panel,
                            window._save_confirm]

    def test_about_panel_pixels_are_not_claimed(self, window):
        """Outside edit mode too: About box buttons must stay clickable."""
        window._open_info()
        assert window._info_panel.isVisible()
        assert window._router.hit_test(router_local(window, window._info_panel)) is None

    def test_close_disposes_pad(self, qapp, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from glasspad.window import MainWindow

        pad = PadService(NullBackend())
        win = MainWindow(pad=pad)
        backend = pad.backend
        win.close()
        assert pad._disposed
        # Repeated dispose must not raise.
        pad.dispose()


# ---------------------------------------------------------------------------
# Input region upkeep
#
# The window's input region is the only thing deciding whether a touch reaches
# the overlay at all, so it must be re-cut whenever anything shows or hides.
# A stale region is invisible in the worst way: the dialog paints, and every tap
# in it goes straight through to the game.
# ---------------------------------------------------------------------------
class TestInputRegionUpkeep:
    @pytest.fixture()
    def spying(self, qapp, tmp_path, monkeypatch):
        """A window plus a log of every region the overlay asked the backend for.

        The recorded value is the *computed* region, not the widget list: which
        widgets actually contribute is decided inside passthrough (hidden and
        fully faded ones contribute nothing), so geometry is the only assertion
        that survives a change to how the blocker list is assembled.
        """
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from glasspad.pad import PadService
        from glasspad.window import MainWindow

        win = MainWindow(pad=PadService(NullBackend()))
        win.resize(1920, 1200)
        win.show()
        qapp.processEvents()
        calls: list = []

        def fake_apply(window, blockers=()):
            calls.append({
                "whole": False,
                "rects": passthrough.scanline_region(
                    list(blockers), window.width(), window.height()),
            })
            return True

        def fake_block(window):
            calls.append({"whole": True, "rects": None})
            return True

        monkeypatch.setattr(passthrough, "apply", fake_apply)
        monkeypatch.setattr(passthrough, "block_whole_window", fake_block)
        # The spy goes in after show(), so record the resting region now: every
        # test below compares a later region against this one.
        win._refresh_passthrough()
        yield win, calls
        win.close()

    @staticmethod
    def covers(entry, widget) -> bool:
        """Does the recorded region accept a touch at the widget's centre?"""
        if entry["whole"]:
            return True
        origin = widget.mapTo(widget.window(), QPoint(0, 0))
        cx, cy = origin.x() + widget.width() / 2, origin.y() + widget.height() / 2
        return any(x <= cx < x + w and y <= cy < y + h
                   for x, y, w, h in entry["rects"])

    @staticmethod
    def settle(qapp, ms=300):
        """Run the loop long enough for the hub animations to finish."""
        deadline = time.time() + ms / 1000.0
        while time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.005)

    def test_opening_the_about_box_covers_it(self, spying, qapp):
        """Regression: the About box was drawn but entirely outside the region.

        Every tap in it fell through to the game, so the dialog looked dead.
        """
        win, calls = spying
        assert not self.covers(calls[-1], win._info_panel)
        win._open_info()
        qapp.processEvents()
        assert calls, "opening a panel must re-cut the input region"
        assert self.covers(calls[-1], win._info_panel)

    def test_hiding_the_about_box_undoes_it(self, spying, qapp):
        """The panel hides itself from its own Close button, so catch that too."""
        win, calls = spying
        win._open_info()
        qapp.processEvents()
        calls.clear()
        win._info_panel.setVisible(False)
        qapp.processEvents()
        assert calls, "a panel vanishing must shrink the region again"
        assert not self.covers(calls[-1], win._info_panel)

    def test_opening_the_hub_covers_its_new_buttons(self, spying, qapp):
        win, calls = spying
        calls.clear()
        win._toggle_fab()
        self.settle(qapp)
        assert calls
        for key in ("quit", "edit", "info"):
            button = win.hub_buttons[key]
            assert button.isVisible(), key
            assert self.covers(calls[-1], button), key

    def test_closing_the_hub_drops_the_buttons(self, spying, qapp):
        """AnimateOut hides from a finished() callback -- not a call site we own."""
        win, calls = spying
        win._toggle_fab()
        self.settle(qapp)
        calls.clear()
        win._close_fab()
        self.settle(qapp)
        assert calls
        for key in ("quit", "edit", "info"):
            button = win.hub_buttons[key]
            assert not button.isVisible(), key
            assert not self.covers(calls[-1], button), key

    def test_a_burst_of_changes_is_one_refresh(self, spying, qapp):
        """Coalescing: the hub's three buttons appear on 0/60/120ms delays.

        Re-cutting per change would spend several full XShape round trips on one
        tap, and the intermediate regions would each describe a half-drawn hub.
        """
        win, calls = spying
        calls.clear()
        win._toggle_fab()
        self.settle(qapp)
        assert len(calls) <= 4, \
            f"{len(calls)} region cuts for one hub opening"

    def test_edit_mode_still_takes_the_whole_window(self, spying, qapp):
        """The editor wants every touch, so the region must be the full window."""
        win, calls = spying
        calls.clear()
        win._toggle_edit()
        qapp.processEvents()
        assert calls and calls[-1]["whole"] is True

    def test_hide_then_show_restores_the_resting_region(self, spying, qapp):
        """Hold-to-disable and release must round-trip to the original region."""
        win, calls = spying
        baseline = calls[-1]["rects"]
        win._set_overlay_enabled(False)
        qapp.processEvents()
        for element in win.layout.all_editable:
            assert not self.covers(calls[-1], element)
        win._set_overlay_enabled(True)
        qapp.processEvents()
        assert calls[-1]["rects"] == baseline


# ---------------------------------------------------------------------------
# X11 error resilience
#
# libX11's default error handler calls exit(). A BadWindow from a shape request
# is routine in real use -- the window manager recreates windows, hold-to-disable
# unmaps them, an X reset orphans the connection -- so an overlay that left the
# default handler installed would quit mid-game. These tests pin the behaviour
# that replaced it.
# ---------------------------------------------------------------------------
class TestTouchRouter:
    """Per-contact routing, driven by real Qt6 touch objects where possible.

    An earlier revision of this class asserted that a real QTouchEvent cannot be
    built from Python, because PyQt6 exposes no QTouchEvent::TouchPoint. That was
    true of the *nested class* and false of the *replacement*: Qt6 moved touch
    points to QtGui.QEventPoint, which is constructible. The stand-ins written
    against the old belief supplied touchPoints(), pos() and an int state --
    accessors that do not exist on Qt6 or do not behave that way -- so the suite
    passed while the shipped router died on the first real touch:

        AttributeError: 'QTouchEvent' object has no attribute 'touchPoints'

    An exception escaping a Python event filter is not caught and reported by
    Qt; PyQt aborts the process. So a wrong accessor was not a wrong touch, it
    was the overlay and the user's game session ending in SIGABRT.

    Single-contact cases therefore use real QEventPoint/QTouchEvent objects,
    which is what makes the API surface an assertion rather than an assumption.
    Where two contacts must be told apart, stand-ins remain, because a
    synthesized QEventPoint reports id() == 0 and an invalid uniqueId() no matter
    what it was constructed with (measured on PyQt6 6.11) -- two of them are
    indistinguishable to Qt. Those stand-ins mirror the Qt6 interface exactly
    (points(), state() with a real .value flag, valid uniqueId()), so the router
    exercises the same code path real hardware takes.
    """

    STATE = QEventPoint.State

    @staticmethod
    def real_point(x, y, state):
        """A genuine QEventPoint. globalPosition is what the router maps."""
        return QEventPoint(0, state, QPointF(x, y), QPointF(x, y))

    @staticmethod
    def point(ident, x, y, state):
        """Interface-faithful stand-in carrying a distinct, VALID contact id."""
        return TestTouchRouter.FakePoint(ident, x, y, state)

    class FakePoint:
        def __init__(self, ident, x, y, state):
            self._id, self._pos, self._state = ident, QPointF(x, y), state

        def uniqueId(self):  # noqa: N802 - Qt's spelling
            return QPointingDeviceUniqueId.fromNumericId(self._id)

        def id(self):
            return self._id

        def state(self):
            # Mirror Qt6 rather than the caller: state() yields a State flag,
            # and Qt6's flags expose .value while rejecting int() and `& int`.
            # Returning the bare int the test passed would let the router read
            # .value only because the double was wrong, which is the exact
            # mistake this rewrite exists to remove.
            s = self._state
            return s if isinstance(s, QEventPoint.State) \
                else QEventPoint.State(s)

        def globalPosition(self):  # noqa: N802
            return self._pos

        def scenePosition(self):  # noqa: N802
            return self._pos

    class FakeTouchEvent:
        """Stands in for QTouchEvent with Qt6's accessor names."""

        def __init__(self, kind, points):
            self._kind, self._points = kind, points
            self.accepted = False

        def type(self):
            return self._kind

        def points(self):
            return self._points

        def accept(self):
            self.accepted = True

    class MouseEvent:
        def __init__(self, kind, x, y, button=Qt.MouseButton.LeftButton):
            self._kind, self._x, self._y, self._button = kind, x, y, button

        def type(self):
            return self._kind

        def button(self):
            return self._button

        def globalPosition(self):  # noqa: N802
            return QPointF(self._x, self._y)

    @pytest.fixture()
    def routed(self, qapp, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from glasspad.pad import PadService
        from glasspad.window import MainWindow

        win = MainWindow(pad=PadService(NullBackend()))
        win.resize(1920, 1200)
        win.show()
        qapp.processEvents()
        yield win
        win.close()

    def touch(self, kind, points):
        """A touch event wrapping whatever points were supplied.

        Accepts both real QEventPoint objects and FakePoint stand-ins; the
        router cannot tell them apart, which is the point.
        """
        return self.FakeTouchEvent(kind, list(points))

    def real_touch(self, kind, x, y, state):
        """A real QTouchEvent holding one real QEventPoint."""
        return QTouchEvent(kind, None, Qt.KeyboardModifier.NoModifier,
                           [self.real_point(x, y, state)])

    # -- delivery ---------------------------------------------------------
    def test_window_accepts_touch_events(self, routed):
        """Without WA_AcceptTouchEvents Qt sends no touch events at all.

        The overlay relied on mouse synthesis for everything, so a real finger
        produced single-pointer mouse input: two buttons at once were impossible
        -- the exact WPF limitation touch_router.py exists to remove -- and the
        whole router was dead code on hardware. Qt only offers touch to widgets
        that opt in, so the router opts in on the window it is constructed for.
        """
        assert routed.testAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)

    def test_filter_rejects_a_foreign_object(self, routed):
        """Touch is claimed only for the overlay window.

        Panels are deliberately left to Qt's touch-to-mouse synthesis, so their
        QPushButtons keep working; a touch on any *other* window must not be
        swallowed here.
        """
        router = routed._router
        other = QWidget()
        event = self.touch(QEvent.Type.TouchBegin,
                          [self.point(1, 500, 500, TOUCH_PRESSED)])
        assert router.eventFilter(other, event) is False
        assert router.grab_count() == 0

    def test_touch_press_and_release_drive_one_control(self, routed):
        router = routed._router
        el = next(e for e, tag in routed.layout.buttons if tag == "A")
        pos = el.mapTo(routed, QPoint(0, 0))
        centre = QPointF(pos.x() + el.width() / 2, pos.y() + el.height() / 2)

        # Real QTouchEvent/QEventPoint objects, not stand-ins: this is the
        # assertion that would have caught touchPoints()/pos()/int(state()) if it
        # had ever existed. A real object has none of those in that form, and the
        # router calling any of them aborts the process rather than returning
        # False, so "the suite passed" was never evidence of "the finger works".
        pressed = self.real_touch(QEvent.Type.TouchBegin, centre.x(),
                                  centre.y(), self.STATE.Pressed)
        assert router.eventFilter(routed, pressed) is True
        assert "A" in routed.pad.backend.last_buttons

        released = self.real_touch(QEvent.Type.TouchEnd, centre.x(),
                                   centre.y(), self.STATE.Released)
        assert router.eventFilter(routed, released) is True
        assert "A" not in routed.pad.backend.last_buttons
        assert router.grab_count() == 0

    def test_two_contacts_press_two_controls(self, routed):
        """The point of the router, and the limitation of the Windows original."""
        router = routed._router
        a = next(e for e, tag in routed.layout.buttons if tag == "A")
        b = next(e for e, tag in routed.layout.buttons if tag == "B")
        pa = a.mapTo(routed, QPoint(0, 0))
        pb = b.mapTo(routed, QPoint(0, 0))
        ca = QPointF(pa.x() + a.width() / 2, pa.y() + a.height() / 2)
        cb = QPointF(pb.x() + b.width() / 2, pb.y() + b.height() / 2)

        both = self.touch(QEvent.Type.TouchBegin, [
            self.point(1, ca.x(), ca.y(), TOUCH_PRESSED),
            self.point(2, cb.x(), cb.y(), TOUCH_PRESSED),
        ])
        assert router.eventFilter(routed, both) is True
        assert {"A", "B"} <= routed.pad.backend.last_buttons
        assert router.grab_count() == 2

        # Release out of order; each grab must still reach its own control.
        router.eventFilter(routed, self.touch(
            QEvent.Type.TouchEnd, [self.point(2, cb.x(), cb.y(), TOUCH_RELEASED)]))
        assert "B" not in routed.pad.backend.last_buttons
        assert "A" in routed.pad.backend.last_buttons

    def test_touch_cancel_event_releases_every_grab(self, routed):
        """Regression: a stolen gesture used to leave the trigger held down.

        The Windows-era code and this port both tested for a cancelled *state*
        bit (Qt5's TouchPointCancelled, 0x20). Qt6 has no such state -- it
        delivers the whole interruption as QEvent.Type.TouchCancel -- so that
        branch could never run, and this test only passed because the stand-in
        fed it the very bit the test invented. Holding RT, then losing the
        gesture to the compositor, left ABS_RZ at full deflection for the rest
        of the session.
        """
        router = routed._router
        el = next(e for e, tag in routed.layout.buttons if tag == "RT")
        pos = el.mapTo(routed, QPoint(0, 0))
        centre = QPointF(pos.x() + el.width() / 2, pos.y() + el.height() / 2)
        router.eventFilter(routed, self.real_touch(
            QEvent.Type.TouchBegin, centre.x(), centre.y(), self.STATE.Pressed))
        assert routed.pad.backend.last_axes[xd360.ABS_RZ] == 1.0
        assert router.grab_count() == 1

        cancel = QTouchEvent(QEvent.Type.TouchCancel, None,
                             Qt.KeyboardModifier.NoModifier, [])
        assert router.eventFilter(routed, cancel) is True
        assert routed.pad.backend.last_axes[xd360.ABS_RZ] == 0.0
        assert router.grab_count() == 0

    @pytest.mark.parametrize("state_name", ["Updated", "Stationary"])
    def test_updated_and_stationary_contacts_move_the_control(self, routed,
                                                              state_name):
        """Qt6 renamed Moved to Updated; both must still drive the stick.

        A drag arrives as Updated (the contact moved) and Stationary (another
        one did not). Dropping either freezes the stick mid-drag: the press
        registered, the release will come, and everything between is lost.

        Asserted on the axis value rather than on a return value, because
        'returns True or False' would pass with the branch deleted.
        """
        router = routed._router
        stick = routed.layout.sticks[0]
        pos = stick.mapTo(routed, QPoint(0, 0))
        cx = pos.x() + stick.width() / 2
        cy = pos.y() + stick.height() / 2

        # Both contacts come from self.point(), not a real QEventPoint: a
        # synthesized real point reports id() 0 and an INVALID uniqueId, so its
        # grab key would not match the stand-in's valid id 21 and _move() would
        # find nothing -- failing for bookkeeping reasons, not the one tested.
        router.eventFilter(routed, self.touch(
            QEvent.Type.TouchBegin,
            [self.point(21, cx, cy, self.STATE.Pressed)]))
        assert router.grab_count() == 1
        resting = dict(routed.pad.backend.last_axes)

        # Drag far to the left of the stick and confirm the axis followed.
        moved = router.eventFilter(routed, self.touch(
            QEvent.Type.TouchUpdate,
            [self.point(21, cx - stick.width() * 0.45, cy,
                        getattr(self.STATE, state_name))]))
        assert moved is True, f"{state_name} contact was ignored"
        assert routed.pad.backend.last_axes[xd360.ABS_X]             != resting[xd360.ABS_X], (
            f"{state_name} contact did not move the stick: axis still "
            f"{routed.pad.backend.last_axes[xd360.ABS_X]}")
        router.release_all()

    def test_a_failing_touch_accessor_does_not_abort_the_process(self, routed):
        """An exception escaping an event filter makes PyQt abort (core dump).

        Verified directly: raising AttributeError inside eventFilter ends the
        process with SIGABRT rather than printing and continuing. So a single
        wrong accessor is not a dropped touch, it is the overlay and the user's
        game disappearing. The router contains dispatch faults and reports the
        first one instead of letting it escape.
        """
        import glasspad.touch_router as tr

        class Poisoned:
            def type(self):
                return QEvent.Type.TouchBegin

            def points(self):
                raise AttributeError("'QTouchEvent' object has no attribute "
                                     "'touchPoints'")

        router = routed._router
        # Reset before as well as after, so the test does not depend on whether
        # some earlier test already consumed the one report.
        tr._touch_error_reported = False
        assert router.eventFilter(routed, Poisoned()) is False
        assert router.grab_count() == 0
        # The fault is reported once, not per contact per frame.
        assert tr._touch_error_reported is True
        assert router.eventFilter(routed, Poisoned()) is False
        tr._touch_error_reported = False

    def test_release_follows_the_grab_not_the_pointer(self, routed):
        """A finger that slides off a button still releases it."""
        router = routed._router
        el = next(e for e, tag in routed.layout.buttons if tag == "A")
        pos = el.mapTo(routed, QPoint(0, 0))
        centre = QPointF(pos.x() + el.width() / 2, pos.y() + el.height() / 2)
        router.eventFilter(routed, self.touch(
            QEvent.Type.TouchBegin, [self.point(9, centre.x(), centre.y(), TOUCH_PRESSED)]))
        assert "A" in routed.pad.backend.last_buttons
        # Released a hundred pixels away, over nothing at all.
        router.eventFilter(routed, self.touch(
            QEvent.Type.TouchEnd, [self.point(9, 5, 5, TOUCH_RELEASED)]))
        assert "A" not in routed.pad.backend.last_buttons

    def test_touch_outside_a_control_is_left_alone(self, routed):
        """Unclaimed contacts must reach Qt, so panels still get synthesized
        clicks for their QPushButtons."""
        router = routed._router
        event = self.touch(QEvent.Type.TouchBegin, [self.point(3, 3, 3, TOUCH_PRESSED)])
        assert router.eventFilter(routed, event) is False
        assert event.accepted is False

    def test_release_all_lifts_everything(self, routed):
        router = routed._router
        el = next(e for e, tag in routed.layout.buttons if tag == "A")
        pos = el.mapTo(routed, QPoint(0, 0))
        centre = QPointF(pos.x() + el.width() / 2, pos.y() + el.height() / 2)
        router.eventFilter(routed, self.touch(
            QEvent.Type.TouchBegin, [self.point(4, centre.x(), centre.y(), TOUCH_PRESSED)]))
        router.release_all()
        assert "A" not in routed.pad.backend.last_buttons
        assert router.grab_count() == 0

    # -- geometry ---------------------------------------------------------
    def test_hit_test_is_shape_exact(self, routed):
        """The corner of a round button belongs to the game, not to the button.

        passthrough.py cuts round shapes out of the input region, so a bounding
        box here would press a button on pixels that already pass through -- the
        press would be invisible and the release could never reach it.
        """
        circles = [e for e in routed.layout.all_editable
                   if getattr(e, "shape_kind", "rect") == "circle"]
        assert circles, "layout lost its round controls"
        for widget in circles:
            origin = widget.mapTo(routed, QPoint(0, 0))
            corner = QPointF(origin.x() + 0.5, origin.y() + 0.5)
            if shape_hit(widget, 0.5, 0.5):
                continue  # a rect-shaped control legitimately claims its corner
            hit = routed._router.hit_test(corner)
            assert hit is not widget, \
                f"{type(widget).__name__} took a touch outside its shape"

    def test_mouse_press_still_routes(self, routed):
        """Desktop parity: a click drives the overlay too."""
        router = routed._router
        el = next(e for e, tag in routed.layout.buttons if tag == "A")
        pos = el.mapTo(routed, QPoint(0, 0))
        centre = (pos.x() + el.width() / 2, pos.y() + el.height() / 2)
        assert router.eventFilter(
            el, self.MouseEvent(QEvent.Type.MouseButtonPress, *centre)) is True
        assert "A" in routed.pad.backend.last_buttons
        assert router.eventFilter(
            el, self.MouseEvent(QEvent.Type.MouseButtonRelease, *centre)) is True
        assert "A" not in routed.pad.backend.last_buttons

    # -- the widget contract ----------------------------------------------
    def _hub_open(self, win, qapp):
        """Open the hub and wait for the fade so its buttons are claimable."""
        win._toggle_fab()
        deadline = time.time() + 0.5
        while time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        return [w for w in win._claimable_targets()
                if isinstance(w, FabButton)]

    def test_every_claimable_widget_implements_the_whole_contract(self, routed,
                                                                  qapp):
        """A control the router can grab must survive press AND move AND release.

        Regression: the hub's FabButton had control_press and control_release but
        no control_move, and _claimable only tested for control_press. So a hub
        button was grabbed on press and the next event raised AttributeError
        inside the event filter -- which PyQt converts into SIGABRT, taking the
        overlay and the game with it. Reporting it in a comment is not enough:
        any future momentary control hits the same wall unless this asserts.

        Checked across every target the router is actually offered, with the hub
        open, rather than across a hand-picked list.
        """
        contract = TouchRouter.CONTRACT
        targets = list(routed._claimable_targets())
        hubs = self._hub_open(routed, qapp)
        assert hubs, "hub buttons never became claimable; nothing was tested"
        targets += hubs

        incomplete = {}
        for widget in targets:
            missing = tuple(n for n in contract if not hasattr(widget, n))
            if missing:
                incomplete.setdefault(type(widget).__name__, missing)
        assert not incomplete, (
            "claimable but incomplete: "
            + ", ".join(f"{n} missing {list(m)}" for n, m in sorted(incomplete.items()))
            + f" -- the router drives {list(contract)} on anything it grabs")

    @pytest.mark.parametrize("via", ["touch", "mouse"])
    def test_dragging_on_a_hub_button_does_not_abort(self, routed, qapp, via):
        """Press then move on a momentary button: it has nowhere to move to.

        Both input paths reach _move(), and only the touch path was wrapped in a
        try/except, so a click plus a small drag on the hub's own button aborted
        the process with no touchscreen involved. Driven through real Qt
        dispatch, because calling the handler directly would not prove the filter
        contains a raising accessor.
        """
        from PyQt6.QtGui import QMouseEvent
        import glasspad.touch_router as tr   # module, not the flag: rebinding
        # the flag by value would read a stale copy after the router sets it.

        hubs = self._hub_open(routed, qapp)
        assert hubs, "hub buttons never became claimable; nothing was tested"
        fab = hubs[0]
        origin = fab.mapTo(routed, QPoint(0, 0))
        cx, cy = origin.x() + fab.width() / 2, origin.y() + fab.height() / 2

        tr._touch_error_reported = False
        if via == "touch":
            routed._router.eventFilter(routed, self.real_touch(
                QEvent.Type.TouchBegin, cx, cy, self.STATE.Pressed))
            routed._router.eventFilter(routed, self.real_touch(
                QEvent.Type.TouchUpdate, cx + 6, cy + 6, self.STATE.Updated))
            routed._router.eventFilter(routed, self.real_touch(
                QEvent.Type.TouchEnd, cx + 6, cy + 6, self.STATE.Released))
        else:
            for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                         QEvent.Type.MouseButtonRelease):
                ev = QMouseEvent(kind, QPointF(cx, cy), QPointF(cx, cy),
                                 Qt.MouseButton.LeftButton,
                                 Qt.MouseButton.LeftButton,
                                 Qt.KeyboardModifier.NoModifier)
                QApplication.sendEvent(routed, ev)   # Qt's own dispatch
                qapp.processEvents()

        # Reaching the assertions at all is the fix: an escaping exception would
        # have aborted the process instead.
        assert tr._touch_error_reported is False, (
            f"{via} path still faults on a hub button (see the printed traceback)")
        assert routed._router.grab_count() == 0
        tr._touch_error_reported = False


# ---------------------------------------------------------------------------
# Wayland pass-through
# ---------------------------------------------------------------------------
class TestWaylandPassthrough:
    def test_scale_rects_is_identity_at_one(self, qapp):
        widget = QWidget()
        widget.resize(100, 100)
        assert passthrough.scale_rects(widget, [(10, 20, 30, 40)]) == [(10, 20, 30, 40)]

    def test_scale_rects_covers_the_drawn_shape(self, qapp, monkeypatch):
        """At a fractional scale the region must be a superset, never a subset.

        Rounding both edges down loses the bottom pixel row of every control, so
        a button appears to ignore touches along its lower edge.
        """
        class Scaled:
            """Stands in for a QWidget on a 125% display.

            devicePixelRatio is a method in Qt, so it has to be one here too --
            a plain float would make scale_rects() format a number and quietly
            skip the scaling being tested.
            """

            class Handle:
                def devicePixelRatio(self):
                    return 1.25

            def windowHandle(self):
                return Scaled.Handle()

        rects = passthrough.scale_rects(Scaled(), [(10, 20, 30, 40)])
        assert len(rects) == 1
        x, y, w, h = rects[0]
        assert x == 12 and y == 25
        # Far edges ceil: 40*1.25=50 -> 12+50=62, and 40+... covers the drawing.
        assert x + w >= (10 + 30) * 1.25 and y + h >= (20 + 40) * 1.25

    def test_wayland_region_available_on_this_build(self, qapp):
        # Qt 6.11 exports QWindow.setMask; a build without it must degrade to a
        # full-window overlay and say so rather than promise pass-through.
        assert passthrough.wayland_input_region_available() is True

    def test_wayland_apply_reaches_qwindow_setmask(self, qapp, monkeypatch):
        """apply() must pick the Wayland backend when Qt is on Wayland."""
        calls = []
        monkeypatch.setattr(passthrough, "is_wayland_client", lambda *a: True)
        monkeypatch.setattr(passthrough, "set_input_rectangles_wayland",
                            lambda window, rects: calls.append(rects) or True)
        monkeypatch.setattr(passthrough, "set_input_rectangles",
                            lambda window, rects: pytest.fail(
                                "XShape path used on a Wayland session"))
        widget = QWidget()
        widget.resize(200, 200)
        pill = Pill("X", QColor("red"), 40, widget)
        pill.setGeometry(10, 10, 80, 40)
        widget.show()
        qapp.processEvents()
        assert passthrough.apply(widget, [pill]) is True
        assert calls and calls[0], "no rectangles reached the Wayland backend"
        # Logical units: no devicePixelRatio multiplication on this path.
        assert all(x >= 10 and x + w <= 90 for x, y, w, h in calls[0])

    def test_empty_region_semantics_are_honest(self, monkeypatch):
        monkeypatch.setattr(passthrough, "is_wayland_client", lambda *a: True)
        assert passthrough.empty_region_passes(app=None) is False
        monkeypatch.setattr(passthrough, "is_wayland_client", lambda *a: False)
        assert passthrough.empty_region_passes(app=None) is True


class TestX11ErrorResilience:
    @pytest.fixture()
    def display(self):
        """A real connection to a real server, or a skipped test without one."""
        x = passthrough._X
        if not x.load():
            pytest.skip("libX11 unavailable")
        handle = x.open_display(":99") or x.open_display(None)
        if not handle:
            pytest.skip("no X server reachable")
        yield handle
        x.close_display(handle)

    def test_xerror_event_matches_header_layout(self):
        # <X11/Xlib.h>: int type; Display* display; XID resourceid;
        # unsigned long serial; unsigned char error_code, request_code,
        # minor_code. ctypes must mirror libX11 or the handler reads garbage.
        f = passthrough.XErrorEvent._fields_
        names = [name for name, _ in f]
        assert names == ["type", "display", "resourceid", "serial",
                         "error_code", "request_code", "minor_code"]
        offsets = {name: getattr(passthrough.XErrorEvent, name).offset
                   for name in names}
        assert offsets["type"] == 0
        assert offsets["display"] == 8
        assert offsets["resourceid"] == 16
        assert offsets["serial"] == 24
        assert offsets["error_code"] == 32
        # Same total size the C struct has, so libX11's writes stay in bounds.
        import ctypes as _ct
        assert _ct.sizeof(passthrough.XErrorEvent) == 40

    def test_error_handler_installed_and_retained(self):
        assert passthrough._X.load()
        # A callback handed to C but not referenced by Python is collected, and
        # libX11 then calls a freed trampoline. Keep-alive is load-bearing.
        assert passthrough._X._error_handler is not None

    def test_bad_window_is_recorded_not_fatal(self, display):
        x = passthrough._X
        before = x.error_count
        # Window id 1 is the offscreen placeholder and never a real resource.
        assert x.set_input_rectangles(display, 1, [(0, 0, 10, 10)]) is False
        # Exactly the finding: the process is still here and we can name the
        # error. libX11's default handler would have called exit() instead.
        assert x.error_count > before
        assert "BadWindow" in (x.last_error or "")
        assert "request 129" in (x.last_error or "")  # 129 == SHAPE opcode

    def test_stale_window_does_not_kill_the_connection(self, display):
        """A rejected window proves the server is still talking to us.

        The window manager recreating our window is routine, so treating that as
        a lost connection would reconnect on every refresh. Only a genuinely
        broken link should be dropped.
        """
        x = passthrough._X
        x.set_input_rectangles(display, 1, [(0, 0, 10, 10)])
        assert x.connection_healthy(display)

    def test_reading_a_bad_window_back_is_empty_not_fatal(self, display):
        assert passthrough._X.get_input_rectangles(display, 1) == []

    def test_error_on_a_foreign_connection_is_ignored(self, display):
        """A shape request on someone else's Display* must not be blamed on ours.

        Qt keeps its own connection in the same process and this handler is
        process-wide, so mis-attribution would break click-through for reasons
        that have nothing to do with the overlay.
        """
        x = passthrough._X
        # Named on purpose: open_display(None) is refused away from the xcb
        # plugin, which is the very guard this module installs.
        own = x.open_display(":99")
        if not own or own == display:
            pytest.skip("need two distinct connections")
        try:
            before = x._errors_for(own)
            x.set_input_rectangles(display, 1, [(0, 0, 10, 10)])
            assert x._errors_for(own) == before
        finally:
            x.close_display(own)

    def test_close_display_is_idempotent(self):
        """A failed request may drop the connection; the caller closes it too.

        XCloseDisplay twice is a use-after-free, so the ownership set has to act
        as the still-open marker.
        """
        x = passthrough._X
        handle = x.open_display(":99") or x.open_display(None)
        if not handle:
            pytest.skip("no X server reachable")
        x.close_display(handle)
        x.close_display(handle)  # must not fault
        assert handle not in x._owned

    def test_widget_validity_gate_blocks_fake_ids(self, qapp):
        # The offscreen plugin reports winId() == 1; shaping that is a BadWindow.
        widget = QWidget()
        assert passthrough.set_input_rectangles(widget, [(0, 0, 10, 10)]) is False

    def test_non_toplevel_is_refused(self, qapp):
        parent = QWidget()
        child = QWidget(parent)
        assert passthrough.set_input_rectangles(child, [(0, 0, 5, 5)]) is False

    def test_shape_survives_session_then_window(self, qapp, monkeypatch, tmp_path):
        """Regression: probing a real DISPLAY then drawing offscreen exited(1).

        detect_session() caches a live Display*; the overlay built afterwards is
        an offscreen window whose id is meaningless on that connection. The
        request used to reach the server and Xlib's default handler called exit.
        """
        require_offscreen(qapp)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("DISPLAY", os.environ.get("DISPLAY", ":99"))
        passthrough._X.reset()
        try:
            passthrough.detect_session()
        except Exception:  # noqa: BLE001 - no server here is fine
            pass
        monkeypatch.delenv("DISPLAY", raising=False)

        from glasspad.pad import PadService
        from glasspad.window import MainWindow

        win = MainWindow(pad=PadService(NullBackend()))
        win.resize(800, 600)
        win.show()
        qapp.processEvents()
        # Reaching the assertion at all is the fix: the process is still alive.
        assert passthrough.apply(win, []) is False
        win.close()


class TestQtPlatformGuard:
    """open_display() must refuse to connect when Qt cannot have an X window.

    The guard used to read `from PyQt6.QtCore import QGuiApplication` inside a
    bare except -- QGuiApplication is in QtGui, so the ImportError was swallowed
    and the check never ran. These assertions fail if it regresses.
    """

    def test_qt_is_xcb_is_false_offscreen(self, qapp):
        require_offscreen(qapp)
        assert qapp.platformName() == "offscreen"
        assert passthrough._X._qt_is_xcb() is False

    def test_open_display_refuses_without_xcb(self, qapp, monkeypatch):
        require_offscreen(qapp)
        monkeypatch.setenv("DISPLAY", os.environ.get("DISPLAY", ":99"))
        passthrough._X.reset()
        assert passthrough._X.open_display() is None

    def test_named_display_still_allowed(self):
        # Verification tools target a server explicitly, which is a deliberate
        # opt-in and must keep working under any Qt platform.
        x = passthrough._X
        handle = x.open_display(":99")
        if not handle:
            pytest.skip("no X server on :99")
        try:
            assert handle
        finally:
            x.close_display(handle)

    def test_apply_on_offscreen_window_is_a_clean_no_op(self, qapp, monkeypatch,
                                                        tmp_path):
        """The whole pass-through stack must degrade quietly, not exit."""
        require_offscreen(qapp)
        monkeypatch.setenv("DISPLAY", os.environ.get("DISPLAY", ":99"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from glasspad.pad import PadService
        from glasspad.window import MainWindow

        win = MainWindow(pad=PadService(NullBackend()))
        win.resize(640, 480)
        win.show()
        qapp.processEvents()
        assert passthrough.apply(win, list(win.layout.all_editable)) is False
        assert passthrough.block_whole_window(win) is False
        win.close()


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
    # is where install.sh and the license texts actually live.
    LOOKUP = (ROOT / "glasspad", ROOT / "desktop", ROOT / "licenses", ROOT)
    # Written at runtime in the user's config dir, never shipped.
    RUNTIME = {"layout.json"}
    NAMED = re.compile(r"(?<![\w/.-])([A-Za-z0-9_-]+\.(?:md|json|txt|sh|py|"
                       r"png|desktop))\b")

    def test_shipped_files_are_present_to_read(self):
        missing = [str(p) for p in self.SHIPPED if not p.is_file()]
        assert not missing, f"test itself is stale: {missing}"

    def test_no_dangling_file_references(self):
        dangling = []
        for path in self.SHIPPED:
            for name in sorted(set(self.NAMED.findall(
                    path.read_text(encoding="utf-8")))):
                if name in self.RUNTIME:
                    continue
                if not any((base / name).is_file() for base in self.LOOKUP):
                    dangling.append(f"{path.name} mentions {name}")
        assert not dangling, dangling

    @classmethod
    def _resolves(cls, name):
        return (name in cls.RUNTIME
                or any((base / name).is_file() for base in cls.LOOKUP))

    def test_the_checker_is_not_vacuous(self):
        # A scan that reads nothing would pass forever, so exercise the pattern
        # and the lookup on samples instead of on whichever mentions happen to
        # survive in the tree today.
        sample = ("see README.md and NOTES.md, run install.sh, load "
                  "layout.json, uses touch_router.py, not .md alone")
        assert set(self.NAMED.findall(sample)) == {
            "README.md", "NOTES.md", "install.sh", "layout.json",
            "touch_router.py"}
        # The lookup is what separates a real pointer from a dead one.
        assert self._resolves("touch_router.py")
        assert self._resolves("layout.json")   # runtime, deliberately exempt
        assert not self._resolves("no-such-file-here.md")

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
    from glasspad import window

    declared = "1.1.0"  # bump alongside glasspad/__init__.py and CHANGELOG.md
    assert glasspad.__version__ == declared, (
        "glasspad/__init__.py says %s but this test expects %s -- a release "
        "bump is supposed to update all three"
        % (glasspad.__version__, declared))
    assert entry.__version__ == glasspad.__version__, (
        "`--version` reports %s while the package says %s"
        % (entry.__version__, glasspad.__version__))
    assert window.APP_VERSION == glasspad.__version__, (
        "the About box shows %s while the package says %s"
        % (window.APP_VERSION, glasspad.__version__))
