"""The log a manager-launched app leaves behind has to be readable.

Installed through Shelly, the overlay runs as a systemd transient unit and stdout goes to
the journal — which is then the ONLY record of the run, because there is no terminal. One
edit session wrote 412 lines of `status: A 1494,601`, and the lines that mattered (pad
created, profile loaded, any error) were under the coordinates.

These tests assert the collapse, and — more importantly — the two things that make a rate
limiter safe: the settled value still gets printed when the drag ends, and a message that
is only ever said once can never be folded away.

Run: python3 tests/test_status_log.py
"""
import contextlib
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import overlay_layer as OL  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


def bare():
    """An Overlay with no __init__ (no display), as tests/test_toast.py does it."""
    return OL.Overlay.__new__(OL.Overlay)


def lines_of(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return [ln for ln in buf.getvalue().splitlines() if ln.strip()]


def test_a_drag_stream_collapses_instead_of_flooding():
    ov = bare()

    def burst():
        for i in range(400):
            ov._say_quiet(f"A  {1400 + i},{600 + (i % 7)}", key="A")
        ov._flush_quiet("A")

    out = lines_of(burst)
    check(f"400 motion updates -> {len(out)} line(s), not 400", 1 <= len(out) <= 3)
    check(f"the settled value is on the record ({out[-1].strip()})",
          "1799,600" in out[-1])
    check(f"and the line says how many it stands for ({out[-1].strip()})",
        "+399" in out[-1] and "folded" in out[-1])


def test_a_long_drag_still_leaves_a_trace():
    """Folding to ONE line for a ten-second drag would hide that anything moved at all."""
    ov = bare()

    def slow():
        t0 = time.monotonic()
        n = 0
        while time.monotonic() - t0 < OL.QUIET_FLUSH_S + 0.15:
            n += 1
            ov._say_quiet(f"A  {100 + n},{200}", key="A")
        ov._flush_quiet("A")

    out = lines_of(slow)
    check(f"a drag longer than the interval produced {len(out)} lines (want >= 2)",
          len(out) >= 2)


def test_an_identical_repeat_is_not_news():
    ov = bare()
    out = lines_of(lambda: [ov._say_quiet("A  100,200", key="A") for _ in range(50)])
    check(f"50 identical readouts -> {len(out)} line", len(out) == 1)


def test_a_one_shot_message_can_never_be_folded_away():
    """The limiter exists for coordinate telemetry. It must not swallow a sentence.

    Profile switches, refusals and "queued; switching when you let go" are said once. A
    fold that dropped one of those would lose the only explanation of why something did
    not happen -- and it would look like the app said nothing at all.
    """
    ov = bare()

    def say():
        ov._say_quiet("profile fighter queued; switching when you let go")
        ov._say_quiet("profile sniper queued; switching when you let go")
        ov._say_quiet("stick: saturate 0.52")
        ov._say_quiet("d-pad 4-way")

    out = lines_of(say)
    check(f"four distinct one-shot messages -> {len(out)} lines (want 4)", len(out) == 4)
    fresh = bare()          # a fresh instance: on this one, "d-pad 4-way" has not been
                            # said yet, so the first must print and the repeat must not
    same = lines_of(lambda: [
        fresh._say_quiet("d-pad 4-way"), fresh._say_quiet("d-pad 4-way")])
    check(f"the same sentence twice -> {len(same)} line (the second says nothing new)",
          len(same) == 1)


def test_ending_a_drag_flushes_the_value_it_was_holding():
    """Without this, the number anyone wants -- where the control ended up -- is exactly
    the number a rate limiter drops, whenever the drag stops between two ticks."""
    ov = bare()
    ov._quiet = {"A": ("A  1512,703", time.monotonic() - 0.01, 37)}
    ov._grab = {}
    out = lines_of(lambda: ov._edit_end("A"))
    check(f"_edit_end flushed the settled geometry ({out})",
          any("1512,703" in ln and "+37" in ln for ln in out))
    check("and the pending entry is gone, so a second release prints nothing",
          not lines_of(lambda: ov._edit_end("A")))


def test_folding_can_be_switched_off_entirely():
    """GLASSPAD_LOG=full. A log that hides information with no way to get it back is a
    bad bargain when the thing being debugged might be the hiding."""
    ov = bare()
    saved = os.environ.get("GLASSPAD_LOG")
    os.environ["GLASSPAD_LOG"] = "full"
    try:
        out = lines_of(lambda: [ov._say_quiet(f"B  {i},{i}", key="B")
                                for i in range(25)])
    finally:
        if saved is None:
            os.environ.pop("GLASSPAD_LOG", None)
        else:
            os.environ["GLASSPAD_LOG"] = saved
    check(f"GLASSPAD_LOG=full -> all 25 lines ({len(out)})", len(out) == 25)


def test_the_streaming_call_sites_actually_pass_their_key():
    """The whole mechanism keys off the control name.

    If a streaming readout is called without key=, it silently takes the one-shot path
    and the flood continues -- with the limiter looking correctly implemented, because in
    isolation it is. This is the check that fails when someone adds a third readout.
    """
    src = open(os.path.join(ROOT, "glasspad", "overlay_layer.py"),
               encoding="utf-8").read()
    move = 'self._say_quiet(f"{name}  {nx:.0f},{ny:.0f}", key=name)'
    size = 'self._say_quiet(f"{name}  {nw:.0f}x{nh:.0f}", key=name)'
    check("the move readout is keyed to its control", move in src)
    check("the resize readout is keyed to its control", size in src)
    streaming = src.count('_say_quiet(f"{name}')
    keyed = src.count('_say_quiet(f"{name}  {n')
    check(f"every per-control readout passes a key ({keyed}/{streaming})",
          streaming == keyed)
    check("_edit_end flushes, so folding cannot lose the settled value",
          "self._flush_quiet(name)" in src.split("def _edit_end")[1][:200])


def test_the_shared_stub_keeps_the_same_signature():
    """tests/_overlay_stub.py replaces this method wholesale.

    It used to be `ov.sayings.append` -- one positional argument -- so the moment `key=`
    arrived, every keyed call raised "list.append() takes no keyword arguments" inside the
    turbo suite, where nothing about turbo had changed. A stand-in that silently diverges
    from the signature it replaces turns an unrelated change into a mysterious failure in
    someone else's test; this makes the divergence fail HERE, with the reason in the name.
    """
    import inspect

    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from _overlay_stub import make_overlay                     # noqa: PLC0415

    stub_ov = make_overlay()
    real = inspect.signature(OL.Overlay._say_quiet)
    stub = inspect.signature(stub_ov._say_quiet)
    check(f"the stub takes what production takes ({stub})",
          len(stub.parameters) >= len(real.parameters) - 1)
    out = []
    try:
        stub_ov._say_quiet("A  120,130", key="A")             # the call production makes
        out.append("accepted")
    except TypeError as exc:
        out.append(f"TypeError: {exc}")
    check(f"a keyed call through the stub works ({out[0]})", out[0] == "accepted")
    check("and the stub recorded both the text and which control it belongs to",
          stub_ov.sayings and stub_ov.say_keys == ["A"])


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
