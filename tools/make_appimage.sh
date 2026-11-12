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
arch=$(uname -m)

if [ "$run_tests" = 1 ]; then
  echo "==> suite"
  set +e
  python3 tools/check_all.py >/tmp/mkai_tests.log 2>&1
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
[ -f LICENSE ] && cp LICENSE "$appdir/usr/share/doc/glasspad/LICENSE" || true
cp packaging/glasspad.svg "$appdir/usr/share/icons/hicolor/scalable/apps/glasspad.svg"

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

cat > "$appdir/usr/share/doc/glasspad/PACKAGING.txt" <<NOTES
GlassPad $version, AppDir layout

  AppRun                      entry point; sets PYTHONPATH and execs python3 -m glasspad
  GlassPad.desktop            menu entry (validated with desktop-file-validate)
  usr/share/glasspad/glasspad the Python package
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
( cd / && PYTHONDONTWRITEBYTECODE=1 "$appdir/AppRun" --status ) | sed 's/^/    /'

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
    echo "    (the runtime and mksquashfs were borrowed -- see the lines above;"
    echo "     install squashfs-tools + appimagetool to stop depending on that)"
    exit 0
  fi
  echo "    offline route failed; falling back to instructions" >&2
fi

if command -v appimagetool >/dev/null 2>&1; then
  out="GlassPad-$version-$arch.AppImage"
  appimagetool "$appdir" "$dist/$out" && { echo "    wrote dist/$out"; exit 0; }
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
