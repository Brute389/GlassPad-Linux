"""Finding the two things a package cannot carry: libgtk4-layer-shell and uinput.

The layer-shell library used to be hardcoded to ``/usr/lib/libgtk4-layer-shell.so.0``.
That is an Arch path. Debian/Ubuntu keep it in ``/usr/lib/x86_64-linux-gnu``, Fedora in
``/usr/lib64``, and a bundle that ships its own copy keeps it under ``$APPDIR/usr/lib``
(AppImage) or ``$GLASSPAD_BUNDLE/lib`` (the self-extracting launcher). Preloading happens
before ``gi`` is imported, so this lives in its own module with no heavy imports and can
be tested without a display.

Why the library must be preloaded at all: ``g_object_new_class_init`` for GtkWindow opens
``libwayland-client.so.0`` with RTLD_LOCAL, and ``wl_proxy_marshal_flags`` is a C11 variadic
macro -- no dynamic symbol -- so the wrapper's call binds to whatever libwayland the loader
already resolved globally. Loading layer-shell with RTLD_GLOBAL first is the only reliable
order. See the note above ``_preload_layer_shell`` in overlay_layer.py.
"""
from __future__ import annotations

import contextlib
import ctypes.util
import os
import shlex
import shutil
import tempfile

# The Qt backend's own modules. Nothing the layer backend imports appears here -- that is
# the point, and it is asserted in the tests rather than assumed: 136 KiB, about a third of
# the package bytes, used to ride along in both bundles.
#
# The modules themselves are GONE -- deleted in the orphan-code pass, recoverable from git
# history. This list exists for one purpose now: the artefact suites assert none of these
# names reappear in a payload. A second backend that nobody runs is how you ship an
# untested code path, and a copy-paste out of history is the likeliest way to get one back
# by accident. Deliberately reviving the Qt port means editing this list on purpose.
REMOVED_QT_MODULES = (
    "window.py", "widgets.py", "layout.py", "zones.py", "touch_router.py",
    "editor.py", "license_window.py", "passthrough.py",
)

ENV_OVERRIDE = "GLASSPAD_LAYER_SHELL"
UINPUT_PATH = "/dev/uinput"

# Versioned first: a system with both usually has .0 as the real soname and the bare
# name as a -dev symlink that may point somewhere else entirely.
LAYER_SHELL_SONAMES = (
    "libgtk4-layer-shell.so.0",
    "libgtk4-layer-shell.so.2",   # a future ABI, if a distro ever bumps it
    "libgtk4-layer-shell.so",
)

# Ordered by "most likely to be the system library rather than a stray file".
LAYER_SHELL_DIRS = (
    "/usr/lib",
    "/usr/lib64",                              # Fedora / RHEL
    "/usr/lib/x86_64-linux-gnu",               # Debian / Ubuntu multiarch
    "/usr/lib64/x86_64-linux-gnu",
    "/usr/local/lib",
    "/usr/local/lib64",
)


def _existing(path: str) -> str | None:
    try:
        return path if path and os.path.isfile(path) else None
    except (OSError, ValueError):
        return None


def bundle_lib_dirs() -> tuple[str, ...]:
    """lib/ directories belonging to the bundle we are running inside, if any."""
    roots = []
    for var in ("GLASSPAD_BUNDLE_LIB", "GLASSPAD_BUNDLE", "APPDIR"):
        v = (os.environ.get(var) or "").strip()
        if v:
            roots.append(v)
    # A tree laid out next to the package: <root>/lib and <root>/usr/lib, where <root>
    # is the parent of the glasspad/ package directory.
    here = os.path.dirname(os.path.abspath(__file__))
    roots.append(os.path.dirname(here))
    out = []
    for r in roots:
        for sub in ("lib", "usr/lib", ""):
            p = os.path.join(r, sub) if sub else r
            if p not in out:
                out.append(p)
    return tuple(out)


def layer_shell_path(extra_dirs=()) -> tuple[str | None, str]:
    """Locate libgtk4-layer-shell.

    Returns ``(path, why)`` where ``why`` names the rule that matched -- the diagnostics
    print it, because "could not find libgtk4-layer-shell" is a useless message when the
    question is which of five places was searched.
    """
    over = (os.environ.get(ENV_OVERRIDE) or "").strip()
    if over:
        hit = _existing(over)
        if hit:
            return hit, f"${ENV_OVERRIDE}"
        # An explicit override that points nowhere is not silently ignored: callers want
        # to know they set it and it is wrong.
        return None, f"${ENV_OVERRIDE} is set but does not exist ({over})"

    dirs = list(bundle_lib_dirs()) + [d for d in extra_dirs if d]
    dirs += [d for d in LAYER_SHELL_DIRS if d not in dirs]

    for d in dirs:
        for soname in LAYER_SHELL_SONAMES:
            hit = _existing(os.path.join(d, soname))
            if hit:
                return hit, "bundle" if d in bundle_lib_dirs() else "distro path"

    found = ctypes.util.find_library("gtk4-layer-shell")
    if found:
        # ld.so may answer with a bare soname, which CDLL resolves the same way anyway.
        return found, "ld.so"

    return None, f"searched {len(dirs)} director{'y' if len(dirs) == 1 else 'ies'}"


def uinput_ready(path: str = UINPUT_PATH) -> tuple[bool, str]:
    """Can we create the virtual pad, and if not, what single command fixes it?

    The docstring used to end "...a package cannot fix this, only explain it", and
    the explanation was "add yourself to the group that owns it, or install an udev
    rule" -- true, abstract, and the kind of sentence that makes a person conclude
    the software is broken rather than that one command is missing. A bundle that
    ships the privileged script plus the rule CAN fix it, so the message names it.
    """
    if not os.path.exists(path):
        return False, (f"{path} does not exist: the uinput kernel module is not "
                       f"loaded. Fix with:  {cli_hint()} --install-uinput")
    if not os.access(path, os.W_OK):
        return False, (f"{path} exists but you cannot write it, so there is no "
                       f"gamepad. One command fixes it, one password:  "
                       f"{cli_hint()} --install-uinput")
    return True, path


def describe() -> str:
    """One line for --status and for the startup banner."""
    p, why = layer_shell_path()
    # --status prints this line among others whose labels are padded to 12 before the
    # colon; "layer-shell" is 11 characters, so one space lines the colons up.
    return f"layer-shell : {p or 'NOT FOUND'} ({why})"

# ---------------------------------------------------------------------------
# Shipped documents: the MIT notice and the third-party notices
# ---------------------------------------------------------------------------

#: Environment override, for a packager that puts them somewhere else entirely.
DOC_ROOT_ENV = "GLASSPAD_DOC_ROOT"

def doc_roots() -> list[str]:
    """Where a shipped document may live, most specific first, deduplicated.

    The three layouts differ and none of them is "next to the code":

        checkout    <repo>/glasspad/bundle.py            -> <repo>/
        AppImage    usr/share/glasspad/glasspad/bundle.py -> usr/share/glasspad,
                    while the documents are staged under usr/share/doc/glasspad

    A third layout, app/glasspad/, belonged to the self-extracting .run bundle and
    went with it. Nothing below specialises in a layout: the search walks candidate
    roots, so whatever a payload directory happens to be called is not a case.

    A single hardcoded dirname() is how the About box ended up reporting a licence
    path it had opened successfully when it had opened nothing at all. Searching is
    cheap; guessing is what breaks.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    out: list[str] = []
    env = os.environ.get(DOC_ROOT_ENV, "").strip()
    if env:
        out.append(env)
    for cand in (root,
                 os.path.join(root, "licenses"),
                 os.path.join(root, "..", "doc", "glasspad"),
                 os.path.join(root, "share", "doc", "glasspad"),
                 "/usr/share/doc/glasspad"):
        out.append(cand)
    seen, uniq = set(), []
    for c in out:
        c = os.path.normpath(c)
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def find_doc(name: str) -> str | None:
    """First existing path for a shipped document, or None. Never raises."""
    for root in doc_roots():
        path = os.path.join(root, name)
        if os.path.isfile(path):
            return path
    return None


def read_doc(name: str) -> tuple[str, str | None]:
    """(text, problem). Text is empty when the document could not be read.

    The problem string is meant to be shown to the user, because a licence panel
    that renders an empty box is indistinguishable from one that renders nothing,
    and both read as "this app has no licence".
    """
    path = find_doc(name)
    if path is None:
        return "", f"{name}: not found (looked in {len(doc_roots())} places)"
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read().strip()
    except OSError as exc:
        return "", f"{name}: {exc}"
    if not body:
        return "", f"{name}: {path} is empty"
    return f"{path}\n{'-' * 72}\n{body}\n", None


# ---------------------------------------------------------------------------
# The privileged installer
# ---------------------------------------------------------------------------
# A bundle cannot carry /etc/udev/rules.d: writing nothing outside itself is the
# premise of the format, and a udev rule inside a squashfs image is not a rule.
# What it CAN carry is the rule file and the script that installs it, which is
# what turns "download one file" into a complete install. These resolvers are the
# only place that knows where those two landed, and --status uses them too so the
# two can never disagree about what is installed.
# The privileged pair: the script that changes permissions, and the rule it installs.
#
#     checkout    <repo>/glasspad/uinput_setup.py   <repo>/packaging/glasspad-udev.rules
#     AppImage    usr/share/glasspad/glasspad/       usr/share/glasspad/packaging/
#
# Both are needed together: the script refuses to invent a policy, so no rule means no
# grant, and it looks for the rule NEXT TO ITSELF because that is the only relationship
# that survives being staged into a temporary directory.

INSTALLER_ROOT_ENV = "GLASSPAD_INSTALLER_DIR"
PRIV_NAME = "uinput_setup.py"
RULE_NAME = "glasspad-udev.rules"
RULE_REL = os.path.join("packaging", RULE_NAME)


def _cwd() -> str | None:
    """The working directory, if it still exists.

    ``os.getcwd()`` does not return something harmless for a deleted directory, it raises
    FileNotFoundError. That is a normal state for a process to be in -- a temp directory
    cleaned up around it, an unmounted drive, a service whose WorkingDirectory was
    removed, a shell that deleted the folder it was standing in -- and none of it has
    anything to do with where GlassPad keeps its files. A diagnostic that crashes with a
    traceback is worse than the bug it was called to explain, so the cwd is one candidate
    among several, and a missing candidate is skipped.
    """
    try:
        return os.getcwd()
    except OSError:
        return None


def installer_roots() -> list[str]:
    """Where to look for the privileged pair: override, package root, then cwd.

    $GLASSPAD_INSTALLER_DIR is first because a test must be able to point this at an
    empty directory and get "not shipped" -- without the override winning, the resolver
    walks on to find the real checkout, which is correct behaviour and a live change to
    /etc when the caller also elevates. That has happened.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    out: list[str] = []
    env = os.environ.get(INSTALLER_ROOT_ENV, "").strip()
    if env:
        out.append(env)
    out.append(root)
    cwd = _cwd()
    if cwd:
        out.append(cwd)
    seen, uniq = set(), []
    for c in out:
        c = os.path.normpath(c)
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def find_priv_script() -> str | None:
    """Path to glasspad/uinput_setup.py, or None when this build did not ship one."""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, PRIV_NAME)]
    cands += [os.path.join(root, "glasspad", PRIV_NAME) for root in installer_roots()]
    for cand in cands:
        if os.path.isfile(cand) and os.access(cand, os.R_OK):
            return cand
    return None


def priv_rule_src(script: str | None) -> str | None:
    """The rule that script installs, or None when it is not alongside.

    Searched next to the script first (the staged layout), then ../packaging/ (the
    checkout), then packaging/ (the payload root). Nothing embedded: two versions of a
    permission policy is how a machine ends up running the one nobody reviewed.
    """
    if not script:
        return None
    d = os.path.dirname(os.path.abspath(script))
    for cand in (os.path.join(d, RULE_NAME),
                 os.path.normpath(os.path.join(d, "..", RULE_REL)),
                 os.path.join(d, RULE_REL)):
        if os.path.isfile(cand):
            return cand
    return None


STAGED_NOTE = (
    "The privileged script and the rule are copied to a private directory first: an\n"
    "AppImage payload is a FUSE mount only your own account may open, so root could\n"
    "not read either of them in place.")


def _inside_bundle(path: str) -> bool:
    # NOT os.path.abspath(os.environ.get("APPDIR") or ""): abspath("") is the cwd, which
    # would make every run from inside a source checkout look like a bundle and silently
    # stage the pair -- the one thing that makes it unclear which file is about to execute
    # as root. An unset APPDIR means no APPDIR at all.
    appdir = os.environ.get("APPDIR") or ""
    appdir = os.path.abspath(appdir) if appdir else ""
    return "/.mount_" in path or bool(appdir and path.startswith(appdir + os.sep))


@contextlib.contextmanager
def staged_priv(script: str | None = None, rule: str | None = None):
    """Yield (script_path, rule_path) that ROOT can open, then clean up.

    An AppImage payload is a FUSE mount -- `ro,nosuid,nodev,user_id=1000` -- and a FUSE
    mount without allow_other is reachable only by the user who mounted it. Root is not
    that user. So the obvious command,

        pkexec python3 /tmp/.mount_GlassXXX/usr/share/glasspad/glasspad/uinput_setup.py

    fails with EACCES on the open(), before a single line runs. Found by a real install
    attempt on a virgin machine, and no dry run of mine could have shown it: --dry-run
    deliberately does not elevate, so the files were read by the one account permitted to.

    Fix: copy BOTH into a fresh 0700 directory on the real filesystem, side by side,
    because the script finds its rule next to itself and refuses to invent one. The
    invoking user owns the directory, so nobody else on the machine can swap the script
    between copy and elevation, and root bypasses the mode bits to read it.

    A checkout is left alone: /home is root-readable, and copying would only obscure
    which file is about to be executed.
    """
    script = script or find_priv_script()
    if not script or not os.path.exists(script):
        yield None, None
        return
    rule = rule or priv_rule_src(script)
    if not (_inside_bundle(script) or _inside_bundle(rule or "")):
        yield script, rule
        return
    if not rule:
        yield script, None          # the script itself will report the missing policy
        return
    tmp = tempfile.mkdtemp(prefix="glasspad-priv-")
    try:
        os.chmod(tmp, 0o700)
        dest_script = os.path.join(tmp, PRIV_NAME)
        shutil.copyfile(script, dest_script)
        shutil.copymode(script, dest_script)
        dest_rule = os.path.join(tmp, RULE_NAME)
        shutil.copyfile(rule, dest_rule)
        yield dest_script, dest_rule
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



# --- what a first-run user can actually run ---------------------------------
#
# Every diagnostic in this app is a sentence telling somebody to run something. For
# most of its life those sentences were written from the developer's side of the wall:
# `glasspad --install-uinput`, when the person reading it has one downloaded file and
# no command called glasspad, and `sudo pacman -S libgtk4-layer-shell`, which is not a
# package that exists. Both fail at the exact moment someone is following instructions
# under stress, and both fail with "not found" -- the least informative answer a
# message can give. These helpers resolve the names against the machine instead of
# against assumptions.

CLI_NAME = "glasspad"
BIN_DIR_ENV = "GLASSPAD_BIN_DIR"
LINK_MARKER = "# GlassPad CLI link -> "      # how we recognise our own files
OS_RELEASE = "/etc/os-release"

# The library that cannot be bundled, and what the package managers call it.
# `verified` is part of the data rather than a comment because the last person who
# guessed got it wrong in five places at once: Arch/CachyOS ships gtk4-layer-shell,
# and libgtk4-layer-shell -- the name we printed everywhere -- matches nothing in the
# repositories. tests/test_one_file_install.py re-derives the Arch entry from
# `pacman -Qo` on the installed library, so this row cannot rot quietly.
DEPENDENCY_PACKAGES = (
    ("Arch / CachyOS / EndeavourOS", ("arch", "cachyos", "manjaro"),
     "gtk4-layer-shell", "verified on Arch family: `pacman -Qo` on the installed "
     "library names this package, and `pacman -Ss libgtk4-layer-shell` matches nothing"),
    ("Fedora / Bazzite / Bluefin", ("fedora", "rhel"),
     "gtk4-layer-shell", "not verified on this machine"),
    ("Debian / Ubuntu / SteamOS", ("debian", "ubuntu"),
     "libgtk4-layer-shell0", "not verified on this machine"),
)


def os_release_ids() -> tuple[str, ...]:
    """ID and ID_LIKE from /etc/os-release, lowercased. Empty if unreadable."""
    out: list[str] = []
    try:
        with open(OS_RELEASE, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                key, _, val = line.partition("=")
                if key.strip() in ("ID", "ID_LIKE"):
                    out.extend(val.strip().strip('"').lower().split())
    except OSError:
        pass
    return tuple(out)


def os_release_label() -> str:
    try:
        with open(OS_RELEASE, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "this distribution"


def dependency_rows() -> list[tuple[str, str, str, bool]]:
    """(label, package, why-we-know, is-this-us) with the local row first."""
    ids = os_release_ids()
    rows = []
    for label, matches, pkg, verified in DEPENDENCY_PACKAGES:
        mine = any(any(i == m or i.startswith(m + "_") for m in matches) for i in ids)
        rows.append([label, pkg, verified, mine])
    rows.sort(key=lambda r: (not r[3],))
    return [(a, b, c, d) for a, b, c, d in rows]


def dependency_hint() -> str:
    """The install instruction, generated from the machine rather than from memory."""
    rows = dependency_rows()
    local = rows[0] if rows and rows[0][3] else None
    lines = [
        "",
        "GlassPad's overlay needs libgtk4-layer-shell. No bundle can carry it: the copy",
        "has to match this machine's libwayland AND GTK 4, and when it does not you get",
        "a blank overlay rather than an error. So the app finds it, and if it is missing",
        "you need the package.",
        "",
    ]
    if local:
        lines.append(f"This looks like {os_release_label()}, so:")
        lines.append("")
        lines.append(f"    sudo {'pacman -S' if 'arch' in os_release_ids() else 'dnf install' if 'fedora' in os_release_ids() else 'apt install'} {local[1]}")
        lines.append("")
        lines.append(f"    ({local[2]})")
        lines.append("")
        lines.append("Elsewhere:")
        lines.append("")
        for label, pkg, verified, mine in rows:
            if mine:
                continue
            lines.append(f"    {label:<28} {pkg:<24} ({verified})")
    else:
        lines.append("Install the package your distribution uses for it:")
        lines.append("")
        for label, pkg, verified, _ in rows:
            lines.append(f"    {label:<28} {pkg:<24} ({verified})")
    lines += ["",
              "Installed somewhere unusual? Say so and --status will use it:",
              f"    GLASSPAD_LAYER_SHELL=/path/to/libgtk4-layer-shell.so.0 {cli_hint()} --status",
              ""]
    return "\n".join(lines)


def app_image_path() -> str | None:
    """The AppImage file being run, or None. Set by the runtime, not by us."""
    p = (os.environ.get("APPIMAGE") or "").strip()
    return p if p and os.path.exists(p) else None


def checkout_root() -> str | None:
    """The source tree, when running out of one."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    return root if (os.path.isdir(os.path.join(root, "glasspad"))
                    and (os.path.exists(os.path.join(root, "README.md"))
                         or os.path.exists(os.path.join(root, "LICENSE")))) else None


def cli_target() -> tuple[str, str] | None:
    """("appimage"|"checkout", path) for the install that is answering right now."""
    img = app_image_path()
    if img:
        return ("appimage", img)
    root = checkout_root()
    return ("checkout", root) if root else None


def _is_our_command(path: str, target: tuple[str, str]) -> bool:
    """Is this file a `glasspad` command belonging to THIS install?

    Two shapes answer yes: a symlink resolving to the AppImage being run, or a wrapper
    script carrying our marker line for the tree being run from. Everything else -- a
    distribution package, another build, a stale link left behind -- is not ours to
    suggest (cli_hint) and not ours to put in Exec= (desktop_entry.launch_target).

    A DANGLING symlink is the case that used to be misread. An AppImage manager (Shelly
    and friends) MOVES the file it installs, so a link we wrote points at a path that
    stops existing -- and neither realpath comparison matches, nor can the file be read.
    It came out as "refused: something else owns it", which is both wrong and
    unfollowable, because the thing that owns it is us. cli_link_state() handles that
    shape explicitly.
    """
    try:
        if os.path.realpath(path) == os.path.realpath(target[1]):
            return True
    except OSError:
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(400)
    except OSError:
        return False
    return head.startswith("#!") and LINK_MARKER + target[1] in head


def cli_hint() -> str:
    """A command the reader can paste on THIS machine, right now.

    Order matters, and the middle case is the dangerous one:

      * `glasspad` when something on PATH resolves to *this* install;
      * otherwise the absolute path of the running AppImage, quoted -- it usually lives
        in a Downloads folder. An older `glasspad` elsewhere on PATH is deliberately NOT
        preferred: telling someone to run a different copy of the app than the one that
        just warned them is how you fix the wrong install. It is the same shadowing that
        AppRun exports PYTHONSAFEPATH to prevent; here the shadow has a name and a
        password prompt attached to it.
      * `python3 -m glasspad` for a checkout, which is what works from inside the tree.
    """
    tgt = cli_target()
    on_path = shutil.which(CLI_NAME)
    if on_path and tgt and _is_our_command(on_path, tgt):
        return CLI_NAME
    if tgt and tgt[0] == "appimage":
        return f'"{tgt[1]}"' if " " in tgt[1] else tgt[1]
    if tgt and tgt[0] == "checkout":
        return "python3 -m glasspad"
    return CLI_NAME


def cli_bin_dir() -> str:
    """Where the `glasspad` command should live. Override for tests or odd setups."""
    over = (os.environ.get(BIN_DIR_ENV) or "").strip()
    if over:
        return os.path.abspath(over)
    return os.path.join(os.path.expanduser("~"), ".local", "bin")


def cli_link_state() -> tuple[str, str]:
    """("ok"|"dangling"|"foreign"|"absent", detail) for the `glasspad` command.

    Existence is not the question anybody asks. The question is whether the thing the
    messages tell them to type will run -- and a link left dangling by a manager that
    moved the AppImage LOOKS installed (`ls` shows it, PATH finds it) while every
    invocation answers "No such file or directory". So `--status` reports this rather
    than "command installed: yes".
    """
    dest = os.path.join(cli_bin_dir(), CLI_NAME)
    if not os.path.lexists(dest):
        return "absent", dest
    if os.path.islink(dest):
        target = os.readlink(dest)
        if not os.path.exists(dest):
            return "dangling", f"{dest} -> {target}"
        return ("ok", dest) if os.access(dest, os.X_OK) else ("foreign", f"{dest} (not executable)")
    if os.access(dest, os.X_OK):
        return "ok", dest
    return "foreign", f"{dest} (not executable)"


def _ours_by_name(path: str) -> bool:
    """Does this link/wrapper look like it was written for a GlassPad of ours?

    Deliberately name-based, and only used for a link we cannot resolve: there is no
    marker to read inside a dangling symlink, and the alternative -- refusing to touch
    a broken link that says `glasspad` -- leaves the user with a command that cannot
    run and a program that won't fix it.
    """
    try:
        base = os.path.basename(os.readlink(path) if os.path.islink(path) else path)
    except OSError:
        return False
    return base.lower().startswith(CLI_NAME)


def install_cli_link(dry: bool = False, force: bool = False) -> int:
    """Make `glasspad` a real command for this user, pointing at THIS install.

    Every message the app prints is imperative -- run this, confirm with that. This is
    what makes those sentences true for somebody holding a single downloaded file.

    The refusals are the part worth reading:

      * A directory that is not on PATH is not a place to install a command. Writing it
        anyway produces an install that appears to succeed and then cannot be run, which
        is the failure shape this whole pass exists to remove; so we say no and print the
        exact line that would fix PATH.
      * If something else already owns the name, it is not ours to replace -- a
        distribution package, or a different build of GlassPad. --force says otherwise.
    """
    tgt = cli_target()
    if not tgt:
        print("  cannot work out what to point a `glasspad` command at;")
        print("  neither an AppImage nor a source tree is answering right now.")
        return 2

    kind, target = tgt
    bin_dir = cli_bin_dir()
    dest = os.path.join(bin_dir, CLI_NAME)
    how = f"symlink {dest} -> {target}" if kind == "appimage" else f"wrapper script {dest}"

    if dry:
        print(f"  would write: {how}")
        return 0

    if os.path.islink(dest) or os.path.exists(dest):
        if _is_our_command(dest, tgt):
            print(f"  `glasspad` already points here: {dest}")
            return 0
        broken = (os.path.islink(dest) and not os.path.exists(dest)
                  and _ours_by_name(dest))
        if broken:
            # Ours, broken by a move -- not a stranger to be polite about. An AppImage
            # manager relocates the file it installs, the link we wrote then points at
            # nothing, and every invocation answers "No such file or directory" while
            # `ls` still lists the command as installed.
            old_target = os.readlink(dest)
            print(f"  the existing `glasspad` link is broken: {dest} -> {old_target}")
            print("  that file no longer exists (a manager moved it). Re-pointing it.")
        elif not force:
            print(f"  refused: something else owns {dest}")
            try:
                real = os.path.realpath(dest)
                print(f"           it resolves to {real}")
            except OSError:
                pass
            print("  Not replacing it. If that is the file you want gone, remove it and")
            print("  re-run, or pass --force.")
            return 1
        else:
            # Asked for explicitly, so do it visibly: name the file and its old target.
            # A silently overwritten command is how a distribution package disappears
            # off PATH with nothing left to point at.
            print(f"  --force: replacing {dest}")
            try:
                print(f"           (was: {os.path.realpath(dest)})")
            except OSError:
                pass
        try:
            os.unlink(dest)
        except OSError as exc:
            print(f"  could not remove it: {exc}")
            return 1

    if bin_dir not in [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]:
        print(f"  refused: {bin_dir} is not on PATH, so a command written there")
        print("  could not be run -- an install that reports success and then says")
        print("  'command not found' is worse than not installing.")
        print('  Add it to your shell profile, then re-run:')
        print(f'      export PATH="{bin_dir}:$PATH"')
        return 1

    try:
        os.makedirs(bin_dir, mode=0o755, exist_ok=True)
        if kind == "appimage":
            os.symlink(target, dest)
        else:
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n"
                         + LINK_MARKER + target + "\n"
                         + "# Written by the GlassPad installer (--install-desktop). Delete to undo.\n"
                         + f"export PYTHONPATH={shlex.quote(target)}\n"
                         + "export PYTHONSAFEPATH=1\n"       # see AppRun: the cwd must not shadow
                         + 'exec python3 -m glasspad "$@"\n')
            os.chmod(dest, 0o755)
    except OSError as exc:
        print(f"  could not write {dest}: {exc}")
        return 1

    print(f"  command installed: {dest}")
    # Re-resolved rather than hard-coded: if the link we just wrote is not reachable
    # (PATH not refreshed in this shell, say) the hint falls back to the real path
    # instead of promising a command that answers "not found".
    print(f"  (you can now run:  {cli_hint()} --status)")
    return 0


def uninstall_cli_link(dry: bool = False) -> int:
    """Remove the `glasspad` command, but only if it is ours."""
    tgt = cli_target()
    bin_dir = cli_bin_dir()
    dest = os.path.join(bin_dir, CLI_NAME)
    if not os.path.lexists(dest):
        print(f"  nothing to remove at {dest}")
        return 0
    ours = tgt and _is_our_command(dest, tgt)
    if not ours:
        print(f"  left alone: {dest} was not written by this install")
        return 0
    if dry:
        print(f"  would remove: {dest}")
        return 0
    try:
        os.unlink(dest)
    except OSError as exc:
        print(f"  could not remove {dest}: {exc}")
        return 1
    print(f"  removed {dest}")
    return 0
