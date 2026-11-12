#!/bin/sh
# GlassPad (Linux) installer -- uinput setup, licenses, desktop entry.
#
# Usage:
#   ./install.sh                install everything (needs sudo)
#   ./install.sh --user         desktop entry + icons only, into ~/.local
#   ./install.sh --autostart    also start the overlay at login (per user)
#   ./install.sh --dry-run      show what would be done
#   ./install.sh --check        report current state, change nothing
#   ./install.sh --uninstall    undo everything, from both install roots
#
# --uninstall is deliberately broader than the mode you installed with: it clears
# ~/.local AND /usr/local AND the autostart entry, because "uninstall
# completely" during bug testing has to leave one command to remember rather
# than three guesses about how the copy being removed was installed.
#
# The Python package itself is not installed here; run it from a checkout or
# install it with your preferred Python tooling. This script only handles the
# parts that need root: the uinput kernel module, permission to open
# /dev/uinput, and the icons/desktop entry.
#
# IMPORTANT: PREFIX relocates ONLY the desktop entry and icons. The uinput
# module config and the udev rule are absolute /etc paths and ignore PREFIX,
# so a sandboxed PREFIX is not a sandbox -- `PREFIX=/tmp/x ./install.sh` still
# writes /etc/modules-load.d/uinput.conf, /etc/udev/rules.d/
# 99-glasspad-uinput.rules and reloads udev. For a genuinely unprivileged run
# use --user, which skips the /etc work entirely.

set -eu

PREFIX="${PREFIX:-/usr/local}"
SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MODULES_CONF=/etc/modules-load.d/uinput.conf
UDEV_RULE=/etc/udev/rules.d/99-glasspad-uinput.rules

say() { printf '%s\n' "$*"; }
run() {
    # Quote each argument so a shell -c payload reads as one argument rather
    # than looking like it was split across several.
    label=$(for a in "$@"; do printf " '%s'" "$a"; done)
    if [ "$DRY" = 1 ]; then
        say "  would run:$label"
    else
        say "  running:  $label"
        "$@"
    fi
}

# The account GlassPad is being installed *for*. Under sudo, $HOME is root's, so
# anything written or removed through $HOME would land in /root and leave the
# real user's launcher, autostart entry and saved layout untouched -- which reads
# as a complete uninstall and is not one. SUDO_USER names the person who typed
# the command; that is whose desktop is being changed.
# Defined before the argument loop because --user resolves PREFIX through it.
TARGET_USER=${SUDO_USER:-$(id -un)}
target_home() {
    home=$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6)
    if [ -n "$home" ]; then
        printf '%s\n' "$home"
    elif [ "$TARGET_USER" = "$(id -un)" ]; then
        printf '%s\n' "$HOME"
    else
        printf '%s\n' "/home/$TARGET_USER"
    fi
}

# Per-user XDG roots. XDG_CONFIG_HOME is honoured only for the account running
# the script: with sudo it would be root's, or unset and therefore /root/.config.
target_config() {
    if [ "$TARGET_USER" = "$(id -un)" ] && [ -n "${XDG_CONFIG_HOME:-}" ]; then
        printf '%s\n' "$XDG_CONFIG_HOME"
    else
        printf '%s\n' "$(target_home)/.config"
    fi
}
target_local() {
    printf '%s\n' "$(target_home)/.local"
}
autostart_dir() {
    printf '%s\n' "$(target_config)/autostart"
}

DRY=0
MODE=install
USER_ONLY=0
AUTOSTART=0
SYSTEM_PREFIX=/usr/local
for arg in "$@"; do
    case "$arg" in
        --dry-run)   DRY=1 ;;
        --check)     MODE=check ;;
        # Sets the install root rather than a mode, so --user --uninstall and
        # --user --dry-run do the obvious thing for a per-user install. Resolved
        # through target_home so `sudo ./install.sh --user` cannot install into
        # /root/.local, where no desktop would ever look.
        --user)      USER_ONLY=1; PREFIX="$(target_home)/.local" ;;
        --autostart) AUTOSTART=1 ;;
        --uninstall) MODE=uninstall ;;
        -h|--help)   sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) say "unknown option: $arg"; exit 2 ;;
    esac
done

# A relocated PREFIX is the closest thing to a sandbox this script has, and it
# only covers half the changes, so say so whenever the run looks contained.
if [ "$USER_ONLY" != 1 ] && [ "$PREFIX" != "/usr/local" ]; then
    say "NOTE: PREFIX only moves the desktop entry and icons."
    say "      These absolute paths are still written regardless of PREFIX:"
    say "        $MODULES_CONF"
    say "        $UDEV_RULE"
    if [ "$MODE" = install ] && [ "$DRY" != 1 ]; then
        say "      and udev is reloaded. Use --user for a run that touches"
        say "      nothing outside \$HOME."
    fi
    say ""
fi

need_root() {
    if [ "$(id -u)" -ne 0 ] && [ "$DRY" != 1 ]; then
        say "needs root (try: sudo ./install.sh)"
        exit 1
    fi
}

# --- reporting --------------------------------------------------------------
uinput_state() {
    if [ ! -e /dev/uinput ]; then
        say "  /dev/uinput : MISSING (module not loaded)"
        return 1
    fi
    perms=$(ls -l /dev/uinput | awk '{print $1" "$3":"$4}')
    if [ -w /dev/uinput ]; then
        say "  /dev/uinput : writable ($perms)"
        return 0
    fi
    say "  /dev/uinput : present but NOT writable ($perms)"
    return 1
}

do_check() {
    say "GlassPad environment check"
    say ""
    say "kernel"
    uinput_state || true
    if [ -f "$MODULES_CONF" ]; then
        say "  modules-load: $MODULES_CONF present"
    else
        say "  modules-load: not configured (uinput will not survive a reboot)"
    fi
    if [ -f "$UDEV_RULE" ]; then
        say "  udev rule   : $UDEV_RULE present"
    else
        say "  udev rule   : not installed"
    fi
    say ""
    say "groups for $TARGET_USER"
    id -nG "$TARGET_USER" 2>/dev/null | tr ' ' '\n' | grep -x input >/dev/null \
        && say "  input       : member" \
        || say "  input       : NOT a member (needed unless /dev/uinput is 0666)"
    as=$(autostart_dir)
    if [ -f "$as/glasspad.desktop" ]; then
        say "  autostart   : $as/glasspad.desktop present"
    else
        say "  autostart   : not configured (install with --autostart)"
    fi
    say ""
    say "python"
    python3 -c "import PyQt6.QtCore as c; print('  PyQt6       :', c.PYQT_VERSION_STR)" 2>/dev/null \
        || say "  PyQt6       : NOT installed (pacman -S python-pyqt6)"
    say ""
    say "display"
    say "  DISPLAY          : ${DISPLAY:-<unset>}"
    say "  WAYLAND_DISPLAY  : ${WAYLAND_DISPLAY:-<unset>}"
    say "  QT_QPA_PLATFORM  : ${QT_QPA_PLATFORM:-<unset, GlassPad will force xcb>}"
}

# --- uinput -----------------------------------------------------------------
enable_module() {
    say "uinput kernel module"
    if ! grep -qx uinput "$MODULES_CONF" 2>/dev/null; then
        run sh -c "printf 'uinput\n' > $MODULES_CONF"
    else
        say "  $MODULES_CONF already lists uinput"
    fi
    if [ ! -e /dev/uinput ]; then
        run modprobe uinput
    else
        say "  already loaded"
    fi
}

install_udev_rule() {
    # Some systemd builds already ship a rule handing /dev/uinput to the input
    # group (check: grep uinput /lib/udev/rules.d/*.rules). Writing our own is
    # harmless and keeps the behaviour identical across distros. Note this
    # mirrors that policy rather than opening the device to the world: the
    # alternative, mode 0666, would let any user create input devices, and a
    # virtual Xbox pad is a keystroke-injecting device as far as the desktop is
    # concerned. Being in `input` is therefore still required -- see below.
    say "udev rule"
    if [ "$DRY" = 1 ]; then
        say "  would write: $UDEV_RULE"
        return
    fi
    cat > "$UDEV_RULE" <<'RULE'
# GlassPad: allow members of the input group to create virtual gamepads.
KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
RULE
    say "  wrote $UDEV_RULE"
    if command -v udevadm >/dev/null 2>&1; then
        run udevadm control --reload-rules
        run udevadm trigger
    fi
}

add_user_to_input_group() {
    target=$TARGET_USER
    say "permissions for $target"
    if [ "$target" = "root" ]; then
        say "  running as root; skipping group membership"
        return
    fi
    if id -nG "$target" | tr ' ' '\n' | grep -qx input; then
        say "  already in group input"
        return
    fi
    run usermod -aG input "$target"
    say "  NOTE: $target must log out and back in for the group to apply."
}

# --- desktop integration ----------------------------------------------------
install_desktop() {
    say "desktop entry and icon"
    icons="$PREFIX/share/icons/hicolor"
    apps="$PREFIX/share/applications"
    if [ "$DRY" = 1 ]; then
        say "  would install into $PREFIX/share"
        return
    fi
    mkdir -p "$apps" "$icons/256x256/apps" "$icons/512x512/apps"
    cp "$SRC/glasspad/img/glasspad_icon.png"     "$icons/256x256/apps/glasspad.png"
    cp "$SRC/glasspad/img/glasspad_icon_512.png" "$icons/512x512/apps/glasspad.png"
    sed -e "s|@PYTHON@|$(command -v python3)|" \
        -e "s|@PACKAGE_DIR@|$SRC|" \
        "$SRC/desktop/glasspad.desktop.in" > "$apps/glasspad.desktop"
    refresh_caches "$PREFIX"
    say "  wrote $apps/glasspad.desktop"
}

install_autostart() {
    # XDG autostart, not a systemd unit: the overlay needs the desktop session's
    # WAYLAND_DISPLAY/DISPLAY and its input region is meaningless outside one, so
    # a service would start it in an environment where it cannot work.
    # POSIX sh has no `local` in every implementation; these names are
    # intentionally prefixed so they cannot collide with the script's globals.
    autostart_target_dir=$(autostart_dir)
    src="$PREFIX/share/applications/glasspad.desktop"
    say "autostart for $TARGET_USER"
    if [ ! -f "$src" ]; then
        say "  no desktop entry at $src; skipping"
        return
    fi
    if [ "$DRY" = 1 ]; then
        say "  would copy $src -> $autostart_target_dir/glasspad.desktop"
        return
    fi
    mkdir -p "$autostart_target_dir"
    cp "$src" "$autostart_target_dir/glasspad.desktop"
    say "  wrote $autostart_target_dir/glasspad.desktop"
}

run_soft() {
    # Like run(), but a non-zero exit is reported instead of being fatal.
    # Advisory steps only -- see refresh_caches for why that distinction matters.
    if ! run "$@"; then
        say "  WARNING: $1 reported a problem; the files themselves are installed."
    fi
    return 0
}

refresh_caches() {
    # Launchers read caches, not the filesystem: a new desktop entry stays hidden
    # and a removed one keeps offering a GlassPad that is gone until the databases
    # are refreshed. Same call on install and on uninstall, since both states look
    # like a broken installer when the cache disagrees with the disk.
    #
    # Neither refresher may abort the run. Under `set -e` a non-zero
    # gtk-update-icon-cache used to kill the installer between writing the files
    # and reporting them, so a completely installed system announced failure --
    # and `sudo ./install.sh && ./install.sh --check` stopped before the check.
    # What triggers it is a first install into /usr/local: gtk-update-icon-cache
    # insists on an index.theme before treating a directory as a theme, and a
    # freshly created /usr/local/share/icons/hicolor has none (the distribution
    # ships hicolor's under /usr/share). The icons still resolve, because hicolor
    # is the mandated fallback theme and is searched as-is with no cache at all,
    # which is why skipping the refresher there costs nothing.
    for pair in "update-desktop-database" "gtk-update-icon-cache"; do
        cmd=${pair%% *}
        command -v "$cmd" >/dev/null 2>&1 || continue
        case "$cmd" in
            update-desktop-database) target="$1/share/applications" ;;
            *) target="$1/share/icons/hicolor" ;;
        esac
        [ -d "$target" ] || continue
        if [ "$cmd" = "gtk-update-icon-cache" ]; then
            [ -f "$target/index.theme" ] || continue
            run_soft "$cmd" -q "$target"
        else
            run_soft "$cmd" "$target"
        fi
    done
}

REMOVAL_ERRORS=0

run_ok() {
    # Like run(), but a failure is reported and counted rather than fatal.
    # `set -e` would otherwise stop the uninstall at the first unreadable root:
    # one file owned by someone else would leave the udev rule, the autostart
    # entry and the other prefix all still in place, with no list of what is
    # left. Reporting every failure is the only way "uninstall" can be trusted
    # to be finished.
    if run "$@"; then
        return 0
    fi
    say "  FAILED (continuing): $*"
    REMOVAL_ERRORS=$((REMOVAL_ERRORS + 1))
    return 0
}

remove_prefix() {
    # Removes one install root's files and refreshes its caches. Called for both
    # roots on uninstall, and never fails a removal over a root that was never
    # used -- rm -f on a missing file is not an error.
    say "  $1"
    run_ok rm -f "$1/share/applications/glasspad.desktop"
    run_ok rm -f "$1/share/icons/hicolor/256x256/apps/glasspad.png"
    run_ok rm -f "$1/share/icons/hicolor/512x512/apps/glasspad.png"
    if [ "$DRY" != 1 ]; then
        refresh_caches "$1"
    fi
}

uninstall() {
    # Everything, from everywhere. The point of uninstalling during bug testing
    # is a known-clean machine, and the copy to be removed may have gone in via
    # --user, via the system install, with autostart, or with a custom PREFIX at
    # different times. Asking which one it was would leave something behind.
    if [ "$USER_ONLY" != 1 ]; then
        need_root
    fi
    say "removing everything GlassPad installed"
    say "kernel and permissions"
    run_ok rm -f "$MODULES_CONF" "$UDEV_RULE"
    user_local=$(target_local)
    say "per-user files for $TARGET_USER ($user_local)"
    remove_prefix "$user_local"
    if [ "$SYSTEM_PREFIX" != "$user_local" ]; then
        say "system files ($SYSTEM_PREFIX)"
        remove_prefix "$SYSTEM_PREFIX"
    fi
    if [ "$PREFIX" != "$user_local" ] && [ "$PREFIX" != "$SYSTEM_PREFIX" ]; then
        say "custom PREFIX ($PREFIX)"
        remove_prefix "$PREFIX"
    fi
    as=$(autostart_dir)
    say "autostart ($as)"
    run_ok rm -f "$as/glasspad.desktop"
    say "saved layout (button positions and opacity)"
    cfg=$(target_config)
    run_ok rm -f "$cfg/GlassPad/layout.json"
    say "  rmdir $cfg/GlassPad"
    if [ "$DRY" != 1 ]; then
        rmdir "$cfg/GlassPad" 2>/dev/null || true
    fi
    # Deleting the rule file does not undo what the install already applied:
    # udevadm trigger wrote the rule's GROUP/MODE onto the live /dev/uinput node
    # and udev keeps the compiled ruleset in memory. Without a reload the node
    # keeps the permissions of a rule that no longer exists until the next boot,
    # so "uninstalled" would not mean restored.
    if command -v udevadm >/dev/null 2>&1; then
        run_ok udevadm control --reload-rules
        run_ok udevadm trigger
    fi
    say ""
    say "left alone on purpose:"
    say "  group membership -- gpasswd -d $TARGET_USER input (then log out)"
    say "  the uinput kernel module -- still loaded, harmless: rmmod uinput"
    say "  the Python package itself -- uninstall it with whatever installed it"
    if [ "$REMOVAL_ERRORS" -gt 0 ]; then
        say ""
        say "$REMOVAL_ERRORS removal(s) FAILED -- GlassPad is not fully gone."
        say "Re-run as the account that owns them, or with sudo:"
        say "    sudo ./install.sh --uninstall"
        return 1
    fi
    say "done."
}

case "$MODE" in
    check)
        do_check
        ;;
    uninstall)
        uninstall
        ;;
    install)
        if [ "$USER_ONLY" = 1 ]; then
            # ~/.local/share/{applications,icons/hicolor} are standard XDG dirs,
            # so the launcher and icon resolve for this user alone, with no root
            # and nothing written under /etc. uinput is deliberately skipped:
            # if /dev/uinput is not already writable, --user cannot fix that and
            # the privileged install is the only route.
            if ! uinput_state >/dev/null 2>&1; then
                say "note: /dev/uinput is not writable for $TARGET_USER."
                say "      The overlay will show but cannot create a pad."
                say "      Run ./install.sh --check to see what is missing."
            fi
            install_desktop
            if [ "$AUTOSTART" = 1 ]; then
                install_autostart
            fi
            say ""
            say "done. Installed without root; uinput left as found."
            exit 0
        fi
        need_root
        enable_module
        install_udev_rule
        add_user_to_input_group
        install_desktop
        if [ "$AUTOSTART" = 1 ]; then
            install_autostart
        fi
        say ""
        say "done. Verify with:  python3 -m glasspad --status"
        ;;
esac
