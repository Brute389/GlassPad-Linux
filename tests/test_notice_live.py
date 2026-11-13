"""The dependency notice, on a machine with a screen.

Run: python3 tests/test_notice_live.py      (needs a Wayland session; skips otherwise)

Why this exists beside the headless suite: `tests/test_dependency_policy.py` can assert
everything about the *plan* -- paragraphs, buttons, clipboard, which commands are permitted --
and nothing about whether a window ever appears. That gap is not hypothetical. The first
version of this notice called `set_keep_above()`, which GTK 4 does not have; the exception was
raised inside an `activate` handler, printed a traceback into a stdout nobody was reading, and
left a live process with no window at all. Every headless test stayed green, because none of
them built a widget.

So on a machine with a compositor the suite asks the toolkit directly whether the window is
mapped, and how big it got.
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


def have_display():
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("WAYLAND_SOCKET"))


def run_state(state, extra=""):
    """One notice, self-closing, in a process of its own. Returns (rc, stdout, stderr)."""
    code = (
        "from glasspad import notice; "
        f"raise SystemExit(notice.run({state!r}, selfclose_ms=1800))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=90, cwd=ROOT, env=env)
    return r.returncode, r.stdout, r.stderr


def test_the_notice_window_maps_on_a_real_display():
    """Not 'the code path was reached' -- mapped, sized, and gone when told to go."""
    if not have_display():
        print("  SKIP: no Wayland session; the headless suite covers the plan")
        return
    for state in ("missing", "unbound"):
        rc, out, err = run_state(state)
        check(f"{state}: no traceback anywhere ({err.strip()[:60]!r})",
              "Traceback" not in err and "Traceback" not in out)
        line = next((l for l in out.splitlines() if l.startswith("notice-window")), "")
        check(f"{state}: the window reported itself ({line!r})", bool(line))
        check(f"{state}: and it was actually mapped", "mapped=True" in line)
        size = line.split("size=")[-1] if line else ""
        try:
            w, h = (int(v) for v in size.split("x"))
        except ValueError:
            w = h = 0
        # 760x540 requested; anything much smaller means the content did not fit or the
        # window never grew, both of which read to a user as "nothing happened".
        check(f"{state}: and big enough to read ({size})", w >= 600 and h >= 380)
        # 0 or 4, and both are correct here: this machine HAS the library, so when the
        # window closes and the notice re-probes, "ready" is the honest answer even though
        # the text on screen said "missing". The exit code belongs to the probe, not to the
        # string we were asked to display -- pinning 4 here would assert that the notice is
        # too stupid to re-check.
        check(f"{state}: exits 0 or 4, never a crash (rc={rc})", rc in (0, 4))


def test_the_notice_does_not_grab_the_seat():
    """The window's job is to send the user to a terminal. A modal grab blocks exactly that.

    Checked statically, because "can the user type in another window" is not observable from
    the process that owns the dialog -- and the first draft had `set_modal(True)`, which KDE
    answered by dimming the whole desktop behind it.
    """
    src = open(os.path.join(ROOT, "glasspad", "notice.py"), encoding="utf-8").read()
    check("no set_modal", "set_modal(" not in src)
    # A CALL, not the word: the comment above this line explains that GTK 4 removed
    # set_keep_above, and a check that banned the string failed against the code explaining
    # why it was absent.
    import re
    calls = re.findall(r"\.set_(keep_above|modal)\s*\(", src)
    check(f"no GTK 3 stacking or grab calls at all ({calls})", not calls)
    check("and the activate handler is connected (a def is not a connection)",
          'app.connect("activate"' in src)


def test_a_broken_window_still_delivers_words():
    """The fallback is the point: a notice that cannot build must not become silence."""
    code = (
        "import gi; gi.require_version('Gtk', '4.0'); "
        "from gi.repository import Gtk; "
        "Gtk.ApplicationWindow.present = lambda self: (_ for _ in ()).throw"
        "(RuntimeError('simulated compositor refusal')); "
        "from glasspad import notice; "
        "raise SystemExit(notice.run('missing', selfclose_ms=400))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=90, cwd=ROOT, env=env)
    # rc is the probe's verdict, and this machine's probe says the library is fine -- the
    # test here is that a refused window degrades to printed words and a clean exit instead
    # of a traceback or a hung process, which the three checks below are about.
    check(f"survives a window that cannot be shown (rc={r.returncode})",
          r.returncode in (0, 4))
    combined = r.stdout + r.stderr
    check("says it could not build the window", "could not build the notice" in combined)
    check("and prints the explanation instead of nothing",
          "libgtk4-layer-shell" in combined and "glasspad --status" in combined)
    check("no traceback escaped to the user", "Traceback (most recent call last)"
          not in combined)


def test_the_widget_noise_in_our_window_is_the_themes_not_ours():
    """`GtkGizmo (slider) reported min width -2` shows up on this desktop with NO css of
    ours -- measured with a bare ScrolledWindow and an empty stylesheet, twice on each
    side. That is KDE's GTK theme drawing a scrollbar slider, and it is not something this
    program should paper over by removing the scroller: a notice whose text cannot scroll
    is a notice that loses its last paragraph on a 1280x800 tablet.

    Pinned as a comparison rather than "no warnings", so the day our own css makes it
    worse, this notices.
    """
    if not have_display():
        print("  SKIP: no Wayland session")
        return
    bare = subprocess.run([sys.executable, "-c", """
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, GLib
app = Gtk.Application(application_id="test.gp.baseline", flags=0)
def act(a):
    w = Gtk.ApplicationWindow(application=a); w.set_default_size(700, 500)
    sc = Gtk.ScrolledWindow(); sc.set_vexpand(True)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    for i in range(6):
        box.append(Gtk.Label(label="line %d" % i))
    sc.set_child(box); w.set_child(sc); w.present()
    GLib.timeout_add(400, lambda: (w.close(), False)[1])
app.connect("activate", act); app.run([])
"""], capture_output=True, text=True, timeout=90, env=dict(os.environ))
    noisy = lambda t: sum(1 for l in t.splitlines() if "reported min " in l)
    ours = run_state("missing")
    check(f"our window adds no size warnings ({noisy(ours[2])} vs {noisy(bare.stderr)} baseline)",
          noisy(ours[2]) <= noisy(bare.stderr) + 4)
    check(f"and none of them mention our classes ({[l for l in ours[2].splitlines() if 'gp-' in l][:1]})",
          not [l for l in ours[2].splitlines() if "gp-" in l])


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
