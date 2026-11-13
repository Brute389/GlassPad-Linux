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
import re
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


def _grant_run(tmp, node, extra=()):
    """install.sh --uinput --dry-run against a chosen node, nothing written.

    GLASSPAD_UINPUT_NODE is why this is possible: without it the decision reads the real
    /dev/uinput, so a test could only ever observe whichever branch the machine running
    it happens to be in -- and the branch nobody observes is the one that rots.
    """
    env = dict(os.environ, GLASSPAD_UINPUT_NODE=node, HOME=tmp)
    return sh(["sh", os.path.join(ROOT, "install.sh"), "--uinput", "--dry-run", *extra],
              cwd=ROOT, env=env)


def test_the_grant_is_the_smallest_one_that_works():
    """Access that already exists is not granted again.

    logind puts a uaccess ACL on /dev/uinput for the active session, and
    60-steam-input.rules and the KDE Connect rule tag it the same way -- verified here,
    where deleting our rule changed nothing. So an unconditional install left a
    permanent /etc edit plus `input` membership on machines that needed neither, and
    `--uninstall-uinput` could not safely take the rule back, because the same policy was
    load-bearing for someone else's package.
    """
    with tempfile.TemporaryDirectory() as tmp:
        node = os.path.join(tmp, "writable")
        open(node, "wb").close()
        os.chmod(node, 0o600)

        r = _grant_run(tmp, node)
        out = r.stdout + r.stderr
        check(f"writable node: declines to touch /etc (exit {r.returncode})",
              r.returncode == 0 and "WOULD NOT install a rule" in out)
        check("writable node: no rule write, no usermod",
              "would copy" not in out and "usermod" not in out)
        check("and it names the way back for a machine that loses that access",
              "--force-rule" in out)

        gone = _grant_run(tmp, os.path.join(tmp, "absent"))
        gout = gone.stdout + gone.stderr
        check("unwritable node: takes the grant path",
              "WOULD NOT install a rule" not in gout and "would copy" in gout)
        check("grant path: writes the ONE rule file, not an embedded copy",
              "packaging/glasspad-udev.rules" in gout)

        forced = _grant_run(tmp, node, ["--force-rule"])
        fout = forced.stdout + forced.stderr
        check("--force-rule grants anyway, and says it is doing so",
              "--force-rule: granting anyway" in fout and "would copy" in fout)


def test_the_input_group_disclosure_is_printed_where_it_is_decided():
    """`input` is the group that owns every evdev node: membership READS every keyboard,
    mouse and touchpad on the machine. The write on /dev/uinput that GlassPad needs is
    the narrow part; the read comes attached, and it is a permanent capability that
    outlives the app.

    This belongs in the installer's own output rather than only in a README nobody reads
    at the moment of decision -- the moment of decision is when a password is being typed.
    And it is said before the change, not after it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        r = _grant_run(tmp, os.path.join(tmp, "absent"))
        out = (r.stdout + r.stderr).lower()
        check("says the group can read every keyboard",
              "read every keyboard" in out)
        check("says it covers mouse and touchpad too",
              "touchpad" in out)
        check("and offers the narrower answer instead of only the wide one",
              "do not install the" in out or "already exists" in out)
        # The flag's own help text carries it too, since that is what a reader of
        # `glasspad --help` sees before anything runs.
        h = sh([sys.executable, "-m", "glasspad", "--help"], cwd=ROOT)
        hout = (h.stdout + h.stderr).lower()
        check("--help carries the same warning", "read every keyboard" in hout)

def test_the_new_flag_is_documented_where_it_is_parsed():
    """--force-rule is accepted by the case statement, so the header block has to name it.

    `--help` prints that block through awk, so an option added to the parser and not to
    the header is invisible: legal to type, impossible to discover -- the same
    advertised-and-absent class as --selftest, arriving from the opposite direction.
    """
    h = sh(["sh", os.path.join(ROOT, "install.sh"), "--help"], cwd=ROOT)
    out = h.stdout + h.stderr
    check(f"install.sh --help works (exit {h.returncode})", h.returncode == 0)
    check("it documents --force-rule", "--force-rule" in out)
    check("and says what skipping is for", "already" in out.lower())
    for flag in ("--uinput", "--force-rule", "--check", "--user", "--dry-run",
                 "--uninstall-uinput"):
        check(f"the header still covers {flag}", flag in out)


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


def test_flags_are_tested_through_main_not_just_the_function():
    """A test that calls the function skips the dispatch users actually take.

    uninstall_uinput() grew --dry-run support and a test drove it directly with
    ["--dry-run"], so it stayed green while main() went on calling the function with
    no arguments at all. The user-visible result: `glasspad --uninstall-uinput
    --dry-run` skipped straight to a pkexec prompt and deleted real files. Both
    flags are driven through main() here, with the actual call stubbed so that a
    regression fails a test instead of changing /etc.
    """
    import subprocess as sp
    import glasspad.__main__ as m

    calls, real = [], sp.call
    sp.call = lambda cmd, *a, **k: (calls.append(list(cmd)), 0)[1]
    try:
        for flag in ("--install-uinput", "--uninstall-uinput"):
            calls.clear()
            m.main([flag, "--dry-run"])
            got = calls[-1] if calls else []
            check(f"{flag} --dry-run reaches the installer unprompted ({got[:1]})",
                  bool(got) and got[0] == "sh")
            # install.sh's modes are --uinput / --uninstall-uinput; the app-level
            # flag is not forwarded verbatim, so pin the mode that actually runs.
            mode = "--uinput" if flag == "--install-uinput" else "--uninstall-uinput"
            check(f"{flag} --dry-run asks for mode {mode} with the rehearsal flag",
                  mode in got and "--dry-run" in got)
    finally:
        sp.call = real


def test_root_is_never_handed_a_path_inside_the_appimage():
    """The payload is a FUSE mount that root may not read.

    /tmp/.mount_GlassXXX/... is `ro,nosuid,nodev,user_id=1000` with no allow_other,
    and the kernel restricts such a mount to the user who created it -- root
    included. Handing pkexec a script inside it produced

        /usr/bin/sh: /tmp/.mount_GlassPPPgJgO/usr/share/glasspad/install.sh:
        Permission denied      (exit 126)

    on the first genuine install attempt, after every dry run had passed: none of
    them elevated, so none of them needed a second user's read permission. The fake
    mount below reproduces the shape -- a payload path whose install.sh is readable
    to us and staged out before the (stubbed) privileged call.
    """
    import subprocess as sp
    import glasspad.__main__ as m

    src = os.path.join(ROOT, "install.sh")
    rule_src = os.path.join(ROOT, "packaging", "glasspad-udev.rules")
    mount = tempfile.mkdtemp(prefix=".mount_FakeAppI")
    payload = os.path.join(mount, "usr", "share", "glasspad")
    os.makedirs(os.path.join(payload, "packaging"))
    shutil.copyfile(src, os.path.join(payload, "install.sh"))
    shutil.copyfile(rule_src, os.path.join(payload, "packaging", "glasspad-udev.rules"))

    seen = {}

    def fake(cmd, *a, **k):
        cmd = list(cmd)
        script = cmd[-2]
        seen["cmd"] = cmd
        seen["outside_mount"] = ".mount_" not in script
        seen["identical"] = (open(script, "rb").read() == open(src, "rb").read())
        seen["rule_beside"] = os.path.exists(
            os.path.join(os.path.dirname(script), bundle.RULE_REL))
        seen["dir"] = os.path.dirname(script)
        seen["mode"] = oct(os.stat(seen["dir"]).st_mode & 0o777)
        return 0

    saved_find, saved_call = bundle.find_installer, sp.call
    bundle.find_installer = lambda: os.path.join(payload, "install.sh")
    sp.call = fake
    try:
        m.install_uinput([])            # a REAL run: must elevate, via a staged copy
    finally:
        sp.call = saved_call
        bundle.find_installer = saved_find
        shutil.rmtree(mount, ignore_errors=True)

    check(f"a staged script was handed over at all ({seen.get('cmd', 'nothing ran')})",
          bool(seen))
    check("the script is NOT inside the mount", seen.get("outside_mount"))
    check("and is byte-identical to the shipped installer", seen.get("identical"))
    check("the udev rule travels beside it (install.sh resolves it relative to $0)",
          seen.get("rule_beside"))
    check(f"staged dir is private to us, not world-writable ({seen.get('mode')})",
          seen.get("mode") == "0o700")
    check(f"the elevated command still asks for privilege ({seen.get('cmd', [''])[0]})",
          seen.get("cmd", [""])[0] in ("pkexec", "sudo"))
    gone = not os.path.exists(seen.get("dir", ""))
    check("and the staging directory is cleaned up afterwards", gone)


def test_a_checkout_installer_is_used_in_place():
    """No copying where root can already read: /home is fine, and a copy would
    obscure which file is about to run as root."""
    src = os.path.join(ROOT, "install.sh")
    before = {d for d in os.listdir(tempfile.gettempdir())
              if d.startswith("glasspad-installer-")}
    with bundle.staged_installer(src) as got:
        check("same path, no detour", got == src)
    after = {d for d in os.listdir(tempfile.gettempdir())
             if d.startswith("glasspad-installer-")}
    check("nothing was staged", before == after)


def _install_with_stubbed_privileges(group_before, node_writable):
    """Run install_uinput() with the privileged call stubbed; return (rc, output)."""
    import contextlib
    import io
    import subprocess as sp

    import glasspad.__main__ as m
    from glasspad import pad

    real_call, real_avail, real_grp = sp.call, pad.uinput_available, m._input_group_in_database
    seen = []
    sp.call = lambda cmd, *a, **k: (seen.append(list(cmd)), 0)[1]
    pad.uinput_available = lambda *a, **k: node_writable
    m._input_group_in_database = lambda: group_before
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = m.install_uinput([])
    finally:
        sp.call, pad.uinput_available, m._input_group_in_database = real_call, real_avail, real_grp
    return rc, buf.getvalue(), seen


def test_verdict_does_not_blame_a_group_it_did_not_change():
    """The post-install report told the truth about the wrong thing.

    On a real virgin install the installer printed:

        permissions for josh
          already in group input
        ...
        Installed -- but /dev/uinput is still not writable *for you*.
        That is expected when this run added the group ... Log out and back in

    It had NOT added the group -- its own line above says so -- and the device was
    in fact already writable: `udevadm trigger` is asynchronous, so the check read
    the node before udevd got to it. The user gets advice that cannot help (log out,
    back in) for a problem that does not exist, while a genuine failure would get the
    same misdiagnosis. So the branch keys on what this run actually did.
    """
    rc, out, _ = _install_with_stubbed_privileges(group_before=True, node_writable=False)
    check("an unchanged group is not offered as the explanation",
          "already" in out or "NOT the reason" in out)
    check("and the user is not told to re-login for nothing",
          "NEXT login" not in out and "Log out" not in out)
    check("the message points at the thing that decides the node",
          "udevadm" in out or "getfacl" in out)
    check(f"and it exits nonzero, because nothing works yet (rc={rc})", rc != 0)


def test_a_group_this_run_added_does_say_next_login():
    rc, out, _ = _install_with_stubbed_privileges(group_before=False, node_writable=False)
    check(f"the next-login advice appears only when the group is new ({rc})",
          "NEXT login" in out or "next login" in out.lower())
    check("and that path is not treated as a failure of the installer", rc == 0)


def test_a_working_install_reports_working():
    rc, out, _ = _install_with_stubbed_privileges(group_before=True, node_writable=True)
    check(f"writable device, exit 0 (rc={rc})", rc == 0)
    check("says the pad can be created now", "can create a pad now" in out)


def test_installer_settles_udev_before_measuring_the_node():
    """`udevadm trigger` queues events and returns; the node changes later.

   install.sh must therefore wait for the queue before asking anyone whether the
    device is writable, in both directions -- the uninstall verdict has the same
    race in reverse (it can report "no longer writable" for a node that a uaccess
    rule is about to hand back).
    """
    src = open(os.path.join(ROOT, "install.sh")).read()
    check("a settle helper exists", "udev_settle()" in src)
    check("it is bounded, so a wedged udevd cannot wedge the installer",
          "--timeout=" in src.split("udev_settle()")[1][:600])
    # Only real command lines count. The phrase also appears in prose, and the first
    # version of this check "found" a trigger inside its own docstring -- a test that
    # fails for the wrong reason is worse than one that is missing.
    import re
    triggers = list(re.finditer(r"^[ \t]*(?:run|run_ok)[ \t]+udevadm trigger",
                                src, re.MULTILINE))
    check(f"the script triggers udev at all ({len(triggers)} call sites)",
          len(triggers) >= 3)
    offenders = []
    for i, m in enumerate(triggers, start=1):
        tail = src[m.end():]
        end = tail.find("\n}")
        body = tail[:end if end != -1 else 1500]
        before_measuring = body.split("su -s")[0].split("user_can_write")[0]
        if "udev_settle" not in before_measuring:
            offenders.append("line " + str(src[:m.start()].count("\n") + 1))
    check("every trigger settles the queue before anything measures the device"
          + (f" (missing at {', '.join(offenders)})" if offenders else ""),
          not offenders)
    check("the install verdict polls for the condition rather than trusting the queue",
          "user_can_write_uinput" in src and "sleep 0.25" in src)


def test_the_parent_flushes_before_the_child_talks():
    """Ordering is part of what a privileged transcript means.

    install.sh writes to the same fd as the Python front end, and Python block-buffers
    when stdout is not a terminal. Piped to a file or `tee`, the child's output landed
    first and the parent's "the installer will change nothing" arrived afterwards -- a
    saved log of a root-escalating command that reads backwards. In a terminal Python
    line-buffers, so this looks fine forever until someone records it.
    """
    src = open(os.path.join(ROOT, "glasspad", "__main__.py")).read()
    spawns = src.count("subprocess.call(cmd)")
    flushes = src.count("sys.stdout.flush()")
    check(f"every spawn is preceded by a flush ({flushes} flushes, {spawns} spawns)",
          spawns > 0 and flushes >= spawns)


def test_a_lie_in_the_exit_status_is_not_passed_on_as_failure():
    """The installer said "writable by josh" and exited 1. Both were on screen.

    install.sh ended its success branch with `[ "$waited" -gt 0 ] && say ...`, which
    returns 1 when the condition is false -- i.e. whenever the device became writable
    immediately, the common case. The front end relayed that as "The installer failed",
    and --install reported "/dev/uinput" as unfinished on a machine where it worked. So
    the front end re-measures the device instead of trusting the status.
    """
    import contextlib
    import io
    import subprocess as sp

    import glasspad.__main__ as m
    from glasspad import pad

    real_call, real_avail = sp.call, pad.uinput_available
    sp.call = lambda cmd, *a, **k: 1             # the installer claims failure
    pad.uinput_available = lambda *a, **k: True  # ...and the device disagrees
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = m.install_uinput([])
    finally:
        sp.call, pad.uinput_available = real_call, real_avail
    out = buf.getvalue()
    check(f"the device wins over the status line (rc={rc})", rc == 0)
    check("the mismatch is reported rather than swallowed", "IS writable now" in out)
    check("and the wrong exit code is still named, so the bug gets reported",
          "exited 1" in out)


def test_a_real_failure_is_still_called_a_failure():
    """The fix must not turn every nonzero into a shrug."""
    import contextlib
    import io
    import subprocess as sp

    import glasspad.__main__ as m
    from glasspad import pad

    real_call, real_avail = sp.call, pad.uinput_available
    sp.call = lambda cmd, *a, **k: 1
    pad.uinput_available = lambda *a, **k: False
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = m.install_uinput([])
    finally:
        sp.call, pad.uinput_available = real_call, real_avail
    check(f"still nonzero when the device really is unwritable (rc={rc})", rc != 0)
    check("and it says so plainly", "failed with exit 1" in buf.getvalue())


def test_install_sh_never_leaves_its_status_to_the_last_print():
    """A guard for the class of bug, not just the instance.

    `[ cond ] && cmd` returns 1 when cond is false. Legal and idiomatic -- and lethal
    as the last statement of a branch in a script whose final status is whatever the
    case fell through to. So: ban the statement form outright, require an explicit exit
    after the case, and require --check to grade itself rather than its last `say`.
    """
    src = open(os.path.join(ROOT, "install.sh")).read()
    lines = src.splitlines()

    def next_significant(i):
        for ln in lines[i + 1:]:
            t = ln.strip()
            if not t or t.startswith("#"):
                continue
            return t
        return ""

    # `[ cond ] && cmd` returns 1 when cond is false. Harmless in mid-function -- the
    # function returns something else afterwards -- and fatal as the LAST statement of a
    # branch whose status becomes the program's. So the rule is positional, not
    # stylistic: flag it only when the branch ends right there. Line 271's
    # `[ "$DRY" = 1 ] && return 0` inside udev_settle() is fine precisely because a
    # `return 0` follows; a guard so blunt it forces cargo-culting is a guard people
    # switch off.
    # (the first word that ENDS a block -- `nxt.startswith("")` is true for every
    # string, which is how this guard flagged a line three times before it was fixed)
    terminators = {"fi", "else", "elif", "esac", "}", ";;"}
    bad = []
    for i, ln in enumerate(lines):
        if re.match(r"^\s*\[.*\]\s*&&\s", ln):
            nxt = next_significant(i)
            first = nxt.split()[0] if nxt.split() else ""
            if first in terminators:
                bad.append(f"line {i + 1} ends on it (next is {nxt!r})")
    check("no branch in install.sh ends on a `[ ... ] && ...`"
          + (f" ({'; '.join(bad)})" if bad else ""), not bad)
    tail = src.rsplit("esac", 1)[-1]
    check("the script exits explicitly after the case", "exit 0" in tail)
    check("--check propagates do_check's own verdict",
          "do_check" in src and "exit $?" in src)
    body = src.split("do_check()")[1] if "do_check()" in src else ""
    check("and do_check grades the device, not its last say",
          "test -w /dev/uinput" in body.split("\n}")[0])


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
