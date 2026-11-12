"""--status must describe the backend you actually run, and must fail honestly.

Two bugs this pins down, both found on a machine where the overlay works:

  * report_status() talked about XShape and pass-through for the *layer* backend, which
    uses neither -- a confident report about a different program.
  * asked about layer-shell without loading the library RTLD_GLOBAL first, GTK answers
    is_supported() == False forever. Measured here: same process, same display, False
    before the preload and True after. So the diagnostic has to reproduce the runtime,
    or it tells you your compositor has no layer-shell on the exact machine where the
    overlay is up.
"""
import ast
import os
import subprocess
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)   # in-suite imports (this file shells out for the rest)
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


def run(*args, env_extra=None, drop=()):
    env = dict(os.environ)
    env.update(env_extra or {})
    for k in drop:
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-m", "glasspad", *args], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def test_layer_status_is_about_the_layer_backend():
    code, out = run("--backend", "layer", "--status")
    check(f"no traceback (got rc={code})", "Traceback" not in out)
    check("says where libgtk4-layer-shell came from", "layer-shell lib :" in out)
    check("reports the virtual gamepad device", "/dev/uinput" in out)
    check("reports the profile store", "profiles        :" in out)
    check("and does not talk about XShape, which this backend never touches",
          "XShape" not in out)
    check("the preload is reported before the compositor answer",
          out.find("preload") != -1 and out.find("compositor") != -1
          and out.find("preload") < out.find("compositor"))


def test_layer_status_passes_on_a_working_session():
    if not os.environ.get("WAYLAND_DISPLAY"):
        print("  skip no Wayland session in this environment")
        return
    code, out = run("--backend", "layer", "--status")
    check(f"exit 0 where the overlay demonstrably runs (got {code})", code == 0)
    check("the compositor is credited with layer-shell",
          "advertises zwlr_layer_shell_v1" in out)
    check("and is not accused of lacking it", "does NOT advertise" not in out)


def test_layer_status_fails_loudly_without_a_display():
    code, out = run("--backend", "layer", "--status",
                    drop=("WAYLAND_DISPLAY", "WAYLAND_SOCKET"))
    check(f"no display means a nonzero exit, not a green report (got {code})", code != 0)
    check("and it says why", "no Wayland socket" in out or "NOT FOUND" in out)
    check("still no traceback", "Traceback" not in out)


def test_override_that_points_nowhere_is_reported_not_hidden():
    code, out = run("--backend", "layer", "--status",
                    env_extra={"GLASSPAD_LAYER_SHELL": "/definitely/not/here.so.0"})
    check("a bad GLASSPAD_LAYER_SHELL is named as the problem", "does not exist" in out)
    check("and the run fails rather than finding something else", code != 0)
    check("no traceback", "Traceback" not in out)


def test_report_status_is_the_layer_one_now():
    """One backend, so report_status() must not quietly still describe the other."""
    from glasspad import __main__ as entry
    src = open(entry.__file__).read()
    fn = [n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "report_status"]
    check("report_status() still exists", bool(fn))
    body = ast.get_source_segment(src, fn[0]) or ""
    check("it reports the layer stack", "libgtk4-layer-shell" in body)
    check("it does not report a Qt session", "QT_QPA_PLATFORM" not in body)


def test_cli_flags_never_start_the_overlay():
    """`--version` once mapped a fullscreen transparent surface instead of printing.

    The flag lived in the parser that was deleted, so nothing matched it and it fell
    through to run_layer(). A blank overlay is not an error message -- it is a silent
    hang with your desktop underneath, invisible to whoever typed the flag. Every
    flag this file owns is run against the real CLI with a timeout, with the overlay's
    entry point patched to raise, so a path that would start it fails loudly.
    """
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    probe = (
        "import sys;"
        "sys.path.insert(0, %r);"
        "import glasspad.overlay_layer as ol;"
        "ol.main = lambda a: (_ for _ in ()).throw(AssertionError('overlay started'));"
        "from glasspad.__main__ import main;"
        "raise SystemExit(main(sys.argv[1:]))" % repo
    )
    cases = [("--version", "GlassPad"), ("--help", "usage:"), ("--status", "session"),
             (["--backend", "bogus"], "unknown backend"),
             (["--backend", "layer", "--status"], "session")]
    for flag, expect in cases:
        args = flag if isinstance(flag, list) else [flag]
        try:
            r = subprocess.run([sys.executable, "-c", probe] + args,
                               capture_output=True, text=True, timeout=90, cwd=repo)
        except subprocess.TimeoutExpired:
            check(f"{' '.join(args)} returned instead of hanging", False)
            continue
        out = r.stdout + r.stderr
        check(f"{' '.join(args)} did not start the overlay", "overlay started" not in out)
        if expect:
            check(f"{' '.join(args)} said {expect!r}", expect in out)

    # the removed backend explains itself rather than tracing back
    r = subprocess.run([sys.executable, "-c", probe, "--backend", "qt"],
                       capture_output=True, text=True, timeout=90, cwd=repo)
    out = r.stdout + r.stderr
    check("--backend qt explains the removal",
              "no 'qt' backend" in out and "layer backend is the only one" in out)
    check("--backend qt does not trace back",
          "Traceback" not in out and "No module named" not in out)
    check("--backend qt is a usage error", r.returncode == 2)


def test_take_backend_is_a_loop_not_a_trap():
    """The stripper runs on every invocation, so a stuck index hangs the whole CLI.

    It actually did: the non-flag branch appended without advancing, and `--version`
    spun forever. Asserted directly on argv shapes, cheaply, because the infinite
    version of this is invisible in a GUI that otherwise looks fine.
    """
    cases = [
        ([], []),
        (["--status"], ["--status"]),
        (["--backend", "layer", "--edit"], ["--edit"]),
        (["--backend=layer", "--profile", "fighter"], ["--profile", "fighter"]),
        (["--edit", "--backend", "layer"], ["--edit"]),
        (["--layout", "x.json", "--backend=layer", "--edit"], ["--layout", "x.json", "--edit"]),
    ]
    from glasspad import __main__ as entry
    for argv, want in cases:
        try:
            got = entry.take_backend(list(argv))
        except Exception as exc:                     # noqa: BLE001 - report, don't crash the suite
            check(f"{argv} -> {want}", False)
            continue
        check(f"{argv} -> {want}", got == want)




if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
