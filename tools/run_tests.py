#!/usr/bin/env python3
"""Run the GlassPad test suite with no pytest installed.

GlassPad ships to Arch desktops where PyQt6 is a system package and pytest
often is not -- and this suite is the only check that a layout still loads, a
stick still centres, and a BadWindow still cannot kill the overlay. A test
suite that needs a package you do not have is not a safety net, so this runner
reimplements the small slice of pytest the suite actually uses:

    pytest.fixture(scope="session"|"function")   (including yield teardown)
    pytest.fixture(autouse=True)
    pytest.mark.parametrize(argnames, argvalues)
    pytest.approx(value)          relative 1e-6, absolute 1e-12
    pytest.skip(reason)           raises Skipped
    fixtures: qapp, tmp_path, monkeypatch, and any fixture defined in the class

Fixtures are function-scoped except those declared session-scoped. `self` is
resolved to the test-class instance, exactly as pytest does for a method.

A fixture is never silently ignored. Signatures are the only way this runner
finds a fixture, so an autouse fixture -- which by definition asks for nothing
-- would simply never run, and the tests it exists to protect would keep
reporting the machine they asserted against. autouse is therefore implemented
here, and an unknown keyword argument is an error rather than a shrug: real
pytest has dozens of fixture kwargs and only the ones listed above mean
anything to this runner, so guessing at an unimplemented one is how a green
run starts lying.

Usage:
    python3 tools/run_tests.py            # whole suite
    python3 tools/run_tests.py -k Window  # substring filter
    python3 tools/run_tests.py -v         # list every test
"""
from __future__ import annotations

import argparse
import importlib.util
import inspect
import os
import pathlib
import shutil
import sys
import tempfile
import traceback
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# The pieces of pytest the suite uses
# ---------------------------------------------------------------------------
class Skipped(Exception):
    """Raised by pytest.skip(); unwound to the runner, not counted as failure."""


class MonkeyPatch:
    """setenv/delenv/setattr with an undo() that runs in reverse order.

    Reverse matters: two setattrs on one attribute must restore to the state at
    fixture entry, not to the intermediate one the earlier record captured.
    """

    def __init__(self) -> None:
        self._undo: list = []
        # Names whose original has already been captured. Recording once per
        # name is what makes setenv(DISPLAY, ':0') then setenv(DISPLAY, ':99')
        # restore the value from before the test rather than ':0'.
        self._seen: set = set()

    def _capture_env(self, name):
        if name not in self._seen:
            self._seen.add(name)
            self._undo.append(("env", name, os.environ.get(name)))

    def setenv(self, name, value):
        self._capture_env(name)
        os.environ[name] = str(value)

    def delenv(self, name, raising=True):
        if name not in os.environ:
            if raising:
                raise KeyError(name)
            return
        self._capture_env(name)
        del os.environ[name]

    def setattr(self, target, name, value, raising=True):
        obj = target if not isinstance(target, str) else sys.modules[target]
        if raising and not hasattr(obj, name):
            raise AttributeError(f"{obj!r} has no attribute {name!r}")
        self._undo.append(("attr", obj, name, getattr(obj, name, None)))
        setattr(obj, name, value)

    def undo(self) -> None:
        for record in reversed(self._undo):
            if record[0] == "env":
                _, name, original = record
                if original is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = original
            else:
                _, obj, name, original = record
                setattr(obj, name, original)
        self._undo.clear()
        self._seen.clear()


class Approx:
    """pytest.approx: passes on relative OR absolute tolerance."""

    def __init__(self, expected, rel=1e-6, abs=1e-12):
        self.expected = expected
        self.rel, self.abs = rel, abs

    def _close(self, actual):
        if isinstance(self.expected, (list, tuple)):
            return len(actual) == len(self.expected) and all(
                Approx(e, self.rel, self.abs) == a
                for e, a in zip(self.expected, actual))
        try:
            diff = abs(float(actual) - float(self.expected))
        except (TypeError, ValueError):
            return False
        return diff <= max(self.rel * abs(float(self.expected)), self.abs)

    def __eq__(self, other):
        return self._close(other)

    def __ne__(self, other):
        return not self._close(other)

    def __repr__(self):
        return f"approx({self.expected!r})"


def _fixture(fn=None, scope="function", autouse=False, **kwargs):
    """Marks a callable as a fixture; usable bare or called.

    Only `scope` and `autouse` are meaningful to this runner. Anything else is
    refused outright: a typo (``autuose``) or a real-pytest argument this runner
    does not implement (``params=``, the only way to parametrise a fixture)
    would otherwise be accepted and ignored, and an ignored fixture option
    makes the tests it was supposed to guard pass for the wrong reason.
    """
    if kwargs:
        raise TypeError(
            "tools/run_tests.py's pytest stand-in does not implement "
            f"fixture argument(s) {sorted(kwargs)!r}; the supported ones are "
            "'scope' and 'autouse'. A fixture option this runner ignores would "
            "be silently ignored by the tests that depend on it, so refuse "
            "rather than guess.")

    def wrap(func):
        func.__gp_fixture__ = scope
        func.__gp_autouse__ = autouse
        return func
    return wrap(fn) if callable(fn) else wrap


def _parametrize(argnames, argvalues):
    names = [n.strip() for n in argnames.split(",")] if isinstance(argnames, str) \
        else list(argnames)
    def deco(func):
        existing = list(getattr(func, "__gp_params__", []))
        existing.append((names, list(argvalues)))
        func.__gp_params__ = existing
        return func
    return deco


class _Marks:
    def __init__(self):
        self.parametrize = _parametrize

    def __getattr__(self, name):          # unknown marks are accepted and ignored
        def _ignore(*a, **k):
            def deco(func):
                return func
            return deco if not (len(a) == 1 and callable(a[0])) else a[0]
        return _ignore


def install_fake_pytest() -> types.ModuleType:
    """Register a minimal `pytest` so `import pytest` inside the suite works."""
    module = types.ModuleType("pytest")
    module.fixture = _fixture
    module.mark = _Marks()
    module.approx = Approx
    module.skip = lambda reason="": (_ for _ in ()).throw(Skipped(reason))
    module.fail = lambda reason="": (_ for _ in ()).throw(AssertionError(reason))
    module.MonkeyPatch = MonkeyPatch
    sys.modules["pytest"] = module
    return module


# ---------------------------------------------------------------------------
# Discovery and execution
# ---------------------------------------------------------------------------
def _autouse_names(cls):
    """Names of the autouse fixtures in effect for `cls`, nearest winning.

    Mirrors pytest's rule that a fixture defined on a class shadows a
    module-level autouse of the same name, which is what lets a class install a
    stricter guard than the module's.

    Bases are walked in reverse MRO order and vars() preserves definition order,
    so a class's own autouse fixtures run in the order they were written. That
    order is visible because they are instantiated before the fixtures named in
    the test's own signature.
    """
    names: list = []
    seen: set = set()

    def offer(fn):
        if getattr(fn, "__gp_autouse__", False) and fn.__name__ not in seen:
            seen.add(fn.__name__)
            names.append(fn.__name__)

    if cls is not None:
        for klass in reversed(cls.__mro__):
            for value in vars(klass).values():
                offer(value)
    for value in vars(_TEST_MODULE).values():
        offer(value)
    return names


def _resolve(name, instance, session_cache, per_test, tmp_root):
    """Resolve one fixture argument, honouring scope and yield teardown."""
    if name in per_test:
        return per_test[name][0]
    if name == "tmp_path":
        path = tempfile.mkdtemp(dir=tmp_root)
        per_test["tmp_path"] = (pathlib.Path(path), None)
        return pathlib.Path(path)
    if name == "monkeypatch":
        mp = MonkeyPatch()
        per_test["monkeypatch"] = (mp, mp.undo)
        return mp
    if name == "qapp":
        if "qapp" not in session_cache:
            from PyQt6.QtWidgets import QApplication
            session_cache["qapp"] = QApplication.instance() or QApplication([])
        return session_cache["qapp"]

    owner = getattr(instance, name, None) if instance is not None else None
    if owner is not None and getattr(owner, "__gp_fixture__", False):
        # A fixture defined on the test class: keep the instance so a fixture
        # that itself asks for a class fixture can be found the same way.
        return _call(owner, instance, session_cache, per_test, tmp_root, name)
    module_fixture = getattr(_TEST_MODULE, name, None)
    if module_fixture is not None and getattr(module_fixture, "__gp_fixture__",
                                              False):
        return _call(module_fixture, None, session_cache, per_test, tmp_root,
                     name)
    raise LookupError(f"no fixture named {name!r}")


def _call(fn, instance, session_cache, per_test, tmp_root, name=None):
    scope = getattr(fn, "__gp_fixture__", "function")
    # fn is already a bound method when it came off an instance, so `self` is
    # not in its signature and never needs to be supplied here.
    if scope == "session":
        # The docstring promises session scope, so honour it for ordinary named
        # fixtures too and not just for the built-in qapp. Worth being explicit:
        # a session fixture that quietly re-ran per test would rebuild, say, a
        # QApplication, and an autouse one would do it without any test asking.
        if name and name in session_cache:
            return session_cache[name]
    kwargs = {}
    for pname in inspect.signature(fn).parameters:
        kwargs[pname] = _resolve(pname, instance, session_cache, per_test,
                                 tmp_root)
    result = fn(**kwargs)
    if inspect.isgenerator(result):
        value = next(result)
        if scope == "session":
            session_cache.setdefault("_finalizers", []).append(result.close)
            if name:
                session_cache[name] = value
        else:
            per_test.setdefault("_adhoc", []).append(result.close)
            # Cache under the name as well, but with no finalizer: the _adhoc
            # list owns the one close call. Registering it twice would run
            # teardown twice, and monkeypatch.undo() a second time after the
            # environment has already been restored would stomp on state that a
            # later test has since installed.
            if name:
                per_test[name] = (value, None)
        return value
    if name:
        if scope == "session":
            session_cache[name] = result
        else:
            per_test[name] = (result, None)
    return result


def _param_sets(func):
    """Expand stacked parametrize decorators into concrete kwargs."""
    stacks = getattr(func, "__gp_params__", [])
    combos = [{}]
    for names, values in stacks:
        nxt = []
        for combo in combos:
            for item in values:
                row = dict(combo)
                if len(names) == 1:
                    row[names[0]] = item
                else:
                    row.update(zip(names, item))
                nxt.append(row)
        combos = nxt
    return combos


_TEST_MODULE = None


def collect(module):
    global _TEST_MODULE
    _TEST_MODULE = module
    tests = []
    for cname, cls in vars(module).items():
        if not (inspect.isclass(cls) and cname.startswith("Test")):
            continue
        for mname, meth in sorted(vars(cls).items(),
                                 key=lambda kv: getattr(kv[1], "__code__", None)
                                 and kv[1].__code__.co_firstlineno or 0):
            if not (mname.startswith("test_") and callable(meth)):
                continue
            for row in _param_sets(meth):
                label = f"{cname}::{mname}"
                if row:
                    label += "[" + ",".join(str(v) for v in row.values()) + "]"
                tests.append((label, cls, meth, row))
    for mname, fn in vars(module).items():
        if mname.startswith("test_") and callable(fn):
            for row in _param_sets(fn):
                label = mname + ("[" + ",".join(str(v) for v in row.values())
                                 + "]" if row else "")
                tests.append((label, None, fn, row))
    return tests


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-k", default="", help="substring filter on test id")
    parser.add_argument("-v", action="store_true", help="list every test")
    parser.add_argument("path", nargs="?",
                        default=str(ROOT / "tests" / "test_glasspad.py"))
    args = parser.parse_args(argv)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    install_fake_pytest()

    spec = importlib.util.spec_from_file_location("glasspad_tests", args.path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["glasspad_tests"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        traceback.print_exc()
        print("\ncould not import the test module", file=sys.stderr)
        return 2

    tests = collect(module)
    if args.k:
        tests = [t for t in tests if args.k in t[0]]

    tmp_root = tempfile.mkdtemp(prefix="glasspad-tests-")
    session_cache: dict = {}
    passed = failed = skipped = 0
    failures = []

    for label, cls, meth, row in tests:
        per_test: dict = {}
        instance = cls() if cls is not None else None
        try:
            # Bind first: vars(cls) hands back the plain function, and calling
            # it without the instance is the difference between running the
            # suite and reporting 152 TypeErrors.
            bound = meth.__get__(instance, cls) if instance is not None else meth
            kwargs = dict(row)
            # Autouse first: a guard fixture must be installed before anything
            # the test itself asks for, and before the test body mutates the
            # interpreter state it is there to police.
            for name in _autouse_names(cls):
                # Through _resolve, not _call: that is what binds a class
                # fixture to the instance (its unbound signature still asks for
                # self) and what makes an autouse fixture and a test naming the
                # same fixture share one instance, as pytest does.
                _resolve(name, instance, session_cache, per_test, tmp_root)
            for pname in inspect.signature(bound).parameters:
                if pname not in kwargs:
                    kwargs[pname] = _resolve(pname, instance, session_cache,
                                             per_test, tmp_root)
            bound(**kwargs)
            passed += 1
            if args.v:
                print(f"PASS  {label}")
        except Skipped as exc:
            skipped += 1
            print(f"SKIP  {label}  ({exc})")
        except Exception:
            failed += 1
            failures.append((label, traceback.format_exc()))
            print(f"FAIL  {label}")
        finally:
            for closer in per_test.pop("_adhoc", []):
                try:
                    closer()
                except Exception:
                    pass
            for _, finalizer in [x for x in per_test.values() if x[1]]:
                try:
                    finalizer()
                except Exception:
                    pass

    for closer in session_cache.pop("_finalizers", []):
        try:
            closer()
        except Exception:
            pass
    shutil.rmtree(tmp_root, ignore_errors=True)

    for label, tb in failures:
        print(f"\n{'=' * 70}\nFAILED {label}\n{'=' * 70}\n{tb}")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
