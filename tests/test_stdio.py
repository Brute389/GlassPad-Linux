"""Stdout that arrives while the program is still running.

Run: python3 tests/test_stdio.py       (no pytest, no display)

The reason this exists is a first-run walkthrough where the overlay was demonstrably up --
sixteen controls measured on screen, GPU buffers open -- and its own log never said so,
because the summary line was sitting in Python's block buffer and the run was killed before
the buffer filled. Redirected stdout is a pipe; a pipe is block-buffered; a manager-launched
GUI app has no terminal, so that buffer is the only copy of the story.

The behavioural test below ships with a CONTROL. A test that passes whether or not the fix
works is decoration, and buffering is exactly the kind of bug where the test passes for the
wrong reason -- usually because the child exited and flushed.
"""
import ast
import io
import os
import select
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad.stdio import line_buffer_stdout  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


class _NoFlush:
    pass


class _BadReconfigure(io.TextIOWrapper):
    def reconfigure(self, **kw):
        raise OSError("device gone")


def test_it_actually_changes_a_pipe_backed_stream():
    r, w = os.pipe()
    try:
        stream = io.open(w, "w", encoding="utf-8", closefd=False)
        check("pipes start block-buffered", not stream.line_buffering)
        check("the helper reports success", line_buffer_stdout(stream) is True)
        check("and the stream is line-buffered", stream.line_buffering)
        check("calling it twice is fine", line_buffer_stdout(stream) is True)
        stream.write("one\n")
        stream.flush()
        os.set_blocking(r, False)
        got = os.read(r, 100).decode()
        check("a line written after the call really arrives", got == "one\n")
        stream.close()
    finally:
        os.close(r)
        os.close(w)


def test_it_never_raises_however_stdout_arrives():
    """A crash on the way to drawing the overlay is worse than block-buffered logging."""
    cases = [
        ("None (a windowed interpreter with no console)", None),
        ("a raw binary stream", io.BytesIO()),
        ("an empty StringIO", io.StringIO()),
        ("something with no flush at all", _NoFlush()),
        ("a text stream whose reconfigure raises", None),       # filled below
    ]
    for label, stream in cases:
        if stream is None and label.startswith("a text stream"):
            r, w = os.pipe()
            try:
                # TextIOWrapper has no closefd; wrap a buffered writer that
                # leaves the descriptor alone when closed.
                bad = _BadReconfigure(io.open(w, "wb", closefd=False))
                ok = line_buffer_stdout(bad)
                check(f"    {label}: returns {ok!r}, raises nothing", isinstance(ok, bool))
                bad.close()
            finally:
                os.close(r)
                os.close(w)
            continue
        try:
            ok = line_buffer_stdout(stream)
            check(f"    {label}: returns {ok!r}, raises nothing", isinstance(ok, bool))
        except Exception as exc:                                # noqa: BLE001
            check(f"    {label}: RAISED {type(exc).__name__}: {exc}", False)
    check("the return value answers 'did it deliver', not 'did I touch anything'",
          line_buffer_stdout(io.StringIO()) is True and line_buffer_stdout(_NoFlush()) is False)


def _child(with_helper: bool, marker: str) -> subprocess.Popen:
    src = (f"import sys; sys.path.insert(0, {ROOT!r})\n"
           + ("from glasspad.stdio import line_buffer_stdout as on; on()\n" if with_helper
             else "")
           + f"print({marker!r})\n"
           + "import time; time.sleep(6)\n")
    return subprocess.Popen([sys.executable, "-c", src], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, cwd=ROOT)


def _read_soon(proc, seconds=2.0):
    """Read whatever the child has delivered, WITHOUT waiting for it to exit."""
    ready, _, _ = select.select([proc.stdout], [], [], seconds)
    return proc.stdout.readline().strip() if ready else ""


def test_a_line_reaches_the_pipe_before_the_process_exits():
    marker = "GLASSPAD-FLUSH-MARKER"
    proc = _child(with_helper=True, marker=marker)
    try:
        got = _read_soon(proc)
        alive = proc.poll() is None
        check(f"the line arrived while the child was still running (got {got!r})",
              got == marker and alive)
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_control_the_test_can_fail():
    """The same read, without the helper, must NOT see anything before exit.

    Without this control the test above would pass on any machine, because the usual way
    stdout gets flushed is the process exiting -- which is precisely the thing that does not
    happen when a user closes an overlay or a manager kills one.
    """
    marker = "GLASSPAD-BLOCKED-MARKER"
    proc = _child(with_helper=False, marker=marker)
    try:
        got = _read_soon(proc, seconds=2.0)
        check(f"nothing arrives without the helper (got {got!r})", got == "")
        check("    and the child is still alive, i.e. its data is buffered, not lost",
              proc.poll() is None)
    finally:
        # Now let it exit and prove the bytes were only waiting, never gone.
        proc.wait(timeout=15)
        rest = proc.stdout.read()
        check("    the marker does arrive once it exits", marker in rest)


def test_main_turns_it_on_before_it_writes_anything():
    """Setting it after the first print is setting it too late for that line."""
    path = os.path.join(ROOT, "glasspad", "__main__.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    helper_line = None
    first_write = None
    for node in ast.walk(main):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", "")
            if name == "line_buffer_stdout" and helper_line is None:
                helper_line = node.lineno
            if isinstance(fn, ast.Name) and fn.id == "print" and first_write is None:
                first_write = node.lineno
    check("main() calls line_buffer_stdout", helper_line is not None)
    check("    and no print() precedes it",
          first_write is None or (helper_line is not None and helper_line < first_write))


def test_the_summary_lines_are_the_ones_that_matter():
    """What we lose when this is off is the startup story, so name it in a test."""
    src = open(os.path.join(ROOT, "glasspad", "overlay_layer.py"), encoding="utf-8").read()
    for probe in ("layer surfaces up", "screen {sw}x{sh} authored"):
        check(f"the summary still reports {probe!r}", probe in src)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
