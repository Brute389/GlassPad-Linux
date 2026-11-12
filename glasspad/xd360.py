"""Xbox 360 wired controller constants (Linux input subsystem ABI).

GlassPad on Windows drives a virtual Xbox pad through the HIDMaestro UMDF2
driver. HIDMaestro is Windows-only (it rides mshidumdf / xinputhid / WGI and
ships no Linux build), so on Linux the equivalent device is created with the
kernel's uinput. These constants are the ABI of that device and are taken from
<uapi/linux/input-event-codes.h> and <uapi/linux/uinput.h>.

The axis layout matches what the in-kernel ``xpad`` driver exposes for a real
wired Xbox 360 pad, which is also what SDL3 / Steam Input / Proton expect:

    ABS_X   left stick X     ABS_Y   left stick Y
    ABS_RX  right stick X    ABS_RY  right stick Y
    ABS_Z   left trigger     ABS_RZ  right trigger
    ABS_HAT0X/Y              d-pad hat, -1..1

That is xpad's own split (drivers/input/joystick/xpad.c): ``xpad_abs[]`` is
ABS_X/Y + ABS_RX/RY for the sticks, ``xpad_abs_triggers[]`` is ABS_Z/RZ, and
``xpad_abs_pad[]`` is ABS_HAT0X/Y. An earlier revision of this module had the
sticks and the triggers swapped (right stick on ABS_Z/RZ, triggers on ABS_RX/RY)
and the hat on 0x39/0x3a, which are ABS_MT_TRACKING_ID and ABS_MT_PRESSURE --
multitouch axes, not a hat.

WHY THE NUMBERS ARE WRITTEN AS LITERALS WITH A CHECK: a hand-typed list of
adjacent codes slips by one, and nothing in a fake-backend test can notice,
because the test asserts the same constant the code declares. The probe on real
hardware could not notice either: it validates the device against these same
declarations, so a wrong constant passes twice. ``test_codes_match_kernel_headers``
therefore pins every value against python-evdev's table, which is generated from
uapi/linux/input-event-codes.h.

Values are normalised 0..1 by the caller (same convention HIDMaestro uses),
where 0.5 is stick centre and 1.0 is a fully pulled trigger.
"""
from __future__ import annotations

import ctypes
import struct
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Event types (input-event-codes.h)
# ---------------------------------------------------------------------------
EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

SYN_REPORT = 0x00

# ---------------------------------------------------------------------------
# Absolute axis codes
# ---------------------------------------------------------------------------
ABS_X = 0x00
ABS_Y = 0x01
ABS_Z = 0x02
ABS_RX = 0x03
ABS_RY = 0x04
ABS_RZ = 0x05

ABS_HAT0X = 0x10
ABS_HAT0Y = 0x11

ABS_CNT = 64  # input-event-codes.h: ABS_MAX(0x3f) - ABS_MIN(0) + 1

# ---------------------------------------------------------------------------
# Key codes used by the Xbox 360 pad
# ---------------------------------------------------------------------------
# 0x100 is BTN_0 and 0x120 is BTN_TRIGGER; neither is the 360 Guide button.
# xpad reports the Guide/X button as BTN_MODE (xpad360_btn[] in xpad.c), which
# is also what SDL and Steam Input look for. Both correct values are defined so
# the old, wrong mapping cannot be reintroduced by reusing a name.
BTN_0 = 0x100
BTN_TRIGGER = 0x120

BTN_A = 0x130
BTN_B = 0x131
BTN_X = 0x133
BTN_Y = 0x134

BTN_TL = 0x136  # Left bumper  (LB)
BTN_TR = 0x137  # Right bumper (RB)
BTN_SELECT = 0x13A  # Back / View
BTN_START = 0x13B
BTN_MODE = 0x13C  # the big X / Guide button
BTN_THUMBL = 0x13D  # L3
BTN_THUMBR = 0x13E  # R3

BTN_DPAD_UP = 0x220
BTN_DPAD_DOWN = 0x221
BTN_DPAD_LEFT = 0x222
BTN_DPAD_RIGHT = 0x223

# ---------------------------------------------------------------------------
# uinput ABI (uinput.h)
# ---------------------------------------------------------------------------
UINPUT_VERSION = 5
UINPUT_MAX_NAME_SIZE = 80
UINPUT_IOCTL_BASE = ord('U')

# USB bus id + Microsoft's vendor id, so the device reads as a Microsoft pad.
BUS_USB = 0x03
USB_VENDOR_ID_MICROSOFT = 0x045E
USB_PRODUCT_ID_XBOX360_WIRED = 0x028E


class InputId(ctypes.Structure):
    _fields_ = [
        ("bustype", ctypes.c_uint16),
        ("vendor", ctypes.c_uint16),
        ("product", ctypes.c_uint16),
        ("version", ctypes.c_uint16),
    ]


class InputAbsinfo(ctypes.Structure):
    _fields_ = [
        ("value", ctypes.c_int32),
        ("minimum", ctypes.c_int32),
        ("maximum", ctypes.c_int32),
        ("fuzz", ctypes.c_int32),
        ("flat", ctypes.c_int32),
        ("resolution", ctypes.c_int32),
    ]


class UinputSetup(ctypes.Structure):
    """``struct uinput_setup``: id + name + ff_effects_max.

    Note there are no axis fields here. Absolute-axis ranges can only be
    declared through the legacy uinput_user_dev write(), which is why
    UinputBackend writes that struct rather than relying on UI_DEV_SETUP.
    """

    _fields_ = [
        ("id", InputId),
        ("name", ctypes.c_char * UINPUT_MAX_NAME_SIZE),
        ("ff_effects_max", ctypes.c_uint32),
    ]


class UinputUserDev(ctypes.Structure):
    """Legacy ``write()`` device description; the only form carrying absinfo.

    Layout pinned by the kernel: name[80] + input_id + ff_effects_max + four
    parallel ABS_CNT arrays.
    """

    _fields_ = [
        ("name", ctypes.c_char * UINPUT_MAX_NAME_SIZE),
        ("id", InputId),
        ("ff_effects_max", ctypes.c_uint32),
        ("absmax", ctypes.c_int32 * ABS_CNT),
        ("absmin", ctypes.c_int32 * ABS_CNT),
        ("absfuzz", ctypes.c_int32 * ABS_CNT),
        ("absflat", ctypes.c_int32 * ABS_CNT),
    ]


def _IO(nr: int) -> int:
    return (UINPUT_IOCTL_BASE << 8) | nr


def _IOW(size: int, nr: int) -> int:
    return (1 << 30) | (size << 16) | (UINPUT_IOCTL_BASE << 8) | nr


UI_DEV_CREATE = _IO(1)
UI_DEV_DESTROY = _IO(2)
UI_DEV_SETUP = _IOW(ctypes.sizeof(UinputSetup), 3)
UI_SET_EVBIT = _IOW(4, 100)
UI_SET_KEYBIT = _IOW(4, 101)
UI_SET_ABSBIT = _IOW(4, 103)
UI_SET_PHYS = _IOW(ctypes.sizeof(ctypes.c_char_p), 108)


@dataclass(frozen=True)
class AxisSpec:
    """Range for one absolute axis of the virtual pad."""

    minimum: int
    maximum: int
    fuzz: int = 0
    flat: int = 0

    @property
    def span(self) -> int:
        return self.maximum - self.minimum


# DELIBERATE DEVIATION from a real pad, and the only one left here.
#
# xpad_set_up_abs() in drivers/input/joystick/xpad.c gives a wired 360's sticks
# (-32768, 32767) with fuzz=16, flat=128, and its triggers (0, 255) with 0/0.
# The sticks keep a narrower range and no dead zone on purpose:
#
#   * the source is a touchscreen, so there is no analog drift for a flat to
#     paper over, and a 128-unit dead zone on a +/-320 axis would eat ~40% of
#     the travel a finger can actually cover;
#   * readers normalise by the declared range (EVIOCGABS), so SDL/Steam Input
#     still see full travel; a game that hardcodes 32768-unit maths instead of
#     normalising would read a stick that tops out around 1% of full scale.
#
# If a game does behave as if the sticks barely move, that is this line, and the
# fix is STICK = AxisSpec(-32768, 32767) -- the rest of the code derives its
# maths from the spec rather than assuming a width. The triggers do match xpad
# exactly (0..255), since those are absolute-press values rather than a range a
# dead zone could damage.
STICK = AxisSpec(-320, 320)
TRIGGER = AxisSpec(0, 255)

#: Axis order the profile exposes: left stick, right stick, then triggers.
AXIS_SPECS: dict[int, AxisSpec] = {
    ABS_X: STICK,
    ABS_Y: STICK,
    ABS_RX: STICK,
    ABS_RY: STICK,
    ABS_Z: TRIGGER,
    ABS_RZ: TRIGGER,
}

#: Buttons the pad supports, in the order the original HMButton set covers.
BUTTON_CODES: dict[str, int] = {
    "A": BTN_A,
    "B": BTN_B,
    "X": BTN_X,
    "Y": BTN_Y,
    "LB": BTN_TL,
    "RB": BTN_TR,
    "L3": BTN_THUMBL,
    "R3": BTN_THUMBR,
    "BACK": BTN_SELECT,
    "START": BTN_START,
    "GUIDE": BTN_MODE,
    "DPAD_UP": BTN_DPAD_UP,
    "DPAD_DOWN": BTN_DPAD_DOWN,
    "DPAD_LEFT": BTN_DPAD_LEFT,
    "DPAD_RIGHT": BTN_DPAD_RIGHT,
}

# input_event struct: struct timeval (two 64-bit longs on this ABI) followed by
# u16 type + u16 code + s32 = 24 bytes.
#
# 'q' not 'l': with the '=' prefix struct uses *standard* sizes, where 'l' is
# 4 bytes, which would silently produce a 16-byte event the kernel rejects as
# EINVAL on every write.
_INPUT_EVENT_FMT = "=qqHHi"
INPUT_EVENT_SIZE = struct.calcsize(_INPUT_EVENT_FMT)


def pack_event(ev_type: int, code: int, value: int) -> bytes:
    """Pack one input_event. tv_sec/tv_usec are filled in by the kernel."""
    return struct.pack(_INPUT_EVENT_FMT, 0, 0, ev_type, code, value)


def normalise(spec: AxisSpec, value: float) -> int:
    """Map a 0..1 normalised value onto the axis' integer range.

    NaN falls back to the axis' resting value, mirroring HIDMaestro's rule
    that an unwritten stick rests at its centre.
    """
    if value != value:  # NaN
        mid = (spec.minimum + spec.maximum) / 2.0
        if spec.minimum < 0 <= spec.maximum:
            return 0
        return int(round(mid))
    raw = spec.minimum + value * spec.span
    if raw < spec.minimum:
        return spec.minimum
    if raw > spec.maximum:
        return spec.maximum
    return int(round(raw))
