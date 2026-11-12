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

import ctypes.util
import os

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
    """Can we create the virtual pad? A package cannot fix this, only explain it."""
    if not os.path.exists(path):
        return False, f"{path} does not exist (kernel module uinput?)"
    if not os.access(path, os.W_OK):
        return False, (f"{path} exists but is not writable -- add yourself to the group "
                       "that owns it, or install an udev rule")
    return True, path


def describe() -> str:
    """One line for --status and for the startup banner."""
    p, why = layer_shell_path()
    # install.sh prints this line among its own, whose labels are padded to 12 before the
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
        .run        app/glasspad/bundle.py               -> app/
        AppImage    usr/share/glasspad/glasspad/bundle.py -> usr/share/glasspad,
                    while the documents are staged under usr/share/doc/glasspad

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
#
# Same three layouts as doc_roots(), because they are the same two files moving
# to the same three places:
#
#     checkout    <repo>/glasspad/bundle.py      -> <repo>/install.sh
#     .run        app/glasspad/bundle.py         -> app/install.sh
#     AppImage    usr/share/glasspad/glasspad/   -> usr/share/glasspad/install.sh

INSTALLER_ROOT_ENV = "GLASSPAD_INSTALLER_DIR"
INSTALLER_NAME = "install.sh"
RULE_REL = os.path.join("packaging", "glasspad-udev.rules")


def installer_roots() -> list[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    out: list[str] = []
    env = os.environ.get(INSTALLER_ROOT_ENV, "").strip()
    if env:
        out.append(env)
    out.extend((root, os.getcwd()))
    seen, uniq = set(), []
    for c in out:
        c = os.path.normpath(c)
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def find_installer() -> str | None:
    """Path to install.sh, or None when this bundle did not ship one."""
    for root in installer_roots():
        cand = os.path.join(root, INSTALLER_NAME)
        if os.path.isfile(cand) and os.access(cand, os.R_OK):
            return cand
    return None


def installer_rule_src(installer: str | None) -> str | None:
    """The rule file that installer copies, or None when it is not alongside."""
    if not installer:
        return None
    cand = os.path.join(os.path.dirname(os.path.abspath(installer)), RULE_REL)
    return cand if os.path.isfile(cand) else None
