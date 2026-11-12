"""The bundle's privileged half: one rule file, one installer, no surprises.

A downloaded AppImage cannot contain /etc/udev/rules.d, so it carries the rule and
the script that writes it instead. That makes the pair part of the security surface
of the product, and it runs as root on strangers' machines, so it gets the same
treatment as the painters: the policy is pinned, the refusal paths are pinned, and
the paths that must NOT escalate are pinned.

test_dry_run_never_elevates exists because of how this feature was written. A probe
of the refusal path pointed $GLASSPAD_INSTALLER_DIR at an empty directory, expecting
"installer not found" -- but find_installer() also searches the package root, found
the real checkout, and ran the real installer through pkexec. Correct behaviour,
unintended consequence, and no way to rehearse the command without changing /etc.
"""
import getpass
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import bundle, pad  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


def sh(argv, cwd=None, env=None):
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=180)


# --- the rule itself ---------------------------------------------------------
def test_rule_is_one_file_and_install_sh_has_no_copy_of_it():
    """Exactly one place states the permission policy.

    install.sh used to write the rule from a heredoc. Duplicating it -- one copy
    in the script, one in a file -- is how a machine ends up running the version
    nobody reviewed, so the script now copies this file or refuses.
    """
    path = os.path.join(ROOT, bundle.RULE_REL)
    check(f"the rule file exists at {bundle.RULE_REL}", os.path.isfile(path))
    script = open(os.path.join(ROOT, "install.sh")).read()
    check("install.sh contains no embedded KERNEL== rule of its own",
          'KERNEL=="uinput"' not in script)
    check("install.sh reads the rule from UDEV_RULE_SRC",
          "UDEV_RULE_SRC" in script and 'cat "$UDEV_RULE_SRC"' in script)


def test_rule_grants_the_input_group_and_never_the_world():
    """0660 + group input, not 0666.

    A virtual Xbox pad is a keystroke-injecting device as far as the desktop is
    concerned. 0666 would hand it to every user and every sandboxed app on the
    machine, which is a wider door than GlassPad needs and has no business
    opening on somebody else's behalf.
    """
    text = open(os.path.join(ROOT, bundle.RULE_REL)).read()
    check('the rule matches KERNEL=="uinput"', 'KERNEL=="uinput"' in text)
    check('the rule hands the node to GROUP="input"', 'GROUP="input"' in text)
    check('the rule is MODE="0660"', 'MODE="0660"' in text)
    # Comments aside: the file explains at length why 0666 is wrong, so only the
    # active lines can be held to the standard. That also pins the rule to a
    # single instruction -- a second KERNEL line would be a second policy.
    active = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    check(f"nothing outside the comments mentions 0666 ({len(active)} active line(s))",
          all("0666" not in l for l in active))
    check(f"exactly one rule is in effect: {active}", len(active) == 1)
    check("the static_node option survives udevadm trigger",
          'OPTIONS+="static_node=uinput"' in text)


def test_udevadm_accepts_the_rule():
    """The distro's own linter, when the distro ships one."""
    if not shutil.which("udevadm"):
        check("udevadm absent on this box; nothing to verify against", True)
        return
    r = sh(["udevadm", "verify", os.path.join(ROOT, bundle.RULE_REL)])
    check(f"udevadm verify accepts the rule (exit {r.returncode}: {r.stderr.strip()[:80]})",
          r.returncode == 0)


# --- resolving it ------------------------------------------------------------
def test_installer_and_rule_resolve_from_a_checkout():
    got = bundle.find_installer()
    check(f"find_installer() finds install.sh ({got})", got == os.path.join(ROOT, "install.sh"))
    rule = bundle.installer_rule_src(got)
    check("the rule is found beside the installer", rule == os.path.join(ROOT, bundle.RULE_REL))
    check("installer_rule_src(None) is None, not a crash",
          bundle.installer_rule_src(None) is None)


def test_install_sh_refuses_when_the_rule_file_is_gone():
    """No fallback policy. A missing rule is an error, not an opportunity to improvise."""
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(os.path.join(ROOT, "install.sh"), os.path.join(d, "install.sh"))
        r = sh([os.path.join(d, "install.sh"), "--uinput", "--dry-run"])
        # --dry-run still exits non-zero here: the installer cannot describe an
        # install it is unable to perform.
        check("missing rule file fails the run", r.returncode != 0)
        check("and says which file it wanted",
              "rule file not found" in r.stdout and "glasspad-udev.rules" in r.stdout)
        check("it does not quietly write the rule anyway",
              "wrote /etc/udev/rules.d" not in r.stdout)


def test_no_installer_anywhere_exits_two_without_a_traceback():
    """A truncated download gets a sentence, not a stack trace.

    The package is copied to an isolated tree with an empty cwd, so the resolver's
    three roots all miss and the refusal path is what runs.
    """
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(os.path.join(ROOT, "glasspad"), os.path.join(d, "glasspad"))
        env = dict(os.environ, PYTHONPATH=d, GLASSPAD_INSTALLER_DIR=os.path.join(d, "nothing"))
        r = subprocess.run([sys.executable, "-c",
                            "import sys;from glasspad.__main__ import main;"
                            "sys.exit(main(['--install-uinput']))"],
                           cwd=d, env=env, capture_output=True, text=True, timeout=180)
        check(f"exit code 2 for a bundle with no installer (got {r.returncode})",
              r.returncode == 2)
        check("it names every place it looked",
              "Looked for install.sh" in r.stdout and d in r.stdout)
        check("no traceback escaped to the user", "Traceback" not in r.stderr)


# --- privilege ---------------------------------------------------------------
def test_dry_run_never_elevates():
    """--install-uinput --dry-run must not reach pkexec or sudo.

    The command writes to /etc. Asking for a password to read a plan is absurd,
    and worse: it means the flag cannot be used to find out what a build would do.
    """
    import subprocess as sp
    import glasspad.__main__ as m
    calls, real = [], sp.call
    sp.call = lambda cmd, *a, **k: (calls.append(list(cmd)), 0)[1]
    try:
        rc = m.install_uinput(["--dry-run"])
    finally:
        sp.call = real
    check(f"dry run returns the installer's status ({rc})", rc == 0)
    check(f"dry run invokes something ({calls})", bool(calls))
    check(f"dry run does not elevate (invoked {calls[0][:2] if calls else 'nothing'})",
          calls and calls[0][0] == "sh")
    check("dry run passes --dry-run through", "--dry-run" in (calls[0] if calls else []))


def test_real_run_goes_through_a_privilege_helper():
    """The mirror image: without --dry-run it MUST elevate, via sh, with --uinput."""
    import subprocess as sp
    import glasspad.__main__ as m
    calls, real = [], sp.call
    sp.call = lambda cmd, *a, **k: (calls.append(list(cmd)), 0)[1]
    try:
        m.install_uinput([])
    finally:
        sp.call = real
    check("a real run elevates", bool(calls) and calls[0][0] in ("pkexec", "sudo"))
    check("and asks for the uinput mode only",
          bool(calls) and calls[0][-1] == "--uinput" and "--user" not in calls[0])
    check("it runs install.sh, never a copy of its logic",
          bool(calls) and calls[0][-2].endswith("install.sh"))


def test_install_sh_uinput_mode_is_documented_and_parses():
    r = sh([os.path.join(ROOT, "install.sh"), "--help"])
    check("--help documents --uinput", "--uinput" in r.stdout)
    check("--help prints the whole header, not a fixed line range",
          r.stdout.rstrip().endswith("skips the /etc work entirely."))
    r = sh([os.path.join(ROOT, "install.sh"), "--uinput", "--dry-run"])
    check(f"--uinput --dry-run runs unprivileged (exit {r.returncode})", r.returncode == 0)
    check("dry run changes nothing", "dry run: nothing changed" in r.stdout)


def test_invoking_user_comes_from_pkexec_uid():
    """pkexec sets only PKEXEC_UID; without this the wrong account joins `input`.

    Under pkexec, $USER is root and SUDO_USER is unset, so a naive installer adds
    root to the input group -- which already has it -- and the person who just
    typed their password is still locked out of /dev/uinput, with an installer
    that reported success.
    """
    env = dict(os.environ)
    env.pop("SUDO_USER", None)
    env["PKEXEC_UID"] = str(os.getuid())
    r = sh([os.path.join(ROOT, "install.sh"), "--uinput", "--dry-run"], env=env)
    who = getpass.getuser()
    check(f"PKEXEC_UID resolves the target account ({who})",
          f"uinput access for {who}" in r.stdout)
    check("it does not say root", "uinput access for root" not in r.stdout)


# --- the honest failure ------------------------------------------------------
def test_falling_back_to_a_null_pad_is_announced():
    """The silent state: controls draw, animate, and reach nothing.

    Nothing else in the UI looks wrong, so the only thing standing between a new
    user and "this software is broken" is this warning.
    """
    import io
    import contextlib
    from glasspad import pad as p

    saved_avail, saved_warned = p.uinput_available, p._NO_UINPUT_WARNED
    p.uinput_available = lambda *a, **k: False
    p._NO_UINPUT_WARNED = False
    try:
        err = io.StringIO()
        svc = p.PadService(None)
        with contextlib.redirect_stderr(err):
            svc.init(lambda *_: None, synchronous=True)
        svc.dispose()
        check("PadService records that it fell back", svc.fell_back_to_null)
        check("the warning names the command that fixes it",
              "--install-uinput" in err.getvalue())
        check("and says plainly that there is no gamepad", "no gamepad" in err.getvalue())
    finally:
        p.uinput_available, p._NO_UINPUT_WARNED = saved_avail, saved_warned


def test_a_deliberate_null_pad_stays_quiet():
    """--null-pad is a choice, not a failure; it must not print the warning."""
    import io
    import contextlib
    from glasspad import pad as p

    saved = p._NO_UINPUT_WARNED
    p._NO_UINPUT_WARNED = False
    try:
        err = io.StringIO()
        svc = p.PadService(p.NullBackend())
        with contextlib.redirect_stderr(err):
            svc.init(lambda *_: None, synchronous=True)
        svc.dispose()
        check("an explicitly requested null pad is not a fallback",
              not svc.fell_back_to_null)
        check("and nothing is printed about permissions", err.getvalue() == "")
    finally:
        p._NO_UINPUT_WARNED = saved


# --- packaging ---------------------------------------------------------------
def test_the_appimage_ships_the_installer_and_the_rule():
    """A bundle without these is a download that cannot finish installing.

    Checked against make_appimage.sh's staging loop, so this fails when the list
    changes rather than only when somebody happens to build an artefact -- and,
    when unsquashfs is available to look inside a built one, checked there too.
    """
    script = open(os.path.join(ROOT, "tools", "make_appimage.sh")).read()
    check("the AppImage builder stages install.sh beside the rule",
          "install.sh packaging/glasspad-udev.rules" in script)
    check("and refuses to build without them", "could not finish an install" in script)
    check("into the payload root the resolver searches",
          '$appdir/usr/share/glasspad/$priv' in script)

    appimage = sorted(__import__("glob").glob(
        os.path.join(ROOT, "dist", "GlassPad-*.AppImage")))
    tool = shutil.which("unsquashfs")
    if not (appimage and tool):
        check("no built AppImage or no unsquashfs here; contents not listed", True)
        return
    out = subprocess.run([tool, "-l", appimage[0]], capture_output=True, text=True,
                         timeout=300)
    check("install.sh is inside the packed payload",
          "usr/share/glasspad/install.sh" in out.stdout)
    check("the udev rule is inside the packed payload",
          "usr/share/glasspad/packaging/glasspad-udev.rules" in out.stdout)


def test_uninstall_mode_is_narrow_and_rehearsable():
    """The undo must not delete a layout nobody asked to lose.

    install.sh --uninstall is the broad one: both prefixes, autostart, layout.json.
    A bundle user removing a device rule should not lose 40 minutes of dragging
    controls into place, so --uninstall-uinput exists and names only two files.
    """
    r = sh([os.path.join(ROOT, "install.sh"), "--uninstall-uinput", "--dry-run"])
    check(f"rehearses without root (exit {r.returncode})", r.returncode == 0)
    check("names the two files it would remove",
          "modules-load.d/uinput.conf" in r.stdout and "99-glasspad-uinput.rules" in r.stdout)
    lowered = r.stdout.lower()
    check("says nothing about deleting the layout", "layout.json" not in lowered)
    check("says nothing about desktop entries or autostart",
          "autostart" not in lowered and ".desktop" not in lowered)
    check("and confirms it changed nothing", "dry run: nothing changed" in r.stdout)


def test_uninstall_flag_follows_the_privilege_rule():
    """Same contract as install: --dry-run stays in sh, a real run elevates."""
    import subprocess as sp
    import glasspad.__main__ as m
    calls, real = [], sp.call
    sp.call = lambda cmd, *a, **k: (calls.append(list(cmd)), 0)[1]
    try:
        m.uninstall_uinput(["--dry-run"])
        check(f"dry run does not elevate ({calls[-1][:2]})", calls[-1][0] == "sh")
        check(f"dry run still asks for the narrow mode ({calls[-1]})",
              "--uninstall-uinput" in calls[-1] and "--dry-run" in calls[-1])
        m.uninstall_uinput([])
        check("a real run elevates", calls[-1][0] in ("pkexec", "sudo"))
        check("and runs no broad uninstall",
              calls[-1][-1] == "--uninstall-uinput" and "--uninstall" not in calls[-1][:-1])
    finally:
        sp.call = real


def test_uninstall_is_documented_in_both_places():
    r = sh([os.path.join(ROOT, "install.sh"), "--help"])
    check("install.sh --help lists --uninstall-uinput", "--uninstall-uinput" in r.stdout)
    h = sh([sys.executable, "-m", "glasspad", "--help"])
    check("the app's own --help lists it too", "--uninstall-uinput" in h.stdout)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
