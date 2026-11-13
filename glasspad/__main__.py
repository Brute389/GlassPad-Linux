"""GlassPad — touchscreen → gamepad overlay for Linux.

Entry point: parse what the launcher passes, report status if asked, otherwise
start the overlay.

There is one backend. The Qt/X11 port that used to live behind `--backend qt` was
removed once the layer-shell backend was the only one shipping, and `bundle.py`
used to carry a list of modules to leave out of the payloads — a list is how you
end up shipping a backend nobody tests. The code is in git history (before the
orphan-code pass); reviving it means adding it back deliberately, not un-hiding it.

Special modes -- none of them need a Wayland session running:

  --status          diagnose the overlay stack and exit (add --json for JSON).
  --install         everything a first run needs, in one command (alias --setup):
                    checks libgtk4-layer-shell, grants /dev/uinput (one password),
                    then adds a menu entry and a `glasspad` command. --dry-run
                    rehearses it without elevating or writing anything; --force lets
                    it replace an existing `glasspad` on PATH.
  --install-uinput  grant /dev/uinput access -- one password, once. Run it the
                    first time, or any time --status says the device is not
                    writable. Without that access the controls draw, animate,
                    and reach no game whatsoever, which is the most confusing
                    failure this program can have.
  --uninstall-uinput  remove the udev rule and modules-load file this program
                    installed. Does NOT touch your layout, your desktop entry,
                    or the input group -- those are yours to decide about.
  --install-desktop   add a menu entry pointing at this AppImage (or checkout),
                    plus the icon, in the user's own directories. No root.
                    --uninstall-desktop takes it away again.
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
    an old desktop entry, a shell history line and a saved terminal command all pass
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




# LAYER_INSTALL_NOTE used to be a hard-coded list of package names, and they were
# all guesses. On Arch and CachyOS the package is gtk4-layer-shell; the note said
# `libgtk4-layer-shell`, which matches no package in the repositories at all -- so
# the first instruction a virgin user copy-pasted was one that cannot work.
# bundle.dependency_hint() reads /etc/os-release instead, and labels which rows
# have actually been verified.

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
        print(bundle.dependency_hint())
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

    # A report ends with the conclusion. Seven facts and no next step leaves the
    # reading to whoever is already worried about their touchscreen, and this is the
    # command they reach for precisely when something is wrong.
    try:
        from glasspad.desktop_entry import DESKTOP_NAME, data_home
        desktop_installed = os.path.exists(
            os.path.join(data_home(), "applications", DESKTOP_NAME))
    except Exception:                                       # noqa: BLE001
        desktop_installed = True        # never advise an install on a lookup failure
    hint = bundle.cli_hint()
    if not uinput_ok:
        nxt = hint + " --install     (grants /dev/uinput, then adds the menu entry)"
    elif not desktop_installed:
        nxt = hint + " --install     (adds the menu entry and the `glasspad` command)"
    else:
        nxt = "nothing -- GlassPad is ready. Start it from the menu, or:  " + hint
    print()
    print(f"NEXT:           {nxt}")

    return rc

def main(argv: list[str] | None = None) -> int:
    rest = take_backend(sys.argv[1:] if argv is None else list(argv))
    if "--install" in rest or "--setup" in rest:
        # First, and ahead of every session-dependent check: this is the command a
        # person runs when the machine has never seen GlassPad, and it must work over
        # ssh, from a display manager that has not started, and from a checkout.
        return install_everything(rest)
    if "--install-uinput" in rest:
        # Checked before anything that cares about a session: this is how you fix
        # the machine so that the session-dependent code has something to report,
        # and on a fresh install it is the first command a user needs. Letting an
        # unrecognised flag fall through would start a fullscreen transparent
        # overlay in answer to a setup request -- see --version below.
        return install_uinput(rest)
    if "--uninstall-uinput" in rest:
        # Narrower than install.sh --uninstall on purpose; see install_uinput().
        return uninstall_uinput(rest)
    if "--install-desktop" in rest:
        # Also before the session check: a menu entry has nothing to do with a
        # running compositor, and this is the command that makes a downloaded
        # AppImage behave like an installed application.
        from .desktop_entry import install_desktop
        return install_desktop(dry="--dry-run" in rest)
    if "--uninstall-desktop" in rest:
        from .desktop_entry import uninstall_desktop
        return uninstall_desktop()
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
              f"[--install] [--install-uinput] [--install-desktop] "
              f"[--backend {BACKEND}]")
        print("  overlay_layer owns the remaining options; --help there lists them")
        return 0
    from glasspad.overlay_layer import main as run_layer
    code = run_layer(rest)
    if code:
        sys.stderr.write(bundle.dependency_hint())
    return code


def uninstall_uinput(argv: list[str] = ()) -> int:
    """Take back the udev rule and the modules-load file, and report what that did.

    Narrow on purpose. `install.sh --uninstall` also clears both install prefixes,
    the autostart entry and `~/.config/GlassPad/layout.json` -- and someone removing
    a device rule from a bundle has not asked for their button positions to be
    deleted with it.

    The verdict is the part worth writing. On a machine with KDE Connect or Steam
    installed, `/dev/uinput` is still writable after the rule is gone, because both
    ship `TAG+="uaccess"` rules for it. Reporting "removed" there is a claim the
    user can disprove simply by opening the overlay, so the answer is measured as
    the account rather than as root, and a still-writable node is explained instead
    of hidden.
    """
    import shutil
    import subprocess

    from . import bundle, pad

    installer = bundle.find_installer()
    if not installer:
        print("This build ships no installer, so it cannot undo what one did.")
        print("Looked for install.sh in:")
        for root in bundle.installer_roots():
            print(f"    {root}")
        print("If you installed the rule by hand, remove it by hand:")
        print("    sudo rm /etc/udev/rules.d/99-glasspad-uinput.rules")
        print("    sudo rm /etc/modules-load.d/uinput.conf")
        print("    sudo udevadm control --reload-rules && sudo udevadm trigger")
        return 2

    tool = next((t for t in ("pkexec", "sudo") if shutil.which(t)), None)
    if tool is None:
        print(f"Neither pkexec nor sudo is available. Run as root:")
        print(f"    sh {installer} --uninstall-uinput")
        return 2

    dry = "--dry-run" in argv
    if dry:
        print("(--dry-run: the installer will report and change nothing)")
    with bundle.staged_installer(installer) as staged:
        if not staged:
            print("the installer could not be copied somewhere root can read it")
            return 2
        if staged != installer:
            print(bundle.STAGED_NOTE)
        cmd = (["sh"] if dry else [tool, "sh"]) + [staged, "--uninstall-uinput"]
        if dry:
            cmd.append("--dry-run")
        sys.stdout.flush()                          # see the note in install_uinput
        sys.stderr.flush()
        rc = subprocess.call(cmd)
    if rc == 125:
        print(f"\nAuthorisation declined ({tool} exit 125); nothing was changed.")
        return 1
    if rc != 0 or dry:
        return rc

    writable, _moved = _uinput_state_settling()
    if writable:
        print("\n/dev/uinput is still writable by your account. That is not the rule")
        print("we just removed -- it is gone. A distro rule is most likely granting")
        print("it (KDE Connect and Steam both do), which means GlassPad needs no")
        print("rule of its own here and will keep working:")
        print("    udevadm info /dev/uinput | grep uaccess")
    else:
        print("\n/dev/uinput is no longer writable for your account. GlassPad will say")
        print("NO PAD and name the fix instead of drawing controls that go nowhere.")
    print("Untouched: your layout, your desktop entry, the input group.")
    return 0


def _input_group_in_database() -> bool:
    """Does the system DATABASE say this account is in group `input`?

    Not os.getgroups(): this process carries the groups it logged in with, which is
    precisely the stale answer. `id -nG <name>` asks the useful question -- what
    would a NEW process get -- and needs no privileges.
    """
    import pwd
    import subprocess

    name = os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name
    try:
        out = subprocess.run(["id", "-nG", name], capture_output=True, text=True,
                             timeout=5)
    except (OSError, subprocess.SubprocessError, KeyError):
        return False
    return "input" in out.stdout.split()


def _uinput_state_settling(samples: int = 12, delay: float = 0.25):
    """Poll /dev/uinput until the answer stops moving. Returns (writable, moved).

    udev applies rules asynchronously. install.sh settles the queue now, and this is
    the second half of the same lesson: report a stable reading rather than whichever
    sample happened first. A single reading is what made a successful install on real
    hardware announce "still not writable for you" about a device that udevd was
    already on its way to handing over.
    """
    import time

    from glasspad import pad

    try:
        prev = bool(pad.uinput_available())
    except Exception:                                       # noqa: BLE001
        return False, False
    moved = False
    for _ in range(samples):
        time.sleep(delay)
        try:
            cur = bool(pad.uinput_available())
        except Exception:                                   # noqa: BLE001
            cur = False
        if cur == prev:
            return cur, moved
        moved = True
        prev = cur
    return prev, moved


def install_everything(argv: list[str] = ()) -> int:
    """From "I downloaded one file" to a working overlay, in one command.

    Three things stand between a fresh machine and a pad that reaches a game, and this
    app can only do two of them:

      1. libgtk4-layer-shell -- a system library that cannot be bundled, because a copy
         has to match the host's libwayland AND GTK 4 and silently misleads when it
         does not. Installing it means running somebody else's package manager on their
         behalf, which is not this program's call to make. So step 1 checks and names.
      2. /dev/uinput -- one password, and the only step that needs one.
      3. menu entry plus a `glasspad` command -- no root, and what makes every other
         message in this program a sentence the reader can actually follow.

    Steps run in that order because that is the order their failures matter: with the
    library missing, nothing else is worth asking the user for.
    """
    from glasspad import bundle, pad

    dry = "--dry-run" in argv
    force = "--force" in argv

    print(f"GlassPad install{' -- dry run, nothing will change' if dry else ''}")
    print("  1  libgtk4-layer-shell   checked here; your package manager installs it")
    print("  2  /dev/uinput           one password")
    print("  3  menu entry + `glasspad` command   no password")

    blocked = []

    lib, why = bundle.layer_shell_path()
    if lib:
        print(f"\n[1/3] library: {lib}\n      ({why})")
    else:
        print("\n[1/3] library: NOT FOUND")
        print(bundle.dependency_hint())
        print("      Everything else is pointless until this is installed, so stopping")
        print("      here: there is no reason to ask for a password for an overlay that")
        print("      cannot be drawn. Install the package and re-run this command.")
        return 1

    print("\n[2/3] /dev/uinput")
    if pad.uinput_available():
        print("      already writable -- nothing to grant.")
    else:
        rc = install_uinput(argv)
        if rc != 0:
            blocked.append("/dev/uinput")

    print("\n[3/3] desktop integration")
    from glasspad.desktop_entry import install_desktop
    if install_desktop(dry=dry, force=force) != 0:
        blocked.append("desktop entry")

    hint = bundle.cli_hint()
    print()
    if blocked:
        print("Not finished: " + ", ".join(blocked))
        print(f"Sort that out, then run it again:  {hint} --install")
        return 1
    print("Done.")
    print(f"Start GlassPad from the applications menu, or with:  {hint}")
    print("Anything looks wrong later:  " + hint + " --status")
    if dry:
        print("(that was a rehearsal: no password was asked for and nothing was written)")
    return 0


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
        print("Every GlassPad AppImage contains one; if yours does not, the download")
        print("was truncated.")
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
    dry = "--dry-run" in argv
    if dry:
        print("(--dry-run: the installer will report and change nothing)")
    # Recorded so the verdict afterwards can say whether THIS run is what changed
    # group membership, instead of suggesting a re-login that cannot help.
    groups_before = None if dry else _input_group_in_database()
    # No privilege elevation at all for a dry run: install.sh --uinput --dry-run is
    # safe unprivileged, and asking for a password to read a plan is absurd.
    #
    # staged_installer() is not decoration. Inside an AppImage this script lives on a
    # FUSE mount that only the mounting user may read, so pkexec used to fail with
    # EACCES on the open() -- found on a real virgin install, and invisible to every
    # dry run, because a dry run does not elevate and so reads the file as the user
    # who IS allowed to read it.
    with bundle.staged_installer(installer) as staged:
        if not staged:
            print("the installer could not be copied somewhere root can read it")
            return 2
        if staged != installer:
            print(bundle.STAGED_NOTE)
        cmd = (["sh"] if dry else [tool, "sh"]) + [staged, "--uinput"]
        if dry:
            cmd.append("--dry-run")
        # Flush before spawning: install.sh writes straight to the same fd, and when
        # stdout is a pipe or a file the parent block-buffers -- so a logged run reads
        # as though the installer had already run and only then been explained. In a
        # terminal Python line-buffers and the order looks fine, which is exactly why
        # nobody notices until someone saves the output of a privileged command.
        sys.stdout.flush()
        sys.stderr.flush()
        rc = subprocess.call(cmd)
    if dry:
        return rc
    if rc == 125:
        print(f"\nAuthorisation declined ({tool} exit 125); nothing was changed.")
        return 1
    if rc != 0:
        # Measure before agreeing that it failed. On real hardware the installer's exit
        # status contradicted its own sentence three lines above:
        #
        #     writable by josh: GlassPad can create a pad now.
        #     The installer failed with exit 1
        #
        # because `[ "$waited" -gt 0 ] && say ...` was the last statement of the success
        # path and returned 1 -- whenever the device became writable immediately, which
        # is the common case. The device is the fact; an exit code is a claim about the
        # fact, and this program has printed confident wrong verdicts often enough that
        # the claim gets checked rather than believed.
        settled, _moved = _uinput_state_settling()
        if settled:
            print(f"\nThe installer exited {rc}, but /dev/uinput IS writable now:")
            print("its status line is wrong, not its work. The grant landed -- confirm")
            print(f"with:  {bundle.cli_hint()} --status")
            print("(please report that exit code regardless: a tool that fails loudly")
            print(" while succeeding is worse than one that is honest about which)")
            return 0
        print(f"\nThe installer failed with exit {rc}; read its output above.")
        print("GlassPad never started, so there is no half-configured state to untangle.")
        return rc

    writable, moved = _uinput_state_settling()
    if writable:
        print("\n/dev/uinput is writable: GlassPad can create a pad now.")
        if moved:
            print("(the node changed while we watched -- udev applies rules in the")
            print(" background, so a single reading right after a trigger means nothing)")
        print("Start it again and the controls will actually reach the game.")
        return 0

    if groups_before is False:
        print("\nInstalled -- but /dev/uinput is still not writable *for you*.")
        print("This run added you to group `input`, and a process keeps the groups it")
        print("started with: membership lands at the NEXT login. Log out and back in,")
        print("then confirm with:")
        print(f"    {bundle.cli_hint()} --status")
        return 0

    print("\nInstalled -- but /dev/uinput is still not writable *for you*, and the")
    print("group is NOT the reason: you were already in `input` before this run, so")
    print("re-logging will not change anything. The rule is installed; something else")
    print("decides this node's permissions. Ask it directly:")
    print("    getfacl /dev/uinput")
    print("    udevadm test /devices/virtual/misc/uinput 2>&1 \\| grep -E "
          "'99-glasspad|MODE|GROUP|uaccess'")
    print("An ACL entry or a TAG+=\"uaccess\" rule aimed elsewhere both outrank these")
    print("mode bits. Report those two outputs if you cannot see the answer.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
