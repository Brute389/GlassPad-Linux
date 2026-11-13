#!/usr/bin/env bash
# Stage GlassPad as an AppImage AppDir, and pack it if this machine can.
#
#   tools/make_appimage.sh              # stage dist/GlassPad.AppDir (+ pack if able)
#   tools/make_appimage.sh --dir        # stage only; run dist/GlassPad.AppDir/AppRun
#   tools/make_appimage.sh --skip-tests # don't run the suite first
#
# WHAT AN APPIMAGE BUYS HERE, AND WHAT IT DOES NOT
#
# This app needs three things from the host that a payload cannot carry:
#
#   1. libgtk4-layer-shell  -- preloaded before libwayland (see glasspad/bundle.py).
#      Carrying our own copy is possible but pointless: the copy must match the host's
#      libwayland and the host's GTK 4, and if it does not you get a blank overlay rather
#      than an error. So AppRun uses the host's, and bundle.py finds it wherever it lives.
#   2. A compositor that implements zwlr_layer_shell_v1 (KWin, KDE, wlroots...). Nothing
#      can bundle that. On GNOME without the layer-shell extension the overlay cannot work
#      in any packaging format, AppImage included.
#   3. Write access to /dev/uinput. AppImage sandboxes nothing, so this is the same
#      permission question as running from a checkout -- but it is the one thing that will
#      fail on a machine that has never had GlassPad, so --status says it out loud.
#
# That is why the payload is the Python tree and NOT a bundled GTK/Python. The full
# linuxdeploy route (bundle GTK 4, pycairo, PyGObject, typelibs, layer-shell) would add
# hundreds of megabytes and trade a clear "install these three packages" for subtle
# ABI-mismatch failures -- and Flatpak is strictly worse here, because its sandbox filters
# zwlr_layer_shell_v1 off the session bus and denies /dev/uinput.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

do_pack=1
run_tests=1
blocked="(not recorded: built with --skip-tests)"
for a in "$@"; do
  case "$a" in
    --dir) do_pack=0 ;;
    --skip-tests) run_tests=0 ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

version=$(python3 -c 'import sys; sys.path.insert(0,"."); import glasspad; print(glasspad.__version__)')
dist="$root/dist"
appdir="$dist/GlassPad.AppDir"

cleanup_staging() {
  # The artefact is the deliverable. The AppDir, the borrowed runtime and the
  # intermediate squashfs are scratch, and they used to be left sitting next to it
  # -- 1.5 MB of "which part of dist/ is necessary?" for anyone who looked, which
  # is the exact question this folder is supposed to answer. Never called on a
  # failed pack: there the AppDir is the thing you carry to a machine that has
  # appimagetool, and the instructions above depend on it.
  if [ "${GLASSPAD_KEEP_STAGING:-0}" = 1 ]; then
    echo "    keeping the AppDir and scratch (GLASSPAD_KEEP_STAGING=1)"
    return
  fi
  rm -rf "$appdir" "$dist/payload.sqsh" "$dist/runtime-active" "$dist/runtime-borrowed"
  echo "    staging cleaned; GLASSPAD_KEEP_STAGING=1 keeps it for inspection"
}
arch=$(uname -m)

if [ "$run_tests" = 1 ]; then
  echo "==> suite"
  set +e
  # --no-artifacts: the dist/ suites compare the existing image against the current
  # source, so they are red by definition until this script rebuilds it. Including them
  # here would mean the only way to pass the gate is to have already packaged.
  python3 tools/check_all.py --no-artifacts --no-live >/tmp/mkai_tests.log 2>&1
  rc=$?
  # Ship the test record with the payload. Read from this run only, so --skip-tests
  # cannot inherit a stale log from a previous build and look better tested than it is.
  blocked=$(grep -E '^  BLOCK' /tmp/mkai_tests.log | awk '{print $2}' | paste -sd ' ' -)
  [ -n "$blocked" ] || blocked="none -- every suite ran"
  set -e
  case "$rc" in
    0) echo "    all green (details: /tmp/mkai_tests.log)" ;;
    2) echo "    everything that ran passed, but a suite never started:" >&2
       grep -E "^  BLOCK|could not run" /tmp/mkai_tests.log | sed 's/^/      /' >&2
       # Wording matters: it is the *runner* that is missing (pytest), so this suite has
       # never run here, in this build or any previous one. Saying "unrelated backend"
       # would be a lie that makes the warning feel safe.
       echo "    packaging anyway: that suite needs pytest, which is not installed and" >&2
       echo "    cannot be fetched (no network). It has never run on this machine." >&2
       echo "    Untested code ships in this payload; see usr/share/doc/glasspad/PACKAGING.txt" >&2;;
    *) echo "    the suite is RED; not packaging. /tmp/mkai_tests.log" >&2
       grep -E "^  FAIL|failing:" /tmp/mkai_tests.log | sed 's/^/      /' >&2
       exit 1 ;;
  esac
fi

echo "==> staging $appdir (GlassPad $version, $arch)"
rm -rf "$appdir"
mkdir -p "$appdir/usr/bin" "$appdir/usr/lib" \
         "$appdir/usr/share/glasspad" "$appdir/usr/share/doc/glasspad" \
         "$appdir/usr/share/icons/hicolor/scalable/apps"

# The package. --exclude keeps __pycache__ out: a stale .pyc from a different Python is
# the worst kind of packaging bug, because it only breaks on the other person's machine.
cp -r glasspad "$appdir/usr/share/glasspad/glasspad"
find "$appdir/usr/share/glasspad" -name '__pycache__' -type d -prune -exec rm -rf {} +
cp tools/glasspad-profile "$appdir/usr/bin/glasspad-profile"
cp README.md "$appdir/usr/share/doc/glasspad/README.md"
# The `[ -f ... ] && cp ... || true` shape here swallowed every failure: a build with
# no licence copied, no error, and a binary that cannot answer "under what terms?".
# These are conditions of distribution, so they abort instead.
for doc in LICENSE licenses/THIRD-PARTY-NOTICES.txt; do
    [ -f "$doc" ] || { echo "  ERROR: $doc is missing; refusing to ship without it" >&2; exit 1; }
    cp "$doc" "$appdir/usr/share/doc/glasspad/$(basename "$doc")"
done
# The udev rule, same reasoning as the licence texts above: the bundle cannot ship
# /etc/udev/rules.d, so it ships the rule and the Python that writes it
# (glasspad/uinput_setup.py, already inside the package copy), and
# `glasspad --install-uinput` runs them through pkexec. On a machine that has never
# had GlassPad this is the difference between "download one file" and "download one
# file, then go find a source tree you do not have".
for priv in packaging/glasspad-udev.rules; do
    [ -f "$priv" ] || { echo "  ERROR: $priv is missing; this bundle could not finish an install" >&2; exit 1; }
    mkdir -p "$appdir/usr/share/glasspad/$(dirname "$priv")"
    cp "$priv" "$appdir/usr/share/glasspad/$priv"
done
# The icon is the application's own artwork, not a stand-in drawn for the build. The
# tree ships glasspad/img/glasspad_icon_512.png (upstream), and Icon=glasspad in
# desktop/glasspad.desktop.in has always been a reference to exactly that.
#
# It goes to two places: the theme directory Shelly or any launcher will copy out when it
# integrates the entry, and the AppDir ROOT, where a .desktop's Icon= name is resolved
# when no theme is in play. Missing icon means the generic picture in the menu, which
# reads as "this app is not really installed" -- so a missing source fails the build
# rather than shipping something that looks provisional.
_icon_src="glasspad/img/glasspad_icon_512.png"
[ -f "$_icon_src" ] || { echo "  ERROR: $_icon_src is missing; there is no icon to ship" >&2; exit 1; }
for _d in "$appdir/usr/share/icons/hicolor/512x512/apps" "$appdir"; do
  mkdir -p "$_d"
  cp "$_icon_src" "$_d/glasspad.png"
done
# Same bytes, three paths: the package already ships img/glasspad_icon_512.png, and the
# theme and AppDir copies are identical to it. mksquashfs is invoked with
# -no-duplicates -- which DISABLES its duplicate detection -- so three copies would be
# three copies on the wire, and a 351 KB icon would add 700 KB to a 1.5 MB download.
# Hardlinks are one inode, which squashfs stores once and every extractor restores as an
# ordinary file at each path.
_icon_in_image="$appdir/usr/share/glasspad/glasspad/img/glasspad_icon_512.png"
if [ -f "$_icon_in_image" ]; then
  for _p in "$appdir/usr/share/icons/hicolor/512x512/apps/glasspad.png" "$appdir/glasspad.png"; do
    rm -f "$_p"
    ln "$_icon_in_image" "$_p" \
      || cp "$_icon_src" "$_p"       # same filesystem in practice; never ship nothing
  done
fi
echo "    icon:       $_icon_src -> hicolor/512x512/apps/glasspad.png and the AppDir root"

cat > "$appdir/GlassPad.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Version=1.0
Name=GlassPad
GenericName=Touch gamepad overlay
Comment=Turn the touchscreen into a virtual Xbox gamepad
# No %u/%f field codes: the parser accepts no file or URL arguments, and a token a
# launcher appends would be an unrecognized argument (exit 2).
Exec=glasspad
Terminal=false
Icon=glasspad
# One main category only: "Game;Utility;" is legal but validate warns the entry may show
# up twice in the menu, and the second entry is pure clutter.
Categories=Game;
Keywords=gamepad;controller;touch;xbox;overlay;
StartupNotify=false
X-KDE-StartupNotify=false
# Two overlays would double every input, so nothing may restore a second instance.
X-GNOME-SingleWindow=true
DESKTOP

# The version, in the key AppImage managers read. Installed through Shelly, our entry
# landed in its list as Version "Unknown" -- nothing breaks, but a manager cannot tell you
# whether your copy is current, which is the whole point of letting it manage the file.
# Appended rather than interpolated: the heredoc above is quoted on purpose (an unquoted
# one once ran `python3 -m glasspad` while generating AppRun), and a version is the one
# thing in this file that changes per build.
_glasspad_version=$(python3 -c '
import re
src = open("glasspad/__init__.py", encoding="utf-8").read()
# \x27 rather than a quote: this whole program is one single-quoted
# shell argument, and the previous attempt to escape its regex ended the
# string early -- the build died with a bare "SyntaxError: invalid syntax"
# from python, three layers away from the quoting mistake.
m = re.search(r"^__version__\s*=\s*[\x27\"]([^\x27\"]+)", src, re.M)
print(m.group(1) if m else "")
')
if [ -n "$_glasspad_version" ]; then
  printf 'X-AppImage-Version=%s\n' "$_glasspad_version" >> "$appdir/GlassPad.desktop"
  echo "    embedded X-AppImage-Version=$_glasspad_version"
else
  echo "    could not read __version__ from glasspad/__init__.py" >&2
  exit 1
fi

if command -v desktop-file-validate >/dev/null 2>&1; then
  echo "==> validating the desktop entry"
  desktop-file-validate "$appdir/GlassPad.desktop" \
    && echo "    valid" || { echo "    desktop-file-validate complained" >&2; exit 1; }
fi

cat > "$appdir/AppRun" <<APPRUN
#!/usr/bin/env bash
# GlassPad entry point. APPDIR is set by the AppImage runtime; when running out of a
# plain directory (AppImage's own --appimage-extract shape) derive it from \$0.
self=\$(readlink -f "\$0")
APPDIR=\${APPDIR:-\$(dirname "\$self")}
export APPDIR
export GLASSPAD_BUNDLE="\$APPDIR"
export GLASSPAD_BUNDLE_LIB="\$APPDIR/usr/lib"
export PYTHONPATH="\$APPDIR/usr/share/glasspad\${PYTHONPATH:+:\$PYTHONPATH}"
# PYTHONSAFEPATH matters more than it looks. "python -m glasspad" puts the CURRENT
# DIRECTORY ahead of PYTHONPATH, so running this AppImage from inside a source
# checkout silently executes that checkout instead of the payload -- found by
# changing a file in the tree and watching the artefact report the new text. The
# reverse case is the nasty one: a stale copy in the download folder shadowing a
# newer AppImage, with no symptom at all. 3.11+ honours this, and it removes the
# cwd entry for -m without isolating PYTHONPATH itself.
#
# Written without backticks on purpose. This block is an UNQUOTED heredoc, so
# backticks here are a command substitution: the first version of this comment ran
# the overlay while the builder was generating its own launcher, which never
# returns. Symptom: a build that hangs after "valid" with a 0-byte AppRun and a
# stray NO PAD warning in the log, and no clue in the source that anything is wrong.
export PYTHONSAFEPATH=1
export PATH="\$APPDIR/usr/bin:\$PATH"
# Only prepend our lib dir; overriding LD_LIBRARY_PATH outright is how bundles break
# the caller's OpenGL/VA drivers.
export LD_LIBRARY_PATH="\$APPDIR/usr/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
# The profile claimer has to be reachable from inside the image: a Steam launch option is
# the whole point of profiles, and a launch option can only name one program. So
#   "GlassPad.AppImage" --appimage-extract-and-run profile NAME && %command%
# works without unpacking anything by hand or pointing Steam at a checkout.
if [ "\${1:-}" = "profile" ]; then
  shift
  # "\$@" with the backslash: the heredoc is unquoted, so an unescaped one here would be
  # expanded while BUILDING the image, baking this script's own arguments into AppRun.
  exec "\$APPDIR/usr/bin/glasspad-profile" "\$@"
fi
if [ "\${1:-}" = "status" ]; then
  exec python3 -m glasspad --backend layer --status
fi

exec python3 -m glasspad "\$@"
APPRUN
chmod +x "$appdir/AppRun" "$appdir/usr/bin/glasspad-profile"

# The AppRun is generated from an unquoted heredoc, which means any unescaped
# backtick or $( ) in its text is executed by the GENERATOR, not stored -- and a
# build that hangs with a zero-byte AppRun is what that looks like. Three cheap
# assertions, all of which would have caught it: the file exists, it is not empty,
# and it parses as a shell script.
if [ ! -s "$appdir/AppRun" ]; then
  echo "  ERROR: AppRun is missing or empty -- the heredoc did not write it" >&2
  echo "  Check for unescaped backticks or \$( ) in the AppRun text." >&2
  exit 1
fi
bash -n "$appdir/AppRun" || { echo "  ERROR: the generated AppRun is not valid shell" >&2; exit 1; }
head -1 "$appdir/AppRun" | grep -q '^#!' || { echo "  ERROR: AppRun has no shebang" >&2; exit 1; }

cat > "$appdir/usr/share/doc/glasspad/PACKAGING.txt" <<NOTES
GlassPad $version, AppDir layout

  AppRun                      entry point; sets PYTHONPATH and execs python3 -m glasspad
  GlassPad.desktop            menu entry (validated with desktop-file-validate)
  usr/share/glasspad/glasspad the Python package, including glasspad/uinput_setup.py,
                              the privileged half (stdlib only; staged and pkexec'd)
  usr/bin/glasspad-profile    the per-game profile claimer (a launcher calls this)
  usr/lib/                    empty on purpose -- see below

Nothing is bundled from the host on purpose: libgtk4-layer-shell must match the host's
libwayland and GTK 4, the compositor provides layer-shell, and /dev/uinput is a
permission, not a library. glasspad/bundle.py finds the library on Arch, Debian multiarch,
Fedora, or here in usr/lib, and says which rule matched. Run:

  AppRun status          what it found, and what is missing
  AppRun                 the overlay
  AppRun profile NAME    claim a profile; `profile --list` / `--status` / `--clear` too

Test status when this was built
  suites that could not start: ${blocked}
  (those need pytest, which is not installed here and cannot be fetched: treat the code
   they would have covered as untested in this build, as in every build on this machine)
NOTES

echo "==> the staged tree runs out of the directory (this is the real test)"
# PYTHONDONTWRITEBYTECODE: this smoke test imports the package, and its .pyc files would
# otherwise land inside the tree we are about to ship (see mk_appimage_offline.py).
# The gate asks whether the BUNDLE is well-formed, not whether this machine is
# allowed to create a gamepad. --status exits nonzero when no pad can be created,
# and that is a permission on the machine doing the building -- so requiring a
# clean exit meant an AppImage could not be built on a virgin system or in CI,
# which is precisely where the artefact is needed. What must hold is that the
# payload started, found libgtk4-layer-shell, and saw the compositor. A /dev/uinput
# complaint is expected here and is reported, not fatal.
set +e
smoke=$( cd / && PYTHONDONTWRITEBYTECODE=1 "$appdir/AppRun" --status 2>&1 )
smoke_rc=$?
set -e
printf '%s\n' "$smoke" | sed 's/^/    /'

if printf '%s\n' "$smoke" | grep -q "Traceback"; then
  echo "  ERROR: the staged AppDir raised an exception" >&2
  exit 1
fi
if ! printf '%s\n' "$smoke" | grep -q "layer-shell lib :"; then
  echo "  ERROR: the payload produced no status report at all" >&2
  exit 1
fi
if printf '%s\n' "$smoke" | grep -qE "layer-shell lib :[[:space:]]*NOT FOUND"; then
  echo "  ERROR: the payload cannot find libgtk4-layer-shell; it would start blank" >&2
  exit 1
fi
if [ "$smoke_rc" != 0 ] && ! printf '%s\n' "$smoke" | grep -q "PROBLEM: /dev/uinput"; then
  echo "  ERROR: --status failed for a reason other than /dev/uinput (exit $smoke_rc)" >&2
  exit 1
fi
if [ "$smoke_rc" != 0 ]; then
  echo "    (exit $smoke_rc solely because /dev/uinput is not writable HERE. That is"
  echo "     this machine's permission, not a defect: the artefact tells its user to"
  echo "     run --install-uinput, and that is the behaviour being shipped.)"
fi

if [ "$do_pack" = 0 ]; then
  echo "==> --dir: leaving the AppDir; AppRun works from it"
  exit 0
fi

echo "==> packing"
if [ "${GLASSPAD_NO_BORROW:-0}" != "1" ] && ! command -v appimagetool >/dev/null 2>&1; then
  # No appimagetool does not mean no AppImage. The format is a runtime ELF plus a squashfs
  # and both can be found on a disk that has run an AppImage before, so try that rather
  # than declaring the job impossible. It prints exactly which binaries it borrowed.
  echo "    no appimagetool; trying the offline route"
  if python3 "$root/tools/mk_appimage_offline.py"; then
    # Only warn when something WAS borrowed: the builder prints its own provenance, and a
    # standing warning that fires on a clean pinned build teaches people to ignore warnings.
    if grep -q "BORROWED\|borrowed" "$dist/BUILD-INFO.txt" 2>/dev/null; then
      echo "    WARNING: something in this build was borrowed, not pinned -- see"
      echo "    $dist/BUILD-INFO.txt (runtime origin / mksquashfs pkg). A release"
      echo "    artefact should name both; install squashfs-tools and let the builder"
      echo "    fetch the pinned runtime from packaging/runtime-pin.json."
    fi
    cleanup_staging
    exit 0
  fi
  echo "    offline route failed; falling back to instructions" >&2
fi

if command -v appimagetool >/dev/null 2>&1; then
  out="GlassPad-$version-$arch.AppImage"
  appimagetool "$appdir" "$dist/$out" && { echo "    wrote dist/$out"; cleanup_staging; exit 0; }
  echo "    appimagetool failed" >&2; exit 1
fi

cat >&2 <<HOWTO
  Neither appimagetool nor squashfs-tools is installed, and this machine has no network
  to fetch them, so the final squash+runtime step cannot run here. The AppDir is complete
  and runs (above); finishing it takes one command on a machine that has the tool:

    Arch/CachyOS   sudo pacman -S squashfs-tools        # then: appimagetool from the AUR
    Debian/Ubuntu  sudo apt install appimagetool squashfs-tools

    APPIMAGE_EXTRACT_AND_RUN=1 appimagetool -n GlassPad.AppDir GlassPad-$version-$arch.AppImage

  Copy the AppDir there (it is self-contained), or re-run this script.
HOWTO
exit 1
