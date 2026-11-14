"""Two first-run routes, and the collisions between them.

A user may install GlassPad by running the AppImage (`--install`), or by dropping it on
an AppImage manager -- Shelly on CachyOS, AppImageLauncher, Discover's AppImage backend.
Managers are genuinely better at part of this: they move the file somewhere sensible,
write the menu entry, refresh the caches, and can update it later. They cannot grant
/dev/uinput (no udev rule writing anywhere in their binaries), and they move files
underneath anything that points at the old path.

So the two routes have to coexist:

  * a `glasspad` command left dangling by a manager's move is recognised as OURS and
    repaired, not refused as "something else owns it";
  * a menu entry that already launches this exact file is not duplicated, while the CLI
    command -- which no manager writes -- still is;
  * a drag-and-drop user with no terminal can still reach the privileged step, because
    the overlay's About box opens a window running it.

Run: python3 tests/test_flexible_install.py   (no pytest, no root, no network)
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import bundle, desktop_entry as de, terminal  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


class env:
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


def make_bin(dirpath, names):
    os.makedirs(dirpath, exist_ok=True)
    for n in names:
        p = os.path.join(dirpath, n)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho fake terminal\n")
        os.chmod(p, 0o755)


class capture_popen:
    """Swap terminal's spawn call for a recorder (and never open a window in CI)."""

    def __init__(self, raises=None):
        self.calls = []
        self.raises = raises
        self.real = terminal.subprocess.Popen

    def __enter__(self):
        def fake(cmd, *a, **k):
            self.calls.append((list(cmd), k))
            if self.raises:
                raise self.raises
            return object()
        terminal.subprocess.Popen = fake
        return self

    def __exit__(self, *exc):
        terminal.subprocess.Popen = self.real
        return False


# --- the terminal launcher ----------------------------------------------------
def test_the_first_terminal_in_priority_order_is_used():
    tmp = tempfile.mkdtemp()
    try:
        bindir = os.path.join(tmp, "bin")
        make_bin(bindir, ["konsole", "alacritty"])
        cmd = [os.path.join(tmp, "My GlassPad.AppImage"), "--install-uinput"]
        with env(PATH=bindir, GLASSPAD_TERMINAL=""), capture_popen() as cap:
            ok, note = terminal.open_command_window(cmd)
        check(f"opened (note: {note[:40]})", ok)
        check(f"konsole wins over alacritty ({cap.calls[0][0][:3]})",
              cap.calls[0][0][:3] == ["konsole", "--hold", "-e"])
        script = cap.calls[0][0][-1]
        check("the path with a space stays ONE quoted token inside the shell script",
              f"'{cmd[0]}' --install-uinput" in script)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_terminals_that_take_the_child_as_one_string_get_one_string():
    tmp = tempfile.mkdtemp()
    try:
        bindir = os.path.join(tmp, "bin")
        make_bin(bindir, ["xfce4-terminal"])
        with env(PATH=bindir, GLASSPAD_TERMINAL=""), capture_popen() as cap:
            ok, _ = terminal.open_command_window(["/tmp/glasspad", "--install-uinput"])
        argv = cap.calls[0][0]
        check(f"opened via xfce4 ({argv[:3]})", ok and argv[0] == "xfce4-terminal")
        check("the child is passed as a single --command value",
              len(argv) == 4 and argv[2] == "--command"
              and argv[3].startswith("sh -c "))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_named_terminal_is_honoured_and_a_missing_one_is_not_silently_swapped():
    """$GLASSPAD_TERMINAL is an escape hatch; quietly picking something else wastes it."""
    tmp = tempfile.mkdtemp()
    try:
        bindir = os.path.join(tmp, "bin")
        make_bin(bindir, ["konsole", "alacritty"])
        with env(PATH=bindir, GLASSPAD_TERMINAL="alacritty"), capture_popen() as cap:
            terminal.open_command_window(["/tmp/glasspad", "--status"])
        check(f"the named terminal is used ({cap.calls[0][0][:2]})",
              cap.calls[0][0][:2] == ["alacritty", "-e"])
        with env(PATH=bindir, GLASSPAD_TERMINAL="kitty"), capture_popen() as cap:
            ok, note = terminal.open_command_window(["/tmp/glasspad", "--status"])
        check("a named-but-absent terminal does not silently fall back",
              ok is False and not cap.calls)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_window_stays_open_after_the_command_exits():
    """Half the terminals here close the instant their child does.

    A three-second installer that shows an unreadable flash teaches the user nothing,
    and the part they need is the verdict that scrolled past.
    """
    wrapped = terminal.wrap_keep_open(["true"])
    script = wrapped[-1]
    check("the wrapper waits for the exit code", "rc=$?" in script)
    check("prints it", "exited $rc" in script)
    check("and waits for the user", "read " in script)


def test_no_terminal_is_reported_with_the_command_to_type():
    tmp = tempfile.mkdtemp()
    try:
        with env(PATH=os.path.join(tmp, "empty"), GLASSPAD_TERMINAL=""), \
                capture_popen():
            ok, note = terminal.open_command_window(["/tmp/glasspad", "--install-uinput"])
        check(f"reports rather than pretending (ok={ok})", ok is False)
        check("and hands over the exact command",
              "/tmp/glasspad --install-uinput" in note)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_terminal_that_fails_to_start_is_not_reported_as_success():
    tmp = tempfile.mkdtemp()
    try:
        bindir = os.path.join(tmp, "bin")
        make_bin(bindir, ["konsole"])
        with env(PATH=bindir, GLASSPAD_TERMINAL=""), \
                capture_popen(raises=OSError("Exec format error")):
            ok, note = terminal.open_command_window(["/tmp/glasspad", "--install-uinput"])
        check(f"the failure is surfaced ({note[:40]})", ok is False and "konsole" in note)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_command_built_for_a_terminal_matches_the_install_shape():
    argv, cwd, extra = terminal.install_command("appimage", "/tmp/My GlassPad.AppImage",
                                                ["--install-uinput"])
    check("a bundle is just the file plus flags",
          argv == ["/tmp/My GlassPad.AppImage", "--install-uinput"] and cwd is None)

    argv, cwd, extra = terminal.install_command("checkout", "/home/x/tree", ["--status"])
    check("a checkout needs -m glasspad run from the tree",
          argv[1:] == ["-m", "glasspad", "--status"] and cwd == "/home/x/tree")
    check("with PYTHONPATH and PYTHONSAFEPATH, so the terminal's cwd cannot shadow it",
          extra.get("PYTHONPATH") == "/home/x/tree"
          and extra.get("PYTHONSAFEPATH") == "1")


# --- the CLI command, after a manager moves the file --------------------------
def test_our_dangling_link_is_repaired_not_refused():
    """"refused: something else owns it" -- about a link we wrote.

    A manager's move breaks the symlink; realpath no longer matches and the file cannot
    be read for its marker, so every test of ownership answered "not ours". The user is
    told to go delete a file that is theirs, by a program that could have fixed it.
    """
    tmp = tempfile.mkdtemp()
    try:
        img = os.path.join(tmp, "GlassPad-1.1.1-x86_64.AppImage")
        open(img, "w").close()
        os.chmod(img, 0o755)
        bindir = os.path.join(tmp, ".local", "bin")
        os.makedirs(bindir)
        dest = os.path.join(bindir, "glasspad")
        os.symlink(os.path.join(tmp, "moved-away", "GlassPad-1.1.1-x86_64.AppImage"),
                   dest)
        buf = io.StringIO()
        with env(HOME=os.path.join(tmp), GLASSPAD_BIN_DIR=bindir,
                 PATH=bindir + ":/usr/bin:/bin", APPIMAGE=img):
            check("status calls it dangling",
                  bundle.cli_link_state()[0] == "dangling")
            with contextlib.redirect_stdout(buf):
                rc = bundle.install_cli_link()
            # These two stay INSIDE the env block. They once sat outside it and passed --
            # not because the sandbox worked, but because this machine had a real
            # ~/.local/bin/glasspad installed, which is exactly the file the assertions
            # were reading. Wiping the machine turned them red and proved the point: an
            # assertion that survives on unrelated host state is testing somebody else's
            # install.
            out = buf.getvalue()
            check(f"repaired (rc={rc})", rc == 0)
            check("says it was broken and what it pointed at",
                  "broken" in out and "moved" in out)
            check(f"now points at the running file ({os.readlink(dest)})",
                  os.path.realpath(dest) == os.path.realpath(img))
            check("and the state agrees", bundle.cli_link_state()[0] == "ok")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_someone_elses_dangling_link_is_still_left_alone():
    tmp = tempfile.mkdtemp()
    try:
        img = os.path.join(tmp, "GlassPad.AppImage")
        open(img, "w").close()
        bindir = os.path.join(tmp, ".local", "bin")
        os.makedirs(bindir)
        dest = os.path.join(bindir, "glasspad")
        os.symlink("/nowhere/firefox", dest)
        buf = io.StringIO()
        with env(HOME=os.path.join(tmp), GLASSPAD_BIN_DIR=bindir,
                 PATH=bindir + ":/usr/bin:/bin", APPIMAGE=img):
            with contextlib.redirect_stdout(buf):
                rc = bundle.install_cli_link()
            check(f"refused (rc={rc})", rc != 0)
            check("and the link is untouched", os.path.islink(dest)
                  and os.readlink(dest) == "/nowhere/firefox")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- menu entries written by someone else ------------------------------------
def _run_desktop(home, extra_env, argv=("--install-desktop",)):
    e = dict(os.environ)
    e.update(HOME=home,
             XDG_DATA_HOME=os.path.join(home, ".local", "share"),
             XDG_CONFIG_HOME=os.path.join(home, ".config"),
             PYTHONPATH=ROOT)
    e.update(extra_env)
    return subprocess.run([sys.executable, "-c",
                           "import sys;from glasspad.__main__ import main;"
                           "sys.exit(main(sys.argv[1:]))"] + list(argv),
                          cwd=home, env=e, capture_output=True, text=True, timeout=300)


def _manager_entry(apps_dir, exec_target, name="GlassPad (AppImage Manager)"):
    os.makedirs(apps_dir, exist_ok=True)
    path = os.path.join(apps_dir, "org.appimage.manager.GlassPad.desktop")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("[Desktop Entry]\nType=Application\n"
                 f"Name={name}\nExec=\"{exec_target}\"\n")
    return path


def test_an_existing_manager_entry_is_not_duplicated():
    """Two menu items for one app, and only one of them knows where the file went."""
    home = tempfile.mkdtemp()
    try:
        img = os.path.join(home, "Applications", "GlassPad-1.1.1-x86_64.AppImage")
        os.makedirs(os.path.dirname(img), exist_ok=True)
        open(img, "w").close()
        os.chmod(img, 0o755)
        apps = os.path.join(home, ".local", "share", "applications")
        theirs = _manager_entry(apps, img)

        r = _run_desktop(home, {"APPIMAGE": img})
        ours = os.path.join(apps, de.DESKTOP_NAME)
        check(f"exits 0 (rc={r.returncode})", r.returncode == 0)
        check("says an entry already exists", "already exists" in r.stdout)
        check("naming the file it found", theirs in r.stdout)
        check("and does NOT write a second one", not os.path.exists(ours))
        check("while still installing the command line, which managers do not write",
              "command line" in r.stdout)

        r2 = _run_desktop(home, {"APPIMAGE": img}, ("--install-desktop", "--force"))
        check(f"--force writes ours anyway (rc={r2.returncode})", os.path.exists(ours))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_an_unrelated_entry_does_not_trigger_the_adoption():
    home = tempfile.mkdtemp()
    try:
        img = os.path.join(home, "GlassPad.AppImage")
        open(img, "w").close()
        os.chmod(img, 0o755)
        apps = os.path.join(home, ".local", "share", "applications")
        _manager_entry(apps, "/opt/something-else/AppImage")
        r = _run_desktop(home, {"APPIMAGE": img})
        ours = os.path.join(apps, de.DESKTOP_NAME)
        check(f"our entry is written normally (rc={r.returncode})",
              r.returncode == 0 and os.path.exists(ours))
        check("and nothing mentions a manager", "already exists" not in r.stdout)
    finally:
        shutil.rmtree(home, ignore_errors=True)


# --- the no-terminal path, from inside the overlay ---------------------------
def test_the_overlay_offers_the_grant_only_when_there_is_no_pad():
    """Read from the source: the pill must be conditional, and must not escalate.

    Building a real Gtk overlay in a test is out of scope for this suite; what matters
    here is the contract -- shown only in the failure state, and it launches our own
    command rather than becoming a second, less auditable privilege path.
    """
    src = open(os.path.join(ROOT, "glasspad", "overlay_layer.py"),
               encoding="utf-8").read()
    panel = src.split("def _build_info_panel")[1].split("def _build_save_confirm")[0]
    check("the About box gates the pill on the null pad",
          "fell_back_to_null" in panel and "_grant_access" in panel)
    grant = src.split("def _grant_access")[1].split("\n    def ")[0]
    check("the handler opens a terminal rather than escalating itself",
          "open_command_window" in grant)
    check("and it contains no privilege path of its own",
          not any(w in grant for w in ("pkexec", "sudo", "usermod", "setfacl")))
    check("failures are said out loud on screen, not only to invisible stdout",
          "_say(" in grant and "stderr" in grant)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
