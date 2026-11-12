"""Shared helper for the artefact suites: do the licence documents resolve?

The About box renders LICENSE and licenses/THIRD-PARTY-NOTICES.txt. Each artefact puts
them somewhere different from the code --

    .run        app/glasspad/…            app/LICENSE, app/licenses/…
    AppImage    usr/share/glasspad/glasspad/…   usr/share/doc/glasspad/…

-- so the resolver in glasspad/bundle.py has to work against layouts that only exist
after packing. Importing the copy that shipped, from where it shipped, is the only way
to test that: a unit test against the checkout proves the checkout works.
"""
from __future__ import annotations

import importlib.util
import os


def load_packed_bundle(root: str):
    """Import the glasspad/bundle.py that lives inside an unpacked artefact.

    A private module name, because the repo's own glasspad is already imported in most
    runs and a second copy under the same name would silently shadow or be shadowed.
    """
    for cand in (os.path.join(root, "app", "glasspad", "bundle.py"),
                 os.path.join(root, "usr", "share", "glasspad", "glasspad", "bundle.py")):
        if os.path.isfile(cand):
            spec = importlib.util.spec_from_file_location("glasspad_packed_bundle", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod, cand
    raise AssertionError(f"no packed bundle.py under {root}")


# The shipped notices must speak about this build.
MUST_MENTION = ("gtk4-layer-shell", "GTK 4", "MIT")

# usbip-win2, DsHidMini and HIDMaestro belong to the Windows original's transport. They
# may appear in the file -- they must, so a reader who knows that project can see they
# were considered and excluded -- but only below the heading that says so. Above it, a
# reader takes them for dependencies of this build, which is the same category of error
# as a colour constant that disagrees with the renderer: plausible, documented, wrong.
WINDOWS_ONLY = ("usbip-win2", "DsHidMini", "HIDMaestro")
EXCLUSION_HEADING = "Not part of this build"


def licence_checks(mod, label, check):
    """Assert the two documents resolve from inside a packed tree, and say what they say."""
    for name in ("LICENSE", "THIRD-PARTY-NOTICES.txt"):
        path = mod.find_doc(name)
        check(f"{label}: {name} resolves from inside the artefact",
              path is not None and os.path.isfile(path or ""))
    body, problems = mod.read_doc("THIRD-PARTY-NOTICES.txt")
    check(f"{label}: notices read without a problem{'' if not problems else f' ({problems})'}",
          not problems and bool(body.strip()))
    missing = [w for w in MUST_MENTION if w not in body]
    check(f"{label}: notices describe what this build uses{'' if not missing else missing}",
          not missing)
    head, sep, _tail = body.partition(EXCLUSION_HEADING)
    check(f"{label}: the exclusions are stated, not merely absent", bool(sep))
    leaked = [w for w in WINDOWS_ONLY if w in head]
    check(f"{label}: nothing Windows-only above the exclusion heading"
          f"{'' if not leaked else leaked}", not leaked)
    lic, lic_prob = mod.read_doc("LICENSE")
    check(f"{label}: the MIT notice is present and names the upstream copyright",
          "MIT" in lic and "Dansunmi" in lic and not lic_prob)
