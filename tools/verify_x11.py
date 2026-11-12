"""End-to-end on a real X server via the xcb plugin: is the input region right?

Run it from anywhere -- the package path is derived from this file, so there is
nothing to configure:

    python3 tools/verify_x11.py

It maps the overlay full-screen on $DISPLAY for a moment and grabs a screenshot
of that screen, so aim it at a server you own rather than mid-work:

    Xvfb :99 -screen 0 1920x1200x24 &   # headless, the usual choice

The layout is designed for 1920x1200 (the Flow Z13), and the pass-through probes
below are coordinates on that canvas. On a smaller screen the window is clamped
and those absolute probes are skipped rather than guessed at; every check that
is still run has to pass.

Also proves the X error handler does not break the happy path -- if it swallowed
something it shouldn't, the region would come back empty.
"""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="glasspad-verify-")
os.environ.setdefault("DISPLAY", ":99")
# Force xcb rather than clearing the variable. Popping it lets Qt pick its own
# default, and on a Plasma/Wayland session with DISPLAY also set that default is
# the wayland plugin -- which connects to the compositor, ignores $DISPLAY, and
# has no X window to shape. The application forces xcb for the same reason
# (see _force_xcb); a checker that quietly tests a different plugin than the app
# ships with is testing the wrong thing. An explicit QT_QPA_PLATFORM still wins
# so the failure mode can be reproduced deliberately.
if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "xcb"

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402

from glasspad import passthrough  # noqa: E402
from glasspad.pad import NullBackend, PadService  # noqa: E402
from glasspad.window import MainWindow  # noqa: E402

DESIGN = (1920, 1200)

app = QApplication([])
print("Qt platform:", app.platformName(), flush=True)
if app.platformName() != "xcb":
    # A bare assert told the user nothing about why, on a machine where the
    # platform is chosen by the compositor rather than by them.
    print(
        f"ERROR: Qt selected '{app.platformName()}', but this check shapes an X\n"
        f"       window and needs 'xcb'.\n"
        f"       DISPLAY is {os.environ.get('DISPLAY', 'unset')!r} "
        f"and WAYLAND_DISPLAY is {os.environ.get('WAYLAND_DISPLAY', 'unset')!r}.\n"
        f"\n"
        f"       Start a headless server and point at it:\n"
        f"           Xvfb :99 -screen 0 1920x1200x24 &\n"
        f"           env DISPLAY=:99 QT_QPA_PLATFORM=xcb "
        f"python3 tools/verify_x11.py\n"
        f"       (needs the xorg-server-xvfb package)",
        file=sys.stderr, flush=True)
    sys.exit(2)

info = passthrough.detect_session()
print("session:", info.server, "shape=", info.shape, "|", info.strategy, flush=True)
print("XShape version:", passthrough._X.shape_version, flush=True)

screen = QGuiApplication.primaryScreen()
avail = screen.geometry() if screen else None
AW, AH = (avail.width(), avail.height()) if avail else (0, 0)
W, H = min(DESIGN[0], AW), min(DESIGN[1], AH)
print(f"display {os.environ['DISPLAY']}  screen {AW}x{AH}  testing at {W}x{H}",
      flush=True)

w = MainWindow(pad=PadService(NullBackend()))
w.resize(W, H)
w.show()
app.processEvents()

blockers = list(w.layout.all_editable) + list(w.hub_buttons.values())
ok = passthrough.apply(w, blockers)
print("apply() ->", ok, " errors:", passthrough._X.error_count,
      "last:", passthrough._X.last_error, flush=True)

disp = passthrough._X.open_display()
rects = passthrough._X.get_input_rectangles(disp, int(w.winId()))
area = sum(rw * rh for _, _, rw, rh in rects)
print(f"SHAPE_INPUT read back: {len(rects)} rects, {area:,} px "
      f"= {100 * area / max(1, W * H):.2f}% of window", flush=True)
assert len(rects) > 0, "handler must not swallow the successful shape request"
assert passthrough._X.error_count == 0, f"unexpected errors: {passthrough._X.last_error}"


def claimed(x, y):
    return any(rx <= x < rx + rw and ry <= y < ry + rh
               for rx, ry, rw, rh in rects)


def centre(el):
    return el.x() + el.width() // 2, el.y() + el.height() // 2


bad = 0
print("\n-- control centres (must CLAIM input) --", flush=True)
targets = [(tag, *centre(el)) for el, tag in w.layout.buttons]
targets.append(("dpad", *centre(w.layout.dpad)))
for index, stick in enumerate(w.layout.sticks):
    targets.append((("LS", "RS")[index % 2], *centre(stick)))
for tag, x, y in targets:
    okc = claimed(x, y)
    bad += not okc
    print(f"  {tag:>4} ({x},{y}) claimed={okc}", flush=True)

print("\n-- empty points (must PASS THROUGH) --", flush=True)
if (W, H) == DESIGN:
    empties = [("screen centre", 960, 600), ("top-left corner", 5, 5),
               ("below hub", 960, 320), ("left gap", 400, 400)]
    for label, x, y in empties:
        okc = not claimed(x, y)
        bad += not okc
        print(f"  {label:>18} ({x},{y}) passthrough={okc}", flush=True)
else:
    print("  SKIPPED: these are coordinates on the 1920x1200 canvas and the "
          f"screen here is {AW}x{AH}.")

a = next(el for el, t in w.layout.buttons if t == "A")
corner = (a.x() + 1, a.y() + 1)
okc = not claimed(*corner)
bad += not okc
print(f"\n  A bounding-box corner {corner} passthrough={okc} (circle parity)",
      flush=True)

shot = os.path.join(os.environ["XDG_CONFIG_HOME"], "glasspad-xcb.png")
QGuiApplication.primaryScreen().grabWindow(int(w.winId())).save(shot)
print(f"screenshot: {shot}", flush=True)

# Dispose before exiting, the way the app's quit button does. sys.exit() here
# left the window alive, so closeEvent never ran and pad.dispose() /
# router.shutdown() were skipped: the event filter stayed installed and the
# null backend was never closed. Harmless for NullBackend, but this tool exists
# to exercise the same lifecycle the release ships, and a teardown bug is only
# visible from a tool that performs the teardown.
# A marker before the verdict: if the process dies during close() the
# transcript ends here, so a teardown crash reads as a teardown crash instead of
# a check that silently never printed a result.
print("\n-- teardown (closeEvent must dispose the pad) --", flush=True)
w.close()
app.processEvents()
print(f"\nRESULT: {'ALL PASS' if bad == 0 else str(bad) + ' FAILED'}", flush=True)
sys.exit(1 if bad else 0)
