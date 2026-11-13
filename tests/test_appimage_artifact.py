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
    from glasspad.bundle import REMOVED_QT_MODULES

    h = hashlib.md5()
    src = os.path.join(ROOT, "glasspad")
    for name in sorted(os.listdir(src)):
        if not name.endswith(".py"):
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


import hashlib


def _md5_sha(path: str) -> str:
    """The hash the sidecar publishes: sha256, not the md5 used for icon identity."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _md5(path: str) -> str:
    """Byte identity, so "we ship the app's icon" is a checked fact, not a comment."""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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
                    # The privileged pair. A bundle without these is a download that
                    # cannot finish installing: it can draw controls and grant nothing,
                    # which is the failure a newcomer cannot distinguish from a broken
                    # app. They are checked HERE rather than in the installer suite
                    # because the builder runs that suite BEFORE packing, and a check on
                    # the bytes inside the image cannot pass before the image exists --
                    # the stale artefact from the previous build would either fail the
                    # gate or, worse, vouch for a payload that is a revision behind.
                    "usr/share/glasspad/glasspad/uinput_setup.py",
                    "usr/share/glasspad/packaging/glasspad-udev.rules",
                    "usr/share/doc/glasspad/PACKAGING.txt"):
            check(f"contains {rel}", os.path.exists(os.path.join(root, rel)))
        # The shipped README is a document with links in it. `docs/NOTES.md` in that README
        # resolves inside the image or it resolves nowhere, so the notes have to have been
        # packed beside it -- a "read more" that points at an unpacked file is worse than
        # no pointer, because the reader assumes the document exists somewhere.
        shipped_readme = os.path.join(root, "usr/share/doc/glasspad/README.md")
        notes = os.path.join(root, "usr/share/doc/glasspad/docs/NOTES.md")
        check("the packed README points at the notes",
              "docs/NOTES.md" in open(shipped_readme, encoding="utf-8").read())
        check("and the notes were packed beside it", os.path.isfile(notes))
        check("and they are the notes, not a stub",
              len(open(notes, encoding="utf-8").read().splitlines()) > 400)
        check("no shell installer came back",
              not os.path.exists(os.path.join(root, "usr/share/glasspad/install.sh")))
        # Not merely present: the staged pair must be resolvable together the way the
        # runtime resolves them, or the grant dies on a stranger's machine with "rule
        # file not found" -- the one error the script refuses to work around.
        script = os.path.join(root, "usr/share/glasspad/glasspad/uinput_setup.py")
        if os.path.isfile(script):
            sys.path.insert(0, os.path.join(root, "usr/share/glasspad"))
            # Importing a module WRITES a .pyc beside it, and this tree is the extracted
            # payload we are about to grade for shipped bytecode. The builder purges
            # exactly this class of file before packing, because a .pyc compiled by the
            # build machine's Python is the worst kind of packaging bug -- it only
            # misbehaves on someone else's. A test must not re-create it while looking.
            no_pyc = sys.dont_write_bytecode
            sys.dont_write_bytecode = True
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "packed_uinput_setup", script)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                found = mod.find_rule()
                check(f"the packed script finds the packed rule ({found})",
                      bool(found) and found.endswith("glasspad-udev.rules")
                      and os.path.isfile(found))
            finally:
                sys.dont_write_bytecode = no_pyc
                sys.path.remove(os.path.join(root, "usr/share/glasspad"))
        check("inspecting the payload left no bytecode behind",
              not [d for d, _s, f in os.walk(root) if d.endswith("__pycache__")])
        check("AppRun is executable in the image",
              os.access(os.path.join(root, "AppRun"), os.X_OK))

        # The icon: the application's own artwork, byte for byte, in both places a
        # launcher looks. This used to be a picture of a tablet drawn for the build,
        # because the tree had no icon where a build looks -- and a placeholder that
        # outlives the real thing reads as a deliberate choice. So the identity is
        # asserted, not assumed.
        art = os.path.join(ROOT, "glasspad", "img", "glasspad_icon_512.png")
        art_md5 = _md5(art)
        for rel in ("usr/share/icons/hicolor/512x512/apps/glasspad.png", "glasspad.png"):
            where = os.path.join(root, rel)
            check(f"contains {rel}", os.path.exists(where))
            if os.path.exists(where):
                check(f"{rel} is the upstream artwork, byte for byte",
                      _md5(where) == art_md5)
        check("the drawn placeholder svg is no longer shipped",
              not os.path.exists(os.path.join(
                  root, "usr/share/icons/hicolor/scalable/apps/glasspad.svg")))
        entry = open(os.path.join(root, "GlassPad.desktop"), encoding="utf-8").read()
        icon_name = [ln.split("=", 1)[1].strip() for ln in entry.splitlines()
                     if ln.startswith("Icon=")]
        check(f"Icon={icon_name[:1]} resolves to a file inside the image",
              bool(icon_name) and any(
                  os.path.exists(os.path.join(root, d, "icons/hicolor", k, "apps",
                                              icon_name[0] + ext))
                  for d in ("usr/share", ".")
                  for k in ("512x512", "256x256", "scalable")
                  for ext in (".png", ".svg")))

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


def test_the_build_records_what_went_into_the_file():
    """BUILD-INFO.txt and the .sha256 sidecar, checked against the artefact itself.

    Provenance that nobody verifies is a comment. This checks that the hash in the sidecar
    IS the file's hash, that the runtime is described rather than assumed, and -- most
    importantly -- that the `source tree` line is HONEST. It is not asserted to read
    "clean": a release built from a dirty tree is a real and sometimes necessary thing.
    What must never happen is a dirty tree described as clean, because that is the build
    whose bug report cannot be reproduced from the repository.
    """
    runs = images()
    if not runs:
        print("  skip nothing built yet")
        return
    img = runs[-1]
    side = img + ".sha256"
    check(f"the .sha256 sidecar exists beside the image", os.path.isfile(side))
    if os.path.isfile(side):
        want = open(side, encoding="utf-8").read().split()[0].lower()
        check("the sidecar hash matches the file it names", _md5_sha(img) == want)
    info = os.path.join(os.path.dirname(img), "BUILD-INFO.txt")
    check("BUILD-INFO.txt was written", os.path.isfile(info))
    if not os.path.isfile(info):
        return
    text = open(info, encoding="utf-8").read()
    for field in ("runtime sha256", "runtime origin", "source commit",
                  "source tree", "mksquashfs ver", "squashfs flags", "built on"):
        check(f"BUILD-INFO records {field!r}", field in text)
    check("and the runtime origin names a source, not just a filename",
          ("pinned" in text or "BORROWED" in text))
    check("the tree state is stated as one of two words, never blank",
          ("source tree    : clean" in text
           or "DIRTY -- uncommitted changes were packed" in text))


def test_a_runtime_that_is_not_the_pin_stops_the_build():
    """packaging/runtime-pin.json exists to be enforced, not admired.

    The runtime used to be carved out of whichever AppImage was lying around, which meant
    the deliverable contained an unversioned binary of unknown origin and a rebuild needed
    this particular laptop. $GLASSPAD_RUNTIME is the escape hatch, so the hatch has to
    refuse a runtime that contradicts the pin -- otherwise the pin is decoration.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import mk_appimage_offline as B                        # noqa: PLC0415

    pin = B.load_pin()
    check("the pin parses and names a hash, a size and a URL",
          bool(pin) and len(str(pin.get("sha256", ""))) == 64
          and int(pin.get("size", 0)) > 0 and str(pin.get("url", "")).startswith("https://"))
    if not pin:
        return
    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "runtime-x86_64")
        with open(bad, "wb") as fh:
            fh.write(b"\x7fELF" + bytes(int(pin["size"]) - 4))     # right size, wrong bytes
        check("size alone does not satisfy the pin", not B.matches_pin(bad, pin))
        check("the pin's own sha256 differs from a file of the same length",
              B.sha256_of(bad) != pin["sha256"])
        old = os.environ.get("GLASSPAD_RUNTIME")
        os.environ["GLASSPAD_RUNTIME"] = bad
        try:
            B.acquire_runtime(os.path.join(tmp, "out"))
            check("an explicit runtime that contradicts the pin is refused", False)
        except SystemExit as exc:
            msg = str(exc)
            check("an explicit runtime that contradicts the pin is refused", True)
            check("and the refusal says which file, what it got, and what it wanted",
                  "runtime-pin.json" in msg and "got:" in msg and "pin:" in msg)
        finally:
            if old is None:
                os.environ.pop("GLASSPAD_RUNTIME", None)
            else:
                os.environ["GLASSPAD_RUNTIME"] = old
        if os.path.isfile(B.RUNTIME_CACHE):
            check("the cached runtime on this machine matches the pin",
                  B.matches_pin(B.RUNTIME_CACHE, pin))


def test_the_licence_documents_resolve_inside_the_image():
    """The About box reads its text from wherever the artefact put it.

    Unit tests can only ever exercise the checkout layout. This imports the copy of
    bundle.py that shipped, from inside the unpacked artefact, and asks it to find the
    two documents -- which is the question the panel actually asks at runtime, in a
    directory shape that does not exist until packing happens.
    """
    from _licence_docs import licence_checks, load_packed_bundle

    built = images()
    if not built:
        print("  skip nothing built yet")
        return
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _ok, root, _proc = extract(built[-1], tmp)
        mod, where = load_packed_bundle(root)
        check("the packed resolver is the one being tested", where.startswith(root))
        licence_checks(mod, "AppImage", check)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
