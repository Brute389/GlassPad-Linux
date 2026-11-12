"""End-to-end on a real Wayland session: is the surface input region right?

Run it from the desktop session you want to test, from anywhere -- the package
path is derived from this file, so there is nothing to configure:

    QT_QPA_PLATFORM=wayland python3 tools/verify_wayland.py

Unlike verify_x11.py this cannot be run against Xvfb: the point is to exercise
the wayland plugin, which needs a compositor. Run it on the machine you are
deploying to. It maps the overlay full-screen for a moment, so close anything
you would rather not have covered.

WHAT THIS CAN PROVE, and what it cannot
---------------------------------------
Qt hands the region to the compositor asynchronously, and Wayland offers no way
to read a surface's input region back, so a client-side tool can only check the
half it controls:

  * the wayland plugin is in use and Qt offers QWindow.setMask,
  * apply() reports success and puts the *expected* region on the surface,
    verified against the geometry the overlay itself computes,
  * every control centre is inside that region and every gap outside it, so the
    region is neither smaller than the controls nor larger than what is drawn.

The compositor half -- "a touch on a gap reaches the game" -- is checked by hand.
Run this tool, then, while the overlay is up for a second, tap a gap and a
button; or simply launch the app and click the desktop through it. If the tool
says ALL PASS but touches still do not reach the game, the compositor is not
honouring input regions, which is a compositor problem rather than a shaping one.

Stacking is likewise outside this check: if the overlay does not appear above a
fullscreen game, write the window rule printed by `python3 -m glasspad --status`.
"""
import os
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="glasspad-verify-wl-")
# Set rather than infer: this tool exists to test the native Wayland plugin, and
# the application forces xcb by default. An explicit QT_QPA_PLATFORM still wins.
os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

# Preflight, before Qt is imported. If the compositor cannot be reached the
# Wayland plugin aborts the process -- "dumped core", with the reason buried in
# qt.qpa.plugin lines -- which reads like a crash in GlassPad rather than the
# absence of a compositor. Say what is missing first, and exit 2.
#
# Qt resolves the compositor as $XDG_RUNTIME_DIR/$WAYLAND_DISPLAY, so that exact
# path is the only real test: both variables can look plausible and still name
# nothing, and a directory that exists with no socket in it aborts just the same.
runtime = os.environ.get("XDG_RUNTIME_DIR", "")
wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
socket_path = os.path.join(runtime, wayland_display) if runtime \
    and wayland_display else ""
if not socket_path or not os.path.exists(socket_path):
    print("ERROR: no Wayland compositor at "
          f"{socket_path or '$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY'}.\n"
          "\n"
          "       Qt would abort before this tool could check anything, so\n"
          "       nothing below ran. Both variables must name a live socket:\n"
          "           ls /run/user/$(id -u)/   # look for wayland-N\n"
          "           export XDG_RUNTIME_DIR=/run/user/$(id -u)\n"
          "           export WAYLAND_DISPLAY=wayland-0\n"
          "\n"
          "       Over SSH, forward them from the desktop session. This tool\n"
          "       cannot test a session that does not exist; the X11 path is\n"
          "       covered headlessly by tools/verify_x11.py.",
          file=sys.stderr, flush=True)
    sys.exit(2)

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from glasspad import passthrough  # noqa: E402
from glasspad.pad import NullBackend, PadService  # noqa: E402
from glasspad.window import MainWindow  # noqa: E402

app = QApplication(["glasspad"])
print("Qt platform:", app.platformName(), flush=True)
if app.platformName() != "wayland":
    print(
        f"ERROR: Qt selected '{app.platformName()}', but this check exercises\n"
        f"       the native Wayland input region and needs 'wayland'.\n"
        f"       WAYLAND_DISPLAY is "
        f"{os.environ.get('WAYLAND_DISPLAY', 'unset')!r}.\n"
        f"\n"
        f"       Run it from a Wayland session without forcing xcb:\n"
        f"           env -u DISPLAY QT_QPA_PLATFORM=wayland \\\n"
        f"               python3 tools/verify_wayland.py\n"
        f"       For the X11/XWayland path use tools/verify_x11.py instead.",
        file=sys.stderr, flush=True)
    sys.exit(2)

if not passthrough.wayland_input_region_available():
    print("ERROR: this Qt build has no QWindow.setMask, so no input region can\n"
          "       be set on Wayland. GlassPad falls back to a full-window\n"
          "       overlay here; --status reports that as the strategy.",
          file=sys.stderr, flush=True)
    sys.exit(2)

info = passthrough.detect_session()
print("session:", info.server, "|", info.strategy, flush=True)
print("pass-through:", info.pass_through, "|", info.detail, flush=True)

W, H = MainWindow._screen_size()
print(f"screen {W}x{H}  scale "
      f"{app.primaryScreen().devicePixelRatio() if app.primaryScreen() else '?'}",
      flush=True)

w = MainWindow(pad=PadService(NullBackend()))
w.resize(W, H)
w.show()
app.processEvents()

blockers = list(w.layout.all_editable) + list(w.hub_buttons.values())
expected = passthrough.scanline_region(blockers, W, H)
ok = passthrough.apply(w, blockers)
print("apply() ->", ok, " computed rects:", len(expected), flush=True)
assert ok, "QWindow.setMask reported failure"

handle = w.windowHandle()
mask = handle.mask()
got = mask.rectCount()
print(f"region on the surface: {got} rects  null={mask.isNull()}  "
      f"computed {len(expected)}", flush=True)
assert not mask.isNull(), "a region with rectangles must not be null"
assert got > 0, "apply() claimed success but left the region empty"
if got != len(expected):
    # Qt is free to merge adjacent rectangles; the point checks below run
    # against the region Qt holds, so a merge cannot make them vacuous.
    print(f"  note: Qt merged rectangles ({len(expected)} -> {got})")

expected_area = sum(rw * rh for _, _, rw, rh in expected)
print(f"  computed area: {expected_area:,} px "
      f"= {100 * expected_area / max(1, W * H):.2f}% of window", flush=True)

# PyQt6 exposes no way to enumerate a QRegion (no rects(), not iterable), so
# membership goes through QRegion.contains(QPoint) -- which is the better test
# anyway: it asks the very region that is about to become the surface's input
# region, rather than a copy of it parsed back here.
def claimed(x, y):
    return mask.contains(QPoint(x, y))


def centre(el):
    return el.x() + el.width() // 2, el.y() + el.height() // 2


bad = 0
print("\n-- control centres (must CLAIM input) --", flush=True)
targets = [(tag, *centre(el)) for el, tag in w.layout.buttons]
if w.layout.dpad is not None:
    targets.append(("dpad", *centre(w.layout.dpad)))
for index, stick in enumerate(w.layout.sticks):
    targets.append((("LS", "RS")[index % 2], *centre(stick)))
for tag, x, y in targets:
    okc = claimed(x, y)
    bad += not okc
    print(f"  {tag:>4} ({x},{y}) claimed={okc}", flush=True)

print("\n-- empty points (must PASS THROUGH) --", flush=True)
empties = [("screen centre", W // 2, H // 2), ("top-left corner", 5, 5)]
if (W, H) == (1920, 1200):
    empties += [("below hub", 960, 320), ("left gap", 400, 400)]
for label, x, y in empties:
    okc = not claimed(x, y)
    bad += not okc
    print(f"  {label:>18} ({x},{y}) passthrough={okc}", flush=True)

a = next(el for el, t in w.layout.buttons if t == "A")
corner = (a.x() + 1, a.y() + 1)
okc = not claimed(*corner)
bad += not okc
print(f"\n  A bounding-box corner {corner} passthrough={okc} (circle parity)",
      flush=True)

# The documented Wayland limitation: Qt has no empty-but-not-null QRegion, so
# hiding every control cannot produce an empty input region. Assert the real
# behaviour rather than the wished-for one, so a future Qt that fixes it shows up
# here as a change worth reading about instead of a silent surprise.
for el in w.layout.all_editable:
    el.setVisible(False)
for btn in w.hub_buttons.values():
    btn.setVisible(False)
passthrough.apply(w, list(w.layout.all_editable) + list(w.hub_buttons.values()))
empty_mask = handle.mask()
print(f"\n-- all controls hidden --\n  region null={empty_mask.isNull()} "
      f"rects={empty_mask.rectCount()} "
      f"(empty_region_passes={passthrough.empty_region_passes()})", flush=True)
if empty_mask.isNull():
    print("  as documented: the surface takes input over its whole area while\n"
          "  the overlay is disabled. Nothing stays pressed; touches just do\n"
          "  not reach the game until the controls return.", flush=True)
else:
    print("  this Qt gives an empty region, so a disabled overlay lets touches\n"
          "  through to the game exactly as xcb does.", flush=True)

# Same teardown as the shipping app: sys.exit() with the window still mapped
# skips closeEvent, so pad.dispose() and router.shutdown() never run. On a real
# compositor that also leaves a fullscreen click-through surface up for as long
# as the interpreter takes to unwind -- which is exactly when a teardown fault
# would show up as a core dump under this tool's "ALL PASS".
print("\n-- teardown (closeEvent must dispose the pad) --", flush=True)
w.close()
app.processEvents()
print(f"\nRESULT: {'ALL PASS' if bad == 0 else str(bad) + ' FAILED'}", flush=True)
print("Remember this only covers the client half: confirm by hand that a touch\n"
      "on a gap reaches the game, and see --status for the stacking rule.",
      flush=True)
sys.exit(1 if bad else 0)
