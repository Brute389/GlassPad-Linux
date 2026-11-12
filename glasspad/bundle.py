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
