"""Open the built AppImage and check what is actually inside it.

Skips quietly when nothing has been built. A missing artefact is not a failure; a broken
one is, and so is a payload that is not the tree it was supposedly built from -- stale
packaging produced two wrong conclusions during development of the offline builder, once
because a smoke test wrote bytecode into the AppDir and once because the AppDir was older
than the source.

The runtime is borrowed from another AppImage on this machine (see
tools/mk_appimage_offline.py), so these checks also pin the two halves together: the
offset the runtime reports must be where our squashfs actually starts.
"""
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


def images():
    return sorted(glob.glob(os.path.join(ROOT, "dist", "GlassPad-*.AppImage")))


def extract(image, dest):
    """--appimage-extract needs no FUSE, so it works in a container or over ssh."""
    p = subprocess.run([image, "--appimage-extract"], cwd=dest, capture_output=True,
                       text=True, timeout=300)
    root = os.path.join(dest, "squashfs-root")
    return (os.path.isdir(root), root, p)


def _shipped_source_digest():
    """Digest of every module the payloads are supposed to carry.

    Hashing one file (it used to be __main__.py) let a stale payload pass while
    bundle.py inside it was a revision behind -- the label in --status came from the
    image, and the check said fresh. Digest the set that ships, and the check means
    what the name says.
    """
    import hashlib
    sys.path.insert(0, ROOT)
    from glasspad.bundle import QT_BACKEND_MODULES

    h = hashlib.md5()
    src = os.path.join(ROOT, "glasspad")
    for name in sorted(os.listdir(src)):
        if not name.endswith(".py") or name in QT_BACKEND_MODULES:
            continue
        h.update(name.encode())
        with open(os.path.join(src, name), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def _payload_digest(pkg_dir):
    import hashlib

    h = hashlib.md5()
    for name in sorted(os.listdir(pkg_dir)):
        if not name.endswith(".py"):
            continue
        h.update(name.encode())
        with open(os.path.join(pkg_dir, name), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def _stale(root):
    pkg = os.path.join(root, "usr/share/glasspad/glasspad")
    if not os.path.isdir(pkg):
        return True
    return _payload_digest(pkg) != _shipped_source_digest()


def md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def test_offset_is_reported_and_points_at_our_payload():
    runs = images()
    if not runs:
        print("  skip nothing built yet (tools/make_appimage.sh)")
        return
    image = runs[-1]
    p = subprocess.run([image, "--appimage-offset"], capture_output=True, text=True,
                       timeout=60)
    try:
        off = int(p.stdout.strip())
    except ValueError:
        check(f"--appimage-offset returned something usable ({p.stdout.strip()[:40]!r})",
              False)
        return
    check(f"runtime reports a payload offset ({off})", off > 0)
    # Our squashfs starts flush after the runtime, so the offset must be inside the file
    # and the payload must start with the squashfs magic.
    with open(image, "rb") as fh:
        fh.seek(off)
        magic = fh.read(4)
    check(f"the bytes at that offset are a squashfs superblock ({magic!r})",
          magic == b"hsqs")


def test_extracted_contents_match_the_source_tree():
    runs = images()
    if not runs:
        print("  skip nothing built yet")
        return
    with tempfile.TemporaryDirectory() as tmp:
        ok, root, p = extract(runs[-1], tmp)
        check("the image extracted", ok)
        if not ok:
            print("       " + (p.stderr or "").strip()[:200])
            return
        for rel in ("AppRun", "GlassPad.desktop", "usr/bin/glasspad-profile",
                    "usr/share/glasspad/glasspad/overlay_layer.py",
                    "usr/share/doc/glasspad/PACKAGING.txt"):
            check(f"contains {rel}", os.path.exists(os.path.join(root, rel)))
        check("AppRun is executable in the image",
              os.access(os.path.join(root, "AppRun"), os.X_OK))

        stale = [d for d, _sub, f in os.walk(root) if d.endswith("__pycache__")]
        check(f"no __pycache__ shipped ({stale[:1]})", not stale)

        packed = os.path.join(root, "usr/share/glasspad/glasspad/__main__.py")
        src = os.path.join(ROOT, "glasspad", "__main__.py")
        check("the packed package IS the current source (not a stale AppDir)"
              + ("" if not _stale(root) else
                 "  -> rebuild: ./tools/make_appimage.sh"),
              not _stale(root))

        v = shutil.which("desktop-file-validate")
        if v:
            pv = subprocess.run([v, os.path.join(root, "GlassPad.desktop")],
                                capture_output=True, text=True)
            check("the desktop entry validates", pv.returncode == 0)
            if pv.returncode != 0:
                print("       " + (pv.stdout or "").strip()[:200])
        else:
            print("  skip desktop-file-validate not installed")

        # and it runs from the extracted tree
        pr = subprocess.run([os.path.join(root, "AppRun"), "--version"], cwd="/",
                            capture_output=True, text=True, timeout=120)
        check(f"the extracted AppRun runs ({pr.stdout.strip()[:40]})",
              pr.returncode == 0 and "GlassPad" in pr.stdout)


def test_the_image_runs_mounted():
    """The normal user path: double-click equivalent, squashfuse through /dev/fuse."""
    runs = images()
    if not runs:
        print("  skip nothing built yet")
        return
    if not os.path.exists("/dev/fuse"):
        print("  skip no /dev/fuse here; the image still runs via --appimage-extract-and-run")
        return
    p = subprocess.run([runs[-1], "--version"], capture_output=True, text=True,
                       timeout=180)
    out = (p.stdout or "") + (p.stderr or "")
    check(f"mounted run reports the version (rc={p.returncode})",
          p.returncode == 0 and "GlassPad" in out)
    check("no traceback on the mounted path", "Traceback" not in out)


def test_extract_and_run_path():
    runs = images()
    if not runs:
        print("  skip nothing built yet")
        return
    p = subprocess.run([runs[-1], "--appimage-extract-and-run", "--version"],
                       capture_output=True, text=True, timeout=180)
    check(f"extract-and-run works (the documented fallback: {p.stdout.strip()[:40]})",
          p.returncode == 0 and "GlassPad" in p.stdout)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
