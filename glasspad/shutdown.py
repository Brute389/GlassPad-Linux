"""Signal handling for a program that owns a kernel input device.

Found on the Shelly-installed AppImage: the overlay ignored SIGINT. Ctrl-C in a
terminal did nothing at all -- the process kept running, still fullscreen and
transparent, still holding a registered ``Microsoft X-Box 360 pad``. SIGTERM worked,
because nothing handles it and the kernel just ends the process. So the universal way to
stop a foreground program was the one way this program could not be stopped, and what it
left behind is worse than a stuck window: a phantom gamepad that games and Steam will
gladly read, with no visible owner.

(The Python default is KeyboardInterrupt, which inside a GLib main loop surfaces between
bytecodes and gets swallowed by whatever exception boundary is nearest -- the loop carries
on. Handlers are the only reliable answer here, not a try/except around loop.run().)

Two decisions worth their reasons:

  * The handler does not tear anything down itself. It asks the overlay to quit the way
    its own Quit button does, so the pad is destroyed on the same path as a normal exit
    rather than a second, subtly different one.
  * The SECOND signal is fatal and immediate. If the clean path is wedged, the only
    lever left is the one that always works. The uinput device does not outlive the
    process -- the kernel closes it when the last fd goes -- so a hard exit still does
    not leave a ghost pad behind.
"""
from __future__ import annotations

import os
import signal
import sys

SIGNALS = (signal.SIGINT, signal.SIGTERM)


def install(request_quit, log=None) -> dict:
    """Route SIGINT and SIGTERM to `request_quit`. Returns the previous handlers.

    `request_quit` is called with no arguments from signal context, so the caller is
    responsible for deferring real work (GLib.idle_add) -- this module deliberately knows
    nothing about the main loop, which is what makes it testable without one.
    """
    say = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
    state = {"fired": 0}

    def make_handler(sig: int):
        def handler(signum, frame):                     # noqa: ARG001
            state["fired"] += 1
            if state["fired"] > 1:
                say(f"glasspad: signal {sig.name} again -- leaving now")
                # Not sys.exit(): that raises, and raising into a wedged loop is how
                # this situation started. The kernel reaps the pad with the process.
                os._exit(128 + signum)
                # Unreachable in production, because os._exit never returns -- but a
                # stubbed or blocked exit must not fall through into the cleanup we have
                # just decided is wedged. Found by the test that stubs it.
                raise SystemExit(128 + signum)
            say(f"glasspad: {sig.name} -- shutting down (release gamepad, close overlay)")
            try:
                request_quit()
            except Exception as exc:                    # noqa: BLE001
                # A failing quit path must not cost the user their terminal: report once,
                # and let the next signal be the hard one.
                say(f"glasspad: clean shutdown failed ({type(exc).__name__}: {exc}); "
                    "send the signal again to exit immediately")
                state["fired"] = 0
        return handler

    previous: dict = {}
    for sig in SIGNALS:
        try:
            previous[sig] = signal.signal(sig, make_handler(sig))
        except ValueError:
            # signal.signal only works on the main thread. Called from a worker, say so
            # once instead of raising out of what is supposed to be setup code.
            say(f"glasspad: could not install a {sig.name} handler off the main thread")
    return previous


def uninstall(previous: dict) -> None:
    """Restore handlers saved by install() -- used by tests, and by any embedder."""
    for sig, handler in (previous or {}).items():
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
