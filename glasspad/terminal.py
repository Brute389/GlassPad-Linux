"""Open a terminal window running one of our own commands.

Why this exists
---------------
Run GlassPad from a terminal and every diagnostic is copy-pasteable. Launch it the way
most people launch an application -- an icon, or drag-and-drop onto an AppImage manager
like Shelly -- and there is no terminal at all: stdout goes nowhere, and a message that
says *run `glasspad --install-uinput`* is a note pinned to a door with no handle. The
one step that needs a password is exactly the step that needs to be seen, because it
shows which udev rule is about to be written and what the verdict was.

So the overlay's About box offers "Grant gamepad access", and this module puts a window
on screen running that command. Not a toast that claims success: a window with the plan,
the polkit prompt, the installer's own output, and the exit code, which is also what
somebody files a bug report with.

Design notes that matter
------------------------

  * The command is wrapped so the window **stays open after it exits**. Half the
    terminals on this list close the moment their child does, which turns a three-second
    installer into an unreadable flash -- and the failure text is the part the user
    needs. The wrapper echoes the exit status and waits for Enter.
  * Each terminal takes the child differently (``-e``, ``--``, ``--command=<string>``),
    so the table holds a builder per terminal rather than one argv with a flag swapped.
  * Nothing here escalates. It launches a terminal running the same command the user
    could type; the elevation (and the password prompt) stays inside
    ``install_uinput``/``pkexec``, where it can be seen and refused.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess

# Priority order: the desktops that ship these tend to have them first, and konsole
# handles --hold plus a multi-word -e more reliably than anything else here.
TERMINALS: tuple[tuple[str, ...]] = (
    ("konsole", ("konsole", "--hold", "-e")),
    ("gnome-terminal", ("gnome-terminal", "--wait", "--")),
    ("kitty", ("kitty", "--")),
    ("alacritty", ("alacritty", "-e")),
    ("foot", ("foot", "--")),
    ("wezterm", ("wezterm", "start", "--")),
    ("xfce4-terminal", ("xfce4-terminal", "--hold", "--command")),   # takes one string
    ("xterm", ("xterm", "-e")),
)

_STRING_COMMAND = {"xfce4-terminal"}      # these want the child as a single --command=


def wrap_keep_open(argv: list[str]) -> list[str]:
    """Re-run `argv`, then report its exit code and wait for Enter.

    shlex.join, not f-string interpolation: the AppImage lives in a Downloads folder
    more often than not, and a path with a space that survives the Python side and then
    splits itself inside the shell is the kind of bug that only appears on someone
    else's machine.
    """
    line = shlex.join(argv)
    script = (
        f"{line}\n"
        "rc=$?\n"
        "echo\n"
        "echo \"[GlassPad exited $rc — press Enter to close]\"\n"
        "read _ < /dev/tty || true\n"
    )
    return ["sh", "-c", script]


def find_terminal(prefer: str = "") -> tuple[str, tuple[str, ...]] | None:
    """First terminal emulator we can actually launch, honouring $GLASSPAD_TERMINAL."""
    want = (prefer or os.environ.get("GLASSPAD_TERMINAL") or "").strip()
    for name, base in TERMINALS:
        # basename so a full path works too ("/usr/bin/kitty"), but compare against the
        # CANDIDATE's name: an earlier version tested `want not in (name, basename(want))`
        # -- basename(want) is always itself, so the filter never skipped anything and
        # $GLASSPAD_TERMINAL silently selected whatever happened to be installed first.
        if want and os.path.basename(want) != name:
            continue
        if shutil.which(name):
            return name, base
    if want:
        # Named explicitly and not present: say so loudly rather than quietly picking
        # something else, or the user debugs the wrong terminal.
        return None
    return None


def open_command_window(argv: list[str], cwd: str | None = None,
                        env_extra: dict | None = None) -> tuple[bool, str]:
    """Show `argv` in a terminal. Returns (opened, what_happened).

    Never raises: this is called from the overlay's UI thread, and a traceback in answer
    to "grant access" is worse than the missing device.
    """
    found = find_terminal()
    if not found:
        named = (os.environ.get("GLASSPAD_TERMINAL") or "").strip()
        if named:
            # Named explicitly and not installed: saying "no terminal found" would send
            # them to install a different one than they asked for.
            why = f"$GLASSPAD_TERMINAL={named!r} is not on PATH"
        else:
            why = ("no terminal emulator found (looked for: "
                   + ", ".join(n for n, _ in TERMINALS) + ")")
        return False, (why + ". Run this yourself:\n      " + shlex.join(argv))
    name, base = found
    child = wrap_keep_open(argv)
    if name in _STRING_COMMAND:
        cmd = list(base) + [shlex.join(child)]
    else:
        cmd = list(base) + child

    env = dict(os.environ)
    env["GDK_BACKEND"] = "wayland" if os.environ.get("WAYLAND_DISPLAY") else \
        env.get("GDK_BACKEND", "x11")
    for k, v in (env_extra or {}).items():
        env[k] = v

    try:
        subprocess.Popen(cmd, cwd=cwd or None, env=env,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except (OSError, ValueError) as exc:
        return False, f"could not start {name}: {exc}"
    return True, f"{name} opened: {shlex.join(argv)}"


def install_command(target_kind: str, target: str, args: list[str],
                    python: str | None = None) -> tuple[list[str], str | None, dict]:
    """The command line to show a terminal, for either shape of install.

    A bundle is one file, so the command is that file plus the flags. A checkout needs
    `python3 -m glasspad` run from the tree -- and PYTHONPATH plus PYTHONSAFEPATH, so
    the terminal's own cwd cannot shadow the package (the same trap AppRun guards).
    """
    if target_kind == "appimage":
        return [target] + list(args), None, {}
    py = python or "/usr/bin/python3"
    return ([py, "-m", "glasspad"] + list(args), target,
            {"PYTHONPATH": target, "PYTHONSAFEPATH": "1"})
