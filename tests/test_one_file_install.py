"""The first-run surface: what a stranger is told to type, and whether it works.

Someone who has never run GlassPad meets four things: a dependency they must install,
a device they must be granted, a menu entry, and messages that tell them to run
`glasspad ...`. The first two have exact package and file names in them; the third and
fourth are only real if a command called `glasspad` exists. Three bugs lived here at
once:

  * `sudo pacman -S libgtk4-layer-shell` -- printed in five places -- is not a package.
    Arch and CachyOS ship `gtk4-layer-shell`, and `Provides:` is empty, so the very
    first instruction a virgin user copy-pasted could not work.
  * Seven messages said `glasspad --install-uinput` when the user's GlassPad is one
    file in a Downloads folder and bash does not search the current directory.
  * Nothing created that command.

So these tests are written against the machine, not against the source: the package
name is re-derived from `pacman -Qo` on the installed library, and every hint is
resolved and then looked for on PATH.

Run: python3 tests/test_one_file_install.py   (no pytest, no display, no root)
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import bundle  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_SO = "/usr/lib/libgtk4-layer-shell.so.0"
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


class env:
    """Scoped environment changes -- these tests all read os.environ at call time."""

    def __init__(self, **changes):
        self.changes = changes
        self.saved = {}

    def __enter__(self):
        for k, v in self.changes.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def arch_row():
    for label, matches, pkg, verified in bundle.DEPENDENCY_PACKAGES:
        if "arch" in matches:
            return pkg
    return None


def test_the_arch_package_name_is_the_one_pacman_reports():
    """The row in DEPENDENCY_PACKAGES must equal what the repository actually says.

    This is the test that would have caught the original mistake, and it is written so
    it cannot rot: it does not assert a hard-coded name, it asks pacman who owns the
    installed library and requires our table to agree. On a machine without the library
    or without pacman it says so instead of passing silently.
    """
    pacman = shutil.which("pacman")
    if not (pacman and os.path.exists(LIB_SO)):
        print(f"  skip no pacman ({bool(pacman)}) or no {LIB_SO} here")
        return
    r = subprocess.run([pacman, "-Qo", LIB_SO], capture_output=True, text=True,
                       timeout=60)
    owner = r.stdout.strip().split()[-2] if r.returncode == 0 and r.stdout.strip() else ""
    table = arch_row()
    check(f"pacman says the library belongs to '{owner}'", bool(owner))
    check(f"our table says '{table}' for the Arch family", table == owner)
    check("and we never print the name that does not exist (libgtk4-layer-shell)",
          "libgtk4-layer-shell" != table)


def test_the_dependency_note_names_the_local_package_first():
    with env():
        note = bundle.dependency_hint()
    ids = bundle.os_release_ids()
    if not ids:
        print("  skip /etc/os-release unreadable here")
        return
    rows = bundle.dependency_rows()
    check("the row for this distribution comes first", rows and rows[0][3])
    check("and the note leads with a real command, not a menu of three",
          "sudo" in note and rows[0][1] in note)
    check("rows we could not check are labelled as unverified",
          "not verified" in note or all(r[2].startswith("verified") for r in rows))


def test_hint_resolves_to_the_appimage_when_that_is_what_is_running():
    """A bundle user's hint must be the file they have, quoted if it needs it."""
    tmp = tempfile.mkdtemp()
    try:
        img = os.path.join(tmp, "GlassPad-1.1.0-x86_64.AppImage")
        open(img, "w").close()
        with env(APPIMAGE=img, PATH="/usr/bin:/bin"):
            got = bundle.cli_hint()
        check(f"the AppImage path is printed verbatim ({got})", got == img)

        spaced = os.path.join(tmp, "My Apps", "Glass Pad.AppImage")
        os.makedirs(os.path.dirname(spaced), exist_ok=True)
        open(spaced, "w").close()
        with env(APPIMAGE=spaced, PATH="/usr/bin:/bin"):
            got = bundle.cli_hint()
        check(f"a path with spaces is quoted so it survives the shell ({got})",
              got == f'"{spaced}"')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_stranger_on_path_is_never_the_one_we_tell_people_to_run():
    """`glasspad` on PATH that points elsewhere is the dangerous answer.

    Telling somebody to run a different copy of the app than the one that just warned
    them is how you install a udev rule while fixing nothing -- and a stale copy in
    ~/.local/bin is exactly as likely as a fresh one. The hint only shortens to
    `glasspad` when that name resolves to THIS install.
    """
    tmp = tempfile.mkdtemp()
    try:
        img = os.path.join(tmp, "GlassPad.AppImage")
        open(img, "w").close()
        # Executable, like a real AppImage: which() refuses a non-executable file, so a
        # 644 stand-in would test the wrong thing and fail for the right reason.
        os.chmod(img, 0o755)
        other = os.path.join(tmp, "bin")
        os.makedirs(other)
        stranger = os.path.join(other, "glasspad")
        open(stranger, "w").write("#!/bin/sh\necho I am an old build\n")
        os.chmod(stranger, 0o755)

        with env(APPIMAGE=img, PATH=other + ":/usr/bin:/bin"):
            got = bundle.cli_hint()
        check(f"a foreign `glasspad` on PATH is not used ({got})", got == img)

        ours = os.path.join(other, "glasspad")
        os.unlink(ours)
        os.symlink(img, ours)
        with env(APPIMAGE=img, PATH=other + ":/usr/bin:/bin"):
            got = bundle.cli_hint()
        check(f"our own symlink shortens to `glasspad` ({got})", got == "glasspad")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_link_is_not_written_where_it_could_not_be_run():
    """Installing a command into a directory that is not on PATH is a false success."""
    tmp = tempfile.mkdtemp()
    try:
        bindir = os.path.join(tmp, ".local", "bin")
        with env(HOME=os.path.join(tmp), GLASSPAD_BIN_DIR=bindir,
                 PATH="/usr/bin:/bin", APPIMAGE=""):
            rc = bundle.install_cli_link()
        check(f"refused, not written (exit {rc})", rc != 0)
        check("nothing appeared in the directory", not os.path.exists(
            os.path.join(bindir, "glasspad")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_link_is_written_and_then_becomes_the_hint():
    tmp = tempfile.mkdtemp()
    try:
        bindir = os.path.join(tmp, ".local", "bin")
        os.makedirs(bindir)
        with env(HOME=os.path.join(tmp), GLASSPAD_BIN_DIR=bindir,
                 PATH=bindir + ":/usr/bin:/bin", APPIMAGE=""):
            rc = bundle.install_cli_link()
            dest = os.path.join(bindir, "glasspad")
            check(f"installed (exit {rc})", rc == 0)
            check("it is executable", os.access(dest, os.X_OK))
            text = open(dest).read()
            check("it names the tree it points at",
                  bundle.LINK_MARKER + ROOT in text)
            check("and it isolates the module search like AppRun does",
                  "PYTHONSAFEPATH=1" in text)
            check("from then on the messages use the short form",
                  bundle.cli_hint() == "glasspad")
            again = bundle.install_cli_link()
            check("running it twice is idempotent, not an error", again == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_someone_elses_file_is_never_replaced():
    tmp = tempfile.mkdtemp()
    try:
        bindir = os.path.join(tmp, ".local", "bin")
        os.makedirs(bindir)
        dest = os.path.join(bindir, "glasspad")
        open(dest, "w").write("#!/bin/sh\necho not yours\n")
        os.chmod(dest, 0o755)
        with env(HOME=os.path.join(tmp), GLASSPAD_BIN_DIR=bindir,
                 PATH=bindir + ":/usr/bin:/bin", APPIMAGE=""):
            rc = bundle.install_cli_link()
            check(f"refused to clobber it (exit {rc})", rc != 0)
            check("contents unchanged", open(dest).read().startswith("#!/bin/sh\necho not yours"))
            check("uninstall leaves it alone too", bundle.uninstall_cli_link() == 0
                  and os.path.exists(dest))
            rc = bundle.install_cli_link(force=True)
            check(f"--force is obeyed when asked for explicitly (exit {rc})", rc == 0)
            check("and then uninstall removes ours", bundle.uninstall_cli_link() == 0
                  and not os.path.exists(dest))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _hint_is_runnable(hint):
    """Can the string we print as an instruction actually be executed here?"""
    h = hint.strip().strip('"')
    if hint.startswith('"') and not os.path.exists(h):
        return False, f"quoted path missing: {h}"
    if h.startswith("python3 -m glasspad"):
        return True, "module form"
    if os.path.sep in h:
        return (os.path.exists(h), h)
    return (shutil.which(h) is not None, f"{h} on PATH={shutil.which(h)}")


def _run(argv, home, path):
    e = dict(os.environ)
    e.update(HOME=home, PATH=path, GLASSPAD_BIN_DIR=os.path.join(home, ".local", "bin"))
    e.pop("APPIMAGE", None)
    # NOTE: deliberately does NOT clear GLASSPAD_LAYER_SHELL -- a test that sets an
    # override and then has the harness remove it proves nothing, and the resolver
    # treats a bad override as "not found" rather than falling back, which is the
    # behaviour step 1 leans on.
    return subprocess.run([sys.executable, "-m", "glasspad"] + argv, capture_output=True,
                          text=True, timeout=300, cwd=ROOT, env=e)


def test_install_dry_run_writes_nothing_anywhere():
    """The rehearsal has to be trustworthy, because it is how people decide to run it."""
    home = tempfile.mkdtemp()
    try:
        before = _etc_state()
        r = _run(["--install", "--dry-run"], home, "/usr/bin:/bin")
        files = _tree(home)
        check(f"exits 0 on a rehearsal (rc={r.returncode})", r.returncode == 0)
        check("no pkexec prompt, no elevation", "126" not in r.stdout)
        check("nothing at all created under $HOME", files == [])
        check("and /etc is untouched", _etc_state() == before)
        check("the rehearsal covers every step, including the command line",
              "would write" in r.stdout and "wrapper script" in r.stdout
              and "dry run" in r.stdout)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _etc_state():
    st = {}
    for f in ("/etc/udev/rules.d/99-glasspad-uinput.rules",
              "/etc/modules-load.d/uinput.conf"):
        try:
            s = os.stat(f)
            st[f] = (s.st_mtime_ns, s.st_size)
        except OSError:
            st[f] = None
    return st


def _tree(root):
    out = []
    for base, _dirs, files in os.walk(root):
        for f in files:
            out.append(os.path.relpath(os.path.join(base, f), root))
    return sorted(out)


def test_install_refuses_to_go_further_without_the_library():
    """Step 1 failing means not asking for a password for an overlay that cannot draw.

    The resolver is stubbed because "the library is missing" is not a state this
    machine can be put into from the outside -- /usr/lib is not ours to hide -- and
    asserting against a real path would make the test pass for the wrong reason the
    moment the package gets installed.
    """
    import contextlib
    import io

    import glasspad.__main__ as m

    saved = bundle.layer_shell_path
    bundle.layer_shell_path = lambda *a, **k: (None, "stubbed for this test")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = m.install_everything(["--dry-run"])
    finally:
        bundle.layer_shell_path = saved
    out = buf.getvalue()
    check(f"exits nonzero (rc={rc})", rc == 1)
    check("says the library was not found", "NOT FOUND" in out)
    check("names the package for this distribution",
          any(r[1] in out for r in bundle.dependency_rows()))
    check("and stops before the password step", "[2/3]" not in out)
    check("without pretending a rehearsal changed anything",
          "rehearsal" not in out)


def test_the_bad_override_reaches_the_child_process():
    """Catches the harness bug above: an override must survive into the subprocess."""
    home = tempfile.mkdtemp()
    try:
        e = dict(os.environ)
        e["GLASSPAD_LAYER_SHELL"] = "/nonexistent/libgtk4-layer-shell.so.0"
        r = subprocess.run([sys.executable, "-c",
                            "import os; print(os.environ['GLASSPAD_LAYER_SHELL'])"],
                           capture_output=True, text=True, timeout=60, env=e)
        check("the override is passed through by _run-style harnesses",
              "/nonexistent/libgtk4-layer-shell.so.0" in r.stdout)
        # Set it in THIS process so the child inherits it -- a harness that copies
        # os.environ cannot carry a variable that was never in os.environ.
        with env(GLASSPAD_LAYER_SHELL="/nonexistent/libgtk4-layer-shell.so.0"):
            r = _run(["--backend", "layer", "--status"], home, "/usr/bin:/bin")
        check(f"and --status reports it as a problem (rc={r.returncode})",
              r.returncode != 0)
        check("naming the override rather than shrugging",
              "GLASSPAD_LAYER_SHELL" in r.stdout)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_status_ends_with_exactly_one_next_step():
    r = _run(["--backend", "layer", "--status"], os.environ.get("HOME", "/tmp"),
             os.environ.get("PATH", "/usr/bin:/bin"))
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("NEXT:")]
    check(f"exactly one NEXT line ({len(lines)})", len(lines) == 1)
    check("and it is the resolved hint, not a hard-coded name",
          bundle.cli_hint() in (lines[0] if lines else ""))
    ok, why = _hint_is_runnable(bundle.cli_hint())
    check(f"the hint --status just printed is runnable on this machine ({why})", ok)


def test_no_message_hard_codes_a_command_that_might_not_exist():
    """Every literal `glasspad --something` in the shipped code is a claim about PATH.

    Scanned from tokens rather than with grep so comments and docstrings -- where the
    old wording is quoted to explain why it was wrong -- do not trip it.

    Two files are allowed to spell it, because in both cases the text is printed where a
    command can actually be typed -- and the test then checks they keep doing that, rather
    than letting the allowlist widen into "a file that happens to contain instructions":

      * `deps.py` -- its prose goes to a terminal (`--explain`, and the terminal window the
        notice opens), where the CLI form is right and a PATH lookup is irrelevant.
      * `__main__.py` -- `--status` and the no-display fallback, which are terminal output
        by definition.

    The rule still bites everywhere else, which is where it matters: the overlay's on-screen
    messages are read on a tablet, held in two hands, with no terminal anywhere near, and
    those go through `bundle.cli_hint()`.
    """
    import ast

    TERMINAL_CONTEXT = {"deps.py", "__main__.py"}
    offenders = []
    spelled = []
    pkg = os.path.join(ROOT, "glasspad")
    for name in sorted(os.listdir(pkg)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(pkg, name)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        # Docstrings are prose about the code, and several quote the old wrong wording
        # on purpose; only constants that are not a docstring can be user-facing text.
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                docstrings.add(id(node.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                if "glasspad --" in node.value:
                    (spelled if name in TERMINAL_CONTEXT else offenders
                     ).append(f"{name}:{node.lineno}")
    check("no string literal tells the user to run `glasspad --...` "
          + (f" ({', '.join(offenders)})" if offenders else ""), not offenders)
    # The allowlist is a claim about WHERE text is printed, so the claim is checked: if the
    # terminal-context files stopped printing their prose to a terminal -- by routing it
    # into the overlay, say -- the exemption has to go with it, and this fails.
    with open(os.path.join(ROOT, "glasspad", "deps.py"), encoding="utf-8") as fh:
        dep_src = fh.read()
    # Not "must not import Gtk": probe_bound() opens a Gdk display on purpose, and a check
    # that banned the string "Gtk" failed against correct code on its first run. The
    # invariant is about SURFACE: deps.py must never draw, because its text is read in a
    # terminal -- and if it ever starts drawing, its prose belongs in notice.py with the
    # rest of the widgets, and this exemption has to be withdrawn.
    forbidden = ("Gtk.Window", "Gtk.Application", "Gtk.Label", "overlay_layer",
                 "import notice")
    drew = [f for f in forbidden if f in dep_src]
    check(f"deps.py stays terminal-only: no widgets, no overlay ({drew})", not drew)
    with open(os.path.join(ROOT, "glasspad", "__main__.py"), encoding="utf-8") as fh:
        main_src = fh.read()
    check("and __main__.py is where --status and the no-display fallback live",
          "def report_status" in main_src and "print_notice" in main_src)
    check("the scan found the spelled forms it knows about",
          len(spelled) >= 3)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
