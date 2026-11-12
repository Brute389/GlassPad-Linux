"""Input pass-through for the transparent overlay.

WPF gets this for free: on a window with AllowsTransparency=True, a pixel with
alpha == 0 is not hit-tested, so touches land on the game underneath unless
they hit an actual button.

The Linux equivalent is an *input region*: input is accepted only inside it,
everything outside falls through to the window below. Set that region to the
union of the on-screen controls and the empty area passes touches to the game --
functionally the same rule WPF applies per alpha channel.

Two backends build the same region:

  * xcb / X11 -- the XShape extension's SHAPE_INPUT, through libX11. Coordinates
    are physical pixels, because the X window is a pixel grid.
  * Wayland  -- QWindow.setMask(), which the Wayland plugin forwards to
    wl_surface.set_input_region. Coordinates are logical device-independent
    pixels; they are *not* multiplied by devicePixelRatio (checked against the
    Qt 6.11 Wayland plugin: QWaylandWindow::setMask -> updateInputRegion ->
    QWaylandDisplay::createRegion walks QRegion rects straight into
    wl_compositor.create_region with no scaling step).

Both paths are pixel-exact, so the app no longer depends on which session it
starts in. Forcing xcb remains the default anyway: an X11 client is driven by
KWin's X11 stacking rules, which keep an override-redirect/always-on-top window
above a fullscreen game far more reliably than a Wayland client can ask to be
kept above a fullscreen surface. What neither session gives for free is that
stacking guarantee, so --status prints the compositor window rule to write when
the overlay does not appear above the game.

One asymmetry between the backends is worth knowing: under XShape an empty
region means "accept nothing", which is what makes a disabled overlay pass every
touch through. Qt cannot express that on Wayland, because an empty QRegion *is*
the null QRegion (QRegion(0,0,0,0).isNull() is True) and the Wayland plugin maps
a null mask to "no input region set", i.e. the whole surface takes input. It
does not stop the disable button working: release_all() has already lifted every
gamepad control and reset the pad, and the controls are hidden, so there is
nothing left for a touch to press -- the difference is that the touches under the
user's fingers go to the overlay rather than to the game. See
empty_region_passes().

If neither backend is available the overlay degrades to a full-window input
catcher and says so in --status output rather than silently swallowing touches.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import math
import os
from dataclasses import dataclass

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QRegion


#: Sentinel meaning "look up the running application". Pass app=None to skip
#: that lookup and judge from the environment alone.
_NO_APP = object()


def is_wayland_client(app=_NO_APP) -> bool:
    """True when this process is talking Wayland natively.

    Checked against the live Qt platform first, because that is what decides
    whether a window has an X resource to shape at all. An explicit
    QT_QPA_PLATFORM=xcb wins even with WAYLAND_DISPLAY exported -- the usual
    arrangement under Xwayland, where the correct answer is "use XShape".

    Passing app=None ignores any running application and reports what the
    environment alone implies -- what a process would do before Qt is up, and
    what a test needs, because a suite holding a live offscreen QApplication
    could otherwise never reach the branch below.
    """
    if app is _NO_APP:
        try:
            from PyQt6.QtGui import QGuiApplication
        except Exception:  # noqa: BLE001 - no Qt: fall back to the environment
            app = None
        else:
            app = QGuiApplication.instance()
    if app is not None:
        return app.platformName() == "wayland"
    platform = os.environ.get("QT_QPA_PLATFORM", "")
    if platform:
        return platform.split(":", 1)[0] == "wayland"
    return bool(os.environ.get("WAYLAND_DISPLAY")) \
        and not bool(os.environ.get("DISPLAY"))

# Constants copied from <X11/extensions/shapeconst.h>. Values matter: guessing
# ShapeInput as 1 selects ShapeClip, which would reshape the window's *visual*
# clip instead of its input region.
ShapeBounding = 0
ShapeClip = 1
ShapeInput = 2
ShapeSet = 0

# <X11/Xutil.h> ordering hints for XShapeCombineRectangles.
Unsorted = 0
YSorted = 1
XSorted = 2
YXSorted = 3


class XRectangle(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_short),
        ("y", ctypes.c_short),
        ("width", ctypes.c_ushort),
        ("height", ctypes.c_ushort),
    ]


class XErrorEvent(ctypes.Structure):
    """<X11/Xlib.h> XErrorEvent. Field order matters: this struct is handed to
    us by libX11, so a wrong layout reads garbage (or faults) inside a callback
    that is supposed to be our safety net."""

    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("resourceid", ctypes.c_ulong),
        ("serial", ctypes.c_ulong),
        ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


# int (*XErrorHandler)(Display*, XErrorEvent*) -- see the note on
# _on_error(): the arity of this callback is NOT portable, which is why both
# arguments are untyped void pointers here.
_X_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

# X11 error codes we can name in diagnostics without a round trip to the
# server; <X11/X.h>. Note these never reach 0, which is X_Error's own value:
# that property is what _as_error_event() uses to reject a bad pointer.
_X_ERRORS = {
    0: "Success",
    1: "BadRequest",
    2: "BadValue",
    3: "BadWindow",
    4: "BadPixmap",
    5: "BadAtom",
    6: "BadCursor",
    7: "BadFont",
    8: "BadDraw",
    9: "BadColor",
    10: "BadGC",
    11: "BadIDChoice",
    12: "BadName",
    13: "BadLength",
    14: "BadImplementation",
}



class Xlib:
    """Minimal lazy ctypes binding over libX11 + libXext.

    Every prototype below was taken from the installed X11 headers rather than
    from memory. This matters: with ctypes a wrong argtype is not a type error
    but a crash or silent corruption. Two mistakes made here were fatal --
    passing a shape kind to XShapeQueryExtension (which takes no such argument,
    so the library wrote through address 1), and calling XDestroyRegion with
    (display, region) when it takes only (region), which freed the Display*.
    """

    def __init__(self) -> None:
        self._x11 = None
        self._xext = None
        self._xfree = None
        self._display = None
        self._loaded: bool | None = None
        self.shape_version: tuple[int, int] | None = None
        # Errors from our own connection, i.e. the last shape update that the
        # server rejected. Surfaced in --status instead of silently swallowed.
        self.last_error: str | None = None
        # Total errors swallowed on connections we own, for --status. Kept
        # separate from the per-connection tally below, which is cleared when a
        # connection is dropped and so cannot answer "did that request fail?"
        # after the failure handler has already cleaned up.
        self.error_count = 0
        # Per-connection error tallies keyed by Display*. A single global count
        # would let a stray error on Qt's connection fail our next shape update,
        # and this overlay depends on every shape update succeeding.
        self._errors: dict = {}
        # Last error code per Display*, so a request can tell a stale resource
        # from a broken connection. Connections we opened, for the same reason.
        self._error_codes: dict = {}
        self._owned: set = set()
        # libX11 keeps only a raw function pointer, so the ctypes callback must
        # outlive install() or Python frees the trampoline and Xlib calls into
        # freed executable memory.
        self._error_handler = None

    # -- loading ----------------------------------------------------------
    def load(self) -> bool:
        if self._loaded is not None:
            return self._loaded
        try:
            x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            xext = ctypes.CDLL(ctypes.util.find_library("Xext") or "libXext.so.6")
        except OSError:
            self._loaded = False
            return False

        VP = ctypes.c_void_p
        I = ctypes.c_int
        PIN = ctypes.POINTER(ctypes.c_int)

        # void XOpenDisplay(const char*); XCloseDisplay(Display*)
        x11.XOpenDisplay.restype = VP
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XCloseDisplay.restype = I
        x11.XCloseDisplay.argtypes = [VP]
        # int XSync(Display*, Bool)
        x11.XSync.restype = I
        x11.XSync.argtypes = [VP, I]
        # void XFree(void*)
        try:
            x11.XFree.restype = None
            x11.XFree.argtypes = [VP]
            self._xfree = x11.XFree
        except AttributeError:
            self._xfree = None

        # int XSetErrorHandler(XErrorHandler)
        # Never leave the default handler in place: Xlib's default handler
        # calls exit() on any *unexpected* error. Shaping a window that was
        # just unmapped, recreated by the window manager, or orphaned by an X
        # server reset yields BadWindow, and "overlay disappears mid-game"
        # becomes "overlay exits and takes the session down with it".
        try:
            x11.XSetErrorHandler.restype = VP
            x11.XSetErrorHandler.argtypes = [VP]
        except AttributeError:
            return True  # no handler hook: proceed with libX11 defaults
        handler = _X_ERROR_HANDLER(self._on_error)
        x11.XSetErrorHandler(ctypes.cast(handler, VP))
        # Keep the trampoline and its prototype alive; libX11 stores a bare
        # pointer, so a collected callback means it calls freed memory.
        self._error_handler = handler

        # Bool XShapeQueryExtension(Display*, int* event_base, int* error_base)
        #   -- no shape-kind parameter.
        xext.XShapeQueryExtension.restype = I
        xext.XShapeQueryExtension.argtypes = [VP, PIN, PIN]
        # Status XShapeQueryVersion(Display*, int* major, int* minor)
        xext.XShapeQueryVersion.restype = I
        xext.XShapeQueryVersion.argtypes = [VP, PIN, PIN]
        # void XShapeCombineRectangles(Display*, Window dest, int dest_kind,
        #      int x_off, int y_off, XRectangle*, int n_rects, int op, int ordering)
        # Preferred over XShapeCombineRegion: it needs no Region object, so
        # there is no client-side allocation to get the lifetime wrong on.
        xext.XShapeCombineRectangles.restype = None
        xext.XShapeCombineRectangles.argtypes = [
            VP, ctypes.c_ulong, I, I, I, ctypes.POINTER(XRectangle), I, I, I]
        # XRectangle *XShapeGetRectangles(Display*, Window, int kind,
        #      int* count, int* ordering)
        xext.XShapeGetRectangles.restype = ctypes.POINTER(XRectangle)
        xext.XShapeGetRectangles.argtypes = [VP, ctypes.c_ulong, I, PIN, PIN]

        self._x11, self._xext = x11, xext
        self._loaded = True
        return True

    # -- error handling ---------------------------------------------------
    @staticmethod
    def _qt_is_xcb() -> bool:
        """True when Qt is on xcb, or when Qt is not involved at all.

        Returns False only for a *known* non-xcb platform, which is the case
        where a window id cannot name a real X resource.
        """
        try:
            from PyQt6.QtGui import QGuiApplication
        except Exception:  # noqa: BLE001 - no Qt/PyQt: trust $DISPLAY
            return True
        app = QGuiApplication.instance()
        if app is None:
            return True
        return app.platformName() == "xcb"

    def _errors_for(self, display) -> int:
        return self._errors.get(display or 0, 0)

    def _as_error_event(self, candidate):
        """Return candidate as an XErrorEvent belonging to a connection we own,
        else None.

        Both arguments arrive untyped because the callback's arity is not
        portable, so each must be proven to be *our* event before its contents
        are trusted. Xlib stamps every event with the connection it came from,
        which is a far stronger test than checking that some bytes look like an
        error: the Display* passed as the other argument is ordinary heap memory
        whose leading fields can easily resemble a type/error pair, and reading
        it as an event yields plausible nonsense rather than an obvious failure.

        Note the reads are safe whichever convention is in force: the arguments
        are always a real Display* and a real event, and the leftover-register
        case only arises on a one-argument libX11, where the first argument is
        the event and validates immediately, so the second is never touched.
        """
        if not candidate or not self._owned:
            return None
        try:
            event = ctypes.cast(candidate, ctypes.POINTER(XErrorEvent)).contents
            if event.type != 0 or not event.error_code:
                return None
            if event.display not in self._owned:
                return None
            return event
        except Exception:  # noqa: BLE001 - never fault inside a handler
            return None

    def _on_error(self, arg0, arg1) -> int:
        """Swallow a failed request on our connection; return 0 so Xlib stops.

        WARNING, this type not in Xlib spec: Debian's libX11 declares
        XErrorHandler as int(Display*, XErrorEvent*) while upstream declares
        int(XErrorEvent*). A callback typed for one of them silently misreads
        the other -- decoding a Display* as an event yields heap garbage, and on
        a layout that does not happen to be readable, a segfault inside the very
        handler installed to stop libX11 from calling exit(). So take both
        arguments as void pointers and work out which one is the event.
        """
        # Attribute the error to our connection before acting on it. Anything
        # unattributable is left alone: it almost certainly belongs to Qt, and
        # resetting our connection or failing a shape update because Qt tripped
        # over one of its own requests would be a much worse bug than a
        # diagnostic we never saw.
        try:
            event = self._as_error_event(arg0) or self._as_error_event(arg1)
            if event is not None:
                self.error_count += 1
                self._errors[event.display] = self._errors_for(event.display) + 1
                self._error_codes[event.display] = event.error_code
                name = _X_ERRORS.get(event.error_code,
                                     f"error#{event.error_code}")
                self.last_error = (
                    f"{name} (request {event.request_code}, "
                    f"resource 0x{event.resourceid:x}, serial {event.serial})"
                )
        except Exception:  # noqa: BLE001 - a handler must never raise
            pass
        # Never chain. The handler we displaced may be libX11's default one,
        # and the default one calls exit(); handing it an error swallows
        # ourselves. Qt tolerates a dropped diagnostic far better than it
        # tolerates the process vanishing mid-game.
        return 0

    def connection_healthy(self, display) -> bool:
        """False only when the last error means the connection itself is lost.

        BadWindow and friends (0x3 window, 0x4 pixmap, 0x9 colour map, 0xa GC)
        prove the server answered us, so the link is fine and only the resource
        is stale -- the usual outcome when the window manager recreates or the
        user unmaps a window. Tearing down a working connection for that costs a
        reconnect on every refresh. A code outside that set, in particular 0xe
        BadImplementation or anything unrecognised, is treated as unhealthy.
        """
        return self._error_codes.get(display, 0) in (3, 4, 9, 10)

    def reset(self, display=None) -> None:
        """Drop a failed connection so the next attempt starts from a clean
        Display* rather than a dead or stale one. Defaults to the cached one."""
        if display is None:
            display, self._display = self._display, None
        self.close_display(display)

    def close_display(self, display) -> None:
        """Close a connection obtained from open_display().

        Idempotent, because a failed shape update drops the connection itself
        and the caller will almost certainly close the same handle afterwards.
        XCloseDisplay twice is a use-after-free, so the ownership set doubles as
        the "still open" marker.
        """
        if not display or display not in self._owned:
            return
        self._owned.discard(display)
        self._errors.pop(display, None)
        self._error_codes.pop(display, None)
        if self._display == display:
            self._display = None
        if self._loaded:
            try:
                self._x11.XCloseDisplay(display)
            except Exception:  # noqa: BLE001
                pass

    # -- operations -------------------------------------------------------
    def open_display(self, display_name=None):
        """Open (and cache) a connection to the X server.

        Pass display_name to target a specific server, e.g. when the overlay
        must follow a particular monitor's X DISPLAY. Returns a handle to pass
        back to close_display().
        """
        if not self.load():
            return None
        if display_name:
            raw = display_name.encode() if isinstance(
                display_name, str) else display_name
            display = self._x11.XOpenDisplay(raw)
            if display:
                self._owned.add(display)
            return display or None
        if self._display is None:
            # Offscreen/minimal Qt has no X connection at all, and winId()
            # there is a fake small integer. Handing that to a real libX11 is
            # what turned a unit test into process exit 1.
            #
            # QGuiApplication lives in QtGui, not QtCore -- and this used to
            # import from QtCore inside a bare except, so the guard silently
            # never ran. It is deliberately narrow now: an absent Qt is the only
            # case that falls through to trusting $DISPLAY.
            if not self._qt_is_xcb():
                return None
            self._display = self._x11.XOpenDisplay(None)
            if self._display:
                self._owned.add(self._display)
        return self._display or None

    def has_shape(self, display) -> bool:
        """True when the server offers SHAPE; records its version for --status."""
        if not self.load():
            return False
        event_base, error_base = ctypes.c_int(-1), ctypes.c_int(-1)
        try:
            if not self._xext.XShapeQueryExtension(
                    display, ctypes.byref(event_base), ctypes.byref(error_base)):
                return False
            major, minor = ctypes.c_int(-1), ctypes.c_int(-1)
            if self._xext.XShapeQueryVersion(
                    display, ctypes.byref(major), ctypes.byref(minor)):
                self.shape_version = (major.value, minor.value)
            return True
        except Exception:  # noqa: BLE001
            return False

    def set_input_rectangles(self, display, window, rects) -> bool:
        """Replace the window's SHAPE_INPUT region with `rects`.

        An empty list is meaningful: it leaves an empty input region, so the
        whole window passes touches through -- the WPF meaning of "every pixel
        has alpha 0".
        """
        if not self.load():
            return False
        prepared = (XRectangle * len(rects))()
        for index, (x, y, w, h) in enumerate(rects):
            prepared[index] = XRectangle(_i16(x), _i16(y), _u16(w), _u16(h))
        before = self._errors_for(display)
        try:
            self._xext.XShapeCombineRectangles(
                display, ctypes.c_ulong(window), ShapeInput,
                0, 0, prepared, len(rects), ShapeSet, Unsorted)
            # XSync flushes and drains the reply queue, so any BadWindow for the
            # request above has already run through _on_error by the time it
            # returns -- that is what makes shaping reportable rather than
            # fire-and-forget.
            self._x11.XSync(display, 0)
        except Exception:  # noqa: BLE001
            self.reset()
            return False
        if self._errors_for(display) != before:
            # The server rejected this window (recreated by the WM, or already
            # destroyed). Report failure so the caller re-resolves winId() and
            # retries, but keep the connection when it demonstrably still works.
            if not self.connection_healthy(display):
                self.reset(display if display != self._display else None)
            return False
        return True

    def get_input_rectangles(self, display, window):
        """Read SHAPE_INPUT back from the server, for verification."""
        if not self.load():
            return []
        count, ordering = ctypes.c_int(0), ctypes.c_int(0)
        before = self._errors_for(display)
        ptr = self._xext.XShapeGetRectangles(
            display, ctypes.c_ulong(window), ShapeInput,
            ctypes.byref(count), ctypes.byref(ordering))
        # This is a round trip, so errors land before it returns; a rejected
        # window would otherwise hand back a null pointer that reads as "the
        # region is simply empty".
        if self._errors_for(display) != before:
            if not self.connection_healthy(display):
                self.reset(display if display != self._display else None)
            return []
        if not ptr:
            return []
        try:
            return [(ptr[i].x, ptr[i].y, ptr[i].width, ptr[i].height)
                    for i in range(count.value)]
        finally:
            if self._xfree is not None:
                self._xfree(ctypes.cast(ptr, ctypes.c_void_p))


def _i16(value: int) -> int:
    return max(-32768, min(32767, int(value)))


def _u16(value: int) -> int:
    return max(0, min(65535, int(value)))


_X = Xlib()


@dataclass(frozen=True)
class SessionInfo:
    server: str  # "x11" | "wayland" | "offscreen"
    shape: bool
    detail: str
    # True only when this process holds a live X connection under Xwayland, so
    # the input region has to be cut with XShape and cannot be cut with
    # QWindow.setMask -- the xcb plugin does not route that call to the
    # compositor. Without it, a reported server of "wayland" means the Wayland
    # plugin owns the surface and setMask is the API in use.
    #
    # The distinction is not academic: "wayland" is reached both by a native
    # client with no $DISPLAY at all and by an X client whose XShape probe
    # failed, and those have opposite answers on pass-through. Collapsing them is
    # what printed "pass-through: True | XWayland client without XShape".
    via_x11: bool = False

    @property
    def strategy(self) -> str:
        # Must describe what this port actually does on that session, not an
        # escape hatch it does not have. There is no per-control-window mode:
        # the overlay is one surface everywhere. What differs is which API can
        # cut the input region out of it.
        if self.server == "x11" and self.shape:
            return "XShape input region (pixel-exact, WPF parity)"
        if self.server == "wayland" and self.via_x11:
            # Shaped over X, and XShape is not there to shape it with. Promising
            # a wl_surface region here would describe an API this process is not
            # using, and contradict pass_through in the same breath.
            return "full-window overlay; XShape unavailable under XWayland, " \
                   "touches are not passed through to the game"
        if self.server == "wayland":
            if wayland_input_region_available():
                return "wl_surface input region via QWindow.setMask " \
                       "(pixel-exact, WPF parity)"
            return "full-window overlay; this Qt build exposes no input-region " \
                   "API on Wayland, so touches are not passed through to the game"
        if self.server == "x11":
            return "full-window overlay; XShape unavailable, touches are not " \
                   "passed through to the game"
        return "no display server"

    @property
    def pass_through(self) -> bool:
        """Whether touches outside the controls reach the game on this session.

        Must be derived from the same evidence as `detail`, or --status prints a
        verdict and a reason that disagree: "pass-through: True | XWayland client
        without XShape" sent users to debug pass-through on a session the detail
        had just called incapable of it.
        """
        if self.server == "x11":
            return self.shape
        if self.server == "wayland":
            if self.via_x11:
                # Connected to X under Xwayland but found no XShape: neither API
                # can cut the region, whatever a standalone capability check on
                # QWindow.setMask says about a plugin that is not loaded.
                return self.shape
            return wayland_input_region_available()
        return False


def detect_session() -> SessionInfo:
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    x11 = bool(os.environ.get("DISPLAY"))
    platform = os.environ.get("QT_QPA_PLATFORM", "")
    # The platform plugin is checked first and wins: Qt forced offscreen has no
    # native window to shape even when the session does have a server. It must
    # not be reported as a missing DISPLAY though -- that sends the user off to
    # export a variable that is already set, while the real culprit is the one
    # they exported to keep Qt headless.
    if platform.startswith(("offscreen", "minimal")):
        detail = f"Qt platform is {platform!r}, so no window can be shaped"
        if x11 or wayland:
            detail += " despite a session being present"
        return SessionInfo("offscreen", False, detail)
    if not wayland and not x11:
        return SessionInfo("offscreen", False, "no DISPLAY and no WAYLAND_DISPLAY")
    if wayland and not x11:
        # xcb is what the app forces, so reaching here means the user unset
        # QT_QPA_PLATFORM on a compositor that offered no X socket, or forced the
        # wayland plugin. Both are supported now; name the mechanism in use.
        if wayland_input_region_available():
            return SessionInfo("wayland", False,
                               "native Wayland session; QWindow.setMask gives "
                               "the surface an input region")
        return SessionInfo("wayland", False,
                           "native Wayland session without QWindow.setMask")
    if wayland and x11:
        # Both set: we are an X11 client under XWayland. Xwayland translates a
        # shaped X window into the wl_surface input region, so shaping still
        # gives real pass-through -- probe for it instead of assuming.
        display = _X.open_display()
        if display is not None and _X.has_shape(display):
            return SessionInfo("x11", True,
                              "XWayland client, XShape translated by Xwayland "
                              "into the surface input region", via_x11=True)
        if display is None:
            # The probe could not run, which is not the same finding as "ran and
            # found no XShape". Either $DISPLAY names a server that is not there
            # -- the usual case on a Wayland desktop whose launcher inherited a
            # stale DISPLAY -- or Qt is on a plugin that may not be handed X
            # connections, which is what Xlib._qt_is_xcb exists to refuse.
            # Either way this process has no X window to shape, so the wayland
            # plugin is what owns the surface and setMask decides pass-through.
            # Saying "without XShape" here blamed a missing extension for a
            # situation the user can only fix by exporting DISPLAY correctly,
            # and reported pass-through True while the detail said it was
            # unavailable.
            why = (f"Qt platform is {platform!r}, so $DISPLAY was not probed"
                   if platform and not platform.startswith("xcb")
                   else "no X server could be reached at $DISPLAY")
            if wayland_input_region_available():
                return SessionInfo(
                    "wayland", False,
                    f"{why}; the Wayland plugin owns this surface and "
                    "QWindow.setMask gives it an input region")
            return SessionInfo(
                "wayland", False,
                f"{why}; the Wayland plugin owns this surface but this Qt "
                "build exposes no QWindow.setMask")
        # Connected, and the server has no SHAPE: this is still an X client
        # under Xwayland... and Qt's xcb plugin will not route setMask to the
        # Wayland compositor either, so pass-through really is unavailable.
        return SessionInfo("wayland", False, "XWayland client without XShape",
                           via_x11=True)
    display = _X.open_display()
    if display is None:
        return SessionInfo("x11", False, "cannot open the X display",
                           via_x11=True)
    if _X.has_shape(display):
        return SessionInfo("x11", True, "XShape extension present",
                           via_x11=True)
    return SessionInfo("x11", False, "XShape extension missing", via_x11=True)


def wayland_input_region_available() -> bool:
    """True when QWindow can hand an input region to the compositor.

    QWindow.setMask() is the public door to QWaylandWindow::setMask, which is
    what turns a QRegion into wl_surface.set_input_region. Reported as a
    capability rather than assumed so --status can say *why* an old Qt build
    would fall back to a full-window overlay.
    """
    try:
        from PyQt6.QtGui import QWindow
    except Exception:  # noqa: BLE001
        return False
    return hasattr(QWindow, "setMask")


def empty_region_passes(app=_NO_APP) -> bool:
    """Whether "no controls on screen" lets touches reach the game.

    xcb: yes -- XShapeCombineRectangles with zero rectangles is a genuine empty
    SHAPE_INPUT region, so nothing under the overlay is swallowed.

    Wayland: no. Qt has no empty-but-not-null QRegion (QRegion(0,0,0,0).isNull()
    is True), and the plugin maps a null mask to set_input_region(NULL), which
    the protocol defines as the whole surface. So a disabled overlay and "all
    controls faded to zero opacity" leave the overlay taking input over its full
    area. That is a cosmetic difference, not a stuck-input one: release_all() and
    pad.reset() have already lifted everything by then. It is stated here rather
    than hidden because a user testing "disable the overlay, touch the game"
    on Wayland would otherwise conclude the button was broken.
    """
    return not is_wayland_client(app)


def set_input_rectangles_wayland(window, rects) -> bool:
    """Apply the input region through QWindow.setMask (native Wayland).

    `rects` are logical window coordinates: the Wayland plugin forwards them to
    wl_compositor.create_region without scaling, so they must not be multiplied
    by devicePixelRatio here (unlike the XShape path).
    """
    try:
        handle = window.windowHandle()
    except Exception:  # noqa: BLE001
        return False
    if handle is None or not wayland_input_region_available():
        return False
    region = QRegion()
    try:
        # setRects() rather than folding rectangles together with united(): the
        # region is one rectangle per scanline run, so a union loop would be
        # quadratic on ~600 rectangles every time the layout moves. QRegion
        # rejects an empty-but-not-null value, so `rects` being empty lands here
        # as a null region -- see empty_region_passes() for what that means.
        region.setRects([QRect(x, y, w, h) for x, y, w, h in rects])
        handle.setMask(region)
    except Exception:  # noqa: BLE001
        return False
    return True


def set_input_rectangles(window, rects) -> bool:
    """rects: iterable of (x, y, w, h) in window coordinates."""
    # Resolve the native id first, and refuse anything that is not a realised
    # top-level. winId() on a non-xcb plugin returns a placeholder integer, and
    # Qt also hands back ids for windows it has not created yet; sending a shape
    # request for either is a BadWindow, not a no-op.
    try:
        if not window.isWindow() or window.windowHandle() is None:
            return False
        native = int(window.winId())
    except Exception:  # noqa: BLE001
        return False
    if native <= 0:
        return False
    display = _X.open_display()
    if display is None:
        return False
    cleaned = [
        (int(x), int(y), int(w), int(h))
        for x, y, w, h in rects
        if int(w) > 0 and int(h) > 0
    ]
    # An empty Set operation leaves an empty region, i.e. the window becomes
    # fully input-transparent -- the WPF meaning of "every pixel has alpha 0".
    return _X.set_input_rectangles(display, native, cleaned)


def scale_rects(window, rects) -> list[tuple[int, int, int, int]]:
    """Logical window rectangles -> physical pixels, for the XShape request.

    QWidget geometry is device-independent, but an X window is a pixel grid and
    SHAPE_INPUT is expressed in that grid. At a fractional scale factor (the
    Flow Z13 panel with Plasma at 125%, say) rounding each rectangle's far edge
    down would shave a pixel off every control, and the bottom pixel row of a
    button would silently stop taking touches. Ceiling the far edges keeps the
    region a superset of what is drawn: swallowing one extra pixel of dead space
    beats dead-zoning a control.

    Wayland needs no such step -- see set_input_rectangles_wayland().
    """
    try:
        ratio = float(window.windowHandle().devicePixelRatio())
    except Exception:  # noqa: BLE001 - no handle yet: logical == physical
        ratio = 1.0
    if ratio <= 0:
        ratio = 1.0
    if ratio == 1.0:
        return [(int(x), int(y), int(w), int(h)) for x, y, w, h in rects]
    return [
        (int(math.floor(x * ratio)), int(math.floor(y * ratio)),
         int(math.ceil((x + w) * ratio)) - int(math.floor(x * ratio)),
         int(math.ceil((y + h) * ratio)) - int(math.floor(y * ratio)))
        for x, y, w, h in rects
    ]


def apply(window, blockers=()) -> bool:
    """Restrict `window`'s input to the visible on-screen control rectangles.

    `blockers` are widgets and/or (x, y, w, h) rectangles in logical window
    coordinates; the conversion each backend needs happens here, so callers
    never have to know which session they are on. Where no backend can shape the
    window the caller keeps a plain full-window overlay, and --status reports the
    strategy in force so the difference is visible rather than a dead overlay.
    """
    rects = scanline_region(blockers, window.width(), window.height())
    if is_wayland_client():
        return set_input_rectangles_wayland(window, rects)
    if not _X.load():
        return False
    return set_input_rectangles(window, scale_rects(window, rects))


def block_whole_window(window) -> bool:
    """Let the overlay eat every touch — used while the layout editor is open."""
    return apply(window, [(0, 0, window.width(), window.height())])


def _shapes(items):
    """Normalise blockers to (kind, x, y, w, h) in window coordinates.

    An item is either a QWidget, honouring visibility and opacity, or a plain
    (x, y, w, h) tuple for callers that just want a rectangle.

    Port of "which pixels are opaque": an element hidden by the disable button, a
    collapsed panel, or one faded to alpha 0 contributes nothing, so it stops
    swallowing touches -- exactly WPF's alpha-0 behaviour.
    """
    from .widgets import shape_hit  # noqa: F401 - keeps the predicate import close

    shapes = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, tuple):
            x, y, w, h = (int(v) for v in item)
            if w > 0 and h > 0:
                shapes.append((None, x, y, w, h))
            continue
        if not item.isVisible():
            continue
        effect = item.graphicsEffect()
        if effect is not None and hasattr(effect, "opacity") and effect.opacity() <= 0.0:
            continue
        if item.width() <= 0 or item.height() <= 0:
            continue
        origin = item.mapTo(window_top(item), item.rect().topLeft())
        shapes.append((item, int(origin.x()), int(origin.y()),
                       item.width(), item.height()))
    return shapes


def row_span(widget, y_local: float) -> tuple[float, float] | None:
    """Analytic x-extent of a widget's drawn shape at one scanline.

    Mirrors widgets.shape_hit() in closed form, so the XShape region and the
    router's hit test describe the same geometry without touching a pixel.
    Returning the exact extent keeps the region neither larger (which would eat
    touches WPF passes through) nor smaller (which would dead-zone a button).
    """
    if widget is None:
        return None  # handled by the tuple branch in scanline_region
    w, h = float(widget.width()), float(widget.height())
    if w <= 0 or h <= 0 or not 0 <= y_local < h:
        return None
    kind = getattr(widget, "shape_kind", "rect")
    if kind == "rect":
        return 0.0, w
    cy = h / 2.0
    dy = y_local - cy
    if kind == "circle":
        ry, rx = h / 2.0, w / 2.0
        if abs(dy) >= ry:
            return None
        half = rx * math.sqrt(1.0 - (dy / ry) ** 2)
        return rx - half, rx + half
    if kind == "pill":
        r = min(w, h) / 2.0
        if abs(dy) >= r:
            return None
        half = math.sqrt(max(0.0, r * r - dy * dy))
        # Left cap, the full-height middle span, right cap: one contiguous run.
        return r - half, w - r + half
    return 0.0, w


def scanline_region(widgets, width: int, height: int) -> list[tuple[int, int, int, int]]:
    """Union of the widgets' drawn shapes, as one rectangle per scanline run.

    XShape accepts only rectangles, but WPF hit-tests rendered geometry: a touch
    just outside a round button falls through to the game. Bounding boxes would
    leave those corner pixels swallowing touches with nothing to press, so the
    region is built per row from the same shape maths the hit test uses. Rows
    with an identical merged span are then coaxed into single taller rectangles
    to keep the rectangle count small.
    """
    items = _shapes(widgets)
    if not items:
        return []

    top = max(0, min(y for _, _, y, _, _ in items))
    bottom = min(height, max(y + h for _, _, y, _, h in items))

    rects: list[tuple[int, int, int, int]] = []
    previous: list[tuple[int, int]] | None = None
    run_y = top
    for y in range(top, bottom):
        spans = []
        for widget, ox, oy, ow, oh in items:
            if widget is None:
                span = (0, ow) if 0 <= y - oy < oh else None
            else:
                span = row_span(widget, y - oy + 0.5)
            if span is None:
                continue
            x0 = max(0, int(math.floor(ox + span[0])))
            x1 = min(width, int(math.ceil(ox + span[1])))
            if x1 > x0:
                spans.append((x0, x1))
        merged = _merge(spans)
        if merged == previous:
            continue
        if previous is not None:
            rects.extend((s, run_y, e - s, y - run_y) for s, e in previous)
        previous, run_y = merged, y
    if previous:
        rects.extend((s, run_y, e - s, bottom - run_y) for s, e in previous)
    return rects


def _merge(spans):
    if not spans:
        return []
    spans = sorted(spans)
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def window_top(widget):
    top = widget
    while top.parentWidget() is not None:
        top = top.parentWidget()
    return top
