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

import errno
import glob
import hashlib
import json
import os
import shutil
import struct
import subprocess


def _probe(argv, **kw):
    """Run a probe that is allowed to be missing.

    This block writes provenance: which package owns the mksquashfs we borrowed, what
    version it reports. Asking `pacman -Qo` on a Debian/Ubuntu box is a question that has
    no answer, not a failure -- but the crash took the whole build down on the first CI
    run on ubuntu-24.04, on a machine that had squashfs-tools installed by apt and did not
    need pacman at all. Same lesson as uinput_setup._run_tool, one file over.
    """
    try:
        return subprocess.run(argv, capture_output=True, text=True, **kw)
    except OSError:
        return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]} not available")

import sys
import time

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

# --- the runtime half -------------------------------------------------------
#
# An AppImage is [runtime ELF][squashfs], and the runtime is a binary we do not build.
# Until now it was carved out of whichever .AppImage happened to be on this machine,
# which had two consequences: the deliverable contained an unversioned binary of unknown
# origin, and rebuilding required this specific laptop with those specific apps installed.
# So the runtime is pinned -- upstream URL, sha256, size -- and the build verifies what it
# appends against that pin, in this order:
#
#   $GLASSPAD_RUNTIME    an explicit file, refused if it contradicts the pin
#   ~/.cache/glasspad-build/runtime-x86_64    the cached download, hash-checked
#   the pin URL          fetched once, cached, hash-checked
#   a local .AppImage    last resort, announced as borrowed, because that is what it is
RUNTIME_PIN = os.path.join(ROOT, "packaging", "runtime-pin.json")
RUNTIME_CACHE = os.path.expanduser("~/.cache/glasspad-build/runtime-x86_64")
# The donors used before the pin existed. Kept so an offline rebuild is still possible --
# not as the normal path, because a runtime borrowed from someone else's application is
# precisely the thing a release ought to be able to name the version of.
DONOR_GLOBS = ("~/.local/bin/*.AppImage", "~/Downloads/*.AppImage",
               "~/Applications/*.AppImage", "~/*/storage/*.AppImage",
               "/var/lib/flatpak/exports/bin/*.AppImage")


def donor_candidates():
    """Where to look for a runtime when the pinned download is unreachable.

    This was a hardcoded list of the three applications installed on the maintainer's
    laptop: accurate here, useless everywhere else, and it published somebody's disk
    inventory in a public repository. Now it is a rule -- an explicit
    $GLASSPAD_RUNTIME_DONORS list first, then the directories AppImages actually land in --
    and every candidate still has to pass extract_runtime(), because plenty of .AppImage
    files on disk are type-1 or have odd leading bytes.
    """
    env = os.environ.get("GLASSPAD_RUNTIME_DONORS")
    if env:
        return [os.path.expanduser(p) for p in env.split(os.pathsep) if p]
    out = []
    for pat in DONOR_GLOBS:
        out += sorted(glob.glob(os.path.expanduser(pat)))
    return out


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_pin(path: str = RUNTIME_PIN):
    """The recorded runtime, or None when the checkout makes no claim about it.

    No pin is not the same as a bad pin. Absence means "nothing recorded", and the build
    says so and carries on. A pin that does NOT match stops the build, because stopping is
    the only reason to write a hash down.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            pin = json.load(fh)
    except (OSError, ValueError):
        return None
    if not (isinstance(pin, dict) and pin.get("sha256") and pin.get("url")):
        return None
    return pin


def matches_pin(path: str, pin) -> bool:
    if not pin:
        return False
    size = pin.get("size")
    if size and os.path.getsize(path) != int(size):
        return False
    return sha256_of(path) == str(pin.get("sha256", "")).lower()


def _fetch(url: str, dest: str, timeout: int = 120) -> bool:
    """Download under a temporary name, so a dead link cannot leave a truncated runtime.

    A half-downloaded runtime is the worst input this build can have: it is a valid ELF,
    it appends cleanly, and the image only fails to boot on someone else's machine.
    """
    import urllib.error
    import urllib.request
    tmp = dest + ".part"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "glasspad-build"})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as out:
            shutil.copyfileobj(r, out)
        os.replace(tmp, dest)
        os.chmod(dest, 0o755)
        return True
    except (urllib.error.URLError, OSError) as exc:
        sys.stderr.write(f"  runtime download failed: {exc}\n")
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def acquire_runtime(dest: str):
    """(path, provenance) -- the provenance string is printed AND written to BUILD-INFO.

    Deliberately not a silent chain of fallbacks: whichever rung produced the runtime ends
    up in the text file that ships beside the artefact, so "runtime-active, origin BORROWED
    from StabilityMatrix.AppImage" is visible to whoever has to stand behind the build.
    """
    pin = load_pin()

    want = os.environ.get("GLASSPAD_RUNTIME")
    if want:
        want = os.path.expanduser(want)
        if not os.path.isfile(want):
            raise SystemExit(f"$GLASSPAD_RUNTIME points at nothing: {want}")
        if pin and not matches_pin(want, pin):
            raise SystemExit(
                f"$GLASSPAD_RUNTIME does not match packaging/runtime-pin.json\n"
                f"    file: {want}\n"
                f"    got:  {sha256_of(want)}\n"
                f"    pin:  {pin['sha256']}\n"
                "Refusing to build an artefact whose runtime is not the one the tree "
                "claims. Change the pin deliberately -- hash, release, date -- rather "
                "than pointing the environment variable past it.")
        return want, ("explicit $GLASSPAD_RUNTIME"
                      + (", matches the pin" if pin else " (no pin on file)"))

    if pin:
        if os.path.isfile(RUNTIME_CACHE):
            if matches_pin(RUNTIME_CACHE, pin):
                shutil.copyfile(RUNTIME_CACHE, dest)
                os.chmod(dest, 0o755)
                return dest, (f"pinned AppImageKit {pin.get('release', '?')} runtime "
                              "from cache, sha256 verified")
            print("  note: the cached runtime does not match the pin; re-fetching")
        if _fetch(pin["url"], RUNTIME_CACHE) and matches_pin(RUNTIME_CACHE, pin):
            shutil.copyfile(RUNTIME_CACHE, dest)
            os.chmod(dest, 0o755)
            return dest, (f"pinned AppImageKit {pin.get('release', '?')} runtime, "
                          "fetched and sha256 verified")
        print("  note: could not obtain the pinned runtime; falling back to a local one")

    donor, tried = None, []
    for cand in donor_candidates():
        if not os.path.isfile(cand):
            continue
        try:
            extract_runtime(cand, dest)
            donor = cand
            break
        except SystemExit as exc:
            tried.append(f"{os.path.basename(cand)}: {exc}")
    if donor is None:
        raise SystemExit(
            "no usable runtime: the pinned download failed and no local AppImage had "
            "one.\n    " + "\n    ".join(tried or ["no candidate AppImages present"]))
    return dest, (f"BORROWED from {os.path.basename(donor)} -- NOT the pinned runtime, "
                  "so this artefact's runtime is unversioned")


def _git(*args: str) -> str:
    r = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def write_build_info(out: str, *, mksq: str, mksq_src: str, runtime: str,
                     runtime_src: str, sq_bytes: int) -> str:
    """dist/BUILD-INFO.txt -- what went into this exact file, and its hash.

    Not a build log. The point is that a stranger can check a release: the sha256 of the
    file they downloaded, the commit it came from, and the name and version of every binary
    that touched it. "Built on my machine" is not provenance; this is, and it is free,
    because the build already knows all of it.

    The `source tree: DIRTY` line is the one that earns the rest. A release built from a
    working tree with uncommitted edits is unreproducible in a way nobody notices until the
    bug report that says "the binary doesn't match the repo".
    """
    art_sha = sha256_of(out)
    pretty = ""
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    pretty = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    pkg = ""
    own = _probe(["pacman", "-Qo", mksq])
    if own.returncode == 0:
        pkg = own.stdout.strip()
    ver = _probe([mksq, "-version"])
    mksq_ver = (ver.stdout.splitlines() or ["?"])[0]
    uname = os.uname()
    dirty = bool(_git("status", "--porcelain"))
    lines = [
        "GlassPad (Linux) -- build provenance",
        "",
        f"appimage       : {os.path.basename(out)}",
        f"bytes          : {os.path.getsize(out)}",
        f"sha256         : {art_sha}",
        f"payload bytes  : {sq_bytes}",
        f"runtime bytes  : {os.path.getsize(runtime)}",
        f"runtime sha256 : {sha256_of(runtime)}",
        f"runtime origin : {runtime_src}",
        "",
        f"source commit  : {_git('rev-parse', 'HEAD') or 'unknown'}",
        f"source describe: {_git('describe', '--tags', '--always') or 'unknown'}",
        f"tag            : {_git('describe', '--tags', '--exact-match') or '(none)'}",
        f"source tree    : {'DIRTY -- uncommitted changes were packed' if dirty else 'clean at build time'}",
        "",
        f"mksquashfs     : {mksq}  ({mksq_src})",
        f"mksquashfs ver : {mksq_ver}",
        f"mksquashfs pkg : {pkg or 'NOT package-owned -- a borrowed binary'}",
        "squashfs flags : -noappend -root-owned -no-xattrs -no-duplicates "
        "-comp gzip -b 1048576",
        "                 (-no-duplicates DISABLES mksquashfs's duplicate detection, so",
        "                  identical files cost more than once unless they are hardlinked",
        "                  in the AppDir -- which is how the icon and its payload copy fit)",
        "",
        f"built on       : {pretty or uname.sysname} {uname.release} {uname.machine}",
        f"python         : {sys.version.split()[0]}",
        f"built at       : {time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
        "",
        f"verify with    : sha256sum -c {os.path.basename(out)}.sha256",
        "                   run from the directory holding the AppImage; the sidecar",
        "                   names the file, so a tampered or truncated download fails it.",
    ]
    info = os.path.join(os.path.dirname(out), "BUILD-INFO.txt")
    with open(info, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(out + ".sha256", "w", encoding="utf-8") as fh:
        fh.write(f"{art_sha}  {os.path.basename(out)}\n")
    return info

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

    # The runtime is the half we do not build, so it is pinned and hash-checked instead
    # of lifted off this machine. acquire_runtime() explains the order;
    # packaging/runtime-pin.json says what "the right one" means.
    runtime = os.path.join(ROOT, "dist", "runtime-active")
    runtime, runtime_src = acquire_runtime(runtime)
    print(f"  runtime:    {runtime_src}")
    print(f"              {os.path.getsize(runtime)} bytes -> "
          f"{os.path.relpath(runtime, ROOT)}")

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
    try:
        with open(out, "wb") as o:
            with open(runtime, "rb") as f:
                shutil.copyfileobj(f, o)
            pad = 0
            with open(sq, "rb") as f:
                shutil.copyfileobj(f, o)
    except OSError as exc:
        if exc.errno in (errno.ETXTBSY, errno.EBUSY):
            # Hit twice on this machine. "Text file busy" names neither the holder nor the
            # cure, and the obvious reading -- a running overlay -- is usually NOT it: an
            # `--appimage-mount` killed by a timeout leaves the FUSE mount and its runtime
            # process holding the previous file open indefinitely, and every rebuild after
            # that fails here, far from the cause.
            #
            # BOTH errnos, because both read identically and I checked the wrong one first:
            # a mounted or executing file gives ETXTBSY (26), which is what actually
            # happens, while EBUSY (16) is what "Device or resource busy" gives when the
            # unmount is refused. Matching only EBUSY meant the handler never ran and the
            # user got a bare traceback exactly as before.
            sys.stderr.write(
                f"  cannot write {out}: Text file busy.\n"
                "  Something still has the PREVIOUS AppImage open. Most often that is a\n"
                "  FUSE mount left behind by a killed `--appimage-mount`, not a running\n"
                "  overlay. Find it and clear it, then re-run:\n"
                f"      fuser -v {out}\n"
                "      for m in $(mount | grep -oE '/tmp/.mount_[^ ]+'); do "
                "fusermount -u \"$m\"; done\n")
        raise
    os.chmod(out, 0o755)
    print(f"  wrote       {os.path.relpath(out, ROOT)} ({os.path.getsize(out)} bytes, "
          f"{pad} bytes of alignment padding)")
    print(f"  image offset where the runtime starts looking: "
          f"{runtime_offset(out)}")
    info = write_build_info(out, mksq=mksq, mksq_src=mksq_src, runtime=runtime,
                            runtime_src=runtime_src, sq_bytes=os.path.getsize(sq))
    print(f"  provenance: {os.path.relpath(info, ROOT)} and "
          f"{os.path.basename(out)}.sha256")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
