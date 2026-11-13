"""The bundle's privileged half: one rule file, one script, no surprises.

A downloaded AppImage cannot contain /etc/udev/rules.d, so it carries the rule and the
Python that writes it (glasspad/uinput_setup.py). That pair is part of the security
surface of the product, and it runs as root on strangers' machines, so it gets the same
treatment as the painters: the policy is pinned, the refusal paths are pinned, and the
paths that must NOT escalate are pinned.

This suite used to drive install.sh. The shell is gone -- the install is one AppImage --
but nothing here was deleted along with it. Where a check existed because a shell
construct lies (a `[ cond ] && cmd` that returns 1 when cond is false; a `--help` sliced
out of a header by line number), it has been replaced by the invariant that construct was
standing in for, stated against Python. Where a check existed because Linux behaves a
certain way (udev is asynchronous; root cannot read your FUSE mount; pkexec rewrites
$USER), it is unchanged, because Linux did not get the memo about the refactor.

test_dry_run_never_elevates exists because of how this feature was written. A probe of
the refusal path pointed $GLASSPAD_INSTALLER_DIR at an empty directory, expecting "not
found" -- but the resolver also searches the package root, found the real checkout, and
ran the real grant through pkexec. Correct behaviour, unintended consequence, and no way
to rehearse the command without changing /etc.
"""
import importlib.util
import io
import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import bundle, pad  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIV = os.path.join(ROOT, "glasspad", "uinput_setup.py")
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


def sh(argv, cwd=None, env=None):
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=180)


def load_priv():
    """Import glasspad/uinput_setup.py as a module, under a private name.

    It is imported -- not merely read as text -- because the grant decision is what is
    under test and it must be exercised, not pattern-matched. It has no package
    imports, so this works from a bare path exactly as it does when staged.
    """
    spec = importlib.util.spec_from_file_location("glasspad_uinput_setup", PRIV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- the rule itself ---------------------------------------------------------
def test_rule_is_one_file_and_nothing_carries_a_copy_of_it():
    """Exactly one place in this repository states the permission policy.

    The shell installer used to write the rule from a heredoc. Duplicating it -- one
    copy in the script, one in a file -- is how a machine ends up running the version
    nobody reviewed. The scan below is broader than the old check it replaces: it looks
    at EVERY tracked text file, so a second copy cannot appear in a new tool, a README
    snippet someone pastes into code, or the Python script itself.
    """
    path = os.path.join(ROOT, bundle.RULE_REL)
    check(f"the rule file exists at {bundle.RULE_REL}", os.path.isfile(path))
    carriers = []
    # A test that QUOTES the policy is not a second policy, and neither is a README that
    # explains it -- the copy that must not exist is one that could be installed. So the
    # scan covers the file types that reach a machine: the script, the packaging, the
    # rule itself.
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "__pycache__", "dist", "node_modules", "tests"}]
        for name in files:
            if not name.endswith((".py", ".sh", ".rules", ".in")):
                continue
            full = os.path.join(base, name)
            try:
                text = open(full, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                if 'MODE="0660"' in line or "MODE=0660" in line:
                    carriers.append(os.path.relpath(full, ROOT))
                    break
    check(f"one file in the tree defines the mode ({carriers})",
          carriers == [os.path.join("packaging", "glasspad-udev.rules")])
    script = open(PRIV).read()
    check("the privileged script contains no embedded KERNEL== rule of its own",
          'KERNEL=="uinput"' not in script)
    check("it resolves the rule from disk instead (find_rule)",
          "def find_rule" in script and "RULE_NAME" in script)


def test_rule_grants_the_input_group_and_never_the_world():
    """0660 + group input, not 0666.

    A virtual Xbox pad is a keystroke-injecting device as far as the desktop is
    concerned. 0666 would hand it to every user and every sandboxed app on the machine,
    which is a wider door than GlassPad needs and has no business opening on somebody
    else's behalf.
    """
    text = open(os.path.join(ROOT, bundle.RULE_REL)).read()
    check('the rule matches KERNEL=="uinput"', 'KERNEL=="uinput"' in text)
    check('the rule hands the node to GROUP="input"', 'GROUP="input"' in text)
    check('the rule is MODE="0660"', 'MODE="0660"' in text)
    # Comments aside: the file explains at length why 0666 is wrong, so only the active
    # lines can be held to the standard. That also pins the rule to a single
    # instruction -- a second KERNEL line would be a second policy.
    active = [l for l in text.splitlines() if l.strip()
              and not l.lstrip().startswith("#")]
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
    check(f"udevadm verify accepts the rule (exit {r.returncode}: "
          f"{r.stderr.strip()[:80]})", r.returncode == 0)


# --- resolving the pair ------------------------------------------------------
def test_priv_script_and_rule_resolve_from_a_checkout():
    got = bundle.find_priv_script()
    check(f"find_priv_script() finds the script ({got})", got == PRIV)
    rule = bundle.priv_rule_src(got)
    check(f"the rule is found from the script ({rule})",
          rule == os.path.join(ROOT, bundle.RULE_REL))
    check("priv_rule_src(None) is None, not a crash", bundle.priv_rule_src(None) is None)


def test_script_refuses_when_the_rule_file_is_gone():
    """No fallback policy. A missing rule is an error, not an opportunity to improvise.

    The interesting case is a truncated download: a bundle whose payload lost the rule
    still has a script that could, in principle, make one up.
    """
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(PRIV, os.path.join(d, "uinput_setup.py"))
        r = sh([sys.executable, os.path.join(d, "uinput_setup.py"),
                "--uinput", "--dry-run", "--target-user", os.environ.get("USER", "you")])
        # --dry-run still exits non-zero here: the script cannot describe an install it
        # is unable to perform.
        check(f"missing rule file fails the run (exit {r.returncode})", r.returncode != 0)
        out = r.stdout + r.stderr
        check("and says which file it wanted",
              "rule file not found" in out and bundle.RULE_NAME in out)
        check("and refuses in as many words, rather than silently improvising",
              "will not fall back to an embedded copy" in out)
        check("it does not quietly write the rule anyway", "would copy" not in out)


def test_no_priv_script_anywhere_exits_two_without_a_traceback():
    """A truncated download gets a sentence, not a stack trace.

    The package is copied to an isolated tree WITHOUT the privileged script, with an
    empty override directory, so every root the resolver searches misses.
    """
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(os.path.join(ROOT, "glasspad"), os.path.join(d, "glasspad"))
        os.unlink(os.path.join(d, "glasspad", "uinput_setup.py"))
        env = dict(os.environ, PYTHONPATH=d,
                   GLASSPAD_INSTALLER_DIR=os.path.join(d, "nothing"))
        r = subprocess.run([sys.executable, "-c",
                            "import sys;from glasspad.__main__ import main;"
                            "sys.exit(main(['--install-uinput']))"],
                           cwd=d, env=env, capture_output=True, text=True, timeout=180)
        check(f"exit code 2 for a bundle with no privileged script (got {r.returncode})",
              r.returncode == 2)
        check("it names every place it looked",
              "uinput_setup.py" in r.stdout and d in r.stdout)
        check("no traceback escaped to the user", "Traceback" not in r.stderr)


# --- privilege ---------------------------------------------------------------
def test_dry_run_never_elevates():
    """--install-uinput --dry-run must not reach pkexec or sudo.

    The command writes to /etc. Asking for a password to read a plan is absurd, and
    worse: it means the flag cannot be used to find out what a build would do.
    """
    import subprocess as sp
    import glasspad.__main__ as m
    calls, real = [], sp.call
    sp.call = lambda cmd, *a, **k: (calls.append(list(cmd)), 0)[1]
    try:
        rc = m.install_uinput(["--dry-run"])
    finally:
        sp.call = real
    argv = calls[0] if calls else []
    check(f"dry run returns the script's status ({rc})", rc == 0)
    check(f"dry run invokes something ({argv[:2]})", bool(calls))
    check(f"dry run does not elevate (invoked {argv[:1]})",
          bool(argv) and os.path.basename(argv[0]).startswith("python"))
    check("dry run passes --dry-run through", "--dry-run" in argv)
    check("and the interpreter is not inside a bundle root",
          not any("/.mount_" in a for a in argv))


def test_real_run_goes_through_a_privilege_helper():
    """The mirror image: without --dry-run it MUST elevate, running the script, --uinput."""
    import subprocess as sp
    import glasspad.__main__ as m
    calls, real = [], sp.call
    sp.call = lambda cmd, *a, **k: (calls.append(list(cmd)), 0)[1]
    try:
        m.install_uinput([])
    finally:
        sp.call = real
    argv = calls[0] if calls else []
    check(f"a real run elevates ({argv[:1]})", bool(argv) and argv[0] in ("pkexec", "sudo"))
    check("and asks for the uinput mode only",
          "--uinput" in argv and "--user" not in argv)
    check("it runs the shipped script, never a copy of its logic",
          any(a.endswith("uinput_setup.py") for a in argv))


def test_privileged_script_documents_every_mode_it_accepts():
    """`--help` is the only contract for a script that root runs on a stranger's machine.

    install.sh printed a header block sliced out by line number, and drifted out of step
    with its own case statement. This one is argparse, so the risk inverted: a mode added
    to the parser that nothing else reaches, or a flag renamed while the front end kept
    typing the old one. Every mode the front end can request must appear here.
    """
    r = sh([sys.executable, PRIV, "--help"])
    check(f"--help works (exit {r.returncode})", r.returncode == 0)
    for flag in ("--uinput", "--uninstall-uinput", "--check", "--dry-run",
                 "--force-rule", "--target-user"):
        check(f"--help documents {flag}", flag in r.stdout)
    check("it explains that it is the staged, stdlib-only half",
          "Standard library only" in r.stdout or "stdlib" in r.stdout.lower())


def test_grant_rehearsal_runs_unprivileged():
    r = sh([sys.executable, PRIV, "--uinput", "--dry-run",
            "--target-user", os.environ.get("USER", "you")])
    check(f"--uinput --dry-run runs without root (exit {r.returncode})", r.returncode == 0)
    check("and says so plainly", "dry run: nothing changed" in r.stdout
          or "WOULD NOT install a rule" in r.stdout)


# --- who is the grant for ----------------------------------------------------
def test_the_target_user_is_named_explicitly():
    """pkexec sets only PKEXEC_UID and rewrites $USER to root.

    An installer that asked $USER under pkexec added root to the input group -- which
    already has it -- while the person who just typed their password stayed locked out of
    /dev/uinput, and reported success. The old shell script decoded PKEXEC_UID itself;
    the Python split makes the problem smaller instead: the UNPRIVILEGED side knows who it
    is, so it names the account on the command line and the root side never guesses.

    What is pinned here is that the name survives the trip, and that a root-looking
    environment does not quietly become "root".
    """
    import glasspad.__main__ as m
    saved = {k: os.environ.get(k) for k in ("USER", "LOGNAME", "SUDO_USER")}
    real_user = saved.get("USER") or "you"
    try:
        os.environ["USER"] = "root"
        os.environ["LOGNAME"] = "root"
        os.environ["SUDO_USER"] = real_user
        got = m._target_user()
        check(f"an inherited SUDO_USER wins over a rewritten USER ({got})",
              got == real_user)
        os.environ.pop("SUDO_USER", None)
        got2 = m._target_user()
        check(f"with nothing to go on it falls back to the real uid, not literal root "
              f"({got2})", got2 != "root" or os.geteuid() == 0)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # And the flag actually crosses the privilege boundary.
    import subprocess as sp
    calls, real_call = [], sp.call
    sp.call = lambda cmd, *a, **k: (calls.append(list(cmd)), 0)[1]
    try:
        m.install_uinput([])
    finally:
        sp.call = real_call
    argv = calls[0] if calls else []
    check(f"--target-user is passed to the privileged script ({argv[-2:]})",
          "--target-user" in argv and argv[argv.index("--target-user") + 1] == real_user)


# --- the grant decision ------------------------------------------------------
def _grant_run(tmp, node, extra=()):
    """The script's --uinput --dry-run against a chosen node, nothing written.

    GLASSPAD_UINPUT_NODE is why this is possible: without it the decision reads the real
    /dev/uinput, so a test could only ever observe whichever branch the machine running it
    happens to be in -- and the branch nobody observes is the one that rots.
    """
    env = dict(os.environ, GLASSPAD_UINPUT_NODE=node, HOME=tmp)
    return sh([sys.executable, PRIV, "--uinput", "--dry-run",
               "--target-user", os.environ.get("USER", "you"), *extra],
              cwd=ROOT, env=env)


def test_the_grant_is_the_smallest_one_that_works():
    """Access that already exists is not granted again.

    logind puts a uaccess ACL on /dev/uinput for the active session, and
    60-steam-input.rules and the KDE Connect rule tag it the same way -- verified here,
    where deleting our rule changed nothing. So an unconditional install left a permanent
    /etc edit plus `input` membership on machines that needed neither, and
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
              bundle.RULE_NAME in gout)

        forced = _grant_run(tmp, node, ["--force-rule"])
        fout = forced.stdout + forced.stderr
        check("--force-rule grants anyway, and says it is doing so",
              "--force-rule: granting anyway" in fout and "would copy" in fout)


def test_the_input_group_disclosure_is_printed_where_it_is_decided():
    """`input` is the group that owns every evdev node: membership READS every keyboard,
    mouse and touchpad on the machine. The write on /dev/uinput that GlassPad needs is
    the narrow part; the read comes attached, and it is a permanent capability that
    outlives the app.

    This belongs in the grant's own output rather than only in a README nobody reads at
    the moment of decision -- the moment of decision is when a password is being typed.
    And it is said before the change, not after it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        r = _grant_run(tmp, os.path.join(tmp, "absent"))
        out = (r.stdout + r.stderr).lower()
        check("says the group can read every keyboard", "read every keyboard" in out)
        check("says it covers mouse and touchpad too", "touchpad" in out)
        check("and offers the narrower answer instead of only the wide one",
              "do not install the" in out or "already exists" in out)
        # The parent prints it too, because that is what a person sees before the
        # password prompt appears -- the script's copy arrives after pkexec answered.
        h = sh([sys.executable, "-m", "glasspad", "--help"], cwd=ROOT)
        hout = (h.stdout + h.stderr).lower()
        check("--help carries the same warning", "read every keyboard" in hout)


# --- the honest failure ------------------------------------------------------
def test_falling_back_to_a_null_pad_is_announced():
    """The silent state: controls draw, animate, and reach nothing.

    Nothing else in the UI looks wrong, so the only thing standing between a new user and
    "this software is broken" is this warning.
    """
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
    from glasspad import pad as p

    saved_warned = p._NO_UINPUT_WARNED
    p._NO_UINPUT_WARNED = False
    try:
        err = io.StringIO()
        # A null backend passed IN, not a uinput failure papered over: the warning exists
        # for the second case only.
        svc = p.PadService(p.NullBackend())
        with contextlib.redirect_stderr(err):
            svc.init(lambda *_: None, synchronous=True)
        svc.dispose()
        check("an explicitly requested null pad is not a fallback",
              not svc.fell_back_to_null)
        check("and nothing is printed about permissions", err.getvalue() == "")
    finally:
        p._NO_UINPUT_WARNED = saved_warned


# --- what ships --------------------------------------------------------------
def test_the_builder_stages_the_privileged_pair():
    """What the builder must do, checked in the builder.

    The bytes INSIDE a built image are pinned by
    tests/test_appimage_artifact.py instead, and the split is not tidiness: the builder
    runs this suite before it packs, so a check here on the packed payload could only be
    satisfied by the previous build's file. That is how stale packaging produced wrong
    conclusions twice during the offline builder's development -- once green for a payload
    a revision behind, once red for a file that had not been written yet.
    """
    script = open(os.path.join(ROOT, "tools", "make_appimage.sh")).read()
    check("the AppImage builder stages the rule",
          "packaging/glasspad-udev.rules" in script)
    check("and refuses to build without it", "could not finish an install" in script)
    check("into the payload root the resolver searches",
          '$appdir/usr/share/glasspad/$priv' in script)
    check("the privileged script travels inside the package copy",
          'cp -r glasspad "$appdir/usr/share/glasspad/glasspad"' in script)
    check("and the .run-era installer is not staged anywhere",
          "install.sh" not in script)


# --- the undo ----------------------------------------------------------------
def test_uninstall_mode_is_narrow_and_rehearsable():
    """The undo must not delete a layout nobody asked to lose.

    A bundle user removing a device rule should not lose 40 minutes of dragging controls
    into place, so --uninstall-uinput exists and names exactly two files.
    """
    r = sh([sys.executable, PRIV, "--uninstall-uinput", "--dry-run"])
    check(f"rehearses without root (exit {r.returncode})", r.returncode == 0)
    check("names the two files it would remove",
          "modules-load.d/uinput.conf" in r.stdout
          and "99-glasspad-uinput.rules" in r.stdout)
    lowered = r.stdout.lower()
    check("says nothing about deleting the layout", "layout.json" not in lowered)
    check("says nothing about desktop entries or autostart",
          "autostart" not in lowered and ".desktop" not in lowered)
    check("and says what it deliberately leaves alone",
          "left alone on purpose" in lowered)


def test_uninstall_flag_follows_the_privilege_rule():
    """Same contract as install: --dry-run stays unprivileged, a real run elevates."""
    import subprocess as sp
    import glasspad.__main__ as m
    calls, real = [], sp.call
    sp.call = lambda cmd, *a, **k: (calls.append(list(cmd)), 0)[1]
    try:
        m.uninstall_uinput(["--dry-run"])
        check(f"dry run does not elevate ({calls[-1][:1]})",
              os.path.basename(calls[-1][0]).startswith("python"))
        check(f"dry run still asks for the narrow mode ({calls[-1][-1:]})",
              "--uninstall-uinput" in calls[-1] and "--dry-run" in calls[-1])
        m.uninstall_uinput([])
        check("a real run elevates", calls[-1][0] in ("pkexec", "sudo"))
        check("and runs no broad uninstall",
              "--uninstall-uinput" in calls[-1]
              and "--uninstall" not in calls[-1][:-1])
    finally:
        sp.call = real


def test_uninstall_is_documented_in_both_places():
    r = sh([sys.executable, PRIV, "--help"])
    check("the script's --help lists --uninstall-uinput", "--uninstall-uinput" in r.stdout)
    h = sh([sys.executable, "-m", "glasspad", "--help"])
    check("the app's own --help lists it too", "--uninstall-uinput" in h.stdout)


def test_flags_are_tested_through_main_not_just_the_function():
    """A test that calls the function skips the dispatch users actually take.

    uninstall_uinput() grew --dry-run support and a test drove it directly with
    ["--dry-run"], so it stayed green while main() went on calling the function with no
    arguments at all. The user-visible result: `glasspad --uninstall-uinput --dry-run`
    skipped straight to a pkexec prompt and deleted real files. Both flags are driven
    through main() here, with the actual call stubbed so that a regression fails a test
    instead of changing /etc.
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
            check(f"{flag} --dry-run reaches the script unprompted ({got[:1]})",
                  bool(got) and os.path.basename(got[0]).startswith("python"))
            # The script's modes are --uinput / --uninstall-uinput; the app-level flag is
            # not forwarded verbatim, so pin the mode that actually runs.
            mode = "--uinput" if flag == "--install-uinput" else "--uninstall-uinput"
            check(f"{flag} --dry-run asks for mode {mode} with the rehearsal flag",
                  mode in got and "--dry-run" in got)
    finally:
        sp.call = real


# --- the FUSE mount that root may not read -----------------------------------
def test_root_is_never_handed_a_path_inside_the_appimage():
    """The payload is a FUSE mount that root may not read.

    /tmp/.mount_GlassXXX/... is `ro,nosuid,nodev,user_id=1000` with no allow_other, and
    the kernel restricts such a mount to the user who created it -- root included. Handing
    pkexec a script inside it produced

        /usr/bin/sh: /tmp/.mount_GlassPPPgJgO/usr/share/glasspad/install.sh:
        Permission denied      (exit 126)

    on the first genuine install attempt, after every dry run had passed: none of them
    elevated, so none of them needed a second user's read permission. The fake mount below
    reproduces the shape -- a payload path whose files are readable to us and must be
    staged out before the (stubbed) privileged call.
    """
    import subprocess as sp
    import glasspad.__main__ as m

    rule_src = os.path.join(ROOT, "packaging", bundle.RULE_NAME)
    mount = tempfile.mkdtemp(prefix=".mount_FakeAppI")
    payload = os.path.join(mount, "usr", "share", "glasspad", "glasspad")
    os.makedirs(payload)
    shutil.copyfile(PRIV, os.path.join(payload, "uinput_setup.py"))
    shutil.copyfile(rule_src, os.path.join(payload, bundle.RULE_NAME))

    seen = {}

    def fake(cmd, *a, **k):
        cmd = list(cmd)
        script = next((c for c in cmd if c.endswith(".py")), "")
        seen["cmd"] = cmd
        seen["outside_mount"] = ".mount_" not in script
        seen["identical"] = (open(script, "rb").read() == open(PRIV, "rb").read())
        # NEXT TO the script, not in a packaging/ subdir: that is where the script looks,
        # and it is the only relationship that survives the copy.
        seen["rule_beside"] = os.path.isfile(
            os.path.join(os.path.dirname(script), bundle.RULE_NAME))
        seen["dir"] = os.path.dirname(script)
        seen["mode"] = oct(os.stat(seen["dir"]).st_mode & 0o777)
        return 0

    saved_find, saved_call = bundle.find_priv_script, sp.call
    bundle.find_priv_script = lambda: os.path.join(payload, "uinput_setup.py")
    sp.call = fake
    try:
        m.install_uinput([])            # a REAL run: must elevate, via a staged copy
    finally:
        sp.call = saved_call
        bundle.find_priv_script = saved_find
        shutil.rmtree(mount, ignore_errors=True)

    check(f"a staged script was handed over at all ({seen.get('cmd', 'nothing ran')})",
          bool(seen))
    check("the script is NOT inside the mount", seen.get("outside_mount"))
    check("and is byte-identical to the shipped script", seen.get("identical"))
    check("the udev rule travels beside it", seen.get("rule_beside"))
    check(f"staged dir is private to us, not world-writable ({seen.get('mode')})",
          seen.get("mode") == "0o700")
    check(f"the elevated command still asks for privilege ({seen.get('cmd', [''])[0]})",
          seen.get("cmd", [""])[0] in ("pkexec", "sudo"))
    gone = not os.path.exists(seen.get("dir", ""))
    check("and the staging directory is cleaned up afterwards", gone)


def test_a_checkout_script_is_used_in_place():
    """No copying where root can already read: /home is fine, and a copy would obscure
    which file is about to run as root."""
    before = {d for d in os.listdir(tempfile.gettempdir())
              if d.startswith("glasspad-priv-")}
    with bundle.staged_priv(PRIV) as (got, rule):
        check("same path, no detour", got == PRIV)
        check("and the rule resolves from the tree", os.path.isfile(rule or ""))
    after = {d for d in os.listdir(tempfile.gettempdir())
             if d.startswith("glasspad-priv-")}
    check("nothing was staged", before == after)


# --- the verdict afterwards --------------------------------------------------
def _install_with_stubbed_privileges(group_before, node_writable):
    """Run install_uinput() with the privileged call stubbed; return (rc, output)."""
    import subprocess as sp

    import glasspad.__main__ as m

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
        sp.call, pad.uinput_available, m._input_group_in_database = (
            real_call, real_avail, real_grp)
    return rc, buf.getvalue(), seen


def test_verdict_does_not_blame_a_group_it_did_not_change():
    """The post-install report told the truth about the wrong thing.

    On a real virgin install the installer printed:

        permissions for josh
          already in group input
        ...
        Installed -- but /dev/uinput is still not writable *for you*.
        That is expected when this run added the group ... Log out and back in

    It had NOT added the group -- its own line above says so -- and the device was in fact
    already writable: `udevadm trigger` is asynchronous, so the check read the node before
    udevd got to it. The user gets advice that cannot help (log out, back in) for a problem
    that does not exist, while a genuine failure would get the same misdiagnosis. So the
    branch keys on what this run actually did.
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
    check("and that path is not treated as a failure of the grant", rc == 0)


def test_a_working_install_reports_working():
    rc, out, _ = _install_with_stubbed_privileges(group_before=True, node_writable=True)
    check(f"writable device, exit 0 (rc={rc})", rc == 0)
    check("says the pad can be created now", "can create a pad now" in out)


# --- udev is asynchronous ----------------------------------------------------
class Recorder:
    def __init__(self):
        self.argv = []

    def __call__(self, cmd, *a, **k):
        self.argv.append(list(cmd))
        return subprocess.CompletedProcess(list(cmd), 0, "", "")


def test_the_grant_script_settles_udev_before_measuring_the_node():
    """`udevadm trigger` queues events and returns; the node changes later.

    The grant must therefore wait for the queue before asking anyone whether the device is
    writable, in both directions -- the uninstall verdict has the same race in reverse (it
    can report "no longer writable" for a node that a uaccess rule is about to hand back).
    """
    # mod.subprocess IS the shared subprocess module, not a copy of it: assigning
    # mod.subprocess.run = rec stubs it for the whole process, and a second test that
    # forgets to put it back leaves every later sh() call returning an empty
    # CompletedProcess -- which reads, downstream, as "the script printed nothing" for a
    # dozen unrelated tests. Saved and restored against this module's own name.
    mod = load_priv()
    rec = Recorder()
    saved_run, saved_which = subprocess.run, shutil.which
    subprocess.run = rec
    shutil.which = lambda name: f"/usr/bin/{name}"
    try:
        mod.settle(timeout=7)
    finally:
        subprocess.run, shutil.which = saved_run, saved_which
    settles = [a for a in rec.argv if "settle" in " ".join(a)]
    check(f"settle() asks udevadm to settle ({settles})", bool(settles))
    check("it is bounded, so a wedged udevd cannot wedge the installer",
          any("--timeout=7" in a[-1] for a in settles))

    # install_rule must go reload -> trigger -> settle, and must not measure anything
    # itself (measuring is the caller's job, after this returns).
    with tempfile.TemporaryDirectory() as d:
        rule = os.path.join(d, bundle.RULE_NAME)
        shutil.copyfile(os.path.join(ROOT, bundle.RULE_REL), rule)
        mod2 = load_priv()
        rec2 = Recorder()
        installed = os.path.join(d, "installed.rules")
        mod2.UDEV_RULE = installed
        subprocess.run = rec2
        shutil.which = lambda name: f"/usr/bin/{name}"
        try:
            ok = mod2.install_rule(rule, dry=False)
        finally:
            subprocess.run, shutil.which = saved_run, saved_which
        flat = [" ".join(a) for a in rec2.argv]
        # Measured INSIDE the block: the target lives in the temp directory, and asking
        # after it has been removed tests the destructor, not the writer.
        wrote = os.path.isfile(installed)
    check(f"install_rule writes the rule and reports success ({ok})", ok and wrote)
    order = [i for i, line in enumerate(flat)
             if "reload-rules" in line or "trigger" in line or "settle" in line]
    check(f"reload, then trigger, then settle ({[flat[i] for i in order]})",
          len(order) == 3
          and "reload-rules" in flat[order[0]]
          and "trigger" in flat[order[1]]
          and "settle" in flat[order[2]])
    src = open(PRIV).read()
    body = src.split("def install(")[1]
    check("the install verdict polls for the condition rather than trusting the queue",
          "time.sleep" in body and "while waited" in body)


def test_the_parent_flushes_before_the_child_talks():
    """Ordering is part of what a privileged transcript means.

    The script writes straight to the same fd as the Python front end, and Python
    block-buffers when stdout is a pipe -- so a logged run reads as though the grant had
    already happened and only then been explained. In a terminal Python line-buffers and
    the order looks fine, which is exactly why nobody notices until someone saves the
    output of a privileged command.
    """
    src = open(os.path.join(ROOT, "glasspad", "__main__.py")).read()
    for i, line in enumerate(src.splitlines(), start=1):
        if "subprocess.call(" in line:
            window = "\n".join(src.splitlines()[max(0, i - 6):i - 1])
            check(f"line {i}: flushed before spawning",
                  "sys.stdout.flush()" in window and "sys.stderr.flush()" in window)


def test_a_lie_in_the_exit_status_is_not_passed_on_as_failure():
    """Measure before agreeing that it failed.

    On real hardware the script's exit status contradicted its own sentence three lines
    above:

        writable by josh: GlassPad can create a pad now.
        The installer failed with exit 1

    because `[ "$waited" -gt 0 ] && say ...` was the last statement of the success path,
    and returned 1 whenever the device became writable immediately -- the common case. The
    device is the fact; an exit code is a claim about the fact, and this program has
    printed confident wrong verdicts often enough that the claim gets checked.
    """
    import subprocess as sp
    import glasspad.__main__ as m

    real_call, real_settle = sp.call, m._uinput_state_settling
    sp.call = lambda cmd, *a, **k: 1
    m._uinput_state_settling = lambda *a, **k: (True, False)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = m.install_uinput([])
    finally:
        sp.call, m._uinput_state_settling = real_call, real_settle
    out = buf.getvalue()
    check(f"exit 1 from the script, but the device works: rc={rc}", rc == 0)
    check("it says the status line was wrong, not the grant", "IS writable now" in out)
    check("and still asks for the exit code to be reported",
          "report that exit code" in out)


def test_a_real_failure_is_still_called_a_failure():
    """The other side of trusting the measurement: a genuinely broken grant stays broken.

    Softening the exit-status path without this check would make the installer unable to
    fail, which is worse than the bug it fixes.
    """
    import subprocess as sp
    import glasspad.__main__ as m

    real_call, real_settle = sp.call, m._uinput_state_settling
    sp.call = lambda cmd, *a, **k: 1
    m._uinput_state_settling = lambda *a, **k: (False, False)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = m.install_uinput([])
    finally:
        sp.call, m._uinput_state_settling = real_call, real_settle
    check(f"device not writable, nonzero exit (rc={rc})", rc != 0)
    check("and the output says where to look", "read its output above" in buf.getvalue())


def test_the_grant_script_never_leaves_its_status_to_the_last_statement():
    """The invariant that `[ cond ] && cmd` was standing in for.

    In POSIX shell a script's status is whatever the last command returned, so a success
    path ending in a conditional print exits 1 on the happy day. Python has the same
    hazard in a different costume: a function that falls off the end returns None, and
    `raise SystemExit(None)` means 0 -- so a path that forgot its return code reports
    success. Pinned structurally instead of by example: every exit out of install(),
    uninstall() and report() is an explicit integer, and each function ends with one.
    """
    import ast

    tree = ast.parse(open(PRIV).read())
    funcs = {n.name: n for n in tree.body
             if isinstance(n, ast.FunctionDef)
             and n.name in ("install", "uninstall", "report")}
    check(f"the three modes are top-level functions ({sorted(funcs)})",
          set(funcs) == {"install", "uninstall", "report"})
    for name, fn in funcs.items():
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
        bare = [n for n in returns if n.value is None]
        check(f"{name}() has no bare `return` ({len(returns)} return statements)",
              not bare)
        ints = [n for n in returns
                if isinstance(n.value, ast.Constant) and isinstance(n.value.value, int)]
        check(f"{name}() returns integers everywhere ({len(ints)}/{len(returns)})",
              len(ints) == len(returns))
        last = fn.body[-1]
        check(f"{name}() ends on an explicit return, not a fall-through",
              isinstance(last, ast.Return) and last.value is not None)
    main = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    tails = [ast.dump(n) for n in main.body if isinstance(n, ast.Return)]
    check("main() returns the mode's verdict rather than its own guess",
          any("Attribute" in t and "Constant(value=0)" not in t for t in tails[-2:]))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    crashed = 0
    for fn in tests:
        print(f"{fn.__name__}:")
        try:
            fn()
        except Exception as exc:                       # noqa: BLE001
            # A traceback is ONE failure, not the end of the run. It used to abort the
            # loop, so the tally never printed and every test after the crash silently
            # stopped existing -- the quietest possible way to lose coverage.
            crashed += 1
            FAILS.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
