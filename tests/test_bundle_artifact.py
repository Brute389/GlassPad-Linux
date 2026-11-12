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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
