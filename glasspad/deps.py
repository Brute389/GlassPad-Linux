"""What this machine will actually let us do about a missing dependency.

Why this module exists
----------------------
The install instruction was always generated correctly -- `bundle.dependency_hint()`
derives the command from /etc/os-release and labels which rows are verified rather than
guessed. What was wrong is that on the machine where the library is genuinely missing,
nothing reaches it: `gi.require_version("Gtk4LayerShell", "1.0")` raises at *import*, so
the process dies with a traceback before any handler runs, and a manager-launched app
has nowhere for a traceback to go. Measured, with the typelib hidden:

    ValueError: Namespace Gtk4LayerShell not available

So the guidance was written for a situation it could never be shown in.

Decisions this module encodes
-----------------------------

  * **Never install silently.** A package transaction pulls a dependency closure into a
    system the user owns. The most this file will do is hand a command to a terminal that
    waits for Enter -- see `staged_run()`, and glasspad/notice.py for the window.
  * **Detect the systems where the command cannot work, and say so.** That is not a
    hypothetical: `DEPENDENCY_PACKAGES` used to label its Debian row "Debian / Ubuntu /
    SteamOS", while SteamOS reports `ID=steamos ID_LIKE=arch`, so a Deck got the Arch row
    and a `pacman -S` against a read-only image -- an error naming neither GlassPad nor the
    reason. Bazzite and Bluefin are named in that same table and are rpm-ostree, where
    `dnf install` is not the tool. Deck and RetroDeck users *are* the Shelly audience, so
    getting this wrong hits the people most likely to install through a manager.
  * **Read, don't assume.** Each verdict names the measurement behind it
    (`/run/ostree-booted`, `steamos-readonly`, `/nix/store`, `/proc/mounts`, which package
    manager binary exists), and `unknown` is a real answer rather than a guess dressed up
    as one.
  * **One rendering.** `notice_lines()` produces the text for the window, for stderr when
    there is no display, and for the terminal staging. Three copies of an explanation is
    three chances to be wrong in different ways -- the same reason `status_rows()` exists.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess

from glasspad import bundle

OS_RELEASE = bundle.OS_RELEASE

#: How a system's packages are managed, as far as we can measure.
FAMILY_PACMAN = "pacman"
FAMILY_DNF = "dnf"
FAMILY_APT = "apt"
FAMILY_RPM_OSTREE = "rpm-ostree"
FAMILY_NIX = "nix"
FAMILY_UNKNOWN = "unknown"


def read_os_release(path: str = OS_RELEASE) -> dict:
    """ID, ID_LIKE, VARIANT_ID and NAME from os-release, lowercased and unquoted.

    Tolerant by design: this runs on machines whose os-release we have never seen, and a
    diagnostic that raises on a weird line is worse than the bug it was called to explain.
    """
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                key, sep, val = line.partition("=")
                if not sep:
                    continue
                key = key.strip()
                if key in ("ID", "ID_LIKE", "VARIANT_ID", "NAME"):
                    out[key] = val.strip().strip('"').strip("'").lower()
    except OSError:
        pass
    return out


def root_mount_options(proc_mounts: str = "/proc/mounts") -> str:
    """The mount options of `/`, or "" if they cannot be read."""
    try:
        with open(proc_mounts, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == "/":
                    return parts[3]
    except OSError:
        pass
    return ""


def _which(name: str, env=None) -> str | None:
    return shutil.which(name, path=(env or os.environ).get("PATH"))


def _detect_family(ids: tuple[str, ...], env=None, exists=os.path.exists) -> str:
    """Package manager family, from what is present -- not from the distro name."""
    # ostree first: these are Fedora-family by ID and would otherwise be handed `dnf`,
    # which on an ostree system is not the tool that changes the system.
    if exists("/run/ostree-booted"):
        return FAMILY_RPM_OSTREE
    if "nixos" in ids or exists("/nix/store"):
        return FAMILY_NIX
    if _which("pacman", env):
        return FAMILY_PACMAN
    if _which("dnf", env) or _which("dnf5", env) or _which("yum", env):
        return FAMILY_DNF
    if _which("apt-get", env) or _which("apt", env):
        return FAMILY_APT
    return FAMILY_UNKNOWN


def system_profile(os_release: str = OS_RELEASE, env=None, exists=os.path.exists,
                   proc_mounts: str = "/proc/mounts") -> dict:
    """What this system is, what we measured, and whether a package command can work.

    Every probe is injectable, because a test that reads the *host's* /etc/os-release is
    testing the host, not this function -- which is how "and the state agrees" once passed
    against a real ~/.local/bin/glasspad on the machine that wrote it.
    """
    rel = read_os_release(os_release)
    ids: list[str] = []
    for key in ("ID", "ID_LIKE"):
        ids.extend(rel.get(key, "").split())
    ids = list(dict.fromkeys(ids))
    variant = rel.get("VARIANT_ID", "")
    name = rel.get("NAME") or rel.get("ID") or "this distribution"

    steamos = "steamos" in ids or "steam" in ids or "steamos" in variant
    ostree = exists("/run/ostree-booted")
    nix = "nixos" in ids or exists("/nix/store")
    ro = False
    opts = root_mount_options(proc_mounts)
    if opts:
        ro = any(o == "ro" for o in opts.split(","))

    family = _detect_family(tuple(ids), env=env, exists=exists)
    notes: list[str] = []

    if steamos:
        mutable = False
        reason = ("SteamOS keeps the base system read-only. `/usr/bin/steamos-readonly` "
                  "toggles that, and Valve's own advice is not to: an updated image "
                  "reverts what was installed into it.")
        notes.append("Deck and RetroDeck users normally install GUI software as a Flatpak.")
        family = FAMILY_PACMAN if _which("pacman", env) else family
    elif ostree:
        mutable = False
        reason = ("This is an rpm-ostree system (`/run/ostree-booted` exists). `dnf "
                  "install` will run and change nothing durable; layered packages go "
                  "through `rpm-ostree install`, which stages a new image and needs a "
                  "reboot.")
    elif nix:
        mutable = False
        reason = ("Nix system packages are declared, not installed: adding one means "
                  "changing your configuration and rebuilding the system. Nothing here "
                  "should do that for you.")
    elif ro:
        mutable = False
        reason = "The root filesystem is mounted read-only, so no package command can work."
        notes.append(f"mount options: {opts}")
    elif family == FAMILY_UNKNOWN:
        mutable = False
        reason = ("No supported package manager was found (looked for pacman, dnf, "
                  "apt-get), so this program cannot tell you a command that would work.")
    else:
        mutable = True
        reason = ""

    return {
        "distro": name,
        "ids": tuple(ids),
        "family": family,
        "mutable": mutable,
        "reason": reason,
        "notes": notes,
        "steamos": steamos,
        "ostree": ostree,
        "nix": nix,
        "root_read_only": ro,
        # What was measured, so a bug report carries the evidence rather than a conclusion.
        # Compact on purpose. The first version pasted the whole mount option list, and one
        # line of --status turned into a wall of subvolume noise that hid the two facts
        # anyone is actually reading for.
        "measured": f"id={rel.get('ID', '?')} variant={variant or '-'} "
                    f"root={'ro' if ro else 'rw'} "
                    f"ostree={int(ostree)} nix={int(nix)} family={family}",
    }


def package_name(profile: dict) -> str | None:
    """The package name for this machine's distro ids -- from the profile, never from
    whatever /etc/os-release happens to say under the test suite."""
    name = bundle.package_for_ids(profile.get("ids") or ())
    if name:
        return name
    # Nothing in the table matched these ids. Fall back to the spelling the family uses,
    # and let notice_lines() print the verification note beside it rather than presenting
    # the guess as knowledge.
    return {FAMILY_PACMAN: "gtk4-layer-shell", FAMILY_RPM_OSTREE: "gtk4-layer-shell",
            FAMILY_DNF: "gtk4-layer-shell", FAMILY_APT: "libgtk4-layer-shell0",
            }.get(profile["family"])


def install_command(profile: dict) -> list[str] | None:
    """The command a human could run, or None when there isn't one that would work."""
    if not profile["mutable"]:
        return None
    pkg = package_name(profile)
    if not pkg:
        return None
    manager = {FAMILY_PACMAN: ["sudo", "pacman", "-S", "--needed", pkg],
               FAMILY_DNF: ["sudo", "dnf", "install", pkg],
               FAMILY_APT: ["sudo", "apt", "install", pkg]}.get(profile["family"])
    return manager


def repo_lookup(pkg: str, family: str, timeout: float = 4.0) -> bool | None:
    """Is the package in what this machine already has? True / False / None = unknown.

    Only pacman and apt are asked, and only against data that is already on disk:
    `pacman -Si` reads the local sync databases and `apt-cache policy` reads its own
    cache. `dnf repoquery` is deliberately not used -- it may fetch metadata, which turns
    a diagnostic into a network transaction the user never asked for.

    A non-zero exit means different things and must not be flattened into "absent": with
    no sync database at all, pacman cannot know anything. `None` is the honest answer and
    the caller says so.
    """
    if not pkg:
        return None
    try:
        if family == FAMILY_PACMAN:
            if not _which("pacman"):
                return None
            synced = os.path.isdir("/var/lib/pacman/sync") and bool(
                [f for f in os.listdir("/var/lib/pacman/sync") if f.endswith(".db")])
            if not synced:
                return None          # never synced: no opinion available
            rc = subprocess.run(["pacman", "-Si", "--quiet", pkg],
                                capture_output=True, timeout=timeout)
            return rc.returncode == 0
        if family == FAMILY_APT:
            if not _which("apt-cache"):
                return None
            out = subprocess.run(["apt-cache", "policy", pkg],
                                 capture_output=True, timeout=timeout, text=True)
            if out.returncode != 0:
                return None
            # "Unable to locate package", or a policy block with no candidate: absent.
            if "Unable to locate" in out.stdout or "Unable to locate" in out.stderr:
                return False
            return "Candidate:" in out.stdout and " (none)" not in out.stdout.split(
                "Candidate:")[1][:40]
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def staged_run(command: list[str]) -> list[str]:
    """A shell command that SHOWS what it will run and waits for Enter before running it.

    Why staged rather than run: the user gets the exact transaction on screen before any
    password is asked for, and EOF cancels -- so this is safe to hand to a terminal that
    was opened non-interactively, which is exactly how a manager-launched app opens one.
    `read` consuming EOF exits non-zero, and nothing runs.
    """
    shown = shlex.join(command)
    script = (
        "printf '%s\\n' "
        "'About to run, as root:' '' \"    $0\" '' "
        "'Enter runs it. Ctrl-C, or EOF, cancels and nothing changes.'; "
        "read _ || exit 130; "
        "exec \"$@\"\n"
    )
    return ["/bin/sh", "-c", script, shown] + list(command)


def probe_bound() -> tuple[str, str]:
    """Ask the compositor, here and now, whether layer-shell actually works.

    Five answers, because "no" had two meanings and the old code printed confidently wrong
    advice for both -- and because "the file is present" is not the same claim as "the
    overlay will draw", which is the distinction this whole module exists to make.

      ok             bound on this compositor
      missing        no library to bind with
      nodisplay      library fine, no Wayland session reachable from this environment
      unbound        library loaded, layer-shell unusable (link order, usually)
      nocompositor   the desktop does not implement the protocol at all

    The last two are still distinguished only by `XDG_CURRENT_DESKTOP` for GNOME: telling
    them apart from the inside needs a Wayland registry walk, and a wrong pointer there is a
    segfault, which no catch clause survives. The wording for `unbound` therefore names both
    causes and the one-line test that separates them, rather than picking one and being wrong
    in public.
    """
    lib, why = bundle.layer_shell_path()
    if not lib:
        return "missing", f"not found ({why})"
    try:
        import ctypes

        import gi
        ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gdk, Gtk4LayerShell
    except Exception as exc:  # noqa: BLE001 - reported, never raised: this is a diagnostic
        return "unbound", f"{type(exc).__name__}: {exc}"
    try:
        display = Gdk.Display.get_default()
    except Exception as exc:  # noqa: BLE001
        return "nodisplay", f"opening a display raised {type(exc).__name__}: {exc}"
    if display is None:
        return "nodisplay", "no Wayland display reachable from this environment"
    try:
        supported = bool(Gtk4LayerShell.is_supported())
    except Exception as exc:  # noqa: BLE001
        return "unbound", f"is_supported raised {type(exc).__name__}: {exc}"
    if supported:
        return "ok", f"{lib} bound on {display.get_name()}"
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "gnome" in desktop:
        return ("nocompositor",
                f"XDG_CURRENT_DESKTOP={desktop}: mutter does not implement "
                f"zwlr_layer_shell_v1")
    return "unbound", f"not bound on {desktop or 'this compositor'}"


def notice_lines(state: str, pkg: str | None = None,
                 profile: dict | None = None, looked_up: bool = True) -> list[str]:
    """The explanation, as paragraphs. One owner for window, stderr and terminal.

    `state` is what we found:
      "missing" -- the library is not on this machine at all
      "unbound" -- the library loaded but cannot use layer-shell here
      "nocompositor" -- the compositor does not offer zwlr_layer_shell_v1
    """
    prof = profile or system_profile()
    cmd = install_command(prof)
    out: list[str] = []

    if state == "ok":
        out.append("Nothing appears to be missing: the library is here, it bound to this "
                   "compositor, and layer-shell is usable.")
        out.append("If the controls still are not visible, the usual reasons are elsewhere: "
                   "a game holding an exclusive fullscreen (borderless works), the overlay "
                   "hidden with the hide control, stacking that needs a keep-above window "
                   "rule, or a layout moved off-screen. `glasspad --status` prints the whole "
                   "chain, including the uinput grant.")
        return out
    if state == "nodisplay":
        out.append("libgtk4-layer-shell is installed, but no Wayland session can be reached "
                   "from this environment, so there is nothing to draw on.")
        out.append("Expected over ssh and before a login. From the desktop session itself:")
        out.append("    WAYLAND_DISPLAY=" + (os.environ.get("WAYLAND_DISPLAY") or "wayland-1")
                   + " glasspad --status")
        return out
    if state == "missing":
        out.append("GlassPad cannot draw its controls: libgtk4-layer-shell is not "
                   "installed on this machine.")
        out.append("No bundle can carry it. The copy has to match this machine's "
                   "libwayland AND GTK 4, and a mismatch does not fail -- it draws "
                   "nothing, which from a tablet looks like a crashed app.")
    elif state == "unbound":
        out.append("libgtk4-layer-shell loaded, but it cannot use layer-shell here.")
        out.append("Two causes, in the order worth ruling out. First, link order: "
                   "libwayland getting there first is fixed by one environment variable, "
                   "so try it before blaming the desktop. Second, your compositor really "
                   "does not implement zwlr_layer_shell_v1 -- stock GNOME does not, and "
                   "no setting changes that. `glasspad --status` prints what it found.")
        out.append("    LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so.0 glasspad")
    else:
        out.append("This compositor does not offer zwlr_layer_shell_v1, so nothing can be "
                   "placed above a fullscreen game. KWin, Hyprland, Sway and wlroots "
                   "implement it; stock GNOME (mutter) does not.")
        return out

    name = pkg or package_name(prof) or "libgtk4-layer-shell"
    if prof["mutable"] and cmd:
        out.append(f"This looks like {prof['distro']}. The package is `{name}`, and "
                   f"installing it is your decision, not this program's:")
        out.append("    " + shlex.join(cmd))
        if looked_up:
            found = repo_lookup(name, prof["family"])
            if found is False and prof["family"] == FAMILY_PACMAN:
                out.append("`pacman -Si` does not list it in your enabled repositories, "
                           "so on Arch-family systems it is likely from the AUR: build it "
                           "with an AUR helper you trust. Do not pipe a build script from "
                           "the internet into a shell.")
            elif found is None:
                out.append("(Whether your enabled repositories carry it could not be "
                           "checked without asking them, so no claim is made either way.)")
        out.append("If a terminal was not opened for you, run the command above yourself.")
    else:
        out.append(f"The package would be `{name}`, but no install command is offered "
                   f"here, and that is not this program being cautious:")
        out.append("    " + prof["reason"])
        for note in prof["notes"]:
            out.append("    " + note)
    out.append("Installed somewhere unusual? Point at it and check, no root needed:")
    out.append("    GLASSPAD_LAYER_SHELL=/path/to/libgtk4-layer-shell.so.0 glasspad "
               "--status")
    return out


def print_notice(state: str, **kw) -> None:
    """The same paragraphs, for a terminal. Used when there is no display to draw on.

    Blank line between paragraphs, nothing else: the window shows the same list with real
    spacing, and this is the copy a user pastes into a bug report.
    """
    for para in notice_lines(state, **kw):
        print(para, flush=True)
        print("", flush=True)
