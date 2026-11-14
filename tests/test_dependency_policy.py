"""The missing-dependency path: what we tell the user, and what this machine allows.

Run: python3 tests/test_dependency_policy.py     (no pytest, no display, no root)

Every probe in glasspad/deps.py is injected here. That is the whole point of the shape of
`system_profile(os_release=..., env=..., exists=..., proc_mounts=...)`: a test that reads
this machine's /etc/os-release is measuring this machine, not the code -- which is exactly
how a suite once asserted "and the state agrees" while passing against a real
~/.local/bin/glasspad that happened to be installed.
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import bundle  # noqa: E402
from glasspad import deps  # noqa: E402
from glasspad import notice  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


def fake_release(text, where):
    path = os.path.join(where, "os-release")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


#: (os-release contents, files that exist, root mount options) -> what we claim
SYSTEMS = {
    "cachyos": dict(rel='ID=cachyos\nID_LIKE="arch linux"\nNAME="CachyOS Linux"\n',
                    exists=(), root="rw,noatime,compress=zstd:3",
                    family=deps.FAMILY_PACMAN, mutable=True),
    "fedora-workstation": dict(rel='ID=fedora\nNAME="Fedora Linux"\n',
                               exists=(), root="rw,seclabel,relatime",
                               family=deps.FAMILY_DNF, mutable=True),
    "bazzite": dict(rel='ID=fedora\nID_LIKE="uos-desktop"\nVARIANT_ID=bona-novalinux\n',
                    exists=("/run/ostree-booted",), root="rw,seclabel,relatime",
                    family=deps.FAMILY_RPM_OSTREE, mutable=False),
    "steamos": dict(rel='ID=steamos\nID_LIKE="arch"\nNAME="SteamOS"\n',
                    exists=("/usr/bin/steamos-readonly",), root="rw,relatime",
                    family=deps.FAMILY_PACMAN, mutable=False),
    "nixos": dict(rel='ID=nixos\nNAME="NixOS 25.05"\n',
                  exists=("/nix/store",), root="rw",
                  family=deps.FAMILY_NIX, mutable=False),
    "debian": dict(rel='ID=debian\nNAME="Debian GNU/Linux"\n',
                   exists=(), root="rw,relatime",
                   family=deps.FAMILY_APT, mutable=True),
    "rbd-root": dict(rel='ID=void\nNAME="Void Linux"\n',
                     exists=(), root="ro,relatime",
                     family=deps.FAMILY_UNKNOWN, mutable=False),
    "unknown": dict(rel="", exists=(), root="", family=deps.FAMILY_UNKNOWN, mutable=False),
}


def profile_for(name, tmpdir, path=None):
    """The verdict for a synthetic machine, with nothing read from the host."""
    spec = SYSTEMS[name]
    rel = fake_release(spec["rel"], tmpdir)
    present = set(spec["exists"])

    def exists(p):
        return p in present

    # PATH stripped to a directory holding only the manager this synthetic system has, so
    # shutil.which cannot see this machine's pacman and hand out a command on a Deck.
    bindir = os.path.join(tmpdir, "bin-" + name)
    os.makedirs(bindir, exist_ok=True)
    manager = {deps.FAMILY_PACMAN: "pacman", deps.FAMILY_DNF: "dnf",
               deps.FAMILY_APT: "apt-get"}.get(spec["family"])
    if manager:
        # A dummy executable, not a symlink to the host's copy: this machine has pacman and
        # no dnf, so linking would make the Fedora and Debian systems "unknown" and the
        # suite would pass for the wrong reason on Arch hosts and fail on others.
        # shutil.which only needs the file to exist and be executable.
        dummy = os.path.join(bindir, manager)
        if not os.path.exists(dummy):
            with open(dummy, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(dummy, 0o755)
    mounts = os.path.join(tmpdir, "mounts-" + name)
    with open(mounts, "w", encoding="utf-8") as fh:
        fh.write(f"rootfs / ext4 {spec['root']} 0 0\n")
    return deps.system_profile(os_release=rel, env={"PATH": bindir}, exists=exists,
                               proc_mounts=mounts)


def test_every_synthetic_system_gets_the_verdict_we_claim():
    """The table is the specification. Eight machines we will never all own, asserted."""
    with tempfile.TemporaryDirectory() as d:
        for name, spec in SYSTEMS.items():
            prof = profile_for(name, d)
            check(f"{name}: family {prof['family']} (want {spec['family']})",
                  prof["family"] == spec["family"])
            check(f"{name}: mutable={prof['mutable']} (want {spec['mutable']})",
                  prof["mutable"] is spec["mutable"])


def test_an_immutable_system_is_never_handed_a_package_command():
    """The SteamOS failure, made impossible.

    Deck and RetroDeck users install through managers, so they are the people most likely
    to run this -- and the command that was suggested to them was `pacman -S` against a
    read-only base image, from a table that labelled Debian the family.
    """
    with tempfile.TemporaryDirectory() as d:
        for name in ("steamos", "bazzite", "nixos", "rbd-root", "unknown"):
            prof = profile_for(name, d)
            cmd = deps.install_command(prof)
            check(f"{name}: no command offered ({cmd})", cmd is None)
            check(f"{name}: and it says why ({prof['reason'][:40]!r})",
                  bool(prof["reason"]))
            text = "\n".join(deps.notice_lines("missing", profile=prof, looked_up=False))
            # "sudo ..." is the shape of a suggestion; a reason may name the command it is
            # refusing to suggest ("`dnf install` will run and change nothing durable") and
            # still be honest. An earlier version of this check banned the words and so was
            # wrong about Bazzite, which is exactly the sentence a Deck user needs.
            suggested = [l for l in text.splitlines() if l.strip().startswith("sudo ")]
            check(f"{name}: no runnable command is suggested ({suggested})",
                  not suggested)
            check(f"{name}: and the reason is printed verbatim",
                  prof["reason"] in text)


def test_a_mutable_system_gets_one_command_for_its_own_manager():
    with tempfile.TemporaryDirectory() as d:
        want = {
            "cachyos": ["sudo", "pacman", "-S", "--needed", "gtk4-layer-shell"],
            "fedora-workstation": ["sudo", "dnf", "install", "gtk4-layer-shell"],
            "debian": ["sudo", "apt", "install", "libgtk4-layer-shell0"],
        }
        for name, cmd_want in want.items():
            prof = profile_for(name, d)
            got = deps.install_command(prof)
            check(f"{name}: {' '.join(got or [])}", got == cmd_want)


def test_the_steamos_claim_is_gone_from_the_package_table():
    """`DEPENDENCY_PACKAGES` is printed verbatim by --status and the README table.

    A label naming a distro whose package manager it does not use is not a typo; it is an
    instruction. SteamOS is Arch-family and image-managed, so it belongs to neither this
    table's Debian row nor to a command at all.
    """
    labels = " ".join(label for label, _ids, _pkg, _v in bundle.DEPENDENCY_PACKAGES)
    check(f"no row claims SteamOS ({labels})", "SteamOS" not in labels and "steamos" not in labels)
    for label, ids, pkg, verified in bundle.DEPENDENCY_PACKAGES:
        check(f"{label}: package name present ({pkg})", bool(pkg))
        check(f"{label}: verification note present",
              verified.startswith(("verified", "not verified")))


def test_notice_text_has_one_owner_for_window_terminal_and_report():
    """The window, the stderr fallback and the staged terminal must not diverge."""
    with tempfile.TemporaryDirectory() as d:
        for state in ("missing", "unbound", "nocompositor", "nodisplay", "ok"):
            prof = profile_for("cachyos", d)
            paras = deps.notice_lines(state, profile=prof, looked_up=False)
            plan = notice.window_plan(state, profile=prof, looked_up=False)
            check(f"{state}: the window shows exactly the shared paragraphs",
                  plan["paragraphs"] == paras)
            check(f"{state}: and a headline of its own", bool(plan["headline"]))
            check(f"{state}: always ends with something to do or a reason not to",
                  bool(paras))


def test_buttons_follow_what_the_machine_permits():
    with tempfile.TemporaryDirectory() as d:
        mutable = notice.window_plan("missing", profile=profile_for("cachyos", d),
                                     looked_up=False)
        deck = notice.window_plan("missing", profile=profile_for("steamos", d),
                                  looked_up=False)
        check("a machine that can install offers the terminal",
              "terminal" in mutable["buttons"])
        check(f"a Deck gets no terminal button ({deck['buttons']})",
              "terminal" not in deck["buttons"])
        check("both can copy, re-check and leave",
              all(b in deck["buttons"] for b in ("copy", "again", "quit")))
        check("clipboard is the bare command where one exists",
              mutable["clipboard"].startswith("sudo "))
        check("and the whole explanation where none does",
              "\n" in deck["clipboard"] and "sudo" not in deck["clipboard"])


def test_staged_command_shows_itself_and_cancels_on_eof():
    """`staged_run` is what gets handed to a terminal. Two properties, both tested by running it."""
    cmd = ["true", "--just-testing"]
    argv = deps.staged_run(cmd)
    check("it is a shell invocation", argv[0] == "/bin/sh" and argv[1] == "-c")
    # Enter pressed: the command runs.
    r = subprocess.run(argv, input=b"\n", capture_output=True, timeout=15)
    check(f"Enter runs it (rc={r.returncode})", r.returncode == 0)
    # EOF, which is what a non-interactive open gives: nothing runs, and the exit says so.
    r2 = subprocess.run(argv, input=b"", capture_output=True, timeout=15)
    check(f"EOF cancels with 130 (rc={r2.returncode})", r2.returncode == 130)
    check("and the command is shown before anything happens",
          "About to run" in r2.stdout.decode() and "--just-testing" in r2.stdout.decode())
    # A command that would not work is never staged at all.
    with tempfile.TemporaryDirectory() as d:
        check("and there is nothing to stage on an immutable system",
              deps.install_command(profile_for("bazzite", d)) is None)


def test_repo_lookup_never_invents_absence():
    """Three answers, and 'we could not ask' must not become 'it is not there'."""
    check("unknown family -> None", deps.repo_lookup("x", "skynet") is None)
    check("empty name -> None", deps.repo_lookup("", deps.FAMILY_PACMAN) is None)
    check("a bogus package on this machine is a bool, not a crash",
          deps.repo_lookup("definitely-not-a-package-xyz", deps.FAMILY_PACMAN)
          in (True, False, None))


def test_probe_bound_reports_and_never_raises():
    """A diagnostic that crashes is worse than the bug, so the contract is: always a pair."""
    state, detail = deps.probe_bound()
    check(f"probe returned a known state ({state})",
          state in ("ok", "missing", "nodisplay", "unbound", "nocompositor"))
    check(f"and a sentence to go with it ({detail[:40]!r})", bool(detail))
    # No Wayland socket in the environment means no claim about the compositor.
    r = subprocess.run([sys.executable, "-m", "glasspad", "--explain"],
                       capture_output=True, text=True, timeout=120,
                       env={**os.environ, "WAYLAND_DISPLAY": "", "WAYLAND_SOCKET": "",
                            "DISPLAY": ""},
                       cwd=ROOT)
    check(f"--explain without a display exits 0 (rc={r.returncode})", r.returncode == 0)
    check("and says there is no session rather than blaming the compositor",
          "nodisplay" in r.stdout and "cannot use layer-shell" not in r.stdout,
          )


def test_explain_command_answers_every_flag_it_advertises():
    """`--explain` in --help is a promise; a flag that starts the overlay is the old bug."""
    help_text = subprocess.run([sys.executable, "-m", "glasspad", "--help"],
                               capture_output=True, text=True, timeout=60,
                               cwd=ROOT).stdout
    check("--help advertises --explain", "--explain" in help_text)
    for flag in ("--explain", "--explain=nocompositor", "--explain=ok"):
        r = subprocess.run([sys.executable, "-m", "glasspad", flag],
                           capture_output=True, text=True, timeout=120, cwd=ROOT,
                           env={**os.environ, "WAYLAND_DISPLAY": "", "DISPLAY": ""})
        check(f"{flag}: exits 0 (rc={r.returncode})", r.returncode == 0)
        check(f"{flag}: prints text, not a traceback",
              "Traceback" not in r.stderr and len(r.stdout) > 120)


def test_status_names_the_machine_before_suggesting_a_command():
    """--status is what people paste. It must carry the mutability verdict with it."""
    r = subprocess.run([sys.executable, "-m", "glasspad", "--status"],
                       capture_output=True, text=True, timeout=180, cwd=ROOT,
                       env={**os.environ})
    check(f"--status ran (rc={r.returncode})", r.returncode in (0, 1))
    check("it reports the system's package policy",
          "system          :" in r.stdout)
    check("and the package name for this family", "package         :" in r.stdout)
    line = next((l for l in r.stdout.splitlines() if l.startswith("system ")), "")
    check("and the measurement behind the verdict",
          "family=" in line and "root=" in line, )


def test_notice_module_imports_without_a_display():
    """The suite has no compositor. The module must still be importable and plan-able."""
    env = {**os.environ}
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("DISPLAY", None)
    r = subprocess.run([sys.executable, "-c",
                        "from glasspad import notice; "
                        "p = notice.window_plan('missing', looked_up=False); "
                        "print(len(p['paragraphs']), len(p['buttons']))"],
                       capture_output=True, text=True, timeout=120, cwd=ROOT, env=env)
    check(f"imports headless (rc={r.returncode}: {r.stderr.strip()[:60]})",
          r.returncode == 0)
    parts = r.stdout.split()
    check("and plans a window headless", len(parts) == 2 and int(parts[0]) >= 3)


def test_the_cairo_bridge_is_a_state_the_app_can_report():
    """Debian and Ubuntu split PyGObject's cairo bridge into python3-gi-cairo.

    Without it every control fails to draw with `KeyError: could not find foreign type
    Context`, raised inside PangoCairo, on a machine where every other --status row is
    green. The suites on ubuntu:24.04 hit this before any user could, so the app now asks
    the question out loud.
    """
    ok, why = deps.cairo_bridge_state(probe=lambda: (True, "fine"))
    check("an injected healthy probe reports ok", ok and why == "fine")
    ok, why = deps.cairo_bridge_state(
        probe=lambda: (False, "no python3-gi-cairo"))
    check("and a broken one says which package to install", (not ok) and "gi-cairo" in why)

    def boom():
        raise RuntimeError("typelib exploded")
    ok, why = deps.cairo_bridge_state(probe=boom)
    check("a probe that raises is reported, never propagated",
          (not ok) and "RuntimeError" in why)

    # The real probe, on this machine. It must not raise here either way, and it must not
    # lie: if it says ok, a cairo.Context really does reach GObject code.
    ok, why = deps.cairo_bridge_state()
    check(f"the real probe answers without raising ({why[:40]}...)", isinstance(ok, bool))
    if ok:
        import cairo
        import gi
        gi.require_foreign("cairo", "Context")
        check("    and 'ok' means require_foreign succeeds", True)
    src = open(os.path.join(ROOT, "glasspad", "__main__.py"), encoding="utf-8").read()
    check("--status prints the row", "cairo bridge" in src)
    check("    via the policy function, not a private copy of the probe",
          "cairo_bridge_state()" in src)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)


