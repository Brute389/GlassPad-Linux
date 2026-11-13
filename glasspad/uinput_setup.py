#!/usr/bin/env python3
"""The privileged half of a GlassPad install. Standard library only, on purpose.

GlassPad ships as one AppImage. There is no source-checkout installer any more, and the
work that needs root still has to happen: load the uinput module at boot, hand
/dev/uinput to the input group, and add your account to that group. The only thing inside
the AppImage is Python, so that work lives here.

This file is deliberately **self-contained** -- no imports from the `glasspad` package, no
GObject, no third-party modules -- because of how it has to be run. Root cannot read an
AppImage's FUSE mount (`pkexec sh /tmp/.mount_XXXX/...` dies with EACCES before line one;
found on a real virgin install, and invisible to every dry run, because a dry run does not
elevate and so reads the file as the user who IS allowed to). So `--install-uinput` copies
this script and the udev rule into a private directory and runs:

    pkexec python3 /tmp/glasspad-priv-XXXX/uinput_setup.py --uinput --target-user josh

A self-contained script is what makes that possible without staging the whole package or
handing root a PYTHONPATH pointing into a temporary directory.

Two properties the shell version earned the hard way, kept here:

  * **One policy file.** The rule comes from `packaging/glasspad-udev.rules`, staged next
    to this script. There is no embedded copy, because two versions of a permission policy
    is how a machine ends up running the one nobody reviewed. A missing rule file is a
    fatal error, not a fallback.
  * **The grant is the smallest one that works.** logind puts a uaccess ACL on /dev/uinput
    for the active session, and Steam's and KDE Connect's rules tag it the same way -- so on
    many machines nothing needs to be installed at all, and installing it anyway leaves a
    permanent /etc edit plus `input` membership that a later uninstall cannot safely undo.

Run it directly to see what root would do to this machine, without doing it:

    python3 -m glasspad.uinput_setup --check
    python3 glasspad/uinput_setup.py --uinput --dry-run --target-user you
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

MODULES_CONF = "/etc/modules-load.d/uinput.conf"
UDEV_RULE = "/etc/udev/rules.d/99-glasspad-uinput.rules"
RULE_NAME = "glasspad-udev.rules"
# The node the permission checks look at. Overridable so the skip-versus-grant decision is
# testable on a machine whose real /dev/uinput already works -- otherwise a test can only
# ever observe whichever branch this one machine happens to be in, and the branch nobody
# observes is the one that rots.
NODE = os.environ.get("GLASSPAD_UINPUT_NODE", "/dev/uinput")
# How the caller should be typed at. The staged script must not print a command the user
# may not have: inside an AppImage the word is the path to the image, and a message that
# says `glasspad --install-uinput` to someone whose PATH has no such thing is a dead end
# printed at the worst possible moment. The front end passes its own hint down; the
# default is the checkout spelling.
CLI_HINT = "glasspad"

DISCLOSURE = (
    "what the grant is, plainly: the input group can read every keyboard,\n"
    "  mouse and touchpad on this machine, not just write /dev/uinput. GlassPad\n"
    "  needs the write; group membership is how Linux hands it out, and the read\n"
    "  comes with it. If that is too much for this machine, do not install the\n"
    "  rule -- see the line above about access that already exists."
)


# --- small helpers ----------------------------------------------------------
def say(text: str = "") -> None:
    print(text)


def is_root() -> bool:
    return os.geteuid() == 0


def run(cmd, dry: bool) -> int:
    """Run a privileged command, or print it. `dry` never executes anything."""
    if dry:
        say(f"  would run: {' '.join(cmd)}")
        return 0
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        say(f"  ! {' '.join(cmd)} exited {p.returncode}: "
            f"{(p.stderr or p.stdout).strip()[:160]}")
    return p.returncode


def settle(timeout: int = 10) -> None:
    # `udevadm trigger` queues events and returns immediately; udevd applies them in the
    # background. Anything that reads the device afterwards is racing it -- the install
    # verdict lost that race on real hardware and reported "NOT yet writable" for a rule
    # that had in fact been applied a moment later. Bounded, because a wedged udevd must
    # not wedge the installer.
    if not shutil.which("udevadm"):
        return
    subprocess.run(["udevadm", "settle", f"--timeout={timeout}"],
                   capture_output=True, text=True)


def writable_by(target_user: str) -> bool:
    """Can the account that will run GlassPad write the node RIGHT NOW?

    Root can always write it, so testing as root proves nothing about the person who just
    typed their password -- which is exactly how an installer used to report success on a
    machine where the user still could not create a pad.
    """
    if is_root() and target_user and target_user != "root":
        p = subprocess.run(["su", "-s", "/bin/sh", target_user, "-c",
                            f"test -w '{NODE}'"], capture_output=True, text=True)
        return p.returncode == 0
    return os.access(NODE, os.W_OK)


def in_group(target_user: str, group: str = "input") -> bool:
    p = subprocess.run(["id", "-nG", target_user], capture_output=True, text=True)
    return group in (p.stdout or "").split()


def find_rule() -> str | None:
    """Where the udev rule lives: next to this file when staged, else in the tree.

    Staged layout first because that is the only layout that exists inside a running
    AppImage. No embedded fallback text, on purpose (see the module docstring).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, RULE_NAME),
                 os.path.join(here, "..", "packaging", RULE_NAME),
                 os.path.join(here, "packaging", RULE_NAME)):
        cand = os.path.normpath(cand)
        if os.path.isfile(cand):
            return cand
    return None


# --- the pieces -------------------------------------------------------------
def ensure_module(dry: bool) -> None:
    say("uinput kernel module")
    listed = False
    try:
        with open(MODULES_CONF, encoding="utf-8") as fh:
            listed = any(ln.strip() == "uinput" for ln in fh)
    except OSError:
        listed = False
    if listed:
        say(f"  {MODULES_CONF} already lists uinput")
    else:
        if dry:
            say(f"  would write: {MODULES_CONF}  (contains: uinput)")
        else:
            try:
                with open(MODULES_CONF, "w", encoding="utf-8") as fh:
                    fh.write("uinput\n")
                say(f"  wrote {MODULES_CONF}")
            except OSError as exc:
                say(f"  ERROR cannot write {MODULES_CONF}: {exc}")
    if os.path.exists(NODE):
        say("  already loaded")
    else:
        run(["modprobe", "uinput"], dry)


def install_rule(rule_src: str, dry: bool) -> bool:
    say("udev rule")
    if dry:
        say(f"  would copy {rule_src} -> {UDEV_RULE}")
        return True
    try:
        shutil.copyfile(rule_src, UDEV_RULE)
    except OSError as exc:
        say(f"  ERROR cannot write {UDEV_RULE}: {exc}")
        return False
    say(f"  wrote {UDEV_RULE}")
    if shutil.which("udevadm"):
        run(["udevadm", "control", "--reload-rules"], dry)
        run(["udevadm", "trigger"], dry)
        settle()
    return True


def add_group(target_user: str, dry: bool) -> bool:
    """Returns whether THIS run added the group. That return value is the whole point.

    The verdict at the end of an install used to say "if this run added the group, it only
    applies at the next login" -- unconditionally, on a machine where the answer was no and
    the group had been there for years, so the user logged out and back in for nothing while
    the real cause sat untouched. Guessing at a cause is worse than not knowing it.
    """
    say(f"permissions for {target_user}")
    if target_user == "root":
        say("  running as root; skipping group membership")
        return False
    if in_group(target_user):
        say("  already in group input")
        return False
    if run(["usermod", "-aG", "input", target_user], dry) != 0 and not dry:
        say("  ERROR could not add the group")
        return False
    if not dry:
        say(f"  NOTE: {target_user} must log out and back in for the group to apply.")
    return True


def install(target_user: str, dry: bool = False, force_rule: bool = False) -> int:
    """Grant access. Returns 0 when a pad can be created afterwards, 1 when it cannot."""
    say(f"uinput access for {target_user}")
    if not dry and not is_root():
        say(f"  ERROR: this step needs root. Use `{CLI_HINT} --install-uinput`,")
        say("  which asks for one password, or run it through pkexec yourself.")
        return 2

    ensure_module(dry)

    rule_src = find_rule()
    # Checked before the decision below, not inside the branch that writes. A bundle
    # carrying no rule is a truncated download, and that is worth saying even on a machine
    # where nothing happens to need installing today -- the skip path must not swallow a
    # defect report.
    if rule_src is None:
        say(f"  ERROR: rule file not found. Expected {RULE_NAME} next to this script.")
        say("  This script will not fall back to an embedded copy: two versions")
        say("  of a permission policy is how a machine ends up running the one")
        say("  nobody reviewed.")
        return 1

    if force_rule:
        say("  --force-rule: granting anyway, whatever already covers the node")
    if not force_rule and writable_by(target_user):
        if dry:
            who = target_user if is_root() else os.environ.get("USER", "you")
            say("  decision: WOULD NOT install a rule, because the node is already")
            say(f"  writable for {who}. (A dry run asks as the invoking user, so on a")
            say(f"  pkexec run the account tested would be {target_user}.)")
            say("  --force-rule rehearses the grant path instead, if you want to see")
            say("  what it would write.")
        else:
            say("  already granted, without GlassPad: the device node is writable for")
            say(f"  {target_user} as things stand, so nothing here changes. No rule in")
            say("  /etc, no group membership.")
            say("  logind's uaccess ACL, or Steam's / KDE Connect's rules, cover it.")
            say("  If you later remove those and the pad stops working, run this")
            say("  again with --force-rule.")
        return 0

    say(f"  {DISCLOSURE}")
    say("")
    if not install_rule(rule_src, dry):
        say("")
        say("FAILED: no udev rule was installed, so nothing about permissions")
        say("changed and GlassPad still cannot create a pad.")
        return 1
    add_group(target_user, dry)

    say("")
    if dry:
        say("  dry run: nothing changed")
        return 0
    if not os.path.exists(NODE):
        say("  /dev/uinput still does not exist: the module did not load.")
        say("  Check with:  sudo modprobe uinput")
        return 1

    # An empty udev queue is not the same as this node having been rewritten, so poll for
    # the condition we care about rather than trusting the queue's word for it.
    waited = 0.0
    while waited < 5.0 and not writable_by(target_user):
        time.sleep(0.25)
        waited += 0.25
    perms = subprocess.run(["ls", "-l", NODE], capture_output=True, text=True).stdout
    perms = " ".join(perms.split()[:4]) if perms.strip() else "?"
    say(f"  /dev/uinput : {perms}")
    if writable_by(target_user):
        say(f"  writable by {target_user}: GlassPad can create a pad now.")
        if 0.25 <= waited <= 0.5:
            say("  (took about half a second for udev to apply the rule)")
        return 0
    say(f"  {target_user} STILL cannot write /dev/uinput.")
    say("  If the group was added by this run, it only applies at the next login.")
    say("  Otherwise look at what else owns the node:")
    say(f"      ls -l {NODE} ; getfacl {NODE}")
    say("      udevadm info /dev/uinput | grep uaccess")
    return 1


def uninstall(dry: bool = False) -> int:
    """Remove ONLY what this installer writes. Narrow on purpose."""
    say("removing the GlassPad uinput grant")
    removed_any = False
    for path in (UDEV_RULE, MODULES_CONF):
        if os.path.isfile(path):
            if dry:
                say(f"  would remove {path}")
            else:
                try:
                    os.unlink(path)
                    say(f"  removed {path}")
                except OSError as exc:
                    say(f"  ERROR removing {path}: {exc}")
                    return 1
            removed_any = True
        else:
            say(f"  not present: {path}")
    if not dry and removed_any:
        if shutil.which("udevadm"):
            run(["udevadm", "control", "--reload-rules"], dry)
            run(["udevadm", "trigger"], dry)
            settle()
    say("")
    say("  left alone on purpose: the uinput kernel module (already loaded by")
    say("  others), group membership (`input` predates GlassPad on many machines,")
    say("  and other devices depend on it), your layout, and every desktop entry.")
    if not dry and os.access(NODE, os.W_OK):
        say("")
        say("  note: /dev/uinput is STILL writable for you without the rule. Something")
        say("  else grants it -- logind's uaccess ACL, Steam's 60-steam-input.rules, or")
        say("  KDE Connect's. Removing our rule changed nothing except which policy file")
        say("  you can see; that is not a failure, it is why this command is honest")
        say("  in both directions.")
    return 0


def status_rows(target_user: str) -> list[tuple[str, str]]:
    """The device chain as label/value rows, for `glasspad --status`.

    install.sh used to own this reporting as `--check`, and it had its own idea of the
    chain. With the shell gone there is one owner and one rendering: --status calls this,
    --check prints the same rows below, so the two cannot drift into disagreeing about
    whether your machine works.
    """
    perms = "?"
    if os.path.exists(NODE):
        out = subprocess.run(["ls", "-l", NODE], capture_output=True,
                             text=True).stdout.split()
        if len(out) >= 4:
            perms = " ".join(out[:4])
    rows = [
        ("modules-load",
         f"{MODULES_CONF} present" if os.path.isfile(MODULES_CONF)
         else "not configured (uinput will not survive a reboot)"),
        ("udev rule", "present" if os.path.isfile(UDEV_RULE) else "not installed"),
    ]
    if writable_by(target_user):
        if os.path.isfile(UDEV_RULE):
            grant = ("IS writable for " + target_user
                     + " (ours is installed; uaccess would do the same on its own)")
        else:
            grant = ("IS writable for " + target_user
                     + " (nothing of ours: logind uaccess, or Steam's / KDE Connect's)")
    else:
        grant = f"NOT writable for {target_user} -- run --install-uinput"
    rows.append(("grant", grant))
    rows.append(("group input", "member" if in_group(target_user) else "NOT a member"))
    return rows


def report(target_user: str) -> int:
    """The read-only verdict when this script is run on its own."""
    say("kernel")
    if not os.path.exists(NODE):
        say("  /dev/uinput : MISSING (module not loaded)")
    else:
        out = subprocess.run(["ls", "-l", NODE], capture_output=True,
                             text=True).stdout.split()
        perms = " ".join(out[:4]) if len(out) >= 4 else "?"
        say(f"  /dev/uinput : {'writable' if os.access(NODE, os.W_OK) else 'present, not writable'} ({perms})")
    for label, value in status_rows(target_user):
        say(f"  {label:<13}: {value}")
    return 0


def main(argv=None) -> int:
    global CLI_HINT
    ap = argparse.ArgumentParser(
        prog="glasspad-uinput-setup",
        description="Grant or remove /dev/uinput access for GlassPad. "
                    "Standard library only: this file is staged out of the AppImage "
                    "and run as root, and root cannot read the AppImage mount.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--uinput", action="store_true",
                      help="install what is needed: module config, udev rule, group. "
                           "Skipped entirely when the node is already writable -- "
                           "read the disclosure it prints before reaching for "
                           "--force-rule.")
    mode.add_argument("--uninstall-uinput", action="store_true",
                      help="remove the rule and modules-load file this installer "
                           "writes. Does not touch group membership, the module, your "
                           "layout or your desktop entry.")
    mode.add_argument("--check", action="store_true",
                      help="report the state of the chain and change nothing")
    ap.add_argument("--target-user", default=os.environ.get("USER") or "root",
                    help="the account that will run GlassPad (required for the "
                         "privileged modes, since root cannot answer for it)")
    ap.add_argument("--dry-run", action="store_true",
                    help="rehearse the decision and the file writes; needs no root")
    ap.add_argument("--cli-hint", default=CLI_HINT,
                    help="how to invoke this program, for the text of error messages "
                         "(the front end passes what it resolved; a staged script "
                         "cannot know whether `glasspad` is on PATH)")
    ap.add_argument("--force-rule", action="store_true",
                    help="install the rule and add the group even where access already "
                         "exists by other means (logind uaccess, Steam, KDE Connect)")
    args = ap.parse_args(list(argv) if argv is not None else sys.argv[1:])
    CLI_HINT = args.cli_hint

    if args.check:
        return report(args.target_user)
    if args.uninstall_uinput:
        if not args.dry_run and not is_root():
            say("  ERROR: removing /etc files needs root.")
            return 2
        return uninstall(args.dry_run)
    return install(args.target_user, dry=args.dry_run, force_rule=args.force_rule)


if __name__ == "__main__":
    raise SystemExit(main())
