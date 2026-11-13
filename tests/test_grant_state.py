"""After the grant: three outcomes, three sentences, and only one of them needs a log out.

Run: python3 tests/test_grant_state.py       (no pytest, no display, no root)

The About box used to end at "terminal opened -- approve the password prompt there". Then the
terminal closed, the overlay kept its null pad, and the user was left to guess whether to
restart the app, log out, or give up -- in a specific order, because restarting is the obvious
thing and the one that cannot work when the grant arrived as group membership: a running
process keeps the groups it started with.

These tests are for the decision, not the widget. The widget part -- that a real label on a
real panel actually changes -- lives in the selftest (`step_grant_verdict`), because a suite
with no compositor cannot see a label.
"""
import os
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import pad  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


class FakeCompleted:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


def test_the_three_states_come_from_the_two_questions():
    """Node writable? Account in `input`? Everything else follows, including 'nothing yet'."""
    cases = [
        (True,  set(),               pad.GRANT_RESTART),
        (True,  {"input"},           pad.GRANT_RESTART),   # both true: restart wins
        (False, {"input", "wheel"},  pad.GRANT_RELOGIN),
        (False, {"wheel"},           pad.GRANT_PENDING),
        (False, set(),               pad.GRANT_PENDING),
    ]
    for can, groups, want in cases:
        got, msg = pad.grant_state(writable=can, groups=lambda: groups)
        check(f"writable={can} groups={sorted(groups)} -> {want} (got {got})", got == want)
        check(f"    and the message is the one for {want}", msg == pad.GRANT_MESSAGES[want])
    check("every state has a message and every message has a state",
          set(pad.GRANT_MESSAGES) == {pad.GRANT_RESTART, pad.GRANT_RELOGIN,
                                      pad.GRANT_PENDING})


def test_the_membership_verdict_says_the_unpleasant_thing_plainly():
    """The expensive wording. A user who restarts here has spent their patience on a no-op."""
    text = pad.GRANT_MESSAGES[pad.GRANT_RELOGIN]
    check("it says log in", "log in" in text.lower())
    check("and says restarting will not help", "restarting" in text.lower()
          and "not help" in text.lower())
    check("and does not offer a restart as the fix",
          "close GlassPad and start it again" not in text)


def test_the_node_verdict_says_no_logout_is_needed():
    text = pad.GRANT_MESSAGES[pad.GRANT_RESTART]
    check("it names the fix", "start it again" in text.lower())
    check("and rules out the bigger nuisance", "no log out needed" in text.lower())


def test_the_pending_verdict_promises_nothing_it_has_not_done():
    text = pad.GRANT_MESSAGES[pad.GRANT_PENDING]
    low = text.lower()
    check("it says nothing was granted", "not granted" in low)
    check("and sends them back to the terminal for the reason", "terminal" in low)
    for forbidden in ("granted --", "done", "success", "now works"):
        check(f"    without claiming {forbidden!r}", forbidden not in low)


def test_verdicts_name_no_command_the_reader_cannot_run():
    """This text appears on a tablet, held in two hands, with no terminal in reach.

    The existing guard in test_one_file_install.py already bans literal `glasspad --...`
    outside the two terminal-context files; this states the reason for these strings
    specifically, so the next verdict wording is written for the place it is read.
    """
    for state, text in pad.GRANT_MESSAGES.items():
        check(f"{state}: no shell command in on-screen text", "glasspad --" not in text)
        check(f"{state}: and no sudo either", "sudo" not in text.lower())


def test_polling_stops_at_a_verdict_and_eventually_anyway():
    check("pending keeps polling", pad.keep_polling(pad.GRANT_PENDING, 1))
    check("restart stops it", not pad.keep_polling(pad.GRANT_RESTART, 1))
    check("relogin stops it", not pad.keep_polling(pad.GRANT_RELOGIN, 1))
    check("and a grant that never lands is not a timer for the rest of the session",
          not pad.keep_polling(pad.GRANT_PENDING, pad.keep_polling.__defaults__[0]))
    limit = pad.keep_polling.__defaults__[0]
    check(f"the bound is a sane wait, not one poll ({limit} polls = {limit * 2 // 60} min)",
          30 <= limit <= 600)


def test_writable_now_asks_by_opening():
    """Not `os.access`: mandatory access control and uaccess ACLs are where those disagree."""
    with tempfile.TemporaryDirectory() as d:
        good = os.path.join(d, "w")
        open(good, "wb").close()
        os.chmod(good, 0o600)
        check("a node we can open is writable", pad.writable_now(good))

        bad = os.path.join(d, "r")
        open(bad, "wb").close()
        os.chmod(bad, stat.S_IRUSR)
        check("a node we cannot open is not", not pad.writable_now(bad))

        check("a missing node is not", not pad.writable_now(os.path.join(d, "gone")))
        check("and a directory is not, even a writable one",
              not pad.writable_now(d))
    # The device must be left exactly as it was: no fd leak, no half-created device.
    if os.path.exists("/dev/uinput"):
        before = pad.writable_now()
        for _ in range(20):
            pad.writable_now()
        check("20 probes in a row change nothing", pad.writable_now() == before)


def test_group_names_come_from_the_account_not_the_process():
    """`id -nG` out of process, because os.getgroups() is frozen at login."""
    names = pad.user_group_names("tester", runner=lambda *a, **k:
                                 FakeCompleted("wheel input audio\n"))
    check("parsed", names == frozenset({"wheel", "input", "audio"}))
    check("and 'input' is what the state machine looks for", "input" in names)
    check("a non-zero exit is no opinion",
          pad.user_group_names("tester", runner=lambda *a, **k:
                               FakeCompleted("", 1)) == frozenset())
    check("empty output is no opinion",
          pad.user_group_names("tester", runner=lambda *a, **k:
                               FakeCompleted("")) == frozenset())

    def boom(*a, **k):
        raise OSError("no id(1) here")
    check("and a failing `id` is no opinion, never a guess",
          pad.user_group_names("tester", runner=boom) == frozenset())
    real = pad.user_group_names()
    check(f"the real call answers for this account ({len(real)} groups)", len(real) >= 1)


def test_the_verdict_on_this_machine_is_consistent_with_the_machine():
    """No injections: the integration has to be sane somewhere, and here it can be seen."""
    can = pad.writable_now()
    in_group = "input" in pad.user_group_names()
    state, _msg = pad.grant_state()
    if can:
        check(f"node writable -> restart (state={state})", state == pad.GRANT_RESTART)
    elif in_group:
        check(f"account in input, node not writable -> relogin (state={state})",
              state == pad.GRANT_RELOGIN)
    else:
        check(f"neither -> pending (state={state})", state == pad.GRANT_PENDING)


def test_the_overlay_glue_stops_polling_and_keeps_the_button_a_button():
    """Two properties that live in the widget code and are cheap to pin statically.

    An unbounded GLib timeout outlives the About box, and a "grant" button that gets
    relabelled into a status line stops being retryable -- which matters most in the case
    where the password prompt was refused and the user wants another go.
    """
    src = open(os.path.join(ROOT, "glasspad", "overlay_layer.py"), encoding="utf-8").read()
    poll = src.split("def _poll_grant", 1)[1].split("def _set_grant_note", 1)[0]
    check("_poll_grant returns keep_polling's answer, not its own opinion",
          "return _pad.keep_polling(verdict, self._grant_polls)" in poll)
    note = src.split("def _set_grant_note", 1)[1].split("\n    def ", 1)[0]
    check("the verdict never relabels the pill", "grant_pill" not in note)
    check("and the pill is still wired to the action",
          "self._grant_access, 200, 34" in src)
    check("the watch starts only after a terminal actually opened",
          "self._watch_grant()" in src.split("if ok:")[1][:200])


def test_a_grant_probe_never_creates_a_device():
    """Opening /dev/uinput is harmless; WRITING or ioctling it is not. This must stay true.

    The probe runs on a timer while a panel is open, so a version of it that set up a real
    device would create and abandon virtual gamepads for the rest of the session -- each one
    visible to Steam as a controller.
    """
    import ast

    path = os.path.join(ROOT, "glasspad", "pad.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    lines = src.splitlines()
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == "writable_now"), None)
    check("found the probe", fn is not None)
    if fn is None:
        return
    # Code, not prose: the docstring explains WHY the probe is harmless by naming
    # UINPUT_DEV_SETUP and ioctl, and a scan over the raw source failed against that
    # explanation. Same lesson as the GTK 4 `set_keep_above` check -- grep the syntax tree
    # when the thing you are banning is also the thing you are describing.
    start = fn.body[0].end_lineno + 1 if (fn.body and isinstance(
        fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant)
        and isinstance(fn.body[0].value.value, str)) else fn.body[0].lineno - 1
    body = "\n".join(lines[start:fn.end_lineno])
    for dangerous in ("ioctl", "UI_DEV_CREATE", "UI_DEV_SETUP", "write(", "os.write"):
        check(f"    the probe does not {dangerous}", dangerous not in body)
    check("it opens for writing only", "os.O_WRONLY" in body)
    check("and closes what it opened", "os.close(fd)" in body)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
