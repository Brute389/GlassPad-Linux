#!/usr/bin/env python3
"""Run every test suite, plus the live self-test, and exit non-zero if anything failed.

Why this exists: the README used to say

    for t in tests/test_*.py; do python3 "$t" | tail -1; done

and a pipeline reports **tail's** exit status. A suite that dies on import -- the
pytest one does on a machine without pytest -- printed its traceback and the loop
still exited 0. A command that cannot report failure is worse than no command, because
it gets trusted, and this one was documented as the way to check the tree.

Every suite here is a standalone stdlib runner that exits 0/1. The live self-test is
different: it drives real layer surfaces, so it needs a Wayland session with
layer-shell support and is reported as *skipped* rather than failed when there isn't
one -- skipping what cannot run is fine, hiding what did fail is not.

Usage:
    python3 tools/check_all.py                 # suites, plus the live one if possible
    python3 tools/check_all.py --no-live       # suites only
    python3 tools/check_all.py -v              # full output of anything that failed
    python3 tools/check_all.py -k turbo        # only files whose name contains turbo
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
LIVE_TIMEOUT_S = 60
LEGACY_TIMEOUT_S = 900


def run_suite(path: str, verbose: bool) -> dict:
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, path], cwd=ROOT, capture_output=True,
                          text=True)
    dt = time.monotonic() - t0
    out = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    # the runners all finish with "N passed, M failed of K"; show that, not noise
    summary = lines[-1].strip() if lines else "(no output)"
    rec = {"name": os.path.basename(path), "ok": proc.returncode == 0,
           "code": proc.returncode, "summary": summary, "secs": dt,
           "text": out}
    if "No module named" in out:
        # A suite that never started is a different fact from a suite that failed. Keep
        # them apart: the first is a to-do, the second is a broken build -- and a
        # packaging gate needs to be able to say "broken blocks me, to-do warns me".
        rec["note"] = "missing dependency -- this suite is NOT running"
        rec["blocked"] = True
    return rec


def run_live(timeout_s: int, verbose: bool) -> dict:
    """GLASSPAD_SELFTEST: drives the real hub, edit mode, resize, rotation, turbo."""
    if not os.environ.get("WAYLAND_DISPLAY"):
        return {"name": "selftest (live)", "ok": True, "skipped": "no Wayland session",
                "summary": "skipped", "secs": 0.0, "text": ""}
    t0 = time.monotonic()
    env = dict(os.environ, GLASSPAD_SELFTEST="1")
    try:
        proc = subprocess.run([sys.executable, "-m", "glasspad", "--null-pad"],
                              cwd=ROOT, env=env, capture_output=True, text=True,
                              timeout=timeout_s)
        text = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        text = (exc.stdout or "") + (exc.stderr or "") if isinstance(exc.stdout, str) \
            else ""
        text += "\n  (timed out)"
        code = -1
    oks = len(re.findall(r"^\s+ok\s+", text, re.M))
    fails = len(re.findall(r"FAIL", text))
    traces = len(re.findall(r"Traceback", text))
    secs = time.monotonic() - t0
    # "selftest done" is the chain's own last word. Without it the run stopped early --
    # which is a failure, not a skip: an incomplete pass is how a broken feature gets
    # shipped.
    if "selftest done" not in text:
        return {"name": "selftest (live)", "ok": False, "code": -1,
                "summary": f"stopped early ({oks} ok, {fails} FAIL, {traces} tracebacks)",
                "secs": secs, "text": text}
    ok = fails == 0 and traces == 0 and code == 0
    return {"name": "selftest (live)", "ok": ok, "code": 0 if ok else 1,
            "summary": f"{oks} checks ok, {fails} failed, {traces} tracebacks",
            "secs": secs, "text": text}


def run_legacy(timeout_s: int, verbose: bool) -> dict:
    """tests/test_glasspad.py, through the runner that can actually execute it.

    That suite is written in pytest style and pytest is not installed (and cannot be
    fetched), so invoking the file directly -- which is what the discovery loop does for
    everything else -- reports "No module named pytest" and the 259 tests inside it are
    silently lost. tools/run_tests.py is the runner for it: it installs a small pytest
    stand-in (fixtures, parametrize, tmp_path, monkeypatch, approx) and needs PyQt6, which
    the Qt backend needs anyway.

    If PyQt6 ever goes away this returns "blocked", not "passed": a suite that never
    started is a to-do, never a green tick.
    """
    runner = os.path.join(ROOT, "tools", "run_tests.py")
    t0 = time.monotonic()
    try:
        proc = subprocess.run([sys.executable, runner], cwd=ROOT, capture_output=True,
                              text=True, timeout=timeout_s)
        text = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
    except subprocess.TimeoutExpired:
        return {"name": "legacy suite (run_tests.py)", "ok": False, "code": -1,
                "summary": f"timed out after {timeout_s}s", "secs": time.monotonic() - t0,
                "text": "(timed out)"}
    secs = time.monotonic() - t0
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    summary = lines[-1].strip() if lines else "(no output)"
    rec = {"name": "legacy suite (run_tests.py)", "ok": code == 0, "code": code,
           "summary": summary, "secs": secs, "text": text}
    if "No module named" in text:
        rec["note"] = ("missing dependency -- these tests are NOT running "
                       "(tools/run_tests.py needs PyQt6)")
        rec["blocked"] = True
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="run every GlassPad test suite")
    ap.add_argument("-k", metavar="SUBSTR", help="only files whose name contains this")
    ap.add_argument("--no-live", action="store_true",
                    help="skip the in-app self-test (needs a Wayland session)")
    ap.add_argument("--no-artifacts", action="store_true",
                    help="skip the suites that inspect dist/ (used by the build scripts)")
    ap.add_argument("--no-legacy", action="store_true",
                    help="skip the pytest-style suite (tools/run_tests.py, needs PyQt6)")
    ap.add_argument("--legacy-timeout", type=int, default=LEGACY_TIMEOUT_S,
                    metavar="SECONDS")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print the full output of anything that failed")
    ap.add_argument("--strict", action="store_true",
                    help="treat a suite that cannot start as a failure (exit 1, not 2)")
    ap.add_argument("--timeout", type=int, default=LIVE_TIMEOUT_S, metavar="SECONDS")
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(TESTS, "test_*.py")))
    if args.k:
        files = [f for f in files if args.k in os.path.basename(f)]

    # The pytest-style suite runs through tools/run_tests.py (see run_legacy). Running the
    # file directly as well would only add a guaranteed "No module named pytest" row and
    # make the gate look worse than it is.
    # Always, not only when the legacy run is on: executing the file directly can only
    # fail ("No module named pytest"), so --no-legacy must report it as skipped rather
    # than resurrect a run that is guaranteed red.
    legacy_name = "test_glasspad.py"
    direct = [f for f in files if os.path.basename(f) != legacy_name]
    skipped_legacy = any(os.path.basename(f) == legacy_name for f in files)

    # The artefact suites inspect dist/ against the CURRENT source, so the moment anything
    # is edited they go red -- which would stop tools/make_appimage.sh from ever rebuilding
    # the artefact that is stale. A gate that can only pass after the thing it gates has
    # already been done is not a gate. They are skipped here and run in full when a human
    # asks for everything.
    if args.no_artifacts:
        direct = [f for f in direct
                  if "artifact" not in os.path.basename(f)]

    results = [run_suite(f, args.verbose) for f in direct]
    if not args.k and skipped_legacy:
        if args.no_legacy:
            results.append({"name": "legacy suite (run_tests.py)", "ok": True,
                            "skipped": True, "summary": "skipped by --no-legacy",
                            "secs": 0.0, "text": ""})
        else:
            results.append(run_legacy(args.legacy_timeout, args.verbose))
    if not args.no_live:
        results.append(run_live(args.timeout, args.verbose))

    width = max(len(r["name"]) for r in results) if results else 10
    for r in results:
        flag = "ok  " if r["ok"] else ("BLOCK" if r.get("blocked") else "FAIL")
        extra = f"   {r['secs']:.1f}s" if r.get("secs") else ""
        print(f"  {flag} {r['name']:<{width}}  {r['summary']}{extra}")
        if r.get("note"):
            print(f"       !! {r['note']}")
        if not r["ok"] and args.verbose:
            for line in r["text"].splitlines():
                print(f"       | {line}")

    failed = [r for r in results if not r["ok"] and not r.get("blocked")]
    warned = [r for r in results if r.get("note")]
    total_s = sum(r.get("secs", 0.0) for r in results)
    print(f"\n  {len(results) - len(failed)}/{len(results)} suites green "
          f"in {total_s:.1f}s")
    if warned:
        print(f"  {len(warned)} suite(s) could not run at all: "
              f"{', '.join(r['name'] for r in warned)}")
        print("  a suite that cannot start is not a passing suite -- install the "
              "dependency or delete the file")
    if failed:
        print("  failing: " + ", ".join(r["name"] for r in failed)
              + "   (-v for output)")
        return 1
    # 2 = everything that ran passed, but something never ran. Callers that must not
    # package on a partial signal can use --strict to fold that into 1.
    if warned:
        return 2 if not args.strict else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
