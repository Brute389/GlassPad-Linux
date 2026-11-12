#!/usr/bin/env python3
"""Every public constant must be referenced by something that runs.

Unused constants are not harmless documentation. This file started with seventeen of
them in ``layer_style.py`` -- colours transcribed straight out of ``MainWindow.xaml``
and never wired to a renderer. They looked like a spec. Three were ``"#AAC0D0FF"``,
and the sheet that actually paints those labels uses ``rgba(192,208,255,0.667)``,
because WPF spells colours ``#AARRGGBB``: the leading ``AA`` is alpha (170/255 =
0.667), not a colour byte. So the dead constants were not merely stale, they were a
second and *wrong* answer to a question a reader cannot check without opening the
Windows tree. Wiring them up -- the obvious tidy-up -- would have recoloured the About
box and the confirm dialog.

A constant nobody references is a second opinion, and this guard is what stops a
second opinion accumulating until it is authoritative-looking.

What counts as "used": an ``ast.Name`` load, or an ``ast.Attribute`` access (so
``S.PANEL_BG`` counts), anywhere in ``glasspad/``, ``tests/`` or ``tools/``. Deliberately
not comments, docstrings or README prose: a name that only ever appears in a sentence is
exactly the thing this test is about. If a constant genuinely exists to document
something, it belongs in ``ALLOWLIST`` below with a reason -- which is a better outcome
than a silent assignment, because someone had to write the reason.

Run: python3 tests/test_orphan_constants.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# NAME -> why it may exist unreferenced. Empty is the intended state; anything added
# here should say what reads it (a human, a debugger, a release checklist) and not
# merely that it is informative.
ALLOWLIST: dict[str, str] = {}

SEARCH = [ROOT / "glasspad", ROOT / "tests", ROOT / "tools"]


def is_public_const(name: str) -> bool:
    return (name.isupper() and len(name) >= 3 and not name.startswith("_")
            and name not in {"__ALL__"})


def declared_constants(path: pathlib.Path) -> list[tuple[str, int, int]]:
    """(name, first_line, last_line) for module-level UPPER_CASE assignments."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    out = []
    for node in tree.body:                     # module level only, by construction
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name) and is_public_const(t.id):
                out.append((t.id, node.lineno, node.end_lineno))
    return out


def python_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for base in SEARCH:
        if not base.is_dir():
            continue
        files.extend(sorted(base.glob("*.py")))
        for extra in sorted(base.iterdir()):   # tools/glasspad-profile has no suffix
            if extra.is_file() and extra.suffix not in {".py", ".md", ".txt", ".png", ".json", ".run", ".sh"}:
                files.append(extra)
    return files


def used_names(files: list[pathlib.Path]) -> set[str]:
    """Names loaded or attribute-accessed anywhere in `files`.

    Definition sites are excluded by only collecting Load contexts and attribute
    names; ``FOO = 1`` puts FOO in a Store context and does not mark itself used.
    """
    seen: set[str] = set()
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                seen.add(node.id)
            elif isinstance(node, ast.Attribute):
                seen.add(node.attr)
            elif isinstance(node, ast.Global):
                seen.update(node.names)
    return seen


def orphans(files: list[pathlib.Path] | None = None,
            pkg_dir: pathlib.Path | None = None):
    """Unreferenced module-level constants in `pkg_dir` (default: the shipped package).

    `pkg_dir` is a parameter rather than a constant because the negative control below
    has to run the same code over a temporary tree -- hardcoding the package here made
    that control scan nothing and report a clean result, which is precisely the
    silent-green failure this file exists to prevent.
    """
    files = files or python_files()
    pkg_dir = pkg_dir or ROOT / "glasspad"
    used = used_names(files)
    out = []
    for path in files:
        if path.parent != pkg_dir:
            continue                           # the package is what ships
        for name, _a, _b in declared_constants(path):
            if name not in used and name not in ALLOWLIST:
                out.append((path.name, name))
    return out


def test_no_orphan_constants_in_the_package():
    bad = orphans()
    assert not bad, (
        "constants nothing references:\n  " + "\n  ".join(f"{m}: {n}" for m, n in bad)
        + "\n\nEither wire it into what needs the value, or delete it. If it truly only "
          "documents something, add it to ALLOWLIST with a sentence saying who reads it.")


def test_allowlist_entries_are_earning_their_place():
    """An allowlist entry for a constant that has since become used is dead too."""
    used = used_names(python_files())
    stale = [n for n in ALLOWLIST if n in used]
    assert not stale, f"referenced now, no longer needs the exemption: {stale}"


def test_the_detector_actually_detects():
    """A meta-test that reads nothing is worse than no meta-test.

    This one has already been vacuous once in anger -- the doc-pointer check spent a
    while flagging `self.sh` as a missing file and nothing as actually missing, which
    reads identical to a clean tree. So the detector is exercised on synthetic sources
    where the expected answer is known, not on whichever constants happen to exist.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        pkg = tmp / "glasspad"
        pkg.mkdir()
        (pkg / "thing.py").write_text(
            "USED = 3\n"
            "ORPHAN = 7\n"
            "_PRIVATE = 9\n"
            "lower_case = 11\n"
            "def f():\n"
            "    return USED\n"
        )
        (pkg / "other.py").write_text(
            "from thing import USED\n"
            "def g():\n"
            "    return USED\n"
        )
        found = {name for _m, name in orphans([*pkg.glob("*.py")], pkg_dir=pkg)}
        assert found == {"ORPHAN"}, found

        # and the two ways a real check goes silently green: nothing parsed, nothing found
        assert ("thing.py", "USED") not in found
        assert used_names([pkg / "thing.py"]) >= {"USED"}
        assert declared_constants(pkg / "thing.py")
        # a file that will not parse must not manufacture a pass out of an empty scan
        broken = tmp / "broken.py"
        broken.write_text("def (:\n")
        assert declared_constants(broken) == []


if __name__ == "__main__":
    fails = 0
    ran = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            ran += 1
            try:
                fn()
                print(f"  ok    {name}")
            except AssertionError as exc:
                fails += 1
                print(f"  FAIL  {name}:\n{exc}")
    print(f"\n  {ran - fails} passed, {fails} failed of {ran}")
    sys.exit(1 if fails else 0)
