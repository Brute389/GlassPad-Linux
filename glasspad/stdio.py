"""Stdout that delivers when something else is holding the pipe.

Why this exists: the overlay's startup summary -- how many layer surfaces came up, what
screen it fitted to, which profile it resolved -- was written with plain ``print``. When a
human runs it in a terminal that is invisible, because a terminal's stdout is line-buffered.
When a *manager* runs it (Steam, GameScope, a .desktop entry, ``systemd-run``,
``nohup … > file``) stdout is a pipe and Python switches to block buffering, so those lines
sit in a 4 KiB buffer until the process exits or the buffer fills. A run that is then killed,
or that hangs, or that the user closes after twenty seconds, produces a log that stops
mid-sentence -- and that log is the only copy of what happened, because a manager-launched
GUI app has no terminal attached to read it.

Seen for real during a first-run walkthrough: the overlay was demonstrably up (sixteen
controls measured on screen, GPU dmabufs open) and its own log never said so.

``line_buffer_stdout`` is called at the top of ``main`` so every subcommand, including the
diagnostics people pipe into bug reports, writes as it goes.
"""
from __future__ import annotations

import io
import sys

_UNSET = object()


def line_buffer_stdout(stream=_UNSET) -> bool:
    """Make stdout flush per line. True if it applied, False if it could not.

    Never raises. Every one of these is a real shape stdout arrives in: a terminal, a pipe,
    a file, ``None`` (a windowed interpreter with no console), an unbuffered ``BytesIO``
    under a test, or something a test has already swapped in that has no ``reconfigure`` at
    all. Failing here must not stop the program: a run with block-buffered logging is worse
    than nothing, and a crash on the way to drawing the overlay is far worse.
    """
    out = sys.stdout if stream is _UNSET else stream
    if out is None:
        return False
    try:
        if isinstance(out, io.TextIOWrapper):
            if out.line_buffering:
                return True
            out.reconfigure(line_buffering=True)
            return True
        # Anything else that flushes at all (pytest's capture object, a StringIO someone
        # handed us) is already delivering; there is nothing to fix and nothing to break.
        return hasattr(out, "flush")
    except (OSError, ValueError, AttributeError):
        # closed, detached, or a wrapper that refuses reconfiguration
        return False
