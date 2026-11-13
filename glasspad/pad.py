"""PadService — virtual Xbox 360 pad, ported from GlassPad 1.0.2.

Windows original (src/GlassPad/Input/PadService.cs) drives HIDMaestro's
UMDF2 virtual controller. HIDMaestro has no Linux build, so the device is
created through the kernel's uinput instead. Everything that is not the
transport is preserved verbatim:

  * the same state fields and resting values (sticks at 0.5, triggers at 0.0),
  * the same ToHM() mapping from zone-space (-1..1) to normalised (0..1),
  * the same single lock held across a submit, so no torn state,
  * the same reset-all semantics used by the disable button,
  * the same "release unpublished resources if the overlay closed during
    startup" behaviour,
  * the same status callback contract that feeds the About panel.

The backend is injectable so the whole state machine is testable on a machine
without /dev/uinput (CI containers, and this dev box).
"""
from __future__ import annotations

import errno
import os
import sys
import threading
from typing import Callable, Iterable

from . import xd360 as X

StatusFn = Callable[[str], None]


class PadBackend:
    """Transport interface. Mirrors the slice of HMController GlassPad uses."""

    name = "null"

    def submit(self, buttons: set[str], hat: str, axes: dict[int, float]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class NullBackend(PadBackend):
    """Keeps the last submitted frame. Used for tests and headless runs."""

    name = "null"

    def __init__(self) -> None:
        self.last_buttons: set[str] = set()
        self.last_hat: str = "None"
        self.last_axes: dict[int, float] = {}
        self.submits = 0

    def submit(self, buttons: set[str], hat: str, axes: dict[int, float]) -> None:
        self.last_buttons = set(buttons)
        self.last_hat = hat
        self.last_axes = dict(axes)
        self.submits += 1


class UinputBackend(PadBackend):
    """A real Xbox 360 wired pad created via /dev/uinput."""

    name = "xbox-360-wired"

    DEVICE_NAME = "Microsoft X-Box 360 pad"
    PHYS = "glasspad/virtual0"

    def __init__(self, path: str = "/dev/uinput") -> None:
        self._fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            self._declare()
        except Exception:
            os.close(self._fd)
            raise

    # -- setup ------------------------------------------------------------
    def _set_bit(self, request: int, code: int) -> None:
        import fcntl

        fcntl.ioctl(self._fd, request, code)

    def _declare(self) -> None:
        self._set_bit(X.UI_SET_EVBIT, X.EV_KEY)
        for code in X.BUTTON_CODES.values():
            self._set_bit(X.UI_SET_KEYBIT, code)
        self._set_bit(X.UI_SET_EVBIT, X.EV_ABS)
        for code in X.AXIS_SPECS:
            self._set_bit(X.UI_SET_ABSBIT, code)
        # D-pad is exposed as a hat on ABS_HAT0X/Y exactly as xpad reports it,
        # on top of the four BTN_DPAD_* keys that a 360 pad also emits.
        for code in (X.ABS_HAT0X, X.ABS_HAT0Y):
            self._set_bit(X.UI_SET_ABSBIT, code)
        self._create_device()

    def _create_device(self) -> None:
        import fcntl

        # The legacy write() description is not a fallback, it is the only
        # interface that carries absinfo. Declaring UI_SET_ABSBIT without it
        # does NOT produce a wide axis: input_alloc_absinfo() is kzalloc'd, and
        # evdev hands EVIOCGABS back verbatim, so every axis reads back 0..0.
        # The kernel does not clamp injected values either, so the numbers do
        # still reach handlers -- but SDL/Steam Input normalise by the declared
        # (max - min), and a zero-width axis is a dead stick, not a fast one.
        dev = X.UinputUserDev()
        dev.name = self.DEVICE_NAME.encode()
        dev.id.bustype = X.BUS_USB
        dev.id.vendor = X.USB_VENDOR_ID_MICROSOFT
        dev.id.product = X.USB_PRODUCT_ID_XBOX360_WIRED
        dev.id.version = 0x0110
        dev.ff_effects_max = 0
        for code, spec in X.AXIS_SPECS.items():
            dev.absmax[code], dev.absmin[code] = spec.maximum, spec.minimum
            dev.absfuzz[code], dev.absflat[code] = spec.fuzz, spec.flat
        for code in (X.ABS_HAT0X, X.ABS_HAT0Y):
            dev.absmax[code], dev.absmin[code] = 1, -1
        os.write(self._fd, bytes(dev))

        # Physical path lets udev rules match this device specifically.
        try:
            fcntl.ioctl(self._fd, X.UI_SET_PHYS, self.PHYS.encode())
        except OSError:
            pass  # purely cosmetic; the device works without it

        fcntl.ioctl(self._fd, X.UI_DEV_CREATE)

    # -- input ------------------------------------------------------------
    def _send(self, ev_type: int, code: int, value: int) -> None:
        os.write(self._fd, X.pack_event(ev_type, code, value))

    def submit(self, buttons: set[str], hat: str, axes: dict[int, float]) -> None:
        send = self._send
        # Every button is written every frame, so a release always lands even
        # if a previous write was lost. The four BTN_DPAD_* keys are cleared
        # here and re-asserted from the hat below, as xpad reports both forms.
        for name, code in X.BUTTON_CODES.items():
            send(X.EV_KEY, code, 1 if name in buttons else 0)
        for name in HAT_BUTTONS.get(hat, ()):
            send(X.EV_KEY, X.BUTTON_CODES[name], 1)
        hx, hy = HAT_VECTORS.get(hat, (0, 0))
        send(X.EV_ABS, X.ABS_HAT0X, hx)
        send(X.EV_ABS, X.ABS_HAT0Y, hy)
        for code, spec in X.AXIS_SPECS.items():
            send(X.EV_ABS, code, X.normalise(spec, axes.get(code, 0.5)))
        send(X.EV_SYN, X.SYN_REPORT, 0)

    def close(self) -> None:
        import fcntl

        try:
            fcntl.ioctl(self._fd, X.UI_DEV_DESTROY)
        finally:
            os.close(self._fd)


#: BTN_DPAD_* keys mirroring each hat position (xpad sends both forms).
HAT_BUTTONS: dict[str, tuple[str, ...]] = {
    "N": ("DPAD_UP",),
    "S": ("DPAD_DOWN",),
    "E": ("DPAD_RIGHT",),
    "W": ("DPAD_LEFT",),
    "NE": ("DPAD_UP", "DPAD_RIGHT"),
    "NW": ("DPAD_UP", "DPAD_LEFT"),
    "SE": ("DPAD_DOWN", "DPAD_RIGHT"),
    "SW": ("DPAD_DOWN", "DPAD_LEFT"),
    "None": (),
}

#: ABS_HAT0X / ABS_HAT0Y for each hat position.
HAT_VECTORS: dict[str, tuple[int, int]] = {
    "N": (0, -1),
    "S": (0, 1),
    "E": (1, 0),
    "W": (-1, 0),
    "NE": (1, -1),
    "NW": (-1, -1),
    "SE": (1, 1),
    "SW": (-1, 1),
    "None": (0, 0),
}


def uinput_available(path: str = "/dev/uinput") -> bool:
    return os.access(path, os.W_OK)


#: The state this program must never reach quietly: controls that draw, animate
#: and light up under a finger while every press disappears into a rehearsal
#: dummy. On screen the overlay is indistinguishable from a working one.
def no_uinput_hint() -> str:
    """The warning, naming a command the reader can actually run.

    It used to be a module constant ending in `glasspad --install-uinput`. For
    somebody holding one downloaded AppImage there is no such command -- the file is
    called something like GlassPad-1.1.0-x86_64.AppImage and bash does not search the
    current directory -- so the sentence that appears exactly when something is wrong
    was itself unfollowable, and the reader's next move was `command not found`.
    bundle.cli_hint() resolves it to the AppImage path, the installed `glasspad`, or
    `python3 -m glasspad`, whichever is true on this machine right now.
    """
    from glasspad import bundle

    hint = bundle.cli_hint()
    return "\n".join([
        "GlassPad cannot write /dev/uinput, so there is no gamepad.",
        "",
        "The overlay still appears and the controls still respond -- to you. "
        "Nothing is",
        "sent anywhere, because the device that would carry it belongs to root and "
        "this",
        "account was never given permission to create one. This is not a bug to "
        "work",
        "around; it is one command, once, and it asks for a password:",
        "",
        f"    {hint} --install-uinput",
        "",
        "Then log out and back in if that command says the group was newly added, "
        "and",
        f"confirm with:  {hint} --status",
        "",
    ])



def _warn_no_uinput() -> None:
    # Once per process: this fires from the pad's own thread and a repeating
    # block in a scrollback is a block nobody reads past the first line.
    global _NO_UINPUT_WARNED
    if _NO_UINPUT_WARNED:
        return
    _NO_UINPUT_WARNED = True
    print(no_uinput_hint(), file=sys.stderr, end="")


_NO_UINPUT_WARNED = False


class PadService:
    """Port of GlassPad.Input.PadService.

    Thread-safety matches the original: every mutator holds one lock through
    the synchronous submit, so the axes dict can never be read while changing.
    """

    #: Profile order of the Windows original: prefer Xbox One S, fall back to
    #: Xbox 360 wired. On Linux the uinput device is the 360 pad, so that is
    #: the reported name; the ordering is kept for parity and future profiles.
    PROFILE_NAME = "Xbox 360 Controller (XInput STANDARD GAMEPAD)"

    def __init__(self, backend: PadBackend | None = None) -> None:
        self._lock = threading.Lock()
        self._backend: PadBackend | None = backend
        self._started = False
        self._disposed = False
        #: True when init() had to fall back to a null pad on its own. The
        #: overlay shows this on screen; see no_uinput_hint().
        self.fell_back_to_null = False

        self._buttons: set[str] = set()
        self._hat = "None"
        self._ls_x, self._ls_y = 0.5, 0.5
        self._rs_x, self._rs_y = 0.5, 0.5
        self._lt, self._rt = 0.0, 0.0

    # -- lifecycle --------------------------------------------------------
    def init(self, on_status: StatusFn, *, synchronous: bool = False) -> None:
        """Create the virtual controller. Mirrors PadService.Init."""
        with self._lock:
            if self._started or self._disposed:
                return
            self._started = True

        def run() -> None:
            probe: PadBackend | None = None
            try:
                on_status("Checking / creating virtual controller...")
                # Take the intended backend under the lock. Reading it unlocked
                # raced dispose(), which clears it under that same lock, so a
                # window closed during startup made this see None and fall
                # through to UinputBackend() -- opening a real /dev/uinput pad
                # for an overlay that had already gone away. On a machine where
                # the node is writable that phantom controller outlives the app
                # until dispose() closes a device it never made.
                with self._lock:
                    if self._disposed:
                        raise _Abandoned()
                    intended = self._backend
                probe = intended or (
                    UinputBackend() if uinput_available() else NullBackend()
                )
                if intended is None and isinstance(probe, NullBackend):
                    # `intended is None` is what separates "the user asked for a
                    # rehearsal pad with --null-pad" from "the kernel said no".
                    # The first is fine and silent; the second has to be said
                    # out loud, because nothing else about the app will look wrong.
                    self.fell_back_to_null = True
                    _warn_no_uinput()
                with self._lock:
                    if self._disposed:
                        raise _Abandoned()
                    if self._disposed:
                        raise _Abandoned()
                    self._backend = probe
                    probe = None
                    self._submit_locked()
                on_status(f"\u2713 {self.PROFILE_NAME}")
            except _Abandoned:
                pass
            except OSError as exc:
                if exc.errno == errno.EACCES:
                    on_status(
                        "PadService error: no permission on /dev/uinput. "
                        "Install the GlassPad udev rule, or run once with sudo."
                    )
                elif exc.errno == errno.ENOENT:
                    on_status(
                        "PadService error: /dev/uinput is missing. "
                        "Load the uinput module (see install.sh)."
                    )
                else:
                    on_status(f"PadService error: {exc}")
            except Exception as exc:  # noqa: BLE001 - status surface, as original
                on_status(f"PadService error: {exc}")
            finally:
                # If the overlay closed during startup, release what never
                # made it into self._backend.
                if probe is not None:
                    try:
                        probe.close()
                    except Exception:  # noqa: BLE001
                        pass

        if synchronous:
            run()
        else:
            threading.Thread(target=run, name="glasspad-init", daemon=True).start()

    def dispose(self) -> None:
        with self._lock:
            if self._disposed:
                return
            self._disposed = True
            backend, self._backend = self._backend, None
        if backend is not None:
            try:
                backend.close()
            except Exception:  # noqa: BLE001
                pass

    # -- input ------------------------------------------------------------
    def press_button(self, name: str) -> None:
        with self._lock:
            self._buttons.add(name)
            self._submit_locked()

    def release_button(self, name: str) -> None:
        with self._lock:
            self._buttons.discard(name)
            self._submit_locked()

    def set_dpad(self, hat: str) -> None:
        with self._lock:
            self._hat = hat
            self._submit_locked()

    def set_left_stick(self, x: float, y: float) -> None:
        with self._lock:
            self._ls_x, self._ls_y = to_hm(x), to_hm(y)
            self._submit_locked()

    def set_right_stick(self, x: float, y: float) -> None:
        with self._lock:
            self._rs_x, self._rs_y = to_hm(x), to_hm(y)
            self._submit_locked()

    def set_left_trigger(self, value: float) -> None:
        with self._lock:
            self._lt = float(value)
            self._submit_locked()

    def set_right_trigger(self, value: float) -> None:
        with self._lock:
            self._rt = float(value)
            self._submit_locked()

    def reset(self) -> None:
        with self._lock:
            self._buttons.clear()
            self._hat = "None"
            self._ls_x = self._ls_y = self._rs_x = self._rs_y = 0.5
            self._lt = self._rt = 0.0
            self._submit_locked()

    # -- submit -----------------------------------------------------------
    def _submit_locked(self) -> None:
        # Callers hold _lock through the synchronous submit, exactly as the
        # original's comment requires: no torn axes read.
        if self._disposed or self._backend is None:
            return
        axes = {
            X.ABS_X: self._ls_x,
            X.ABS_Y: self._ls_y,
            X.ABS_RX: self._rs_x,
            X.ABS_RY: self._rs_y,
            X.ABS_Z: self._lt,
            X.ABS_RZ: self._rt,
        }
        self._backend.submit(self._buttons, self._hat, axes)

    # -- introspection for tests -----------------------------------------
    @property
    def backend(self) -> PadBackend | None:
        return self._backend


class _Abandoned(Exception):
    pass


def to_hm(value: float) -> float:
    """Zone space (-1..1) -> HIDMaestro/normalised space (0..1).

    Byte-for-byte the original PadService.ToHM.
    """
    return (float(value) + 1.0) / 2.0
