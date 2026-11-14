"""No display is a supported situation, and it must say so instead of raising.

Run: python3 tests/test_no_display.py        (no pytest; runs headless ON PURPOSE)

Two virgin-run walkthroughs on this machine ended in the same traceback:

    File "glasspad/overlay_layer.py", in screen_size
        model = Gdk.Display.get_default().get_monitors()
    AttributeError: 'NoneType' object has no attribute 'get_monitors'

`Gdk.Display.get_default()` returns None when nothing can be opened -- over ssh, from a
service unit, or from a foreign user's account whose XDG_RUNTIME_DIR does not contain the
compositor's socket. That is a situation with an explanation and a next step, and the app had
neither: it crashed four frames below the entry point, in a build where the user has no
terminal to read it in.

Every test here scrubs the session variables, because a suite that only passes on a machine
with a desktop cannot test the absence of one.
"""
import os
import subprocess
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "glasspad")
FAILS = []

# The environment of a headless session: no Wayland, no X11, no backend preference that
# might make GDK pick something and fail differently.
HEADLESS = {k: v for k, v in os.environ.items()
            if k not in ("WAYLAND_DISPLAY", "WAYLAND_SOCKET", "DISPLAY", "GDK_BACKEND")}
SKIPS = []

# CI convention in this repository: a machine that does not carry libgtk4-layer-shell runs a
# NARROWER suite and says so, rather than failing or quietly passing. Everything that only
# needs Python (the headless subprocess runs) stays mandatory everywhere.
try:
    from glasspad import overlay_layer as _ol
    HAVE_OL = True
except Exception as exc:                                     # noqa: BLE001
    _ol, HAVE_OL = None, False
    _OL_WHY = f"{type(exc).__name__}: {exc}"


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


def run_headless(args, extra_env=None, timeout=90):
    env = dict(HEADLESS)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["COLUMNS"] = "100"
    env.update(extra_env or {})
    p = subprocess.run([sys.executable, "-m", "glasspad"] + args, cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _overlay_module():
    return _ol


def skip(what):
    print(f"  skip {what}" + (f"   ({_OL_WHY})" if not HAVE_OL else ""))
    SKIPS.append(what)


def test_screen_size_raises_the_named_error_not_an_attribute_error():
    if not HAVE_OL:
        return skip("NoDisplay guard needs the overlay module (and its typelibs)")
    ol = _overlay_module()
    ol.Gdk = types.SimpleNamespace(Display=types.SimpleNamespace(get_default=lambda: None))
    try:
        ol.screen_size()
        check("raises instead of returning a made-up size", False)
    except ol.NoDisplay as exc:
        check("raises NoDisplay", True)
        check(f"    and names the environment in the message: {exc}",
              "WAYLAND_DISPLAY" in str(exc) and "XDG_RUNTIME_DIR" in str(exc))
    except AttributeError as exc:                       # the bug we are fixing
        check(f"regression: AttributeError again ({exc})", False)


def test_screen_size_still_answers_when_a_display_exists():
    """The None-guard must not swallow the two normal paths."""
    if not HAVE_OL:
        return skip("monitor paths need the overlay module")
    ol = _overlay_module()

    class Geo:
        def __init__(self, w, h):
            self.width, self.height = w, h

    class Surf:
        def __init__(self, geo):
            self._g = geo

        def get_geometry(self):
            return self._g

    class Mon:
        def __init__(self, n, geo):
            self._n, self._g = n, geo

        def get_n_items(self):
            return self._n

        def get_item(self, _i):
            return Surf(self._g)

    class Disp:
        def __init__(self, monitors):
            self._m = monitors

        def get_monitors(self):
            return self._m

    ol.Gdk = types.SimpleNamespace(Display=types.SimpleNamespace(
        get_default=lambda: Disp(Mon(1, Geo(1707, 1067)))))
    check("a real monitor is used", ol.screen_size() == (1707, 1067))
    ol.Gdk = types.SimpleNamespace(Display=types.SimpleNamespace(
        get_default=lambda: Disp(Mon(0, None))))
    check("a display with no monitors falls back rather than crashing",
          ol.screen_size() == (1920, 1200))
    ol.Gdk = types.SimpleNamespace(Display=types.SimpleNamespace(
        get_default=lambda: Disp(None)))
    check("and a display whose monitor model is None falls back too",
          ol.screen_size() == (1920, 1200))


def test_headless_run_reports_and_exits_without_a_traceback():
    code, out = run_headless(["--null-pad"])
    if HAVE_OL:
        check(f"exit code is EXIT_NO_DISPLAY (got {code})",
              code == _overlay_module().EXIT_NO_DISPLAY)
    else:
        skip("EXIT_NO_DISPLAY constant needs the overlay module")
        check(f"at least it is a reported failure, not a crash (exit {code})",
              code in (4, 5))
    check("no traceback in the output", "Traceback" not in out)
    check("    and no AttributeError either", "AttributeError" not in out)
    if not HAVE_OL:
        # With no libgtk4-layer-shell the app stops one step earlier and reports the missing
        # library, which is the correct answer for that machine -- asserting the no-display
        # wording here would fail an honest build. The one claim that survives every
        # environment is the one checked above: it did not crash.
        return skip("the nodisplay wording needs the library to get that far")
    check("says there is nothing to draw on", "nothing to draw on" in out)
    check("names the variable that is missing", "WAYLAND_DISPLAY is not set" in out)
    check("offers the command that would work from a session", "glasspad --status" in out)


def test_headless_run_with_a_wrong_runtime_dir_names_the_socket_it_tried():
    """The failure that cost real time to diagnose: the socket is elsewhere."""
    code, out = run_headless(["--null-pad"], {"WAYLAND_DISPLAY": "wayland-9",
                                              "XDG_RUNTIME_DIR": "/run/user/4321"})
    check(f"same verdict (got {code})",
          not HAVE_OL or code == _overlay_module().EXIT_NO_DISPLAY)
    if not HAVE_OL:
        return skip("socket-path wording needs the library")
    check("names the exact path GDK looked at", "/run/user/4321/wayland-9" in out)
    check("and says it is not there", "does not exist" in out)


def test_the_no_display_notice_does_not_send_you_installing_a_package():
    """The library is fine here. Appending the install hint would be confidently wrong."""
    if not HAVE_OL:
        return skip("on a machine without the library, printing that hint is CORRECT")
    from glasspad import bundle
    marker = bundle.dependency_hint().strip().splitlines()[0]
    code, out = run_headless(["--null-pad"])
    check("the install hint is absent", marker not in out)
    check("    (marker: %r)" % marker[:48], bool(marker))


def test_help_version_and_status_still_work_with_no_display():
    """--help and --version must not depend on a compositor; over ssh they are how you look.

    This is the guard against "fixing" the crash by bailing out at the top of main(),
    before argparse: the diagnostic commands have to keep answering.
    """
    code, out = run_headless(["--help"])
    # The top-level parser answers --help, not the overlay's parser: assert against THIS
    # help's own content, which is what a person over ssh actually reads.
    check(f"--help works headless (exit {code})",
          code == 0 and "--status" in out and "Traceback" not in out)
    code, out = run_headless(["--version"])
    check(f"--version works headless (exit {code})", code == 0 and "1.1" in out)
    code, out = run_headless(["--status"])
    check("--status answers headless without a traceback",
          "Traceback" not in out and "layer-shell lib" in out)
    check("    and it reports the display honestly",
          "nodisplay" in out or "no Wayland" in out.lower() or "Gdk display" in out)


def test_the_notice_accepts_the_reason_without_breaking_the_window_caller():
    from glasspad import deps
    import inspect
    sig = inspect.signature(deps.notice_lines)
    check("notice_lines takes detail", "detail" in sig.parameters)
    check("    and it is optional, so notice.py's existing call still works",
          sig.parameters["detail"].default is None)
    plain = deps.notice_lines("nodisplay")
    with_detail = deps.notice_lines("nodisplay", detail="socket refused")
    check("detail is appended, not woven in", with_detail[:-1] == plain
          or with_detail[:len(plain) - 1] == plain[:-1])
    check("the reason reaches the reader", any("socket refused" in p for p in with_detail))
    # print_notice takes **kw, so a signature check here could never fail. The proof is
    # behavioural, below: the running app's own stderr has to carry the reason.
    code, out = run_headless(["--null-pad"])
    check("the running app's stderr carried its own reason",
          not HAVE_OL or "What the attempt reported" in out)


def test_no_display_state_is_still_one_of_the_five_the_policy_defines():
    """The overlay now routes into deps' vocabulary; it must not invent a sixth word."""
    from glasspad import deps
    known = set()
    src = open(os.path.join(PKG, "deps.py"), encoding="utf-8").read()
    body = src.split("def notice_lines", 1)[1].split("\ndef ", 1)[0]
    for word in ("nodisplay", "missing", "unbound", "nocompositor"):
        check(f"notice_lines handles {word!r}", f'"{word}"' in body)
    check("overlay_layer uses that word, not a private one",
          'print_notice("nodisplay"' in open(os.path.join(PKG, "overlay_layer.py"),
                                             encoding="utf-8").read())


def test_exit_code_is_distinct_from_the_other_failure_codes():
    """A caller must be able to tell 'no display' from 'no library' without reading text."""
    if not HAVE_OL:
        return skip("exit-code distinctness needs the overlay module")
    ol = _overlay_module()
    used = {ol.EXIT_NO_DISPLAY}
    src = open(os.path.join(PKG, "__main__.py"), encoding="utf-8").read()
    for literal in ("return 2", "return 3", "return 4"):
        n = int(literal.split()[-1])
        check(f"    {literal} elsewhere does not collide with EXIT_NO_DISPLAY",
              n not in used)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s), {len(SKIPS)} skipped")
    sys.exit(1 if FAILS else 0)
