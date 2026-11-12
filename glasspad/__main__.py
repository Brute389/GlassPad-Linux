"""GlassPad — touchscreen → gamepad overlay for Linux.

Entry point: parse what the launcher passes, report status if asked, otherwise
start the overlay.

There is one backend. The Qt/X11 port that used to live behind `--backend qt` was
removed once the layer-shell backend was the only one shipping, and `bundle.py`
used to carry a list of modules to leave out of the payloads — a list is how you
end up shipping a backend nobody tests. The code is in git history (before the
orphan-code pass); reviving it means adding it back deliberately, not un-hiding it.

Special modes, both of which work with no Wayland session running:

  --status          diagnose the overlay stack and exit (add --json for JSON).
  --install-uinput  grant /dev/uinput access -- one password, once. Run it the
                    first time, or any time --status says the device is not
                    writable. Without that access the controls draw, animate,
                    and reach no game whatsoever, which is the most confusing
                    failure this program can have.
"""
from __future__ import annotations

import ctypes
import os
import sys

# Duplicated from glasspad/__init__.py deliberately: this file is also run
# as `python3 glasspad/__main__.py`, where sys.path holds only the package
# directory, so a top-level `from glasspad import __version__` dies with
# "No module named 'glasspad'". test_version_strings_agree() is what keeps
# the two equal, so drifting is a failed test rather than a wrong --version.
__version__ = "1.1.0"

BACKEND = "layer"

# A launcher or desktop entry written for the Qt backend still names it, and the
# worst reply to that is a traceback or silence.
QT_REMOVED = """glasspad: there is no 'qt' backend -- the layer backend is the only one.

The Qt/X11 port was removed in the orphan-code pass: a second GUI toolkit, a third
of the package bytes, and nothing in a shipped artefact that exercised it. It lived
for compositors without zwlr_layer_shell_v1, and every compositor in normal use has
one -- which is exactly why the layer-shell overlay is the one that ships. It asks
the compositor for a layer instead of trying to out-stack a fullscreen game.

The code is in git history, and the names are listed in glasspad/bundle.py so the
artefact tests can refuse to ship them again by accident.
"""


def take_backend(argv: list[str]) -> list[str]:
    """Strip --backend from argv, and refuse a value that no longer exists.

    The option survives because launch options outlive the thing they configured:
    the .run launcher, the desktop entry and any line you typed last week all pass
    `--backend layer`. Accepting it keeps those working; rejecting anything else
    keeps a typo from quietly starting something you did not ask for.

    GLASSPAD_BACKEND is honoured for the same reason, and gets the same refusal.
    """
    env = os.environ.get("GLASSPAD_BACKEND")
    if env and env != BACKEND:
        sys.stderr.write(QT_REMOVED if env == "qt" else
                         f"glasspad: unknown backend {env!r} in GLASSPAD_BACKEND; "
                         f"only {BACKEND!r} exists\n")
        raise SystemExit(2)
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--backend":
            if i + 1 >= len(argv):
                sys.stderr.write(f"glasspad: --backend needs a value ({BACKEND!r})\n")
                raise SystemExit(2)
            value, i = argv[i + 1], i + 2
        elif arg.startswith("--backend="):
            value, i = arg.split("=", 1)[1], i + 1
        else:
            rest.append(arg)
            i += 1          # without this the loop never advances and ANY argument
            continue        # that is not --backend spins here forever

        if value != BACKEND:
            sys.stderr.write(QT_REMOVED if value == "qt" else
                             f"glasspad: unknown backend {value!r}; only {BACKEND!r} "
                             f"exists\n")
            raise SystemExit(2)
    return rest




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

def report_status() -> int:
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
        gi.require_version("Gdk", "4.0")
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

def main(argv: list[str] | None = None) -> int:
    rest = take_backend(sys.argv[1:] if argv is None else list(argv))
    if "--install-uinput" in rest:
        # Checked before anything that cares about a session: this is how you fix
        # the machine so that the session-dependent code has something to report,
        # and on a fresh install it is the first command a user needs. Letting an
        # unrecognised flag fall through would start a fullscreen transparent
        # overlay in answer to a setup request -- see --version below.
        return install_uinput(rest)
    if rest and rest[0] in ("status", "--status", "-s"):
        return report_status()
    # --version and --help used to belong to the Qt parser. Losing that branch is
    # worse than a wrong string: with nothing matching them they fell through to
    # run_layer() and STARTED THE OVERLAY. A flag that opens a fullscreen
    # transparent surface instead of printing a number is, from a launcher's point
    # of view, a hang -- and it is invisible to whoever typed it, because the
    # surface is see-through. So the flags this file owns are answered here, before
    # anything can map a surface, and test_cli_flags_never_start_the_overlay guards
    # the whole family.
    if rest and rest[0] in ("--version", "-V"):
        print(f"GlassPad {__version__} (Linux)")
        return 0
    if rest and rest[0] in ("--help", "-h"):
        print((__doc__ or "").strip())
        print(f"\nusage: glasspad [--status] [--edit] [--profile NAME] "
              f"[--install-uinput] [--backend {BACKEND}]")
        print("  overlay_layer owns the remaining options; --help there lists them")
        return 0
    from glasspad.overlay_layer import main as run_layer
    code = run_layer(rest)
    if code:
        sys.stderr.write(LAYER_INSTALL_NOTE)
    return code


def install_uinput(argv: list[str] = ()) -> int:
    """Grant /dev/uinput access from inside a bundle, with exactly one prompt.

    This is the one step a bundle cannot sneak past: a virtual gamepad means an
    entry in /etc/udev/rules.d and membership of a group, and both belong to
    root. What a bundle CAN do -- and what until now required a source checkout,
    which nobody who downloaded a single file has -- is carry the rule and the
    installer that writes it, so that one download is a complete install.

    The check afterwards runs as *ourselves*, never as root: root can always
    write /dev/uinput, so a root-side verdict reports "writable" on a machine
    where the person who just typed their password still cannot make a pad.
    """
    import shutil
    import subprocess

    from . import bundle, pad

    installer = bundle.find_installer()
    if not installer:
        print("This build ships no installer, so it cannot grant uinput access.")
        print("Looked for install.sh in:")
        for root in bundle.installer_roots():
            print(f"    {root}")
        print("Every GlassPad .AppImage and .run contains one; if yours does not,")
        print("the download was truncated.")
        return 2

    rule = bundle.installer_rule_src(installer)
    if not rule:
        print(f"Found the installer at {installer}")
        print("...but not the rule it installs, expected at:")
        print(f"    {os.path.join(os.path.dirname(installer), bundle.RULE_REL)}")
        print("Refusing to invent a permission policy this bundle did not ship.")
        return 2

    tool = next((t for t in ("pkexec", "sudo") if shutil.which(t)), None)
    if tool is None:
        print("Neither pkexec nor sudo is available, so nothing under /etc can be")
        print("written. Run the installer as root yourself:")
        print(f"    sh {installer} --uinput")
        return 2

    print("One password is needed. Everything below belongs to root:")
    print("    /etc/modules-load.d/uinput.conf    load the uinput module at boot")
    print("    /etc/udev/rules.d/99-glasspad-...  hand /dev/uinput to group input")
    print("    group `input`                      your account is added to it")
    print()
    print("The exact rule being installed is this file, which you can read first:")
    print(f"    {rule}")
    print()

    # --dry-run is not a courtesy: this command writes to /etc through pkexec,
    # and a mistyped flag should never be able to do that on a first try. It was
    # written after exactly such a mistake, when a probe of the refusal path ran
    # the real installer instead -- the resolver searched past the empty override
    # directory and found a checkout, which is correct behaviour and a live
    # change to /etc.
    cmd = [tool, "sh", installer, "--uinput"]
    dry = "--dry-run" in argv
    if dry:
        cmd.append("--dry-run")
        print("(--dry-run: the installer will report and change nothing)")
        # No privilege elevation at all for a dry run: install.sh --uinput --dry-run
        # is safe unprivileged, and asking for a password to read a plan is absurd.
        cmd.pop(0)

    rc = subprocess.call(cmd)
    if dry:
        return rc
    if rc == 125:
        print(f"\nAuthorisation declined ({tool} exit 125); nothing was changed.")
        return 1
    if rc != 0:
        print(f"\nThe installer failed with exit {rc}; read its output above.")
        print("GlassPad never started, so there is no half-configured state to untangle.")
        return rc

    if pad.uinput_available():
        print("\n/dev/uinput is writable: GlassPad can create a pad now.")
        print("Start it again and the controls will actually reach the game.")
        return 0

    print("\nInstalled -- but /dev/uinput is still not writable *for you*.")
    print("That is expected when this run added the group: a process keeps the")
    print("groups it started with, so membership lands at the NEXT login. Log out")
    print("and back in, then confirm with:")
    print("    glasspad --status")
    return 0


if __name__ == "__main__":
    sys.exit(main())
