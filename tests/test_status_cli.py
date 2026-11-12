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
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def test_qt_backend_still_reports_its_own_internals():
    code, out = run("--backend", "qt", "--status")
    check(f"the fork left the Qt diagnostics intact (rc={code})",
          "pass-through" in out or "PyQt" in out)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
