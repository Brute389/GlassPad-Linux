"""Ctrl-C must stop a program that owns a kernel input device.

The overlay ignored SIGINT: the process stayed up, invisible, still holding a registered
gamepad. These drive the real signal machinery -- signal.raise_signal, not a mocked
callback -- because the bug was in what the interpreter does with a signal, not in a
function somebody could stub out.

Run: python3 tests/test_shutdown.py
"""
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import shutdown  # noqa: E402

FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


def test_sigint_and_sigterm_reach_the_quit_callback():
    calls = []
    saved = shutdown.install(lambda: calls.append(1))
    try:
        signal.raise_signal(signal.SIGINT)
        check(f"SIGINT reached the callback ({len(calls)} call(s))", len(calls) == 1)
        # second signal escalates, so reset by reinstalling
        shutdown.uninstall(saved)
        saved = shutdown.install(lambda: calls.append(1))
        signal.raise_signal(signal.SIGTERM)
        check(f"SIGTERM reached it too ({len(calls)} call(s))", len(calls) == 2)
    finally:
        shutdown.uninstall(saved)


def test_a_second_signal_is_fatal_rather_than_wedged():
    """If the clean path is stuck, the only lever left has to work."""
    calls = []
    exits = []
    real_exit = os._exit
    shutdown.os._exit = lambda code: exits.append(code)      # type: ignore[prop-dep]
    try:
        shutdown.install(lambda: calls.append(1))
        signal.raise_signal(signal.SIGINT)
        code = None
        try:
            signal.raise_signal(signal.SIGINT)
        except SystemExit as exc:            # the belt-and-braces path, if _exit is stubbed
            code = exc.code
    finally:
        shutdown.os._exit = real_exit                          # type: ignore[prop-dep]
        shutdown.uninstall({})
    check(f"first signal asked nicely ({calls})", calls == [1])
    check(f"second signal exited immediately ({exits})", exits == [128 + signal.SIGINT])
    check("and it did not call the wedged cleanup again", calls == [1])
    check(f"nor fell through past a stubbed exit (SystemExit {code})",
          code == 128 + signal.SIGINT)


def test_a_failing_quit_path_does_not_eat_the_terminal():
    """The handler must not turn "Ctrl-C did nothing" into "Ctrl-C crashed the app"."""
    msgs = []

    def boom():
        raise RuntimeError("idle_add refused")

    saved = shutdown.install(boom, log=msgs.append)
    try:
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGINT)
    finally:
        shutdown.uninstall(saved)
    text = "\n".join(msgs)
    check(f"the failure was reported ({msgs[:1]})", "clean shutdown failed" in text)
    check("and it named the next lever", "again" in text or "immediately" in text)


def test_off_the_main_thread_it_says_so_instead_of_raising():
    """signal.signal() only works on the main thread; setup code must not die there."""
    box = {}

    def worker():
        msgs = []
        try:
            box["result"] = shutdown.install(lambda: None, log=msgs.append)
        except BaseException as exc:                        # noqa: BLE001
            box["raised"] = exc
        box["msgs"] = msgs

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=10)
    check("no exception escaped the worker", "raised" not in box)
    check(f"and the situation was stated plainly ({box.get('msgs')})",
          any("main thread" in m for m in box.get("msgs", [])))


def test_previous_handlers_are_handed_back():
    seen = {"n": 0}

    def prior(signum, frame):                                # noqa: ARG001
        seen["n"] += 1

    signal.signal(signal.SIGINT, prior)
    saved = shutdown.install(lambda: None)
    check("install returned what it replaced",
          saved.get(signal.SIGINT) is prior)
    shutdown.uninstall(saved)
    signal.raise_signal(signal.SIGINT)
    check(f"uninstall put it back and it runs ({seen['n']})", seen["n"] == 1)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
