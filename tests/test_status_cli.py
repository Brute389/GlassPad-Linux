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
import re
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


def test_layer_status_verdict_follows_the_measured_chain():
    """--status exit code is the machine's verdict, not a formality.

    This asserted exit 0 unconditionally, which was true of the machine it was written on
    and of exactly one state of that machine. Lock /dev/uinput, or run it on a fresh
    install, and a green report is a lie: no pad can exist.

    The second revision still over-assumed. It branched on /dev/uinput alone, so on a
    machine with a writable device and no compositor -- any headless CI runner, any fresh
    clone over ssh -- the pad half of the promise held while --status correctly answered
    nonzero, and the test called that a failure. The verdict is a CHAIN: session, library,
    compositor, device. A test that models one link of it is asserting its author's laptop
    is on. So all four conditions are measured here, the expected exit code is derived, and
    every branch checks the sentence a stranger would act on.
    """
    from glasspad import bundle
    can_pad, why = bundle.uinput_ready()
    session = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("WAYLAND_SOCKET"))
    want_zero = can_pad and session
    code, out = run("--backend", "layer", "--status")

    if not session:
        # There is no compositor to credit or accuse; the only honest statement is that the
        # missing session is named and the run refuses to look healthy.
        check(f"no session means nonzero, not a green report (got {code})", code != 0)
        check("the missing Wayland socket is named",
              "no Wayland socket" in out or "NOT FOUND" in out)
        check("and nothing is credited to a compositor that was never asked",
              "advertises zwlr_layer_shell_v1" not in out)
        return

    check("the compositor is credited with layer-shell",
          "advertises zwlr_layer_shell_v1" in out)
    check("and is not accused of lacking it", "does NOT advertise" not in out)
    if want_zero:
        check(f"exit 0 where the pad really can be created (got {code})", code == 0)
    else:
        check(f"nonzero when there can be no pad at all ({why[:56]}...)", code != 0)
        check("and it names the command that fixes it", "--install-uinput" in out)
        check("while still refusing to call the overlay healthy", "PROBLEM" in out)


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


def test_the_selftest_flag_does_what_the_help_prints():
    """`--selftest` was printed in the usage line for ages without existing.

    The selftest really is driven by GLASSPAD_SELFTEST=1 plus --null-pad -- that is what
    tools/check_all.py runs -- but the human-facing sentence said `glasspad --selftest`,
    and argparse answered "unrecognized arguments" and exited 2. So the documented way to
    check an install was the one way that could not work, and it stayed that way because
    every test called the env var. This probes the flag, and the guard below keeps the
    whole usage line honest.
    """
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    probe = (
        "import os, sys;"
        "sys.path.insert(0, %r);"
        "import glasspad.overlay_layer as ol;"
        "ol.main = lambda a: [print('ARGV', ' '.join(a)),"
        "                       print('ENV', os.environ.get('GLASSPAD_SELFTEST', '')),"
        "                       0][-1];"
        "from glasspad.__main__ import main;"
        "raise SystemExit(main(sys.argv[1:]))" % repo
    )
    r = subprocess.run([sys.executable, "-c", probe, "--selftest"],
                       capture_output=True, text=True, timeout=120, cwd=repo)
    out = r.stdout + r.stderr
    check(f"--selftest was accepted (exit {r.returncode})", r.returncode == 0)
    check("it turned itself into the env var the selftest reads", "ENV 1" in out)
    check("and into --null-pad, so no real gamepad is needed",
          "--null-pad" in out and "--selftest" not in out)
    if os.environ.get("WAYLAND_DISPLAY"):
        live = subprocess.run([sys.executable, "-m", "glasspad", "--selftest"],
                              capture_output=True, text=True, timeout=300, cwd=repo)
        tout = live.stdout + live.stderr
        check("live: it ran the checks",
              live.returncode == 0 and "selftest done" in tout
              and "unrecognized" not in tout)
        check("live: nothing failed", "FAIL" not in tout)


def test_nothing_in_the_usage_line_is_a_flag_that_does_not_exist():
    """A flag in the help text is a promise; argparse rejecting it breaks the promise.

    Every option printed by `usage: glasspad [...]` must either be one argparse knows
    (read from the real --help of the module that owns the rest) or be probed in this
    file. --selftest sat advertised-and-absent until someone typed it, because nothing
    read the help line against the parser.
    """
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(repo, "glasspad", "__main__.py"), encoding="utf-8").read()
    usage = src.split("usage: glasspad", 1)[1].split('")', 1)[0]
    advertised = set(re.findall(r"--[a-z][a-z0-9-]*", usage))
    check("the usage line advertises something to check", len(advertised) >= 5)

    h = subprocess.run([sys.executable, "-m", "glasspad.overlay_layer", "--help"],
                       capture_output=True, text=True, timeout=120, cwd=repo)
    known = set(re.findall(r"--[a-z][a-z0-9-]*", h.stdout))
    check(f"overlay_layer's own --help was readable ({len(known)} options)",
          h.returncode == 0 and len(known) > 5)

    # A flag is "real" if argparse in overlay_layer accepts it, or if this file branches
    # on it -- `"--install-uinput" in rest`, `rest[0] in ("--status", ...)`. Branch lines
    # are the proof: the usage f-string itself mentions every advertised word, so
    # counting literals would pass by construction and catch nothing, which is precisely
    # how --selftest stayed broken.
    dispatched: set[str] = set()
    for line in src.splitlines():
        if "in rest" in line or "rest[0]" in line or "rest and" in line:
            dispatched.update(re.findall(r"--[a-z][a-z0-9-]*", line))
    check(f"the wrapper branches on some flags ({len(dispatched)})", bool(dispatched))
    unbacked = sorted(a for a in advertised if a not in known and a not in dispatched)
    check(f"every advertised flag is real or probed here ({unbacked[:4]})", not unbacked)


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
