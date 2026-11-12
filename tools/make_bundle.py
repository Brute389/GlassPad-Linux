#!/usr/bin/env python3
"""Build dist/glasspad-<version>-<arch>.run -- one executable file, no installer.

Why this exists when the user asked for an AppImage: an AppImage needs three things this
machine does not have and cannot fetch (no network) -- squashfs-tools to build the payload,
the AppImage runtime ELF to prepend, and appimagetool to glue them. tools/make_appimage.sh
builds the AppDir and stops honestly at that step.

What a .run file gives you is the part of AppImage that actually mattered here: one file,
chmod +x, copy it to another machine, run it. It does NOT bundle GTK or Python -- see the
long comment in tools/make_appimage.sh for why bundling libgtk4-layer-shell is a bad idea
rather than an unfinished one -- so the launcher checks for the three host prerequisites and
says what to install in the words of that distro's package manager.

The tool verifies its own output before it claims success: a build that reports a file
nobody executed is how you ship a payload that cannot start.

    tools/make_bundle.py                 build and verify
    tools/make_bundle.py --no-verify     build only
    dist/glasspad-1.1.0-x86_64.run --status
    dist/glasspad-1.1.0-x86_64.run install-desktop
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LAUNCHER = """#!/bin/sh
# GlassPad ${VERSION} (${ARCH}) -- self-extracting bundle, no installer.
# Everything after this script is a ZIP archive; the unpacked tree is reused, so
# repeated runs cost nothing. Written by tools/make_bundle.py -- edit that, not this.
#
# Payload hash: ${STAMP}
set -e

self=$0
case $self in
  /*) ;;
  *) self=`pwd`/${self} ;;
esac
# readlink -f follows symlinks, which matters: a launcher in ~/.local/bin pointing here
# must unpack the payload next to the real file, not wherever the link lives.
if command -v readlink >/dev/null 2>&1; then
  real=`readlink -f "$self" 2>/dev/null || echo "$self"`
else
  real=$self
fi

cache=${XDG_CACHE_HOME:-$HOME/.cache}
dest="$cache/glasspad/${STAMP}"

say() {
  printf '%s\\n' "$*" >&2
}

# The three host prerequisites. Deliberately not bundled: a libgtk4-layer-shell that does
# not match the host's libwayland/GTK produces a blank overlay instead of an error, and no
# package can carry the compositor's layer-shell global or a /dev/uinput permission.
need_python() {
  say "GlassPad needs Python 3 with GObject introspection, and python3 is not on PATH."
  say ""
  say "    Arch / CachyOS   sudo pacman -S python"
  say "    Debian / Ubuntu  sudo apt install python3"
  exit 3
}

need_gi() {
  say "GlassPad needs the GTK 4 bindings for Python (PyGObject)."
  say ""
  say "    Arch / CachyOS   sudo pacman -S python-gobject gtk4"
  say "    Debian / Ubuntu  sudo apt install python3-gi gir1.2-gtk-4.0"
  say "    Fedora           sudo dnf install python3-gobject gtk4"
  say ""
  say "Check with: $0 status"
  exit 3
}

if ! command -v python3 >/dev/null 2>&1; then need_python; fi
# Ask for the Gtk 4 typelib, not just PyGObject: `import gi` succeeds on a stock system
# that has no GTK 4 at all, and then the real failure arrives as a GIRepository error
# three steps later. Fail here, with the package names.
if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0")' >/dev/null 2>&1; then
  need_gi
fi

if [ ! -f "$dest/.unpacked" ]; then
  # Unpack into a scratch directory and move it into place. Two reasons, both learned the
  # hard way: `unzip` without -o *prompts* before overwriting, so a half-extracted tree
  # from a killed run made every later run die on stdin ("NULL (EOF), treating as None");
  # and a tree that is half-present but has no marker would otherwise be reused as if it
  # were complete.
  tmp="$dest.tmp.$$"
  rm -rf "$tmp" && mkdir -p "$tmp" || { say "cannot create $tmp"; exit 1; }
  # Keep the tool's stderr so a real failure can be shown, and judge by exit status: the
  # "extra bytes at beginning" warning unzip prints for a prefix-appended archive is
  # expected noise, but suppressing all stderr would suppress the message you need too.
  log="$tmp/.unpack-log"
  # python3 first, and not by accident: Info-ZIP treats the launcher bytes as a warning
  # and exits 1 for EVERY prefix-appended archive, so its exit status cannot be trusted
  # here (measured: clean extraction, nonzero status, every run). Python's zipfile reads
  # the central directory from the tail and handles the prefix -- it is how zipapp
  # self-extracting files work. unzip stays as a fallback, and only status > 1 counts.
  ok=0
  python3 -m zipfile -e "$self" "$tmp" 2>"$log" || ok=$?
  if [ "$ok" != 0 ] && command -v unzip >/dev/null 2>&1; then
    unzip -q -o "$self" -d "$tmp" 2>>"$log" || { w=$?; [ "$w" -le 1 ] || ok=$w; }
  fi
  # The status of an unpacker is not evidence that the payload arrived. Ask the payload.
  if [ ! -f "$tmp/app/glasspad/__main__.py" ]; then
    say "could not unpack $self -- no usable payload in $tmp"
    if [ -s "$log" ]; then cat "$log" >&2; fi
    rm -rf "$tmp"
    exit 1
  fi
  rm -f "$log"
  rm -rf "$dest" && mv "$tmp" "$dest" || { say "cannot move into $dest"; exit 1; }
  : > "$dest/.unpacked"
fi

export GLASSPAD_BUNDLE="$dest"
export GLASSPAD_BUNDLE_LIB="$dest/lib"
export PYTHONPATH="$dest/app${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$dest/app/tools:$PATH"

first=${1:-}
if [ "$first" = "install-desktop" ]; then
  # A menu entry that points at this file, wherever the user moved it to. Written into
  # the user dir so nothing needs root, and rewritten on every install so a moved file
  # self-heals instead of leaving a dead entry behind.
  dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  mkdir -p "$dir"
  # Exactly one Exec, no %F: glasspad's parser accepts no file or URL argument, so a
  # field code a launcher appends becomes an unrecognized argument (exit 2).
  cat > "$dir/org.glasspad.GlassPad.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=GlassPad (bundled)
GenericName=Touch gamepad overlay
Comment=Turn the touchscreen into a virtual Xbox gamepad
Exec="$real"
Terminal=false
Icon=$dest/app/share/icons/glasspad.svg
Categories=Game;
Keywords=gamepad;controller;touch;xbox;overlay;
StartupNotify=false
X-KDE-StartupNotify=false
X-GNOME-SingleWindow=true
DESKTOP
  say "wrote $dir/org.glasspad.GlassPad.desktop"
  say "(Icon= points into the unpacked payload, so the menu shows the real icon"
  say " without installing anything into an icon theme.)"
  exit 0
fi
if [ "$first" = "uninstall-desktop" ]; then
  rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/org.glasspad.GlassPad.desktop"
  say "removed the menu entry (the unpacked payload stays in $cache/glasspad)"
  exit 0
fi
if [ "$first" = "status" ]; then
  exec python3 -m glasspad --backend layer --status
fi

exec python3 -m glasspad "$@"
"""


def sources() -> list[tuple[str, str]]:
    """(archive path, absolute source path) for everything the payload carries."""
    out = []
    pkg = os.path.join(ROOT, "glasspad")
    for dirpath, dirnames, filenames in os.walk(pkg):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if fn.endswith((".pyc", ".pyo")) or fn.endswith(".orig"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT)
            out.append((os.path.join("app", rel), full))
    # LICENSE and the notices are conditions of shipping, not nice-to-haves: the MIT
    # notice must accompany the copy, and the About box renders both files. Skipping a
    # missing extra silently is how a release goes out with no licence attached and
    # nobody finds out until somebody asks for one, so these abort the build.
    required = ("README.md", "LICENSE", os.path.join("licenses", "THIRD-PARTY-NOTICES.txt"))
    for extra in ("tools/glasspad-profile",) + required:
        full = os.path.join(ROOT, extra)
        if os.path.isfile(full):
            out.append((os.path.join("app", extra), full))
        elif extra in required:
            raise SystemExit(f"make_bundle: {extra} is missing from {ROOT}; refusing "
                             "to build a payload without the licence texts")
    icon = os.path.join(ROOT, "packaging", "glasspad.svg")
    if os.path.isfile(icon):
        out.append(("app/share/icons/glasspad.svg", icon))
    return out


def build(version: str, arch: str) -> str:
    stamp = version
    # STAMP is deliberately NOT substituted here: it is filled in once, below, from the
    # payload hash. Blanking it in the same pass as VERSION would mean substituting the
    # version string everywhere it appears -- including in the banner comment.
    launcher_vars = {"VERSION": version, "ARCH": arch}
    header = ""
    for line in LAUNCHER.splitlines(keepends=True):
        for k, v in launcher_vars.items():
            line = line.replace("${" + k + "}", v)
        header += line

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arc, full in sources():
            z.write(full, arc)
        # A marker so a stray zip in the wrong place is obvious in `unzip -l`.
        z.writestr("app/GLASSPAD-BUNDLE.txt",
                   f"GlassPad {version} ({arch})\n"
                   f"built by tools/make_bundle.py from {ROOT}\n"
                   f"python {sys.version.split()[0]}\n"
                   "Run it:  ./glasspad-%s.run status\n" % version)
    payload = buf.getvalue()

    # The stamp is the payload hash, so rebuilding changed sources cannot reuse a stale
    # unpacked tree. Version alone would: `make_bundle` twice on the same version with
    # edited code would keep running the first copy forever.
    digest = hashlib.sha256(payload).hexdigest()[:10]
    stamp_full = f"{version}-{digest}"
    header = header.replace("${STAMP}", stamp_full)
    if "${STAMP}" in header:
        raise SystemExit("the ${STAMP} placeholder survived substitution")

    # Parse the launcher before anything ships. A shell syntax error in a heredoc that
    # writes a heredoc is easy to introduce and only shows up on the target machine, at
    # the worst possible moment -- and `sh -n` costs nothing.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as tf:
        tf.write(header)
        probe = tf.name
    try:
        p = subprocess.run(["sh", "-n", probe], capture_output=True, text=True)
        if p.returncode != 0:
            raise SystemExit("generated launcher does not parse:\n" + p.stderr)
    finally:
        os.unlink(probe)

    out = os.path.join(ROOT, "dist", f"glasspad-{version}-{arch}.run")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as fh:
        fh.write(header.encode())
        fh.write(payload)
    os.chmod(out, 0o755)
    return out


def verify(path: str, version: str) -> list[str]:
    """Run the artefact. Returns a list of problems; empty means ship it."""
    problems = []
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "GLASSPAD_BUNDLE", "GLASSPAD_BUNDLE_LIB")}

    def sh(args, expect_zero=True, note=""):
        p = subprocess.run([path, *args], cwd="/", env=env, capture_output=True,
                           text=True, timeout=180)
        out = (p.stdout or "") + (p.stderr or "")
        if expect_zero and p.returncode != 0:
            problems.append(f"{note or args}: exit {p.returncode}\n{out.strip()[:500]}")
        if "Traceback" in out:
            problems.append(f"{note or args}: traceback\n{out.strip()[:500]}")
        return p.returncode, out

    code, out = sh(["--version"], note="--version")
    if version not in out:
        problems.append(f"--version did not report {version}: {out.strip()[:200]}")

    code, out = sh(["status"], expect_zero=bool(os.environ.get("WAYLAND_DISPLAY")),
                   note="status")
    if "layer-shell lib" not in out:
        problems.append("status did not report the layer-shell library")

    # A second run must not re-unpack (cheap check: the marker survives and the run works)
    _, out = sh(["--version"], note="second --version")

    # The shipped profile CLI has to work from the payload, not from the repo
    code, out = sh(["--help"], note="--help")

    # install-desktop writes into the user's home, so it gets tested in a sandboxed one.
    # It had a bug where \${XDG_DATA_HOME} reached the shell escaped and stayed literal,
    # which would have created a directory named "${XDG_DATA_HOME:-...}" in $HOME and put
    # the menu entry inside it -- invisible to the menu, and nobody would have connected
    # the two. This block is the only reason that was catchable.
    import shutil
    import tempfile
    home = tempfile.mkdtemp(prefix="glasspad-home-")
    xdg = tempfile.mkdtemp(prefix="glasspad-xdg-")
    try:
        env2 = dict(env, HOME=home, XDG_DATA_HOME=xdg)
        p = subprocess.run([path, "install-desktop"], cwd="/", env=env2,
                           capture_output=True, text=True, timeout=120)
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode != 0:
            problems.append(f"install-desktop: exit {p.returncode}\n{out.strip()[:300]}")
        entries = os.path.join(xdg, "applications")
        deskt = os.path.join(entries, "org.glasspad.GlassPad.desktop")
        if not os.path.isfile(deskt):
            problems.append("install-desktop wrote no menu entry where it said it would")
        else:
            body = open(deskt).read()
            n_exec = sum(1 for ln in body.splitlines() if ln.startswith("Exec="))
            if n_exec != 1:
                problems.append(f"menu entry has {n_exec} Exec lines, expected exactly 1")
            if os.path.abspath(path) not in body:
                problems.append("menu entry does not point at this file")
            if "%F" in body or "%u" in body:
                problems.append("menu entry has field codes the parser would reject")
            if "${" in body:
                problems.append(f"menu entry contains unsubstituted shell text: {body!r}")
            v = shutil.which("desktop-file-validate")
            if v:
                pv = subprocess.run([v, deskt], capture_output=True, text=True)
                if pv.returncode != 0:
                    problems.append("desktop-file-validate: " + (pv.stdout or "").strip()[:300])
        # Nothing should have been created under HOME by a well-behaved installer.
        stray = [d for d in os.listdir(home) if "XDG" in d or d.startswith("${")]
        if stray:
            problems.append(f"install-desktop created junk under $HOME: {stray}")
        # and the entry must survive a second install (idempotent, not duplicated)
        subprocess.run([path, "install-desktop"], cwd="/", env=env2,
                       capture_output=True, text=True, timeout=120)
        again = open(deskt).read() if os.path.isfile(deskt) else ""
        if again.count("[Desktop Entry]") != 1:
            problems.append("re-installing did not leave exactly one entry")
        p = subprocess.run([path, "uninstall-desktop"], cwd="/", env=env2,
                           capture_output=True, text=True, timeout=120)
        if os.path.exists(deskt):
            problems.append("uninstall-desktop left the entry behind")
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(xdg, ignore_errors=True)
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)

    sys.path.insert(0, ROOT)
    from glasspad import __version__ as version

    arch = os.uname().machine
    path = build(version, arch)
    size = os.path.getsize(path)
    print(f"  wrote {os.path.relpath(path, ROOT)}  "
          f"({size / 1024:.0f} KiB, sha256 "
          f"{hashlib.sha256(open(path, 'rb').read()).hexdigest()[:12]})")

    if args.no_verify:
        print("  --no-verify: nobody has executed this file")
        return 0

    print("  verifying by running it (cwd=/, no PYTHONPATH in the environment)")
    problems = verify(path, version)
    for p in problems:
        print(f"    FAIL {p}")
    if problems:
        return 1
    print("    the artefact ran: --version, status, and a repeat run all clean")
    print(f"  copy it anywhere:  ./glasspad-{version}-{arch}.run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
