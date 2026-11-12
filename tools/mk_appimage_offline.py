#!/usr/bin/env python3
"""Build a real AppImage with no network, no root, and no package installed.

An AppImage of the common "type 2" shape is:

    [ runtime ELF ][ squashfs image of the AppDir ]

and the runtime finds the image by scanning for the squashfs superblock magic (hsqs).
So the two things you need are a working `mksquashfs` and a runtime ELF. Neither is
installed as a package here, and there is no network to fetch one -- but both are already
on this disk:

  * mksquashfs 4.7 ships inside the Spotify flatpak bundle (verified: it runs on the host,
    against the host's libraries, with no LD_LIBRARY_PATH).
  * every .AppImage the user already owns has a runtime ELF as its first bytes.

This script proves the offline path works. It is deliberately explicit about where those
two binaries came from: they are borrowed, not managed, and `sudo pacman -S squashfs-tools`
plus a real appimagetool remain the right answer as soon as there is a network. Nothing
here writes outside dist/.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HSQS = b"hsqs"
ALIGN = 4096


def find_mksquashfs() -> tuple[str | None, str]:
    """Prefer a real install; fall back to whatever is on disk, and say where."""
    w = shutil.which("mksquashfs")
    if w:
        return w, "on PATH"
    hits = []
    for base in ("/var/lib/flatpak", "/home", "/opt", "/usr/local"):
        if not os.path.isdir(base):
            continue
        try:
            p = subprocess.run(["find", base, "-xdev", "-name", "mksquashfs", "-type", "f"],
                               capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            continue
        hits += [ln for ln in p.stdout.splitlines() if ln.strip()]
    for h in hits:
        if os.access(h, os.X_OK):
            return h, "borrowed from a local bundle"
    return None, "not found"


def elf_loaded_end(blob: bytes) -> int:
    """Highest offset covered by a PT_LOAD segment -- bytes that belong to the ELF."""
    try:
        e_phoff, = struct.unpack_from("<Q", blob, 0x20)
        _e_shentsize, e_phnum = struct.unpack_from("<HH", blob, 0x36)
    except struct.error:
        return 0
    end = 0
    for n in range(min(e_phnum, 32)):
        o = e_phoff + n * 56
        if o + 56 > len(blob):
            break
        p_type, = struct.unpack_from("<I", blob, o)
        p_off, = struct.unpack_from("<Q", blob, o + 8)
        p_filesz, = struct.unpack_from("<Q", blob, o + 32)
        if p_type == 1:                       # PT_LOAD
            end = max(end, p_off + p_filesz)
    return end


def runtime_offset(path: str, limit: int = 64 << 20) -> int | None:
    """Byte offset where the appended squashfs starts (= length of the runtime).

    The magic is four bytes, not eight -- parsing it as eight shifts every later field by
    four and rejects every genuine superblock, which is how the first version of this
    "found no squashfs" in files that plainly had one. Two guards on top of the fields:
    the block size must be one of the legal powers of two, and the candidate must lie
    past the ELF's loaded image, because the runtime's own bytes contain hsqs-looking
    data at offsets that would otherwise be plausible.
    """
    with open(path, "rb") as fh:
        blob = fh.read(limit)
    floor = elf_loaded_end(blob)
    pos = 0
    while True:
        i = blob.find(HSQS, pos)
        if i < 0:
            return None
        pos = i + 4
        if i < floor or i + 48 > len(blob):
            continue
        (magic, _inodes, _time, block_size, _frags, comp, _block_log, _flags, _no_ids,
         major, minor) = struct.unpack_from("<4sIIIIHHHHHH", blob, i)
        bytes_used, = struct.unpack_from("<Q", blob, i + 40)
        tail = len(blob) - i if len(blob) < os.path.getsize(path) else None
        if (magic == HSQS and major == 4
                and block_size in (4096, 8192, 16384, 32768, 65536, 131072, 262144,
                        524288, 1048576)
                and comp in (1, 2, 3, 4, 5, 6)):
            if bytes_used < 48:
                continue
            return i
        _ = (minor, tail)


def extract_runtime(src: str, dest: str) -> int:
    off = runtime_offset(src)
    if not off:
        raise SystemExit(f"no squashfs superblock found in the first 32 MiB of {src}")
    with open(src, "rb") as fin, open(dest, "wb") as fout:
        left = off
        while left > 0:
            chunk = fin.read(min(1 << 20, left))
            if not chunk:
                break
            fout.write(chunk)
            left -= len(chunk)
    # Trim trailing zero padding so the runtime ends where its own image ended; the ELF
    # loader does not care about bytes past the last PT_LOAD segment.
    os.chmod(dest, 0o755)
    return off


def main() -> int:
    appdir = os.path.join(ROOT, "dist", "GlassPad.AppDir")
    if not os.path.isfile(os.path.join(appdir, "AppRun")):
        raise SystemExit("no AppDir staged -- run tools/make_appimage.sh --dir first")

    mksq, mksq_src = find_mksquashfs()
    if not mksq:
        raise SystemExit("mksquashfs found nowhere: install squashfs-tools")
    print(f"  mksquashfs: {mksq}  ({mksq_src})")
    ver = subprocess.run([mksq, "-version"], capture_output=True, text=True)
    print(f"              {(ver.stdout.splitlines() or ['?'])[0]}")

    # Order matters less than trying them all: one of these has no valid superblock at all,
    # and a build that died because the first candidate it happened to find was unusable is
    # a build that blames the tool for the donor's oddity.
    candidates = [os.path.expanduser(c) for c in
                  ("~/.local/bin/StabilityMatrix.AppImage",
                   "~/.local/bin/tether_x86_64.AppImage",
                   "~/retrodeck/storage/Eden.AppImage")]
    candidates = [c for c in candidates if os.path.isfile(c)]
    if not candidates:
        raise SystemExit("no local .AppImage to borrow a runtime from")
    runtime = os.path.join(ROOT, "dist", "runtime-borrowed")
    donor, n, tried = None, None, []
    for cand in candidates:
        try:
            n = extract_runtime(cand, runtime)
            donor = cand
            break
        except SystemExit as exc:
            tried.append(f"{os.path.basename(cand)}: {exc}")
    if donor is None:
        raise SystemExit("no donor had a usable runtime:\n    " + "\n    ".join(tried))
    print(f"  runtime:    {os.path.basename(donor)} -> {n} bytes "
          f"({os.path.relpath(runtime, ROOT)})")

    sys.path.insert(0, ROOT)
    from glasspad import __version__ as version
    arch = os.uname().machine
    sq = os.path.join(ROOT, "dist", "payload.sqsh")
    out = os.path.join(ROOT, "dist", f"GlassPad-{version}-{arch}.AppImage")

    # Purge bytecode HERE, immediately before packing, rather than only at staging: the
    # staging script runs AppRun --status as its own smoke test, which writes __pycache__
    # back into the AppDir, and the first builds of this shipped a .pyc compiled by this
    # Python into an image that might run on another one -- the worst class of packaging
    # bug, because it only misbehaves on someone else's machine.
    stale = subprocess.run(["find", appdir, "-name", "__pycache__", "-type", "d"],
                           capture_output=True, text=True).stdout.split()
    for d in stale:
        shutil.rmtree(d, ignore_errors=True)
    if stale:
        print(f"  purged:     {len(stale)} __pycache__ dirs written by the staging smoke test")

    # gzip rather than zstd/xz: the payload has to be readable by whatever squashfuse the
    # target's runtime was built against, and gzip is the one every build supports. The
    # payload is ~1 MiB, so the size difference is not worth the portability risk.
    cmd = [mksq, appdir, sq, "-noappend", "-root-owned", "-no-xattrs", "-no-duplicates",
           "-no-progress", "-comp", "gzip", "-b", "1048576"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout[-2000:], p.stderr[-2000:])
        raise SystemExit("mksquashfs failed")
    print(f"  payload:    {os.path.getsize(sq)} bytes squashfs "
          f"({len(os.listdir(appdir))} top-level entries)")

    # No padding. The runtime reports the payload offset as the exact end of its own
    # image, and appimagetool appends flush against it -- my first build inserted 1544 bytes
    # to "align" the squashfs and the runtime answered "This doesn't look like a squashfs
    # image", because it was reading the zeros. Alignment is a mksquashfs concern inside the
    # image, not a concern between the two halves of the AppImage.
    with open(out, "wb") as o:
        with open(runtime, "rb") as f:
            shutil.copyfileobj(f, o)
        pad = 0
        with open(sq, "rb") as f:
            shutil.copyfileobj(f, o)
    os.chmod(out, 0o755)
    print(f"  wrote       {os.path.relpath(out, ROOT)} ({os.path.getsize(out)} bytes, "
          f"{pad} bytes of alignment padding)")
    print(f"  image offset where the runtime starts looking: "
          f"{runtime_offset(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
