"""Install or remove a desktop menu entry -- from a checkout or from a bundle.

Ported out of the `.run` launcher, which had the only bundle-side way to get a
menu entry: a heredoc writing `org.glasspad.GlassPad.desktop` next to a hardcoded
`Icon=` path pointing INTO the unpacked payload. That was fine for a `.run`, whose
payload stays unpacked in `~/.cache/glasspad` until you clear it. It is actively
wrong for an AppImage, whose payload is a FUSE mount at `/tmp/.mount_*` that stops
existing the moment the app exits -- the menu would show a broken icon within
seconds, which is a worse first run than having no entry at all.

So the icon goes where `--install-desktop` puts it, into the user's icon theme, and `Icon=`
names it for theme lookup instead of hardcoding a path. The entry itself is the
`.run`'s, improvements included:

* exactly one `Exec=`, and no field codes (`%f`, `%u`) -- glasspad's argument
  parser accepts no file or URL, so a token a launcher appends is an unrecognised
  argument and the launch exits 2;
* rewritten on every run, so moving the AppImage and re-running self-heals the
  entry rather than leaving a dead one behind;
* refuses outright when it cannot work out what to launch. A menu entry that
  launches nothing is worse than no menu entry, because it looks like the app is
  broken rather than like the installation was incomplete.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

DESKTOP_NAME = "org.glasspad.GlassPad.desktop"
# Written into every entry this installer produces, so that ownership is PROVABLE.
# Filenames are not ours to reason about: Shelly, the AppImage manager on CachyOS, writes
# its entry for this application as `glasspad.desktop` -- exactly the name LEGACY_NAMES
# reserves for a very old install of ours. Judging ownership by name therefore called
# Shelly's file "ours": install wrote a second entry, and --uninstall-desktop would have
# deleted the manager's entry from under its own metadata database. See entry_owner().
MARKER_KEY = "X-GlassPad-Installer"
# The name a per-user install writes. Uninstall removes both,
# because "take the menu entry away" means that whatever is in the menu, not
# whichever of the two installers happens to have put it there.
LEGACY_NAMES = ("glasspad.desktop",)
ICON_NAME = "glasspad"

DESC = ("Turn the touchscreen into a virtual Xbox gamepad")


def data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def launch_target(env=None) -> tuple[str | None, str]:
    """What `Exec=` should point at, plus a sentence saying why that one.

    The AppImage runtime exports `APPIMAGE` with the path of the file the user
    actually ran -- measured, not assumed: a live overlay process had
    `APPIMAGE=/tmp/p.AppImage` alongside `APPDIR=/tmp/.mount_p.AppILfpLLd`. It is
    the only path that survives the mount going away, so inside a bundle it is the
    only correct answer.

    The other cases are separated by evidence rather than by hope. "There is a
    glasspad package next to me" proves nothing about how it gets launched -- that
    is true of an installed copy, a copied-out tree, and this source file alike --
    so a checkout has to look like a tree the user holds (README.md and LICENSE
    beside the package), and an installed copy has to be in site-packages, where
    `python -m glasspad` genuinely finds it with nothing inherited from a shell.
    Anything else is refused: an entry that cannot launch is worse than none,
    because it reads as a broken app rather than an unfinished install.
    """
    env = os.environ if env is None else env

    appimage = (env.get("APPIMAGE") or "").strip()
    if appimage and os.path.isfile(appimage):
        return os.path.abspath(appimage), "the AppImage you ran"

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    py = sys.executable or shutil.which("python3")
    runnable = os.path.isfile(os.path.join(here, "__main__.py"))

    if runnable and py and os.path.isfile(os.path.join(root, "README.md")):
        # `python -m glasspad` resolves the package from the working directory,
        # which is what the `Path=` key sets below, so no PYTHONPATH is needed.
        return f"{py} -m glasspad", f"the checkout at {root}"

    # A `glasspad` on PATH is accepted only when it is demonstrably THIS install.
    # An unqualified `which` was here before, and it was a hole: any command by that
    # name -- another build, a distribution package, a symlink to an AppImage that
    # happens to be in someone's ~/.local/bin -- became Exec= for whichever tree asked.
    # Found by installing the CLI link on a real machine: a copied-out folder that used
    # to refuse then wrote a menu entry launching the *other* GlassPad, and exited 0.
    # The rule is the one bundle.cli_hint() uses, so the command a message suggests and
    # the command a menu entry runs are chosen the same way.
    ignored = None
    on_path = shutil.which("glasspad")
    if on_path:
        from glasspad import bundle
        if bundle._is_our_command(on_path, ("checkout", root)):
            return on_path, "the glasspad command this install wrote"
        try:
            ignored = os.path.realpath(on_path)
        except OSError:
            ignored = on_path

    if runnable and py and os.path.abspath(here).startswith(_site_dirs()):
        return f"{py} -m glasspad", "the installed glasspad package"

    why = ("nothing launchable: no APPIMAGE, no checkout beside this "
           "package, no glasspad on PATH")
    if ignored:
        why += (f"\n                 (a `glasspad` IS on PATH at {on_path}, pointing at"
                f"\n                  {ignored} -- not this install, so it is not used;"
                "\n                  an entry that launches a different GlassPad than"
                " the one\n                  you installed is worse than no entry)")
    return None, why


def _site_dirs() -> str:
    """A prefix meaning "this package was installed, not copied out"."""
    try:
        import sysconfig
        for key in ("purelib", "platlib"):
            path = sysconfig.get_paths().get(key)
            if path and os.path.abspath(here_dir()).startswith(os.path.abspath(path) + os.sep):
                return os.path.abspath(path) + os.sep
    except Exception:
        pass
    return "\x00never-matches"


def here_dir() -> str:  # tiny helper so _site_dirs stays honest about its input
    return os.path.dirname(os.path.abspath(__file__))


def icon_source() -> str | None:
    """The icon to copy into the theme: the application's own artwork.

    There was a time when this preferred `packaging/glasspad.svg`, a picture of a tablet
    with a gamepad on it that I drew because the tree had no icon in the place a build
    looks. It does have one -- `glasspad/img/glasspad_icon_*.png`, the upstream artwork,
    which is what `Icon=glasspad` in the generated entry has always meant to resolve to. A placeholder that stays after the real thing arrives is worse than no
    placeholder, because it looks like a deliberate choice.

    Resolution order puts the source tree first (a checkout is what gets built from),
    then the paths that exist inside a bundle, where the repo layout is gone and the
    staged copy under usr/share/icons is the only icon file outside the package.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pkg = os.path.dirname(os.path.abspath(__file__))
    appshare = os.path.dirname(os.path.dirname(pkg))          # .../usr/share in a bundle
    for cand in (os.path.join(here, "glasspad", "img", "glasspad_icon_512.png"),
                 os.path.join(here, "glasspad", "img", "glasspad_icon.png"),
                 os.path.join(here, "packaging", "glasspad.png"),
                 os.path.join(pkg, "img", "glasspad_icon_512.png"),
                 os.path.join(pkg, "img", "glasspad_icon.png"),
                 os.path.join(appshare, "icons", "hicolor", "512x512", "apps",
                              "glasspad.png"),
                 os.path.join(appshare, "icons", "hicolor", "256x256", "apps",
                              "glasspad.png")):
        if os.path.isfile(cand):
            return cand
    return None


# Sizes the installer renders, and the ones the *panel* actually uses are at the small end
# of this list.
ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)
ICON_DIR = "hicolor"


def _png_size(path: str) -> tuple[int, int] | None:
    """Pixel dimensions of a PNG, from its header, with no image library.

    The IHDR width and height are the two 32-bit big-endian fields after the signature and
    length/type, at bytes 16..24. This is what lets the suite assert the numeric property it
    actually cares about: a file installed into `256x256/apps` must BE 256x256.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or not head.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    wh = (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))
    # A file that starts like a PNG and declares 0x0 is a truncated or forged header, and
    # "0x0" would satisfy no directory promise anyway. Callers treat None as "unknown",
    # which is what they should be told.
    return wh if wh[0] > 0 and wh[1] > 0 else None


def render_icon(src: str, dst: str, size: int) -> str:
    """Write `src` into `dst` at `size` pixels, and report how.

    Why this exists, in one measured fact: a launcher draws a menu tile at 22-32 px and an
    app tile at ~128 px. Copying one large PNG into `512x512/apps` therefore changes the
    picture at exactly none of the sizes anything draws -- and the smaller copies left by an
    earlier install keep being used. That is why "we shipped the app's artwork and refreshed
    the icon caches" was reported, plausibly, while the menu carried on looking identical:
    the theme was answering at sizes nobody had touched.

    GdkPixbuf does the scaling (it is here: GTK 4 is a hard requirement of the overlay
    itself). When it is somehow not importable, the fallback is a plain copy rather than a
    refusal -- an entry with one artwork at one size still beats no icon -- and the caller
    says which happened.
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
    except Exception:                                     # noqa: BLE001 - degrade, do not fail
        shutil.copyfile(src, dst)
        return "copied (no GdkPixbuf: not rescaled)"
    try:
        buf = GdkPixbuf.Pixbuf.new_from_file_at_scale(src, size, size, True)
        buf.savev(dst, "png", [], [])
    except Exception:                                     # noqa: BLE001 - corrupt source, etc.
        shutil.copyfile(src, dst)
        return "copied (scaling failed)"
    return "rendered"


def purge_icons(base: str) -> list[str]:
    """Delete every glasspad artwork file this installer could have written, at any size.

    One artwork must not live at two sizes with two different pictures. Without this, an
    upgrade leaves the pre-upgrade copies at the sizes the panel draws, and the new artwork
    is picked up only where the launcher happens to ask for 512.
    """
    gone = []
    root = os.path.join(base, "icons", ICON_DIR)
    for dirpath, _dirs, files in os.walk(root) if os.path.isdir(root) else []:
        for name in files:
            if name.startswith(ICON_NAME + ".") and os.path.basename(dirpath) == "apps":
                try:
                    os.remove(os.path.join(dirpath, name))
                    gone.append(os.path.join(dirpath, name))
                except OSError:
                    pass
    return gone


def install_icons(base: str, src: str) -> tuple[list[str], list[str]]:
    """Render the whole size set for one artwork. Returns (written, notes)."""
    written, notes = [], []
    for size in ICON_SIZES:
        dst = os.path.join(base, "icons", ICON_DIR, f"{size}x{size}", "apps",
                           ICON_NAME + ".png")
        how = render_icon(src, dst, size)
        written.append(dst)
        note = f"{size}x{size}: {how}"
        if note not in notes:
            notes.append(note)
    return written, notes


def desktop_text(exec_line: str, workdir: str | None = None,
                 status_line: str | None = None) -> str:
    """The .desktop body, keys in the order the spec groups them.

    This docstring used to read "field order mirrors desktop/glasspad.desktop.in". That
    file is gone, and the reason it went is the point: it was a template from the first
    packaging attempt that nothing ever built from -- the entry is generated here, and for
    the bundle in tools/make_appimage.sh -- while three comments and two docstrings cited
    it as the specification. Unobserved, it drifted: `Path=` unconditionally (wrong for a
    bundle, where it would name a directory inside the FUSE mount), and no
    `X-GlassPad-Installer`, the key the installer uses to prove an entry is ours rather
    than a manager's. A file held up as the spec and free to disagree with the code is
    worse than no spec, because it stops anyone from checking the code against reality.

    `Path=` is emitted only for a checkout. For a bundle it would name a
    directory inside the FUSE mount, which stops existing when the app exits --
    the same mistake as pointing Icon= at the payload, one key earlier in the file.
    """
    status = status_line or f"{exec_line} --status"
    path_key = f"Path={workdir}\n" if workdir else ""
    return f"""[Desktop Entry]
Type=Application
Version=1.0
Name=GlassPad
GenericName=Touch gamepad overlay
Comment={DESC}
# No %u/%f field codes: glasspad's parser accepts no file or URL arguments, so a
# token a launcher appends is an unrecognised argument and the launch exits 2.
Exec="{exec_line}"
{path_key}Icon={ICON_NAME}
Terminal=false
# One main category: "Game;Utility;" is legal but desktop-file-validate warns the
# entry may then appear twice in the menu.
Categories=Game;
Keywords=gamepad;controller;touch;xbox;overlay;
StartupNotify=false
X-KDE-StartupNotify=false
{MARKER_KEY}=1
# A second instance would double every input, so never restore duplicates.
X-GNOME-SingleWindow=true
Actions=status;

[Desktop Action status]
Name=Check GlassPad environment
Exec="{status}"
"""


def entry_exec(path: str) -> str:
    """The Exec= value of a .desktop file, quotes stripped, or ""."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if ln.startswith("Exec="):
                    return ln[5:].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def entry_owner(path: str, target: str) -> str:
    """"ours" | "foreign" | "other" -- judged on content, never on filename.

    The middle state is the whole reason this exists:

      ours     -- carries MARKER_KEY, or sits at our own filename with an Exec resolving
                  to this very file (an entry written by a GlassPad from before the marker
                  existed, so upgrades still recognise and replace those).
      foreign  -- launches THIS file but was not written by us: Shelly's entry,
                  AppImageLauncher's, a hand-made one. Do not duplicate it on install, and
                  never delete it on uninstall: the manager tracks the file's location and
                  owns the entry, whatever it chose to call it.
      other    -- some other application; nothing to say about it.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read(8192)
    except OSError:
        return "other"
    if MARKER_KEY in body:
        return "ours"
    ex = entry_exec(path)
    if target and ex and os.path.realpath(ex) == os.path.realpath(target):
        return "ours" if os.path.basename(path) == DESKTOP_NAME else "foreign"
    return "other"


def applications_dirs() -> list[str]:
    """Where a menu entry might live, in the order the desktop would read them."""
    dirs = [os.path.join(data_home(), "applications")]
    for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
        if d:
            dirs.append(os.path.join(d, "applications"))
    return dirs


def foreign_entries_for(target: str) -> list[tuple[str, str]]:
    """Entries that launch THIS file but were not written by us: (path, Name=).

    An AppImage manager (Shelly, AppImageLauncher, Discover's AppImage backend) integrates
    an AppImage by copying it into its own directory and writing a .desktop that it then
    owns -- it is the thing that moves, updates and removes the file afterwards. A second
    entry written by us means two menu items for one application, only one of which the
    manager keeps current; the moment it relocates the file, ours launches nothing, and a
    menu item that fails reads as a broken app.

    Matching is on the resolved Exec path via entry_owner(), never on filename -- which is
    how Shelly's `glasspad.desktop` gets recognised for what it is.
    """
    if not target or (" " in target and not os.path.exists(target)):
        return []            # e.g. "python3 -m glasspad": nothing for a manager to own
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for d in applications_dirs():
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if not n.endswith(".desktop"):
                continue
            path = os.path.join(d, n)
            if os.path.realpath(path) in seen:
                continue
            if entry_owner(path, target) != "foreign":
                continue
            seen.add(os.path.realpath(path))
            label = n[: -len(".desktop")]
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    for ln in fh:
                        if ln.startswith("Name=") and len(ln) > 5:
                            label = ln[5:].strip()
                            break
            except OSError:
                pass
            out.append((path, label))
    return out


def _exec_line_for(target: str) -> tuple[str, str | None]:
    """`Exec=` for the machine being installed to, and a line explaining the choice.

    On a host without libfuse2 -- Ubuntu 24.04 and anything derived from it -- a menu
    entry pointing straight at the AppImage launches nothing: the runtime prints
    "AppImages require FUSE to run" to a terminal that no launcher opened, and the user
    is left with an icon that appears to be broken. APPIMAGE_EXTRACT_AND_RUN=1 is the
    documented route there: it unpacks to a temp dir instead of mounting, which costs a
    moment per launch and works everywhere.

    Decided at install time, on the machine in question, and said out loud in the install
    output. A wrapper nobody asked for and cannot see is its own kind of surprise, and
    the cost is real -- so the note says what it cost and how to undo it.
    """
    from glasspad import bundle        # lazy, like every other use here
    if not target.strip().endswith(".AppImage") or bundle.libfuse2_present():
        return target, None
    quoted = target if " " not in target else f'"{target}"'
    return (f"env APPIMAGE_EXTRACT_AND_RUN=1 {quoted}",
            "wrapped with APPIMAGE_EXTRACT_AND_RUN=1: this system has no libfuse2, so "
            "the image cannot mount. Remove that prefix from Exec= once you install it "
            "(Ubuntu 24.04 dropped the package).")


def _write_entry_and_icon(target: str, why: str, base: str, apps: str,
                     dest: str, icon: str | None, icon_dest: str | None) -> None:
    """The menu entry, its icon, and a cache refreshed only if one exists."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    workdir = root if " -m glasspad" in target and os.path.isfile(
        os.path.join(root, "glasspad", "__main__.py")) and not os.environ.get("APPIMAGE") else None

    exec_line, exec_note = _exec_line_for(target)
    os.makedirs(apps, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(desktop_text(exec_line, workdir=workdir))
    os.chmod(dest, 0o644)
    print(f"wrote {dest}")
    print(f"  launching: {exec_line}  ({why})")
    if exec_note:
        print(f"  {exec_note}")

    if icon_dest:
        # Not one file at one size: purge, then render every size the theme dirs claim.
        # See render_icon for the measured reason -- copying a single 512 PNG into a
        # directory named 512x512 left the 22 px tile, the one a panel draws, untouched.
        purged = purge_icons(base)
        written, notes = install_icons(base, icon)
        print(f"installed the icon as '{ICON_NAME}' in {len(written)} sizes "
              f"-> {os.path.dirname(os.path.dirname(os.path.dirname(written[-1])))}")
        for note in notes:
            print(f"    {note}")
        if purged:
            print(f"    replaced {len(purged)} older copy/copies at other sizes")
    else:
        # Worth saying: an entry without an icon still launches, but it looks
        # provisional in the menu, and a provisional-looking app reads as broken.
        print("no icon found in this build; the entry will use the default")

    # Cache refresh, with a rule rather than a reflex.
    #
    # update-desktop-database is NOT run. It maintains MIME association caches,
    # and this entry declares no MimeType=, so it has nothing to contribute -- yet
    # running it on a pristine account CREATES share/applications/mimeinfo.cache
    # and intent.cache. Installing a gamepad overlay should not scatter MIME caches
    # into a home directory that had none. (Found the hard way: the "writes
    # nothing else" test failed on exactly those two files.)
    #
    # gtk-update-icon-cache IS run, but only where a cache already exists. If the
    # theme has one, a stale one can mask a newly installed icon and refreshing is
    # the difference between seeing it now and seeing it after the next login; if
    # it has none, creating one is the same unasked-for file as above.
    theme = os.path.join(base, "icons", "hicolor")
    cache = os.path.join(theme, "icon-theme.cache")
    exe = shutil.which("gtk-update-icon-cache")
    if exe and os.path.exists(cache):
        try:
            subprocess.run([exe, "-q", "-f", theme], capture_output=True, timeout=30)
            print("refreshed the existing icon cache")
        except (OSError, subprocess.SubprocessError):
            pass

    print("the entry appears on the next menu refresh; log out and in if it does not.")


def install_desktop(dry: bool = False, force: bool = False) -> int:
    """Write the menu entry and the icon into the user's own directories.

    Never needs root, never writes outside $XDG_DATA_HOME, and prints every path
    it touched so the user can undo it by hand from the terminal alone.
    """
    target, why = launch_target()
    if not target:
        print(f"Cannot install a menu entry: {why}.")
        print("Run this from inside the AppImage, or from a source checkout.")
        return 2

    base = data_home()
    apps = os.path.join(base, "applications")
    icon = icon_source()
    icon_dest = None
    if icon:
        kind = "scalable" if icon.endswith(".svg") else ("512x512" if "512" in icon else "256x256")
        ext = ".svg" if icon.endswith(".svg") else ".png"
        icon_dest = os.path.join(base, "icons", "hicolor", kind, "apps", ICON_NAME + ext)

    dest = os.path.join(apps, DESKTOP_NAME)
    foreign = foreign_entries_for(target)
    if dry:
        # Rehearse the DECISION, not only the file writes. This used to print "would
        # write <path>" and then, two lines later, "a real run would not add another" --
        # which reads as a contradiction and tells the user nothing about what happens.
        if foreign and not force:
            print(f"would NOT write {dest}")
            for path, label in foreign:
                print(f"           {label} already launches this file:")
                print(f"             {path}")
            print("           (pass --force to write ours as well)")
        else:
            print(f"would write {dest}")
            if icon_dest:
                print(f"would copy {icon} -> {icon_dest}")
        print(f"launch target: {target}  ({why})")
        # The rehearsal has to rehearse all of it: a --dry-run that quietly
        # omits the command line is how you discover the difference on the real
        # run, which is the one that has a password prompt in it.
        from glasspad import bundle
        print()
        bundle.install_cli_link(dry=True, force=force)
        return 0

    foreign = foreign_entries_for(target)
    if foreign and not force:
        print("A menu entry that launches this exact file already exists, written by")
        print("something else -- an AppImage manager integrates the files it installs:")
        for path, label in foreign:
            print(f"    {label:<26} {path}")
        print("Not writing a second one. Two entries for one application is how a")
        print("manager's later move leaves a stale item pointing at a file that is gone,")
        print("and only the manager's entry knows where the file went. Pass --force to")
        print("write ours as well.")
    else:
        _write_entry_and_icon(target, why, base, apps, dest, icon, icon_dest)

    # The menu entry makes the app clickable. The command makes it fixable:
    # every diagnostic this program prints is imperative -- run this, confirm
    # with that -- and for someone whose GlassPad is one file in a Downloads
    # folder, none of those sentences are followable until `glasspad` exists.
    # A refused link does not undo the entry, so it is reported rather than
    # returned: the menu item works either way.
    print("\ncommand line")
    from glasspad import bundle
    if bundle.install_cli_link(dry=dry, force=force) != 0:
        print("  (the menu entry is installed regardless; the messages above")
        print("   name the file directly until a `glasspad` command exists)")
    return 0


def icon_install_paths(base: str) -> list[str]:
    """Every path install_desktop could have written the icon to, that exist now.

    install picks one size according to which source it found (svg -> scalable, 512 ->
    512x512, otherwise 256x256), and uninstall cannot know which one that was -- so it
    does not guess, it clears the three sizes x two extensions. The basename is the app
    id, so `hicolor/512x512/apps/glasspad.png` is not a file another package would own;
    anything else in those directories is untouched by construction.

    The reason this exists: --uninstall-desktop used to end with "left alone: ... the
    icon theme entry", which is an install that adds three files and an uninstall that
    takes two. Discovered while wiping a machine back to virgin, where the leftover is
    the whole point.
    """
    # Walked, not enumerated from a list of sizes: an installer that later renders a
    # different set must not leave artwork behind at a size nobody thought to name. That
    # asymmetry -- install adds three files, uninstall takes two -- is how a "virgin system"
    # test fails for reasons that look like the test's fault.
    out = []
    root = os.path.join(base, "icons", ICON_DIR)
    for dirpath, _dirs, files in os.walk(root) if os.path.isdir(root) else []:
        if os.path.basename(dirpath) != "apps":
            continue
        for name in files:
            if name.startswith(ICON_NAME + "."):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def uninstall_desktop() -> int:
    """Remove the menu entry, its icon, and the CLI link. Not the app, not the config."""
    base = data_home()
    apps = os.path.join(base, "applications")
    removed = []
    # The target this installer would have written, for the ownership test. Uninstall can
    # run from any copy of the app, so "is this file's Exec pointing at where I would have
    # written one" is not the question; MARKER_KEY is, with a filename-plus-Exec fallback
    # for entries written before the marker existed and no running bundle to compare to.
    target, _why = launch_target()
    for name in (DESKTOP_NAME,) + LEGACY_NAMES:
        path = os.path.join(apps, name)
        if not os.path.isfile(path):
            continue
        owner = entry_owner(path, target or "")
        if owner != "ours":
            # Something else holds this filename. On this machine right now, Shelly's entry
            # for the very same application is `glasspad.desktop` -- deleting it by name
            # would take the manager's menu item away while its database still believed it
            # existed. Leave it, and say so, because "I removed your menu entry" and then
            # a missing icon is worse than a leftover.
            print(f"left alone: {path} was not written by this installer")
            print(f"           (no {MARKER_KEY} key; it launches "
                  f"{entry_exec(path) or 'nothing recognisable'})")
            continue
        try:
            os.remove(path)
            removed.append(path)
        except OSError as exc:
            print(f"could not remove {path}: {exc.strerror}")
            return 1
    # The CLI link is part of what install_desktop created, so it is part of what
    # uninstall takes back. uninstall_cli_link() only removes a link that points at
    # this install, so a `glasspad` belonging to a package or another build survives.
    from glasspad import bundle
    bundle.uninstall_cli_link()

    icons = icon_install_paths(base)
    for path in icons:
        try:
            os.remove(path)
            removed.append(path)
        except OSError as exc:
            print(f"could not remove {path}: {exc.strerror}")
            return 1

    if removed:
        for p in removed:
            print(f"removed {p}")
        # No update-desktop-database here either: it would recreate the very MIME
        # caches install declined to create.
    else:
        print(f"no menu entry to remove in {apps}")

    # Same rule as install: refresh a cache that exists, never create one. Removing an
    # icon while a stale cache still lists it is how a menu keeps showing a picture of
    # an application that has gone.
    theme = os.path.join(base, "icons", "hicolor")
    if icons and os.path.exists(os.path.join(theme, "icon-theme.cache")):
        exe = shutil.which("gtk-update-icon-cache")
        if exe:
            try:
                subprocess.run([exe, "-q", "-f", theme], capture_output=True, timeout=30)
                print("refreshed the existing icon cache")
            except (OSError, subprocess.SubprocessError):
                pass

    print("left alone: the app itself and ~/.config/GlassPad -- your layout is yours")
    print("to decide about.")
    return 0
