"""Install or remove a desktop menu entry -- from a checkout or from a bundle.

Ported out of the `.run` launcher, which had the only bundle-side way to get a
menu entry: a heredoc writing `org.glasspad.GlassPad.desktop` next to a hardcoded
`Icon=` path pointing INTO the unpacked payload. That was fine for a `.run`, whose
payload stays unpacked in `~/.cache/glasspad` until you clear it. It is actively
wrong for an AppImage, whose payload is a FUSE mount at `/tmp/.mount_*` that stops
existing the moment the app exits -- the menu would show a broken icon within
seconds, which is a worse first run than having no entry at all.

So the icon goes where install.sh puts it, into the user's icon theme, and `Icon=`
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
# The name install.sh writes for a per-user install. Uninstall removes both,
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
    so a checkout has to look like a tree the user holds (install.sh or README.md
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

    if runnable and py and (os.path.isfile(os.path.join(root, "install.sh"))
                            or os.path.isfile(os.path.join(root, "README.md"))):
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
    """An icon file to copy into the theme, svg preferred, or None."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(here, "packaging", "glasspad.svg"),
                 os.path.join(here, "glasspad", "img", "glasspad_icon_512.png"),
                 os.path.join(here, "glasspad", "img", "glasspad_icon.png")):
        if os.path.isfile(cand):
            return cand
    # Inside a bundle the repo layout is gone: the AppDir stages the svg two roots
    # up under usr/share/icons, and the PNGs stay inside the package. Without the
    # icon path here a bundle silently falls back to the raster icon -- the entry
    # still works, it just looks soft on a HiDPI panel, which is exactly the kind
    # of thing nobody notices and everybody sees.
    pkg = os.path.dirname(os.path.abspath(__file__))
    appshare = os.path.dirname(os.path.dirname(pkg))          # .../usr/share
    for cand in (os.path.join(appshare, "icons", "hicolor", "scalable", "apps", "glasspad.svg"),
                 os.path.join(pkg, "..", "packaging", "glasspad.svg"),
                 os.path.join(pkg, "img", "glasspad_icon_512.png"),
                 os.path.join(pkg, "img", "glasspad_icon.png")):
        if os.path.isfile(cand):
            return cand
    return None


def desktop_text(exec_line: str, workdir: str | None = None,
                 status_line: str | None = None) -> str:
    """The .desktop body. Field order mirrors desktop/glasspad.desktop.in.

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
# A second instance would double every input, so never restore duplicates.
X-GNOME-SingleWindow=true
Actions=status;

[Desktop Action status]
Name=Check GlassPad environment
Exec="{status}"
"""


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
    if dry:
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

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    workdir = root if " -m glasspad" in target and os.path.isfile(
        os.path.join(root, "glasspad", "__main__.py")) and not os.environ.get("APPIMAGE") else None

    os.makedirs(apps, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(desktop_text(target, workdir=workdir))
    os.chmod(dest, 0o644)
    print(f"wrote {dest}")
    print(f"  launching: {target}  ({why})")

    if icon_dest:
        os.makedirs(os.path.dirname(icon_dest), exist_ok=True)
        shutil.copyfile(icon, icon_dest)
        print(f"installed the icon as '{ICON_NAME}' -> {icon_dest}")
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
    out = []
    for kind in ("scalable", "256x256", "512x512"):
        for ext in (".svg", ".png"):
            p = os.path.join(base, "icons", "hicolor", kind, "apps", ICON_NAME + ext)
            if os.path.isfile(p):
                out.append(p)
    return out


def uninstall_desktop() -> int:
    """Remove the menu entry, its icon, and the CLI link. Not the app, not the config."""
    base = data_home()
    apps = os.path.join(base, "applications")
    removed = []
    for name in (DESKTOP_NAME,) + LEGACY_NAMES:
        path = os.path.join(apps, name)
        if os.path.isfile(path):
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
