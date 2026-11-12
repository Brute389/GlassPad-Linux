#!/bin/sh
# GlassPad-Linux — self-contained state check. No paths to edit, no root.
#
#   ./verify.sh [dir] [--fast]
#
#   no args    check the current directory, full run
#   --fast     skip the pytest runs; state + tooling only
#   dir        check that tree instead (relative or absolute, any order)
#
# Lives in tools/ and expects to be run from the project root, like the other
# tools/. Section 1 judges each fix by CONTENT, and the input-ABI check imports
# the module rather than grepping it -- this port's bug class was constants whose
# names were right and whose numbers were off by one, which no grep can see.
#
# Exits non-zero if something is wrong. Runs under dash or bash.

TARGET=$PWD
FAST=0
for a in "$@"; do
    case $a in
        --fast) FAST=1 ;;
        -*) printf 'unknown option: %s\n' "$a" >&2; exit 2 ;;
        *) TARGET=$a ;;
    esac
done
cd "$TARGET" || { printf 'cannot cd to %s\n' "$TARGET" >&2; exit 2; }

# Known revisions, newest first. The FIX itself is judged by content (section 1),
# never by hash -- these only say WHICH revision a file is, so that an older but
# correct tree is described as "older" rather than "broken". The 199-test
# test_glasspad.py already carries both fixes; the 210-test one only adds
# coverage for tools/probe_real_pad.py.
#
# One record per LINE -- not space-separated. An earlier draft used spaces, and the
# lookup loop iterated an unquoted $table, so any label containing a space split one
# record in half: "…:ok:210 tests (probe coverage):210+0/206+4" arrived as the entry
# ending ":ok:210" plus junk words, which both truncated the printed label and lost
# the tally the suite legs need. Labels must not contain ":" or a newline.
#
# Fields per record: hash:verdict:label[:tally], where tally is
# "<offscreen_passed>+<offscreen_skipped>/<xcb_passed>+<xcb_skipped>". Keeping the
# expected tally IN the table is what stops section 4 from failing a legitimately
# older tree: section 2 already knows which revision the file is, so demanding the
# newest count of an older file reported the user's own correct copy as a regression.
HASHES_install__sh="cc8afe49742a24ec5567a0987e1d189c:ok:fixed
7bd15825da08070896776de68b5c8b92:broken:PRE-FIX no run_soft guard"
HASHES_tools_uinput_watch_py="3cc85a47aacca270a0113ef5d8515ce7:ok:fixed
25fb113c71334f6bbbfda4151f253133:broken:PRE-FIX no dashdash guard"
HASHES_tests_test_glasspad_py="9ac07f7c2f3439c3428e2a3858c50603:ok:265 tests, + disable button latches as a toggle (momentary in the Windows original):265+0/261+4
00052185ee19be5f3746bc1b958040df:ok:261 tests, + hold-to-disable round-trips through the router (overlay was one-way):261+0/257+4
196c4e4835c1b1efefb9cfb04fdff740:ok:259 tests, + quit-on-close (overlay must end the process):259+0/255+4
d5c76d2e37ed1580732414b17994ac32:ok:256 tests, widget-contract guard + hub press-then-drag on both input paths:256+0/252+4
ee31bd7c8d0c35b0278f7ff50af5a35a:ok:253 tests, real Qt6 touch objects (touchPoints/pos/int(state)/TouchCancel):253+0/249+4
2bf25f87a7025e3e46ea2945157a407f:ok:250 tests, --status reason agrees with its verdict + pad init/dispose race + verify tools tear down:250+0/246+4
c4aa4920d01b1ae1b9ce94de5356b86e:ok:243 tests, version 1.1.0 + test_version_strings_agree:243+0/239+4
3d79c2fc6aee57bc872b3d3dd51320d8:ok:242 tests, documents why a permission denial counts as alive:242+0/238+4
30bc407eba7951064fb0d6c64e42df7f:ok:242 tests, adds socket-liveness probing (stale socket is not a display):242+0/238+4
1489310e518555508b2eaade58b2ece8:ok:238 tests, adds TestForceXcb socket-hermetic autouse guard + high-display-number regression:238+0/234+4
f46200e0e706ab691cd0e7b1f3d4c58e:ok:236 tests (probe + ABI pinning + declaration + interrupt path):236+0/232+4
303946302469c3aaa5224009ee72448c:ok:234 tests, pre-interrupt-path fix:234+0/230+4
7d416d99df45cacd32d79f843e3d042a:ok:230 tests, no TestBackendDeclaration:230+0/226+4
0d8a6b924b885bb749b8ac87ea7d3d8d:ok:210 tests, PRE-ABI codes (hat on ABS_MT_*, Guide on BTN_0, triggers and right stick swapped):210+0/206+4
a8c46ac794c173f558b74cb7f5db3e27:ok:199-test revision, PRE-ABI:199+0/195+4"

# Expected suite tallies for the test file in $TARGET. Defaults to the newest
# revision; overwritten by section 2 when it recognises an older one, and relaxed
# to "zero failures only" when the file is unrecognised -- a tree carrying its own
# extra tests would otherwise fail a hardcoded count for no real reason.
WANT_OFF_P=265; WANT_OFF_S=0
WANT_XCB_P=261; WANT_XCB_S=4
SUITE_PIN=yes

# describe_rev FILE TABLE
# Sets REV_VERDICT (latest | ok | broken | unknown), REV_LABEL, REV_TALLY.
# The FIRST record of a table is by convention the newest revision. Returning the
# table's own verdict matters: an earlier draft hardcoded "older but correct", so a
# PRE-FIX file -- genuinely broken -- was reported as merely behind.
#
# REV_* globals rather than printed output, because the caller used to do
# `read -r verdict label <<EOF` on the function's stdout, which cannot survive a
# label containing a space ("210 tests" read back as verdict="210", label="tests").
describe_rev() {
    _h=$(md5_of "$1")
    REV_VERDICT=unknown; REV_LABEL='?'; REV_TALLY=
    _first=1
    while IFS= read -r _e; do
        [ -n "$_e" ] || continue
        _want=${_e%%:*}
        _rest=${_e#*:}
        _verdict=${_rest%%:*}
        _rest=${_rest#*:}
        _label=$_rest
        _tally=
        case $_label in
            # tally is after the LAST colon, label before the FIRST, so the label
            # may contain neither a colon nor a newline (documented above).
            *:*) _tally=${_label##*:}; _label=${_label%%:*} ;;
        esac
        if [ "$_h" = "$_want" ]; then
            [ "$_first" = 1 ] && _verdict=latest
            REV_VERDICT=$_verdict; REV_LABEL=$_label; REV_TALLY=$_tally
            return 0
        fi
        _first=0
    done <<EOF
$2
EOF
}

fails=0
warnings=0

ok()   { printf '  \033[32mok\033[0m      %-42s %s\n' "$1" "$2"; }
bad()  { printf '  \033[31mFAIL\033[0m    %-42s %s\n' "$1" "$2"; fails=$((fails+1)); }
warn() { printf '  \033[33mskip\033[0m    %-42s %s\n' "$1" "$2"; warnings=$((warnings+1)); }
hdr()  { printf '\n%s\n' "$1"; }

# Pull "N passed" / "N skipped" / "N failed" out of either runner's summary.
# Substring matching is unsafe here: run_tests.py always prints "0 failed", so a
# `*failed*` test would fail every green run, and its "passed, 0 failed, 4
# skipped" ordering matches neither of pytest's forms.
tally() { printf '%s' "$1" | grep -o "[0-9][0-9]* $2" | cut -d' ' -f1 | head -1; }

# Keep the FULL output of a suite leg, and print the interesting lines when a
# leg fails. Passing only `tail -1` to judge_suite made a real user's failure
# undiagnosable: the report said "229 passed, 1 failed" and "RESULT: 12 FAILED"
# with no clue which test or why, because every line that named it was thrown
# away. RUN_FULL is the whole transcript; RUN_SUMMARY is its last line, which is
# what the tallies parse.
RUN_FULL=""; RUN_SUMMARY=""
run_suite() {
    RUN_FULL=$(eval "$1" 2>&1)
    RUN_SUMMARY=$(printf '%s\n' "$RUN_FULL" | tail -1)
}
# Both runners name failures near the top (pytest 'FAILED path::test',
# run_tests.py 'FAIL  Class::test') and repeat the traceback at the bottom under
# a '==== FAILED ====' banner, so take both and let the caller truncate.
failure_detail() {
    [ -n "$RUN_FULL" ] || return 0
    printf '%s\n' "$RUN_FULL" \
        | grep -E '^(FAILED|ERROR|FAIL|SKIP) |^={6,}|^(Assertion|Runtime|Type|Value|Import|ModuleNotFound|Attribute|Name)Error|^[A-Za-z_.]*Error:|^E +[A-Za-z]' \
        | head -18 | sed 's/^/      | /'
}

# $1 label  $2 output  $3 want_passed  $4 want_skipped
# $5 yes  -> tolerate "$3 minus 6 passed, 6 skipped" (no X server anywhere)
# $6 no   -> $3/$4 are unknown for this tree, so judge failures only
# $7 <n>  -> the OFFSCREEN passed-count; an xcb leg reporting it means the platform
#            never reached the suite. Only passed counts 6 apart can be confused
#            (xcb skips exactly the 4 require_offscreen tests), and $3 is 0 for the
#            offscreen leg itself so that leg can never trip this.
judge_suite() {
    p=$(tally "$2" passed); sk=$(tally "$2" skipped); f=$(tally "$2" failed)
    _fails_before=$fails
    [ -n "$sk" ] || sk=0
    [ -n "$f" ] || f=0
    # `6=$6` would be invalid -- a digit cannot start an assignment, so dash treats
    # it as a command named "6=yes". Defaults go through real names.
    pin=$6; [ -n "$pin" ] || pin=yes
    nosrv=$5; [ -n "$nosrv" ] || nosrv=no
    offp=$7
    if [ -z "$p" ]; then
        bad "$1" "$(printf '%s' "$2" | tail -c 120) - no tally; is pytest or tools/run_tests.py present?"
    elif [ "$f" != 0 ]; then
        bad "$1" "$2"
    elif [ "$pin" = no ]; then
        ok "$1" "$2 - no failures (exact tally not asserted: see this leg's own notes above)"
    elif [ "$p" = "$3" ] && [ "$sk" = "$4" ]; then
        ok "$1" "$2"
    elif [ "$nosrv" = yes ] && [ "$sk" = 6 ] && [ "$p" = "$(( $3 - 6 ))" ]; then
        warn "$1" "$2 - 6 skips are the libX11 tests with no server, not a failure"
    elif [ -n "$offp" ] && [ "$p" = "$offp" ] && [ "$sk" = 0 ]; then
        # The xcb leg must differ from the offscreen leg, or QT_QPA_PLATFORM never
        # got through. Keyed on the leg's OWN expected passed-count, so it stays
        # correct for every revision (210/206 today, 199/195 before the probe tests).
        bad "$1" "$2 - that is the OFFSCREEN tally; the platform never reached the suite"
    else
        bad "$1" "$2 - expected $3 passed, $4 skipped"
    fi
    # Naming the failing test matters more than the tally: the tally is already
    # on the line above and tells the reader nothing actionable.
    if [ "$fails" != "$_fails_before" ]; then failure_detail; fi
}

md5_of() { md5sum "$1" 2>/dev/null | cut -d' ' -f1; }
# grep -c prints nothing at all (not "0") when the file is missing, which would
# make `[ ... -ge N ]` die with "Illegal value" instead of reporting FAIL.
count_of() { n=$(grep -c "$1" "$2" 2>/dev/null || true); printf '%s\n' "${n:-0}"; }

[ -f install.sh ] || { echo "run this from the GlassPad-Linux directory"; exit 2; }

hdr "1. the fixes are in the files"
if [ "$(count_of 'cmd\[:1\] == \["--"\]' tools/uinput_watch.py)" -ge 1 ]; then
    ok "uinput_watch strips the -- separator" "line $(grep -n 'cmd\[:1\]' tools/uinput_watch.py | cut -d: -f1)"
else
    bad "uinput_watch strips the -- separator" "absent -> 'run -- cmd' raises FileNotFoundError"
fi
if [ "$(count_of 'run_soft()' install.sh)" -ge 1 ] && \
   [ "$(count_of 'index.theme' install.sh)" -ge 1 ]; then
    ok "install.sh refresh_caches is non-fatal" "run_soft at line $(grep -n 'run_soft()' install.sh | cut -d: -f1)"
else
    bad "install.sh refresh_caches is non-fatal" "absent -> a cache tool exit 1 aborts a finished install"
fi

# The input ABI, checked by IMPORTING the module rather than grepping it. Grep
# cannot see what AXIS_SPECS actually maps, and this port's whole bug class was
# constants whose NAMES were right while their NUMBERS were off by one -- a
# grep for 'ABS_HAT0X' matches happily whether it is 0x10 (a hat) or 0x39
# (ABS_MT_TRACKING_ID). Values are the kernel's own, restated here so the check
# is independent of the module under test.
# NOT `abi=$(python3 - <<'PY' 2>&1)`: closing the command substitution on the
# heredoc's own opening line ends $() right there, so the body is read by the
# SHELL -- `import sys` then runs /usr/bin/import (ImageMagick), which fails with
# "unable to open X server", and the following `)` is a syntax error. The closing
# paren belongs on the line AFTER the terminator.
abi=$(python3 - <<'PY' 2>&1
import sys
sys.path.insert(0, '.')
from glasspad import xd360 as X
want = {'BTN_A': 0x130, 'BTN_B': 0x131, 'BTN_X': 0x133, 'BTN_Y': 0x134,
        'BTN_TL': 0x136, 'BTN_TR': 0x137, 'BTN_SELECT': 0x13A,
        'BTN_START': 0x13B, 'BTN_MODE': 0x13C, 'BTN_THUMBL': 0x13D,
        'BTN_THUMBR': 0x13E, 'BTN_DPAD_UP': 0x220, 'BTN_DPAD_DOWN': 0x221,
        'BTN_DPAD_LEFT': 0x222, 'BTN_DPAD_RIGHT': 0x223,
        'ABS_HAT0X': 0x10, 'ABS_HAT0Y': 0x11}

# Every attribute read through here, never X.NAME directly: a tree predating the
# fix has no BTN_MODE at all, and a bare attribute access made this check print a
# Python traceback where it should print which codes are wrong. The whole point
# is a legible verdict on a tree that is broken.
def has(n):
    return hasattr(X, n)

bad = []
for n, v in sorted(want.items()):
    if not has(n):
        bad.append('%s absent (want 0x%x)' % (n, v))
    elif getattr(X, n) != v:
        bad.append('%s=0x%x want 0x%x' % (n, getattr(X, n), v))
guide = X.BUTTON_CODES.get('GUIDE')
want_guide = getattr(X, 'BTN_MODE', 0x13C)
if guide != want_guide:
    bad.append('GUIDE=0x%x want 0x%x (BTN_MODE: 0x100 is BTN_0, 0x120 is BTN_TRIGGER)'
               % (guide if guide is not None else -1, want_guide))
stick = {c for c, s in X.AXIS_SPECS.items() if s is X.STICK}
trig = {c for c, s in X.AXIS_SPECS.items() if s is X.TRIGGER}
if stick != {X.ABS_X, X.ABS_Y, X.ABS_RX, X.ABS_RY}:
    bad.append('sticks on %s want ABS_X/Y/RX/RY' % sorted(hex(c) for c in stick))
if trig != {X.ABS_Z, X.ABS_RZ}:
    bad.append('triggers on %s want ABS_Z/RZ' % sorted(hex(c) for c in trig))
declared = set(X.AXIS_SPECS) | {X.ABS_HAT0X, X.ABS_HAT0Y}
mt = sorted(c for c in declared if 0x2F <= c <= 0x3F)
if mt:
    bad.append('multitouch axes on a gamepad: %s' % [hex(c) for c in mt])
print('OK' if not bad else '; '.join(bad))
PY
)
if [ "$abi" = OK ]; then
    ok "input ABI matches the kernel" "codes, hat on 0x10/0x11, sticks ABS_XY/RXRY, triggers ABS_ZRZ"
elif [ -n "$abi" ]; then
    bad "input ABI matches the kernel" "$abi"
fi

# Does a leftover socket from a dead server still get chosen as a display?
# Checked by running it, not by grepping for a function name: the whole bug was
# a filename being believed where a probe was needed, so the pass condition has
# to be "make a stale socket, ask what the module says".
#
# Why it matters on a Wayland desktop specifically: Xwayland leaves these behind
# every time a session ends, and a DISPLAY pointing at a server nobody listens to
# is not a degraded start -- the xcb plugin cannot initialise and Qt abort()s.
# Native Wayland would have worked, so one stale inode cost the whole launch.
stale_check=$(python3 - <<'PY' 2>&1
import os, socket, sys, tempfile
sys.path.insert(0, ".")
from glasspad import __main__ as m
d = tempfile.mkdtemp()
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.bind(os.path.join(d, "X7"))
sock.close()                      # bound then closed: exactly a killed server
target = os.path.join(d, "X7")
if not hasattr(m, "_X_SOCKET_DIR"):
    print("no-probe")              # tree predates the liveness fix
else:
    m._X_SOCKET_DIR = d
    n = m._x_socket_number()
    if n is None:
        # Confirm the probe is not simply vetoing everything, which would also
        # answer None here and pass for the wrong reason.
        keep = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        keep.bind(os.path.join(d, "X3"))
        keep.listen(1)
        try:
            print("ok" if m._x_socket_number() == 3 else "no-live-either")
        finally:
            keep.close()
    else:
        print("stale-chosen-%s" % n)
os.path.exists(target)
PY
)
case "$stale_check" in
    ok) ok "stale X socket is not treated as a display" \
            "dead :7 refused, live :3 found" ;;
    no-probe) bad "stale X socket is not treated as a display" \
            "no liveness probe in this tree -> a leftover socket file can set DISPLAY and Qt abort()s" ;;
    stale-chosen*) bad "stale X socket is not treated as a display" \
            "reported $stale_check from a socket with no listener behind it" ;;
    *) bad "stale X socket is not treated as a display" "$stale_check" ;;
esac

# The router's widget contract. FabButton had control_press and control_release
# but no control_move, while _claimable tested only for control_press -- so a hub
# button was grabbed on press and the NEXT event raised AttributeError inside the
# event filter, which PyQt turns into SIGABRT. Josh hit it on touch and reported
# it; the mouse path reached the same _move() with no containment at all, so a
# click plus a small drag aborted the overlay with no touchscreen involved.
#
# Grepped in two halves on purpose. A check that only asked "does FabButton have
# control_move" would have stayed green while _claimable still gated on one
# member, which is the half that lets the NEXT momentary widget repeat the crash.
# Both halves must be asserted for the class of defect to be closed.
contract_gate=$(python3 - <<'PY' 2>&1
import re, sys
try:
    src = open('glasspad/touch_router.py').read()
except OSError as exc:
    print('unreadable', exc); sys.exit(0)
m = re.search(r'\bCONTRACT\s*=\s*\(([^)]*)\)', src)
names = re.findall(r'["\']([a-z_]+)["\']', m.group(1)) if m else []
# The GATE is the function's last top-level return. Matching the first one read
# `return False` -- the early exit for an invisible widget -- so a healthy tree
# was reported as gate-one-member, i.e. the check failed a correct file.
body = re.search(r'\n    def _claimable\b(.*?)(?=\n    def |\n# |\Z)',
                 src, re.S)
returns = re.findall(r'^        return\b(.*)$', body.group(1), re.M) \
    if body else []
gate = returns[-1] if returns else ''
if names != ['control_press', 'control_move', 'control_release']:
    print('contract-names', names)
elif not (gate and ('CONTRACT' in gate
                    or all(n in gate for n in names))):
    print('gate-one-member', gate.strip()[:60])
else:
    try:
        wsrc = open('glasspad/widgets.py').read()
    except OSError as exc:
        print('widgets-unreadable', exc); sys.exit(0)
    fab = wsrc.split('class FabButton(')[-1]
    have = {n for n in names if re.search(r'\bdef %s\b' % n, fab)}
    print('ok' if have == set(names) else 'fab-missing',
          sorted(set(names) - have) if have != set(names) else '')
PY
)
case $contract_gate in
    ok*)  ok "router drives a complete widget contract" \
            "CONTRACT + all-member gate + FabButton implements all three" ;;
    gate-one-member*)
        bad "router drives a complete widget contract" \
            "_claimable gates on one member ($contract_gate) -> a momentary widget aborts the overlay on move" ;;
    fab-missing*)
        bad "router drives a complete widget contract" \
            "FabButton lacks $contract_gate -> SIGABRT on a hub button press + drag" ;;
    *)    bad "router drives a complete widget contract" "$contract_gate" ;;
esac

hdr "2. which revision of each file"
for f in install.sh tools/uinput_watch.py tests/test_glasspad.py; do
    have=$(md5_of "$f")
    case $f in
        install.sh) table=$HASHES_install__sh ;;
        tools/uinput_watch.py) table=$HASHES_tools_uinput_watch_py ;;
        *) table=$HASHES_tests_test_glasspad_py ;;
    esac
    describe_rev "$f" "$table"
    verdict=$REV_VERDICT; label=$REV_LABEL
    case $verdict in
        latest)  ok "$f" "$have" ;;
        broken)  bad "$f" "$label" ;;
        # The label is what carries the meaning here. An older test file is no
        # longer merely "less coverage": the PRE-ABI revisions are the ones whose
        # input codes were wrong, so printing the label matters more than ever.
        ok)      warn "$f" "$label ($have)" ;;
        unknown) [ -n "$have" ] \
                     && warn "$f" "unrecognised revision ($have) - section 1 is the real check" \
                     || bad "$f" "missing" ;;
    esac
    # The suite legs must judge THIS file, not a count hardcoded elsewhere: the
    # same script has to report a clean 199-test tree, a clean 210-test tree and a
    # clean 230-test tree without calling either of the older two a regression.
    if [ "$f" = tests/test_glasspad.py ]; then
        case $verdict in
            latest|ok) tally=$REV_TALLY
                       if [ -z "$tally" ]; then
                           # A recognised revision with no recorded tally must not
                           # fall through to comparing against an empty expectation,
                           # which no runner can ever print.
                           SUITE_PIN=no
                           warn "suite tally to expect" \
                               "$label has no recorded tally: asserting no failures only"
                       else
                           WANT_OFF_P=${tally%%+*}
                           rest=${tally#*+}; WANT_OFF_S=${rest%%/*}
                           tally=${rest#*/}
                           WANT_XCB_P=${tally%%+*}; WANT_XCB_S=${tally#*+}
                           SUITE_PIN=yes
                       fi ;;
            *)         SUITE_PIN=no
                       warn "suite tally to expect" \
                           "unknown test revision: asserting no failures only" ;;
        esac
    fi
done
[ -x install.sh ] && ok "./install.sh is executable" "" || bad "./install.sh is executable" "chmod +x install.sh"

hdr "3. syntax"
bash -n install.sh 2>/dev/null && ok "bash -n install.sh" "" || bad "bash -n install.sh" "parse error"
python3 -m py_compile tools/uinput_watch.py 2>/dev/null && ok "uinput_watch compiles" "" || bad "uinput_watch compiles" ""
python3 -c "import glasspad, PyQt6.QtCore as Q; " 2>/dev/null \
    && ok "glasspad + PyQt6 import" "$(python3 -c 'import PyQt6.QtCore as Q; print(Q.PYQT_VERSION_STR)' 2>/dev/null)" \
    || bad "glasspad + PyQt6 import" "pip install pyqt6"

if [ "$FAST" = 0 ]; then
    hdr "4. the suite"
    # Bring up a throwaway X server FIRST. Six tests call libX11 open_display()
    # directly -- independent of QT_QPA_PLATFORM -- and pytest.skip themselves
    # when no server answers, so an offscreen run with no X anywhere reports
    # "230 passed, 6 skipped" on the 236-test revision, and a checker that
    # demanded 236 would call that a regression when it is only a missing
    # server. Start it before counting.
    #
    # The tests map a full-screen transparent overlay, so running them on the
    # session you are typing in steals input and flashes the UI. Xvfb is the
    # default; USE_MY_DISPLAY=1 forces your real session instead (the only way
    # to exercise your actual compositor, if that is what you want).
    # Never default to $DISPLAY. This fell through to the session display
    # whenever Xvfb was not installed (it is not part of a minimal Arch X
    # install), printed a reassuring 'ok display :0', and then ran a suite that
    # maps a full-screen transparent overlay -- stealing the user's input and
    # flashing UI across their desktop. Worse, a compositor (Wayland/Xwayland)
    # answers differently from Xvfb, so tests keyed to a specific display skip
    # and geometry checks fail on a tree that is fine. Using the real session is
    # now an explicit opt-in.
    d=
    if [ "${USE_MY_DISPLAY:-0}" != 1 ] && command -v Xvfb >/dev/null 2>&1; then
        # Check the SOCKET, not pgrep: `pgrep -f 'Xvfb :99'` matches the command
        # line of whatever shell is running this script, so it reports a server
        # that is not there.
        if [ ! -e /tmp/.X11-unix/X99 ]; then
            (Xvfb :99 -screen 0 1920x1080x24 >/dev/null 2>&1 &) || true
            i=0
            while [ ! -e /tmp/.X11-unix/X99 ] && [ "$i" -lt 10 ]; do
                sleep 0.5; i=$((i+1))
            done
        fi
        [ -e /tmp/.X11-unix/X99 ] && d=:99
    fi
    if [ "${USE_MY_DISPLAY:-0}" = 1 ] && [ -z "$d" ] && [ -n "${DISPLAY:-}" ]; then
        d=$DISPLAY
        warn "X server for the suite" "USING YOUR SESSION $d (USE_MY_DISPLAY=1): overlay tests will flash full-screen and may steal input"
    elif [ -n "$d" ]; then
        ok "X server for the suite" "display $d"
    elif command -v Xvfb >/dev/null 2>&1; then
        # Xvfb IS installed but :99 never came up, so say that instead of
        # telling the user to install a package they already have. A leftover
        # /tmp/.X99-lock from a killed server is the usual cause (seen while
        # testing this script: the lock outlived its Xvfb and every later run
        # silently lost the xcb leg).
        warn "X server for the suite" "Xvfb is installed but would not start on :99 - a stale /tmp/.X99-lock is the usual cause (remove it, or kill the Xvfb still holding :99)"
        warn "  what that costs" \
            "expect $(( WANT_OFF_P - 6 )) passed + 6 skipped offscreen; xcb + verify_x11 legs skipped"
    else
        warn "X server for the suite" "no Xvfb found - install it (Arch: sudo pacman -S xorg-server) for the xcb leg"
        warn "  what that costs" \
            "expect $(( WANT_OFF_P - 6 )) passed + 6 skipped offscreen; the 6 are libX11 tests wanting a server on :99, and the xcb + verify_x11 legs are skipped. Your own \$DISPLAY is deliberately NOT used: these tests map a full-screen transparent overlay."
    fi

    # No pytest? tools/run_tests.py is the bundled stdlib runner and prints the
    # SAME tallies (verified here: 210 offscreen, 206 passed/4 skipped under
    # xcb), so a missing package is not a reason to skip any part of this
    # battery. Choosing once lets both suite legs and the leak check agree.
    # Is a LITERAL :99 reachable? That is a different question from whether
    # $d is set, and the offscreen tally depends on the first, not the second:
    # six libX11 tests call x.open_display(":99") by name, four of them with
    # no open_display(None) fallback, so they can only be satisfied by :99.
    # The offscreen leg is even started without DISPLAY, so a Wayland session
    # with Xwayland on :0 has nothing that can answer them.
    #
    # Earlier it was derived as "is $d empty", which is false for every
    # Wayland desktop that has any X at all -- so their six legitimate skips
    # were reported as a suite failure ("expected 238 passed, 0 skipped").
    # Probe the socket the way the tests do, as the same user: if WE cannot
    # connect, neither can they, and "no" is the honest answer either way.
    x99=$(python3 -c "
import socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(0.5)
try:
    s.connect('/tmp/.X11-unix/X99'); print('yes')
except OSError:
    print('no')
" 2>/dev/null || echo no)
    if [ -n "$d" ] && [ "$x99" != yes ]; then
        warn "  what that costs" "$d answers but not :99, so 6 libX11 tests skip on both legs; start Xvfb on :99 to run them"
    fi
    if [ "$x99" = yes ]; then off_nosrv=no; else off_nosrv=yes; fi

    if python3 -c "import pytest" 2>/dev/null; then
        RUNNER="python3 -m pytest tests -q"; RUNNER_NAME="pytest"
    else
        RUNNER="python3 tools/run_tests.py"; RUNNER_NAME="run_tests.py"
    fi
    # The assignment must live INSIDE the string handed to eval. Writing
    #   QT_QPA_PLATFORM=xcb eval "$RUNNER"
    # looks equivalent and is not: under dash the prefix applies to the eval
    # builtin and never reaches the child, so the "xcb" leg silently ran
    # offscreen and reported the offscreen tally.
    suite="QT_QPA_PLATFORM=offscreen $RUNNER"
    suite_name="offscreen $RUNNER_NAME"
    run_suite "$suite"
    out=$RUN_SUMMARY
    judge_suite "$suite_name" "$out" "$WANT_OFF_P" "$WANT_OFF_S" \
        "$off_nosrv" "$SUITE_PIN"

    if [ -n "$d" ]; then
        # Prove the leg really ran on X before believing its tally: the suite
        # skips 4 tests under xcb that pass offscreen, so an identical count
        # would mean the platform never got through.
        # BOTH assignments belong inside the eval string. `DISPLAY=$d eval "..."`
        # is the same dash trap and drops DISPLAY, which makes Qt abort() on a
        # xcb run with no server -- a core dump that looks like a GlassPad bug.
        eff=$(eval "DISPLAY=$d QT_QPA_PLATFORM=xcb python3 -c \
\"import os;print(os.environ.get('QT_QPA_PLATFORM'),os.environ.get('DISPLAY'))\"")
        case $eff in
            xcb\ $d) ok "xcb handoff (platform + DISPLAY)" "$eff" ;;
            *) bad "xcb handoff (platform + DISPLAY)" "child saw '${eff:-nothing}', want 'xcb $d'"
               out="" ;;
        esac
        if [ -n "$eff" ] && [ "$eff" = "xcb $d" ]; then
            run_suite "DISPLAY=$d QT_QPA_PLATFORM=xcb $RUNNER"
            out=$RUN_SUMMARY
            # 234/4 was measured WITH a server on :99. Against a display that
            # is not :99 the four tests with an open_display(None) fallback
            # still pass but the two that name :99 outright skip, giving a
            # tally that matches no pinned revision. Reuse the existing
            # "assert zero failures only" mode rather than pin a number
            # inferred from a stand-in server and never measured here.
            judge_suite "xcb $RUNNER_NAME (z-order)" "$out" "$WANT_XCB_P" "$WANT_XCB_S" \
                no "$([ "$x99" = yes ] && echo "$SUITE_PIN" || echo no)" "$WANT_OFF_P"
        fi
        # verify_x11 prints one line per check plus a verdict; on failure the
        # verdict alone says only 'N FAILED', so keep the whole transcript and
        # show which checks failed.
        #
        # QT_QPA_PLATFORM must be set HERE, like the two legs above. It was not,
        # so this was the only Qt leg that inherited it from the caller: running
        #   QT_QPA_PLATFORM=offscreen tools/verify.sh
        # started verify_x11 on the offscreen plugin, which made it exit 2 with
        # "needs the xorg-server-xvfb package" -- a message about a missing
        # package on a machine that had Xvfb running and had just passed the xcb
        # leg above. The hint was wrong twice over: nothing was missing, and the
        # variable that broke it was one the user had exported. An environmental
        # leak must not be reported as a missing dependency.
        RUN_FULL=$(DISPLAY=$d QT_QPA_PLATFORM=xcb timeout 120 python3 tools/verify_x11.py 2>&1)
        out=$(printf '%s\n' "$RUN_FULL" | tail -1)
        case $out in
            *"ALL PASS"*) ok "tools/verify_x11.py (display $d)" "$out" ;;
            *) bad "tools/verify_x11.py (display $d)" "${out:-failed}"
               printf '%s\n' "$RUN_FULL" | grep -iE "^(FAIL|WARN)" | head -14 \
                   | sed 's/^/      | /' ;;
        esac
    else
        warn "xcb pytest + verify_x11.py" "skipped: no X server and no Xvfb"
    fi

    hdr "5. no virtual pad leaked by the tests"
    # This is the check that used to die: FileNotFoundError: '--'
    # After `run --` the next word must be a real executable: uinput_watch hands
    # argv straight to subprocess, so a shell assignment there is exec'd as if it
    # were a program name ("QT_QPA_PLATFORM=offscreen": No such file). Same shape
    # as the original '--' bug. Export from a subshell instead -- no `env` needed.
    out=$( (QT_QPA_PLATFORM=offscreen; export QT_QPA_PLATFORM \
            && exec python3 tools/uinput_watch.py run -- $RUNNER) 2>&1 )
    # TestUinputWatcher runs uinput_watch on itself, so several verdicts appear;
    # show only the last few to keep the report readable.
    printf '%s\n' "$out" | grep -E "command exit|^clean:|^LEAK|rror" \
        | tail -3 | sed 's/^/          /'
    # One signature only means the fix is absent. An earlier revision keyed on
    # any "Traceback", which accused a WORKING watcher of the '--' bug whenever
    # the child failed for an unrelated reason (missing pytest, a red test):
    # the watcher prints the child's traceback and then still reports its own
    # verdict, so plumbing and outcome have to be judged apart.
    if printf '%s' "$out" | grep -q "FileNotFoundError.*'--'"; then
        bad "uinput_watch run --" "execs a program named '--': the fix is missing"
    elif ! printf '%s' "$out" | grep -q "command exit:"; then
        bad "uinput_watch run --" "child never ran: $(printf '%s\n' "$out" | grep -i 'rror' | head -1)"
    elif printf '%s' "$out" | grep -q "^LEAK"; then
        bad "uinput_watch run --" "a GlassPad device reached the input layer"
    elif printf '%s' "$out" | grep -q "^clean:"; then
        ok "uinput_watch run --" "child ran via $RUNNER_NAME, no device leaked"
    else
        bad "uinput_watch run --" "no verdict printed"
    fi
    out=$(timeout 300 python3 tools/probe_uinput.py 2>&1 | grep -E "construction attempts")
    case $out in
        *"attempts: 0"*) ok "probe_uinput" "$out" ;;
        "") bad "probe_uinput" "no count printed" ;;
        *) bad "probe_uinput" "$out" ;;
    esac
fi

hdr "6. your machine's uinput state (this is what the container cannot test)"
if [ -e /dev/uinput ]; then
    if [ -w /dev/uinput ]; then ok "/dev/uinput" "$(ls -l /dev/uinput | awk '{print $1, $3":"$4}')"
    else bad "/dev/uinput" "exists but not writable by you -> sudo ./install.sh, then re-login"; fi
    # NOT '^uinput$': /proc/modules lines read "uinput 28672 2 - Live 0x0...",
    # so an anchored end-of-line pattern never matches and every host looks like
    # it needs modprobe.
    if grep -q '^uinput ' /proc/modules 2>/dev/null; then
        ok "uinput module loaded" "$(grep '^uinput ' /proc/modules | cut -d' ' -f1-3)"
    else
        warn "uinput module loaded" "run  sudo modprobe uinput"
    fi
else
    bad "/dev/uinput" "missing: run  sudo ./install.sh  (writes modules-load.d + the udev rule)"
fi
# No `tr`: it is not guaranteed here, and a missing tr left $groups empty, which
# reported "not a member" on a machine that was. Plain shell word splitting does
# the job, and `set --` keeps it POSIX.
groups=$(id -nG 2>/dev/null)
in_input=0
for g in $groups; do [ "$g" = input ] && in_input=1; done
if [ "$in_input" = 1 ]; then ok "you are in group input" "$groups"
else warn "you are in group input" "groups are [$groups] - re-login after sudo ./install.sh"; fi
# The decisive check, and the one this container can never run: it creates the
# actual pad and reads identity + axis ranges back from the kernel.
if [ -w /dev/uinput ]; then
    out=$(timeout 120 python3 tools/probe_real_pad.py --hold 0 2>&1 | tail -1)
    case $out in
        *"RESULT: PASS"*) ok "probe_real_pad (real pad)" "$out" ;;
        "") bad "probe_real_pad (real pad)" "no verdict printed" ;;
        *) bad "probe_real_pad (real pad)" "$out" ;;
    esac
else
    warn "probe_real_pad (real pad)" "needs a writable /dev/uinput"
fi
if python3 -c "import evdev" 2>/dev/null; then
    # Name what to look for. Reading events back proves the device works; the
    # handlers line proves nothing about xpad, which cannot bind a virtual
    # device (README: "What binds this device -- and it is not xpad"). joydev
    # is the handler that appears, and its presence is the pass condition.
    ok "python evdev (read the pad back)" \
        "expect handlers evdev + joydev; xpad never appears here"
    if grep -A1 -i "glasspad\|X-Box 360" /proc/bus/input/devices 2>/dev/null | grep -q .; then
        ok "a live pad is registered" "$(grep -i 'Name=' /proc/bus/input/devices | grep -i 'x-box\|glasspad' | head -1 | cut -c1-60)"
    else
        warn "a live pad is registered" "none right now - start GlassPad and check again"
    fi
else
    # Never tell the user to go looking for xpad: it is a USB driver, its probe
    # takes a struct usb_interface, and a uinput device has no USB parent, so
    # that check cannot pass on any machine. An earlier revision of this line
    # claimed exactly that and sent users hunting for a phantom defect.
    warn "python evdev (read the pad back)" \
        "pip install evdev  -> read events off /dev/input/eventN; the handler to expect is joydev, never xpad"
fi

# Which shaping path is this session actually going to use, and has anything
# checked it? Stated rather than probed on purpose: verify_wayland.py maps the
# overlay full-screen and can take input, which is exactly why the suite legs
# refuse to touch $DISPLAY unasked. So this only tells the user which of the two
# code paths their session selects, and names the one nothing automated covers.
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
    if [ -n "${DISPLAY:-}" ]; then
        ok "shaping path this session picks" \
            "DISPLAY is set, so xcb + XShape -- the path verify_x11.py covers"
    else
        warn "shaping path this session picks" \
            "native wayland + QWindow.setMask: no automated check covers this -- run QT_QPA_PLATFORM=wayland python3 tools/verify_wayland.py here (it flashes full-screen)"
    fi
fi

printf '\n'
if [ "$fails" = 0 ]; then
    printf '\033[32mRESULT: PASS\033[0m  (%s warnings)\n' "$warnings"
    [ "$warnings" != 0 ] && echo "warnings are environment gaps, not code failures."
else
    printf '\033[31mRESULT: %s FAILURE(S)\033[0m  (%s warnings)\n' "$fails" "$warnings"
fi
exit "$fails"
