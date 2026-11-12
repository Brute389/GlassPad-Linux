"""Run the built artefact, if one exists.

tools/make_bundle.py already verifies what it writes; this is the regression net for
edits to the launcher template afterwards. Skips quietly when nothing has been built --
a missing artefact is not a failed test, but a broken artefact is.

Bugs this would have caught, all found in the launcher template:

  * `\\${XDG_DATA_HOME...}` reached the shell escaped, so install-desktop tried to mkdir
    a directory literally named "${XDG_DATA_HOME:-" instead of using the variable.
  * unzip exits 1 for prefix-appended archives (a warning), which read as "extraction
    failed" on every run.
  * extraction without -o prompts before overwriting, so a half-unpacked tree from an
    interrupted run made every subsequent run die on stdin.
"""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


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


def artifacts():
    return sorted(glob.glob(os.path.join(ROOT, "dist", "glasspad-*.run")))


def clean_env():
    # No PYTHONPATH: the launcher has to establish the payload's import path itself, and
    # inheriting ours would test nothing about that.
    keep = ("HOME", "PATH", "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "DISPLAY",
            "XDG_CURRENT_DESKTOP")
    return {k: os.environ[k] for k in keep if k in os.environ}


def test_artefact_runs_from_an_empty_environment():
    runs = artifacts()
    if not runs:
        print("  skip nothing built yet (tools/make_bundle.py)")
        return
    path = runs[-1]
    p = subprocess.run([path, "--version"], cwd="/", env=clean_env(),
                       capture_output=True, text=True, timeout=180)
    out = (p.stdout or "") + (p.stderr or "")
    check(f"{os.path.basename(path)} --version exited 0", p.returncode == 0)
    check(f"and identified itself ({out.strip()[:40]})", "GlassPad" in out)
    check("no traceback", "Traceback" not in out)


def test_artefact_reports_the_layer_backend():
    runs = artifacts()
    if not runs or not os.environ.get("WAYLAND_DISPLAY"):
        print("  skip needs an artefact and a Wayland session")
        return
    p = subprocess.run([runs[-1], "status"], cwd="/", env=clean_env(),
                       capture_output=True, text=True, timeout=180)
    out = (p.stdout or "") + (p.stderr or "")
    check(f"status exits 0 on a machine where the overlay works (got {p.returncode})",
          p.returncode == 0)
    check("it found the layer-shell library", "layer-shell lib :" in out)
    check("it credited the compositor with layer-shell",
          "advertises zwlr_layer_shell_v1" in out)
    check("and it did the preload that makes that answer truthful",
          "preload         : ok" in out)


def test_the_payload_is_the_current_source():
    """A .run built from an older tree is a bug that only bites after you ship it.

    The version string is no use here: two builds of the same version differ in code and
    agree in version, which is exactly how a stale payload survives a release.
    """
    runs = artifacts()
    if not runs:
        print("  skip nothing built yet")
        return
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["unzip", "-q", "-o", runs[-1], "-d", tmp], capture_output=True)
        pkg = os.path.join(tmp, "app", "glasspad")
        if not os.path.isdir(pkg):
            check("payload layout is app/glasspad", False)
            return
        ok = _payload_digest(pkg) == _shipped_source_digest()
        check("every shipped module matches the source tree"
              + ("" if ok else "  -> rebuild: python3 tools/make_bundle.py"), ok)
        qt = [n for n in os.listdir(pkg) if n.endswith(".py")
              and n in ("window.py", "widgets.py", "passthrough.py", "layout.py")]
        check(f"and the Qt backend is not in the payload ({qt})", not qt)


def test_the_qt_backend_explains_itself_instead_of_tracebacking():
    """--backend qt inside a bundle: a clean sentence, not a ModuleNotFoundError.

    The Qt modules are excluded from the payload on purpose. What matters is that asking
    for them says which of the two reasons applies, because "No module named
    glasspad.window" reads like a broken install.
    """
    runs = artifacts()
    if not runs:
        print("  skip nothing built yet")
        return
    env = clean_env()
    env["GLASSPAD_BUNDLE"] = "1"        # irrelevant to the message; keeps env honest
    p = subprocess.run([runs[-1], "--backend", "qt"], cwd="/", env=env,
                       capture_output=True, text=True, timeout=180)
    out = (p.stdout or "") + (p.stderr or "")
    check("it refuses rather than starting something half-built", p.returncode != 0)
    check("and says the layer backend is what ships",
          "layer backend" in out.lower())
    check("no traceback", "Traceback" not in out)
    check("does not blame PyQt6 for a packaging decision",
          "PyQt6" not in out)


def test_the_launcher_header_is_plain_posix_sh():
    """Parse the script half of the artefact, not the zip half.

    `sh -n whole-file` is not a thing you can do: it reads the appended archive as shell
    text and dies on the first non-UTF-8 byte (measured -- it took the test's own stderr
    decoder down with it). So split at the first local-file header and check the script.
    """
    runs = artifacts()
    if not runs:
        print("  skip nothing built yet")
        return
    raw = open(runs[-1], "rb").read()
    cut = raw.find(b"PK\x03\x04")
    check("there is a zip payload after the script", cut > 0)
    if cut <= 0:
        return
    header = raw[:cut]
    check(f"shebang is /bin/sh, not bash ({header.splitlines()[0]!r})",
          header.startswith(b"#!/bin/sh\n"))
    import tempfile
    with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as tf:
        tf.write(header)
        probe = tf.name
    try:
        p = subprocess.run(["sh", "-n", probe], capture_output=True)
        check("sh -n parses the launcher header", p.returncode == 0)
        if p.returncode != 0:
            print("       " + (p.stderr or b"").decode("utf-8", "replace")[:300])
    finally:
        os.unlink(probe)
    for bashism in (b"[[ ", b"<<<", b"=((", b"function ", b"readarray"):
        check(f"no bashism {bashism!r} in the header", bashism not in header)


def test_the_licence_documents_resolve_inside_the_payload():
    """The About box reads its text from wherever the artefact put it.

    Unit tests can only ever exercise the checkout layout. This imports the copy of
    bundle.py that shipped, from inside the unpacked artefact, and asks it to find the
    two documents -- which is the question the panel actually asks at runtime, in a
    directory shape that does not exist until packing happens.
    """
    from _licence_docs import licence_checks, load_packed_bundle

    built = artifacts()
    if not built:
        print("  skip nothing built yet")
        return
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["unzip", "-q", "-o", built[-1], "-d", tmp], capture_output=True)
        mod, where = load_packed_bundle(tmp)
        check("the packed resolver is the one being tested", where.startswith(tmp))
        licence_checks(mod, ".run", check)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
