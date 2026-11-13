"""Reading what is inside a built AppImage, without assuming unsquashfs can.

`unsquashfs` cannot open a type-2 AppImage at all: the squashfs begins at an offset behind
the runtime ELF, so `unsquashfs -l image.AppImage` answers "FATAL ERROR: Can't find a valid
SQUASHFS superblock". Two tests used it anyway and were green for months -- not because the
assertions held, but because squashfs-tools was not installed on this machine and the tests
skipped. The day it was installed (as part of build-provenance work, so mksquashfs would be
package-owned rather than borrowed from a flatpak), both went red on a build that had not
changed at all.

That is the lesson worth keeping: a skip that hides a broken assertion is worse than no
assertion, because it makes the coverage look real. `check_all.py` already fails a suite
that prints no tally; this is the same failure one level down, where one assertion inside a
suite vacuums itself away on a missing tool.

`--appimage-extract` is the honest route: the runtime unpacks its own payload, needs no
FUSE, and yields the exact tree a user gets.
"""
import os
import subprocess


def paths_in(image: str, dest: str):
    """(ok, relative_paths, note) for the payload of `image`, extracted into `dest`.

    `ok` is False when nothing is built or extraction failed, and `note` carries the reason
    -- callers print it as a skip rather than counting it as a pass.
    """
    if not os.path.isfile(image):
        return False, set(), "nothing built yet"
    try:
        p = subprocess.run([image, "--appimage-extract"], cwd=dest,
                           capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, set(), f"could not run the image: {exc}"
    root = os.path.join(dest, "squashfs-root")
    if not os.path.isdir(root):
        return False, set(), ((p.stderr or p.stdout).strip()[:160]
                              or "extraction produced no squashfs-root")
    found = set()
    for dirpath, _sub, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        for name in files:
            found.add(name if rel == "." else os.path.normpath(
                os.path.join(rel, name)))
    return True, found, ""
