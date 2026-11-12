"""GlassPad — touchscreen → gamepad overlay for Linux.

Entry point. Mirrors App.xaml's StartupUri="MainWindow.xaml" plus a few CLI
options a Linux user actually needs (backend choice, diagnostics).
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import os
import signal
import socket
import sys

# Duplicated from glasspad/__init__.py deliberately: this file is also run
# as `python3 glasspad/__main__.py`, where sys.path holds only the package
# directory, so a top-level `from glasspad import __version__` dies with
# "No module named 'glasspad'". test_version_strings_agree() is what keeps
# the two equal, so drifting is a failed test rather than a wrong --version.
__version__ = "1.1.0"


def _force_xcb() -> None:
    """Prefer xcb. Both plugins do click-through; X11 stacks more reliably.

    Pass-through is reachable either way now -- XShape through libX11 on xcb,
    QWindow.setMask -> wl_surface.set_input_region on the native Wayland plugin.
    xcb is still chosen whenever an X server is actually reachable, because KWin
    applies its X11 stacking rules to an always-on-top X client, which stays
    above a fullscreen game far more often than a Wayland surface asking to be
    kept on top does.

    A headless plugin the user chose (QT_QPA_PLATFORM=offscreen for --status or
    CI) is always left alone, and so is a Wayland-only session with no X socket:
    inventing DISPLAY=:0 there would point Qt at a server that does not exist
    and fail the launch outright, where native Wayland works.
    """
    if os.environ.get("QT_QPA_PLATFORM"):
        return
    if os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        return
    number = _x_socket_number()
    if number is not None:
        # Xwayland is running but its socket was not exported to this
        # environment; name it explicitly rather than guess.
        os.environ["DISPLAY"] = f":{number}"
        os.environ["QT_QPA_PLATFORM"] = "xcb"


# The socket directory, in one place so the listing and the probe can be pointed
# at the same temporary directory -- which is what lets a test drive a real
# connect() against sockets it made itself.
#
# It is read from the module at CALL time, never as a default argument. A
# `socket_dir=_X_SOCKET_DIR` default would bake the literal in when the def runs,
# so monkeypatching the constant would change nothing and the tests would be
# probing the developer's machine while believing they were not.
_X_SOCKET_DIR = "/tmp/.X11-unix"

# connect() errors that PROVE nothing is listening. Deliberately short: the
# converse rule, "any failure means dead", is the mistake this port has already
# shipped once in the xpad check, where a test incapable of passing sent users
# hunting for a defect that does not exist.
#
# Measured rather than guessed, and the guesses were wrong: a plain file and a
# directory named like a socket both give ECONNREFUSED here, not ENOTSOCK, so
# ENOTSOCK is not listed -- it is unreachable for an AF_UNIX connect, and naming
# it would imply a case this code does not actually handle.
_DEAD_SOCKET_ERRNOS = frozenset({
    errno.ECONNREFUSED,   # no listener; also what non-sockets report
    errno.ENOENT,         # removed between listdir() and the probe
})


def _socket_is_live(number: int, socket_dir: str | None = None) -> bool:
    """Whether a server is actually accepting on `number`.

    The directory entry cannot answer this. A killed server leaves its socket
    inode behind, looking exactly like a live one; only connect() tells them
    apart. Just connected and hung up -- no X handshake is needed to learn that
    someone is listening and none is attempted, so a running server is not
    disturbed by asking.

    Conservative about anything short of proof of absence. EACCES/EPERM mean "you
    may not ask", not "nobody is home"; a timeout from a wedged listener says
    nothing either. Those keep the pre-probe behaviour of trusting the entry, as
    does any errno not listed above. The asymmetry is deliberate: a wrong "live"
    is only what this function did before it probed at all, while a wrong "dead"
    removes a launch that works today.

    The denial case is not hypothetical and not merely timid, because the path
    this probes is only one of two ways to reach a server. On Linux X11 also
    listens on the ABSTRACT socket "\0/tmp/.X11-unix/XN", which lives in the
    network namespace and is not subject to filesystem permissions at all.
    Measured: with the path chmod 000 the connect() here returns EACCES, and
    with the path removed entirely (ENOENT) an abstract connect to the same
    display still succeeds, so a server can be perfectly reachable by libX11
    while this function is told it may not look. Reading a denial as "dead"
    would therefore veto displays that work -- the same class of mistake as the
    shipped xpad claim, a check that cannot pass being trusted as a verdict.
    """
    if socket_dir is None:
        socket_dir = _X_SOCKET_DIR
    try:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError:
        return True                     # cannot even ask: do not veto a display
    try:
        probe.settimeout(1.0)           # a wedged listener must not stall launch
        probe.connect(f"{socket_dir}/X{number}")
        return True
    except OSError as exc:
        return exc.errno not in _DEAD_SOCKET_ERRNOS
    finally:
        probe.close()


def _x_socket_number(socket_dir: str | None = None) -> int | None:
    """Lowest X display number with a LIVE socket, or None if there is none.

    The socket directory is the only evidence available to a process started
    without DISPLAY, and each candidate is then probed, because the inode
    outlives the server that made it. Listing alone is wrong in both directions:
    a machine whose real server is on :1 has no :0 socket, and a leftover socket
    on :7 is not a server at all.

    Believing the bare filename is the worse error. Pointing Qt at a dead server
    does not fail quietly -- the xcb plugin cannot initialise and Qt abort()s --
    so one leftover inode turns a launch native Wayland would have served into a
    core dump. That is the very failure this function exists to prevent, arriving
    by another route, and it is common on a Wayland desktop, where Xwayland
    sockets go stale every time a session ends.
    """
    if socket_dir is None:
        socket_dir = _X_SOCKET_DIR
    try:
        names = os.listdir(socket_dir)
    except OSError:
        return None
    # Parse the names rather than testing X0..X31. Probing a fixed range misses
    # any server numbered above it, and :99 is exactly where Xvfb and many
    # second-head/VNC setups put one -- a real server then reads as "no X at
    # all" and the launcher silently falls through to native Wayland or fails.
    numbers = []
    for name in names:
        if not name.startswith("X"):
            continue                      # Xauth leftovers, lock files, stray sockets
        digits = name[1:]
        # isascii() as well as isdigit(): int() accepts non-ASCII digits, so
        # "X\u0663" would otherwise become a nonsense DISPLAY of :3.
        if digits.isascii() and digits.isdigit():
            numbers.append(int(digits))
    # Ascending, stopping at the first that answers: "lowest wins" is unchanged,
    # but a stale low-numbered socket can no longer win the choice.
    for number in sorted(numbers):
        if _socket_is_live(number, socket_dir):
            return number
    return None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="glasspad",
        description="Touchscreen → Xbox gamepad overlay (Arch Linux port).",
    )
    parser.add_argument("--null-pad", action="store_true",
                        help="run the overlay without creating a virtual pad "
                             "(useful for layout editing over VNC)")
    parser.add_argument("--status", action="store_true",
                        help="print the detected session, pass-through strategy "
                             "and pad backend, then exit")
    parser.add_argument("--backend", choices=BACKENDS, default=None,
                        help="which overlay implementation to run (default: layer); "
                             "this flag is consumed before the chosen backend parses "
                             "the rest, so the other backend's options are not listed "
                             "here -- see `python3 -m glasspad.overlay_layer --help`")
    parser.add_argument("--version", action="version",
                        version=f"GlassPad {__version__} (Linux)")
    return parser.parse_args(argv)


STACKING_NOTE = """
Stacking, if the overlay is not above the game:
  Plasma (Wayland) System Settings > Window Management > Window Rules,
  New Rule from a currently open window (WM_CLASS "glasspad"/"GlassPad"),
  then set "Keep above windows" -> Force -> Yes.
  Hyprland hyprland.conf:    windowrulev2 = float,windowtitle:^GlassPad$
                             windowrulev2 = pin,windowtitle:^GlassPad$
  Others: any rule matching class GlassPad that forces always-on-top.
"""

WAYLAND_EMPTY_NOTE = """
NOTE: on native Wayland an empty input region cannot be expressed (Qt has no
      empty-but-not-null QRegion), so with every control hidden -- hold to
      disable -- the overlay still covers the game. Nothing stays pressed;
      touches just do not reach the game until the controls come back.
"""


LAYER_INSTALL_NOTE = """
GlassPad's overlay needs libgtk4-layer-shell, and no Python package provides it -- it is
a system library, and the one thing this bundle deliberately does not carry (a copy would
have to match this machine's libwayland *and* GTK 4; when it does not you get a blank
overlay rather than an error):

    Arch / CachyOS   sudo pacman -S libgtk4-layer-shell
    Debian / Ubuntu  sudo apt install libgtk4-layer-shell0
    Fedora           sudo dnf install libgtk4-layer-shell

Installed somewhere unusual? Say so, and --status will use it:
    GLASSPAD_LAYER_SHELL=/path/to/libgtk4-layer-shell.so.0 glasspad --status
"""


def report_status_layer() -> int:
    """The layer backend's questions, in the order they fail.

    The shared --status talks about XShape and pass-through, which this backend neither
    uses nor needs; run on the packaged backend it printed a confident report about a
    different program. And the three things that *do* decide whether the overlay works --
    libgtk4-layer-shell, the compositor's layer-shell global, /dev/uinput -- went
    unmentioned, which is exactly the report you need on a machine you just copied this
    to. Exit status is 0 only when the overlay could actually run.
    """
    from glasspad import bundle

    rc = 0
    wayland = os.environ.get("WAYLAND_DISPLAY")  # noqa: E501 or os.environ.get("WAYLAND_SOCKET")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or "unknown"
    print(f"session         : {desktop}" + (f", socket {wayland}" if wayland else ""))
    if not wayland:
        rc = 1
        print("                  no Wayland socket: the overlay cannot run here "
              "(expected over ssh)")

    lib, why = bundle.layer_shell_path()
    print(f"layer-shell lib : {lib or 'NOT FOUND'}  (via {why})")
    if not lib:
        print(LAYER_INSTALL_NOTE)
        return 1

    # Everything below needs the library to load and a display to ask. Each step is
    # guarded, because --status is what you reach for when things are broken and a
    # traceback from a diagnostic is worse than the bug.
    # Load it the way the app does, before anything opens the display. Measured here:
    # with the typelib loaded normally, is_supported() returns False forever -- the library
    # never binds the compositor's global. So a --status that skipped this step tells you
    # your KWin has no layer-shell, on the exact machine where the overlay works. The
    # diagnostic has to reproduce the runtime, not merely name the libraries.
    try:
        ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
        print(f"preload         : ok ({os.path.basename(lib)})")
    except OSError as exc:
        print(f"preload         : FAILED ({exc})")
        rc = 1

    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gdk, Gtk4LayerShell
    except Exception as exc:                                # noqa: BLE001
        print(f"typelibs        : cannot import -- {type(exc).__name__}: {exc}")
        print("                  needs python-gi, GTK 4 and the Gtk4LayerShell typelib")
        return 1

    display = None
    try:
        display = Gdk.Display.get_default()
    except Exception as exc:                                # noqa: BLE001
        print(f"Gdk display     : lookup raised {type(exc).__name__}: {exc}")
    print(f"Gdk display     : {display.get_name() if display else 'none'}")

    if display is None:
        print("compositor      : not checked (no display to ask)")
    else:
        try:
            supported = bool(Gtk4LayerShell.is_supported())
            print("compositor      : "
                  + ("advertises zwlr_layer_shell_v1" if supported
                     else "does NOT advertise zwlr_layer_shell_v1"))
            if not supported:
                rc = 1
                print("                  KWin, Hyprland, Sway and wlroots have it; stock "
                      "GNOME does not.")
        except Exception as exc:                            # noqa: BLE001
            rc = 1
            print(f"compositor      : check raised {type(exc).__name__}: {exc}")

    uinput_ok, uinput_why = bundle.uinput_ready()
    print(f"/dev/uinput     : {uinput_why if uinput_ok else 'PROBLEM: ' + uinput_why}")
    if not uinput_ok:
        rc = 1

    try:
        from glasspad import layer_profiles as prof
        from glasspad.layer_geom import default_layout_path

        cfg = os.environ.get("GLASSPAD_CONFIG_DIR") or os.path.dirname(default_layout_path())
        names = prof.list_profiles(cfg)
        claim = prof.read_claim(cfg)
        print(f"profiles        : {', '.join(names) if names else 'none yet'}"
              f"  ({os.path.join(cfg, 'profiles')})")
        if claim:
            print(f"claimed         : {claim['profile']} "
                  f"by {claim.get('source', '?')}")
        else:
            print("claimed         : nothing -- the startup layout is used")
    except Exception as exc:                                # noqa: BLE001
        print(f"profiles        : could not read ({type(exc).__name__}: {exc})")

    return rc


def report_status(backend: str = "qt") -> int:
    if backend == "layer":
        return report_status_layer()
    from glasspad import passthrough
    from glasspad.pad import uinput_available

    session = passthrough.detect_session()
    print(f"display server : {session.server}")
    print(f"pass-through   : {session.strategy}")
    if session.detail:
        print(f"detail         : {session.detail}")
    if passthrough._X.shape_version:
        major, minor = passthrough._X.shape_version
        print(f"XShape         : {major}.{minor}")
    if session.server == "wayland":
        print(f"input region   : "
              f"{'QWindow.setMask -> wl_surface' if session.pass_through else 'unavailable'}")
        scale = _declared_scale()
        if scale:
            print(f"scale factor   : {scale}")
    print(f"/dev/uinput    : "
          f"{'writable' if uinput_available() else 'missing or not writable'}")
    # Shaping failures are otherwise invisible: they neither crash nor log, they
    # just quietly stop the game from receiving touches anywhere but a button.
    if passthrough._X.error_count:
        print(f"X11 errors     : {passthrough._X.error_count} swallowed"
              f" (last: {passthrough._X.last_error})")
    if session.server == "wayland":
        # Self-contained on purpose: --status is where a user comes to find out
        # what is wrong, so the remedy has to be printed here rather than left
        # to another document. Pass-through works on native Wayland now; what a
        # compositor may still refuse is staying above a fullscreen game.
        print(STACKING_NOTE)
        if session.pass_through and not passthrough.empty_region_passes():
            print(WAYLAND_EMPTY_NOTE)
    if session.server == "x11" and not session.shape:
        print("\nNOTE: XShape is unavailable on this display, so the overlay\n"
              "      swallows every touch instead of passing the empty area to\n"
              "      the game. A compositor without SHAPE is the usual cause.")
    if not uinput_available():
        print("\nNo usable /dev/uinput. Load the module and add the udev rule:\n"
              "      sudo modprobe uinput\n"
              "      sudo tee /etc/modules-load.d/uinput.conf <<< uinput")
    return 0


def _declared_scale() -> str | None:
    """Report a scale factor the user exported, else None.

    --status runs before QApplication exists, so QScreen cannot be consulted
    here; and on Wayland the compositor, not the environment, decides the scale.
    Only the variables Qt itself would honour are printed, because those are the
    ones a user can actually have set wrong -- QT_SCALE_FACTOR scales the logical
    grid the Wayland input region is built on, so a mismatch with the desktop is
    worth seeing. Printing a made-up "1.00" from a screen that was never opened
    would be worse than printing nothing.
    """
    found = [f"{name}={os.environ[name]}"
             for name in ("QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS",
                          "QT_FONT_DPI")
             if os.environ.get(name)]
    return " ".join(found) if found else None


#: The two overlay implementations.
#:
#   layer -- one gtk4-layer-shell OVERLAY surface per control. This is the only
#            mechanism in KWin that composites above a fullscreen game, which is
#            why it is the default on this machine.
#   qt    -- the original single Qt window with click-through pass-through. Kept
#            because it is the port's X11 path, it renders the same pad, and it is
#            what --status reports pass-through strategy for.
BACKENDS = ("layer", "qt")


def pop_backend(argv: list[str]) -> tuple[str, list[str]]:
    """Take --backend out of argv, and hand back what is left for one parser.

    The two backends each own their own options (--status on the Qt side,
    --edit/--layout/--save-layout on the layer side), so there is no single parser
    that could validate argv. Asking either of them to tolerate the other's flags
    would mean neither could report a genuine typo. So only the option that chooses
    the parser is read here, in both spellings, and everything else travels on
    untouched.

    GLASSPAD_BACKEND does the same job for a launcher that cannot pass arguments.
    """
    backend = os.environ.get("GLASSPAD_BACKEND") or "layer"
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--backend":
            if i + 1 >= len(argv):
                sys.stderr.write("glasspad: --backend needs a value "
                                 f"({', '.join(BACKENDS)})\n")
                raise SystemExit(2)
            backend = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--backend="):
            backend = arg.split("=", 1)[1]
            i += 1
            continue
        rest.append(arg)
        i += 1
    if backend not in BACKENDS:
        sys.stderr.write(f"glasspad: unknown backend {backend!r}; "
                         f"expected one of {', '.join(BACKENDS)}\n")
        raise SystemExit(2)
    return backend, rest


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    backend, rest = pop_backend(argv)

    # diagnostics answer the same questions whichever backend is running, so they
    # are served before the fork rather than duplicated into both sides
    # `glasspad status` as well as `glasspad --status`: the packaged launchers advertise
    # the bare word, and the layer parser takes no positionals, so without this alias the
    # documented command exits 2 ("unrecognized arguments") on the packaged build while
    # working from a checkout.
    if "status" in rest:
        rest = ["--status" if a == "status" else a for a in rest]

    if "--status" in rest:
        return report_status(backend)
    if "--version" in rest:
        print(f"GlassPad {__version__} (Linux)")
        return 0

    if backend == "layer":
        from glasspad import overlay_layer
        return overlay_layer.main(rest)

    args = parse_args(rest)

    _force_xcb()

    from PyQt6.QtCore import QSocketNotifier
    from PyQt6.QtWidgets import QApplication

    # Qt derives WM_CLASS from argv[0], and WM_CLASS is the only handle a
    # compositor window rule has. The overlay does ask for
    # WindowStaysOnTopHint, but on Wayland a fullscreen game can still win that
    # contest, and pinning the overlay above it is a user-written window rule --
    # matched on WM_CLASS -- rather than something a client can demand.
    # Launched as `python -m glasspad`, argv[0] is the path to __main__.py, so
    # the overlay would be filed under "__main__.py" alongside every other
    # Python window and no such rule could be written. Qt only consumes its own
    # options from this list, so user args still work.
    app = QApplication(["glasspad"] + sys.argv[1:])
    app.setApplicationName("GlassPad")
    app.setApplicationDisplayName("GlassPad")
    app.setDesktopFileName("glasspad")

    from glasspad.pad import NullBackend, PadService
    from glasspad.window import MainWindow

    pad = PadService(NullBackend()) if args.null_pad else PadService()
    window = MainWindow(pad=pad)
    window.show()

    # Ctrl-C in a terminal must dispose the pad rather than orphan a virtual
    # device. Touching Qt from a signal handler is unsafe, so the quit travels
    # through a socketpair and a QSocketNotifier delivers it on the GUI thread.
    #
    # The byte must be written by signal.set_wakeup_fd(), not by a Python
    # handler. CPython calls a Python handler only between bytecodes, and
    # app.exec() blocks inside Qt's poll() for as long as nothing repaints --
    # so a sendall() there is deferred until some other event wakes the
    # interpreter, and SIGINT/SIGTERM never reach app.quit(). Measured: the
    # overlay ignored both signals until the next event, which for a click-
    # through overlay the user cannot focus or click means Ctrl-C in the
    # terminal does nothing and only kill -9 stops a full-screen window. The
    # wakeup fd is written synchronously in C inside the signal handler, so
    # poll() returns on the spot; the kept-alive Python handler exists only to
    # hold the default disposition off until the notifier runs.
    read_sock, write_sock = socket.socketpair()
    read_sock.setblocking(False)
    write_sock.setblocking(False)
    notifier = QSocketNotifier(read_sock.fileno(), QSocketNotifier.Type.Read, app)
    notifier.activated.connect(lambda *_: app.quit())

    previous_wakeup = signal.set_wakeup_fd(write_sock.fileno())

    def forward(signum, _frame):
        # Intentionally empty; see above.
        pass

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, forward)

    try:
        return app.exec()
    finally:
        signal.set_wakeup_fd(previous_wakeup)


if __name__ == "__main__":
    # `python glasspad/__main__.py` from inside the package is the obvious thing
    # to try and used to die with "No module named 'glasspad'", which reads like
    # a broken install rather than a wrong command. Absolute imports only need
    # the checkout on sys.path, so add it and say which way is intended instead
    # of leaving a traceback to interpret. WM_CLASS is unaffected either way:
    # QApplication is handed "glasspad" explicitly, not argv[0].
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
        print("note: launch with  python3 -m glasspad  (running the file "
              "directly works too)", file=sys.stderr)
    raise SystemExit(main())
