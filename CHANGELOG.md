# Changelog

Version numbers here belong to the **Linux port**. The Windows original stays
1.0.2 forever, and docstrings throughout the tree that say "ported from GlassPad
1.0.2" name that source release — they are provenance, not this version, and do
not move on a bump. `test_version_strings_agree()` pins the distinction.
## Unreleased — a downloaded bundle can finish its own install

Uninstalled everything and ran the AppImage the way a stranger would: download,
`chmod +x`, run. Four things came out of it, one of them serious.

**The serious one: a fresh machine gets a pad that silently does nothing.** With
`/dev/uinput` unwritable — the state of every Linux machine that has never had
GlassPad — `PadService` chose `NullBackend()` in silence. The overlay mapped 20
surfaces, the controls drew and lit up under a finger, and **the self-test passed**,
because a null backend records its own events. Zero bytes of output in seven
seconds. A pad that conveys nothing is indistinguishable from an overlay that is
broken, and that was the whole first-run experience. Now: `PadService.fell_back_to_null`
is set only when the fallback was *not* requested (`--null-pad` stays quiet), which
prints `NO_UINPUT_HINT` once on stderr and puts `NO PAD … run: glasspad
--install-uinput` on the overlay itself.

**So the bundle now carries the fix.** An AppImage cannot contain
`/etc/udev/rules.d` — writing nothing outside itself is the premise of the format,
and a rule inside a squashfs image is not a rule — but it can carry the rule and
the script that installs it. Added `glasspad --install-uinput`, which escalates once
through `pkexec` and runs `install.sh --uinput`, and both packers now ship
`install.sh` plus `packaging/glasspad-udev.rules` and refuse to build without them.
Verified from inside each artefact: the dry run resolves
`/tmp/.mount_*/usr/share/glasspad/packaging/glasspad-udev.rules` and offers to copy
it to `/etc`, changing nothing.

- `packaging/glasspad-udev.rules` is now the only statement of the policy. It used
  to live in a heredoc inside `install.sh`; a second copy in a script is how a
  machine ends up running the version nobody reviewed, so `install.sh` copies the
  file or refuses, and the file passes `udevadm verify`.
- `install.sh --uinput` is the privileged half alone, safe to `--dry-run`
  unprivileged. Its final verdict asks the *account*, not root — via `su` —
  because root can always write the node and a root-side check reports success on a
  machine where the person who just authenticated still cannot make a pad.
- `TARGET_USER` now honours `PKEXEC_UID`. `pkexec` documents only that variable and
  resets the rest of the environment to root, so without it the installer adds root
  to `input` — which already has it — and leaves the actual user locked out
  while reporting a successful install.
- `--install-uinput --dry-run` exists because of how this feature was written: a
  probe of the refusal path pointed `$GLASSPAD_INSTALLER_DIR` at an empty directory,
  the resolver correctly fell through to the checkout, and the real installer ran
  through `pkexec`. Correct behaviour, live change to `/etc`, and no safe way to
  rehearse. A dry run elevates nothing at all.
- `install.sh --help` printed a fixed line range, so the four new header lines fell
  off the end; it now prints the header block whatever its length. Caught by
  `test_installer_still_parses_and_documents_itself`, which exists for exactly this.
- `--status` no longer advises "add yourself to the group that owns it": it names
  the command. The first line of output is also no longer a `PyGIWarning` about a
  missing `gi.require_version("Gdk", "4.0")`.

`tests/test_uinput_installer.py` (13) pins the rule contents (0660/group input, one
active line, never 0666 — a virtual pad is a keystroke injector as far as the
desktop is concerned), the refusal when the rule file is missing, exit 2 with no
traceback when the bundle ships no installer, that a dry run invokes `sh` and a real
run invokes `pkexec`, `PKEXEC_UID` resolution, and that an explicitly requested null
pad prints nothing. 22/22 suites green.
## Unreleased — the licence is readable, and says what this build is

The `Open-source licenses` pill reported success and opened nothing. It now opens a
scrollable reader inside the overlay, and the notices describe the port rather than the
Windows product it came from.

**Why the pill never worked**, recorded because the failure shape is the lesson:
`Gtk.UriLauncher.launch()` is asynchronous and was called with no callback, so PyGObject
started it and discarded the result — the `except` could never fire, and the status line
printed the path as though it had opened. There was also nothing to open *with*:
`get_default_for_uri_scheme("file")` is `None` on a normal desktop; a path is not a URI
scheme.

**In-app reader instead of a launch.** A layer surface lives on the OVERLAY layer, so a
launched editor appears *under* the controls — from a tablet that is indistinguishable
from a crash. So: a read-only `TextView` in a `ScrolledWindow`, in the same centred-panel
shape as the About box and save dialog, showing `LICENSE` then
`licenses/THIRD-PARTY-NOTICES.txt`. Missing text is reported in the panel with the paths
tried, because an empty box reads as "this app has no licence".

**Notices rewritten for this build.** The old `THIRD-PARTY-NOTICES.txt` was HIDMaestro's
— usbip-win2 drivers, DsHidMini report descriptors, SHA-256 sums of `.sys` files. None of
it belongs to a port that creates its device through `uinput`. It now separates what is
loaded from your system (GTK 4, Cairo, Pango, PyGObject, gtk4-layer-shell, Python — none
redistributed), what is only *numbers* copied from the kernel headers, and the one thing
actually shipped in an artefact: the AppImage type-2 runtime, with an honest note about
the offline build borrowing it and `mksquashfs`. Windows-original material moved to
`licenses/windows-original/` and ships in neither artefact.
`licenses/GlassPad-LICENSE.txt` was byte-identical to `LICENSE` and is gone.

**Packaging is now conditional on the licence being present.** Both builders abort if a
document is missing instead of `|| true`-ing it away, and the artefact suites import the
packed `bundle.py` from inside each artefact to prove the resolver works in layouts that
exist only after packing. Checked from inside: `.run` puts the docs under `app/`, the
AppImage under `usr/share/doc/glasspad/` while the code sits in
`usr/share/glasspad/glasspad/` — which is exactly the layout mismatch the old
`dirname(__file__)` guess could not survive.

Caught on the way, by the guards added earlier: an unused `DOCS` constant in `bundle.py`
(the reader names what it reads), and three new attributes missing from
`tests/_overlay_stub.py`. My own first version of the notices test was also wrong — it
failed on `usbip-win2` appearing in the file, where it appears precisely to say it is
*not* used; the check is now positional (nothing Windows-only above the exclusion
heading) instead of presence-based.

Suite: 21 suites, 217 stdlib + 87 pytest-style + 92 live checks, zero skips.

## Unreleased — About box credits what it ports

The About box said "by Dansunmi", copying the client's own credit line onto a build that
is not the client. It now reads `Linux port of:` above `github.com/dansunmi/GlassPad` —
the author's name is still on screen, in the URL, and in the MIT notice shipped as
`LICENSE`. Guarded by a test, because this is a decision about attribution rather than
styling, and "restore parity with the original" is how it would silently come back.

The "Open-source licenses" pill still does nothing, and is now documented as broken in
`_open_licenses()`: `Gtk.UriLauncher.launch()` is async and is called without a callback,
so the failure is discarded while the status line prints the path as if it had worked. No
handler for the `file://` scheme exists here regardless, and neither bundle carries
`licenses/` -- so the plural in the label was never satisfiable in a shipped artefact.
The replacement is an open decision, not an edit.

## Unreleased — dead spec constants deleted, one number corrected

Seventeen unreferenced constants removed from `layer_style.py`. They were not spare
documentation; they were a competing spec. `PANEL_LABEL_COLOR`, `CONFIRM_BODY_COLOR` and
the rest spelled their colours the WPF way (`#AARRGGBB`) and were never wired to anything,
while the CSS sheet in `overlay_layer.py` — the thing that actually paints — carries the
converted values (`#AAC0D0FF` = alpha `0xAA` = 0.667 over `#C0D0FF`). Wiring them up, the
obvious tidy-up, would have recoloured the About box and the confirm dialog.

Found while checking them against `MainWindow.xaml`, both fixed in the CSS sheet with the
XAML element and attribute named alongside:

* **About box corner radius**: `InfoPanel CornerRadius="16"`, `SaveConfirmPanel 14`, and a
  single shared rule gave both 14. Now split, so the About box has the radius the client
  has.
* **Settings-panel padding**: XAML says `16,12`, the sheet paints `20px` across / `14px`
  down. Left as-is and *documented as a divergence* — the panel's visible size was tuned
  against captures, and changing it is an 8×4 px edit to a box a user looks at. A tidy
  number is not evidence.

`xd360.UINPUT_VERSION` was made load-bearing instead of deleted: the About box printed
`ABI v5` in prose next to a struct built from that constant, so a user's bug report could
quote a number nothing enforced. It is interpolated now, and `TestXbox360Abi` asserts the
caption tracks the constant — this port's recorded bug class is an ABI name that is right
and a number that is off by one. `BTN_0` / `BTN_TRIGGER` exist purely as a fence against
reusing those codes for Guide; a fence with nothing checking it is decoration, so their
kernel values are asserted too (verified: corrupting `BTN_MODE` to `0x120` fails four
tests).

`tests/test_orphan_constants.py` is the guard: any module-level `UPPER_CASE` constant in
the package must be loaded or attribute-accessed somewhere in `glasspad/`, `tests/` or
`tools/`, with an allowlist whose entries must state who reads them. It checks itself
against a synthetic tree (the control found nothing at all until the package directory was
parameterised — same silent-green shape this whole pass is chasing), and an allowlist entry
for a constant that later becomes used fails, so exemptions cannot fossilise.

Incident during the sweep, worth recording: the deletion script sorted its spans as tuples,
which ordered them by *name* rather than position, so each deletion shifted the offsets of
the rest. Five of seventeen went, and three constants that were in use — `FAB_FONT`,
`PILLBTN_RADIUS`, `CONFIRM_MIN_WIDTH` — went with them, plus lines from the font-fallback
comment. The file still parsed and the diff still looked like a cleanup. Caught by diffing
against git line by line rather than trusting the script's own report.

Suite: 21 suites, 215 stdlib + 86 pytest-style + 85 live checks, zero skips.

## Unreleased — the Qt backend is deleted, not quarantined

Eight modules gone: `window.py`, `widgets.py`, `layout.py`, `zones.py`,
`touch_router.py`, `editor.py`, `license_window.py`, `passthrough.py` — 136 KiB and a
third of the package bytes. They are in git history, and their names survive in
`glasspad/bundle.py` as `REMOVED_QT_MODULES`, which the artefact suites use to refuse
shipping them again. Keeping the exclusion list while the files existed was how a
second backend stayed half-shipped forever; keeping the *names* is what stops a
copy-paste out of history reinstating an untested path by accident.

A third of the package and none of it exercised: `--backend qt` inside a bundle used to
import half a toolkit that no test, no CI, and no artefact had touched in months. Every
verification tool for it had already been retired in the previous pass.

**What that cost, and what replaced it**

* 148 tests went with the code they tested. What is left runs **with no display at all** —
  `env -u DISPLAY -u WAYLAND_DISPLAY python3 tools/run_tests.py` gives 84 passed, 0 skipped,
  where the suite previously needed Xvfb on `:99` for six of them. Zero skips is the point:
  a green run with skips is green with holes in it.
* Two Qt cross-checks in `test_layer_geom.py` became pins. The element order is frozen
  (reordering it silently moves every saved `layout.json`'s rectangles, so the failure is the
  migration prompt, not a nuisance), and the default rectangles are frozen at the Windows
  numbers. Both verified negative: halving the d-pad to an invented 96 px fails the pin,
  which is precisely the bug the deleted cross-check was written to catch.
* `test_version_strings_agree` lost its About-box leg and gained a better one: the hub must
  interpolate the package constant rather than carry its own literal, which is the failure
  mode the original leg existed to catch.
* The doc-pointer guard now distinguishes a broken link from provenance. Eighteen live
  comments cite `window.py` for *which behaviour* was copied; rewriting them to "see git
  history" would trade an explanation for a shrug. Exempting `REMOVED_QT_MODULES` keeps the
  guard aimed at `NOTES.md`-style fiction.

**Bugs the deletion surfaced**

* `--version` and `--help` were owned by the Qt parser. Deleting the parser without the
  branch meant both fell through to `run_layer()` and **started a fullscreen transparent
  overlay** — a hang as far as any launcher is concerned, and invisible to the person who
  typed it because the surface is see-through. Answered before anything can map a surface,
  and guarded by a test that patches the overlay's entry point to raise.
* `take_backend` spun forever on any argument that was not `--backend`: the append branch
  forgot to advance the index. Diagnosed with `faulthandler.dump_traceback_later`, because
  "the CLI hangs" has a lot of explanations and only one stack trace.
* `tools/check_all.py` now fails a suite that reports no tally. One lost its `__main__`
  block during an edit, printed nothing, exited 0, and the tree was called green; an exit
  code cannot see a runner with no tests in it. `test_overlay_refs.py` was also made to
  print a count rather than "all clean", for the same reason.

## Unreleased — orphan-code pass (layer backend is what ships)

Deletions are recoverable: the tree is now a git repository, with the pre-cleanup
state captured as the first commit.

**Removed.** `tools/verify.sh` (689 lines), `tools/verify_wayland.py`,
`tools/verify_x11.py`, `tools/probe_uinput.py` — Qt-era verification that tested a
backend the bundles no longer contain, and the only thing pinning test counts so a
stale pin silently weakened the check. `TestVerifyToolTeardown` went with them.
128 lines of measurement scaffolding left `overlay_layer.py` (the
`GLASSPAD_*DEMO`, `SHOWPANELS`, `AUTOHUB`, `TOASTCYCLE` hooks); `GLASSPAD_SELFTEST`,
`TRACE` and `DEBUG` stay, because something has to drive the handlers.

**Corrected, not deleted.** `tools/run_tests.py` looked like a duplicate runner and
was nearly removed; it is the only thing that executes `tests/test_glasspad.py`, so
**257 tests were running all along** and `check_all.py` simply never called it. They
are wired together now: 20 suites, 204 stdlib tests + 257 pytest-style + 85 live
checks, one command. `--no-legacy` reports that suite as skipped instead of
resurrecting a run that can only fail.

**Fixed.** `install.sh --check` reported **PyQt6** as the dependency and never
mentioned `libgtk4-layer-shell` — the one library the shipped overlay cannot start
without; it now walks the real chain (PyGObject, GTK 4, layer-shell, compositor) and
labels PyQt6 optional. `tools/hit_test.py` hardcoded the Arch path for the layer-shell
library and now uses the same finder the app does. The doc-pointer test read raw
source, so `self.sh` parsed as a shell script and every runtime path literal looked
dangling; it reads comments and docstrings only, which is what it claimed to do, and
still catches a planted `NOTES.md`.

**Packaging.** The Qt backend's eight modules are excluded from both artefacts —
136 KiB, a third of the package bytes — listed once in `glasspad/bundle.py`.
`--backend qt` inside a bundle now explains the two possible reasons instead of
raising `ModuleNotFoundError`, and a test asserts it.

## 1.1.0 — 2026-11-06

First Linux release with the input ABI corrected against the kernel and the
display-server selection hardened. Nine fixes below are user-visible: one changes
which codes a game receives, two change whether the overlay starts at all, one
changes what `--status` prints about a session, two stop the overlay aborting on
input -- the first touch, then a hub button on either input path -- one makes
the Quit button actually quit, and one makes hold-to-disable reversible instead
of a one-way switch that ends the session. Plus one deliberate change away from
the Windows original: hold-to-disable is now a latching toggle.

### Fixed: X input ABI — hats, Guide button, sticks and triggers

The axis and button numbers this port declared were off in ways that were
invisible from the code: every constant had the right *name*.

| | was | now (kernel) |
| --- | --- | --- |
| D-pad | `ABS_HAT0X/Y` on `ABS_MT_*` slots | `0x10` / `0x11` |
| Guide | `BTN_0` | `BTN_MODE` `0x13C` |
| Sticks | swapped axes | `ABS_X/Y` and `ABS_RX/RY` |
| Triggers | shared an axis | `ABS_Z` / `ABS_RZ`, `0..255` |

A wrong hat number does not raise; it silently lands on multitouch tracking, so
the D-pad appeared to work while no consumer could read it. This is checked by
**importing** `glasspad.xd360` and comparing values, never by grepping — a grep
for `ABS_HAT0X` matches happily whether the value is `0x10` (a hat) or `0x39`
(`ABS_MT_TRACKING_ID`).

Verified end-to-end on real hardware: the created device reports
`PRODUCT='3/45e/28e/110'`, name `Microsoft X-Box 360 pad`, phys
`glasspad/virtual0`, handlers `evdev + joydev`, and all eight axis ranges as
specified.

### Fixed: an X server above display :31 was invisible

`_x_socket_number()` scanned a fixed window, `X0`..`X31`. `:99` is exactly where
`Xvfb` and most second-head/VNC setups put a server, so a machine with a
perfectly good X display read as "no X at all" — the launcher fell through to
native Wayland, or failed to start.

Socket names are now parsed instead of probed against a range, so any display
number is found, and non-numeric entries are skipped. `X٣` (non-ASCII digit) is
rejected deliberately: `int()` accepts it and would have produced a nonsense
`DISPLAY=:3`.

### Fixed: a stale socket from a dead server aborted the launch

The socket directory was trusted by filename. A server that was killed — an
`Xvfb` whose verify run timed out, an **Xwayland whose session ended** — leaves
its socket inode behind, and the file looks exactly like a live server. Pointing
Qt at a server nobody listens to does not degrade gracefully: the xcb plugin
cannot initialise and Qt `abort()`s.

So one leftover inode turned a launch that native Wayland would have served into
a core dump, on precisely the Wayland laptops that have no X fallback to cover
for it. Each candidate is now probed with a real `connect()`; `ECONNREFUSED` and
`ENOENT` prove absence, while `EACCES`/`EPERM`/timeout are *not* evidence of
absence — a socket you may not ask about can still be reachable, and X11 also
listens on the abstract socket `\0/tmp/.X11-unix/XN`, which is immune to
filesystem permissions. Lowest-numbered-still-wins is unchanged.

### Also fixed: the bundled test runner silently ignored `autouse`

`tools/run_tests.py` reimplements the slice of pytest the suite uses, for Arch
desktops where PyQt6 is a system package and pytest is not. It resolved fixtures
only by signature parameter, so an `autouse` fixture — which by definition asks
for nothing — never ran. Silently. A `@pytest.fixture(autouse=True)` was instead
crashing the whole suite at import with `TypeError`.

`autouse` is now implemented, instantiated before the fixtures named in a test's
signature, with class scope shadowing module scope. Unknown fixture keyword
arguments now raise rather than being ignored, so a typo or an unimplemented
option cannot quietly disable the guard a test depends on. Session scope is
honoured for named fixtures too, not only the builtin `qapp`.

### Also fixed: the test suite's result depended on the host

`TestForceXcb` asserted what plugin the launcher picks, which means reading
`/tmp/.X11-unix` — the developer's machine, not a fixture. Cases passed only
because the container they were written in had never had an X socket. Measured
on one revision: **235 passed / 3 failed** on a host running `Xvfb :99`,
**228 / 4** on a host without it. Same file, same code, different verdict.

The class is now hermetic: an `autouse` guard refuses both the real socket
directory *and* a real `connect()` into it, and a fixture builds **real**
sockets in a temporary directory — `live=` bound and listening, `stale=` bound
then closed — so liveness is exercised rather than mocked. Tallies are now
identical on hosts that differ.

### Also fixed: diagnostics sent users after an impossible check

`verify.sh` told users to install `evdev` to "confirm xpad sees the device".
`xpad` is a USB driver: `xpad_probe()` takes a `struct usb_interface` and
`xpad_table` matches on USB vendor/product. A `uinput` device has no USB parent
at all — its sysfs path is `/devices/virtual/input/inputN` — so that check
**cannot pass on any machine**. The message now says to expect `evdev + joydev`
and that `xpad` never appears. `README.md` gained a section on why.

`verify.sh` also derived "no X server anywhere" from "is `$DISPLAY` set", which
is false for every Wayland desktop that runs Xwayland — so their six legitimate
skips were reported as a suite failure. It now probes `/tmp/.X11-unix/X99` the
way the tests do.

That same class of defect turned up in `--status` itself, which printed:

```
pass-through: True | XWayland client without XShape
```

The detail says pass-through is unavailable; the flag on the same line says it
works. Both came out of one branch. `server` was `"wayland"` because the XShape
probe could not run — `$DISPLAY` named nothing, and Qt on a non-xcb plugin is
refused the connection outright by `Xlib._qt_is_xcb()` — while `pass_through`
reported the `QWindow.setMask` capability of the *Wayland* plugin, an API a
process Qt is drawing through Xwayland is not using. Reporting `"wayland"` had
collapsed two opposite findings into one: a native client with no `$DISPLAY` at
all, and an X client whose probe failed. `SessionInfo` now carries `via_x11`, and
the reason says which of the two happened instead of blaming a missing X
extension the user could only fix by exporting `DISPLAY` correctly.

### Fixed: the overlay aborted on the first touch

`python3 -m glasspad` on hardware died the moment the screen was touched:

```
File "glasspad/touch_router.py", line 176, in eventFilter
AttributeError: 'QTouchEvent' object has no attribute 'touchPoints'
fish: Job 1, terminated by signal SIGABRT (Abort)
```

This is the overlay's entire purpose, so 1.1.0 was unusable on a touchscreen
while reporting 250 passing tests. Four defects were stacked in one function, and
only the first was reachable — each hid the next:

| Shipped | Qt6 reality | |
| --- | --- | --- |
| `event.touchPoints()` | `event.points()` | the Qt5 name was removed |
| `tp.pos()` | `tp.globalPosition()` | also absent; would have aborted next |
| `int(tp.state())` | `tp.state().value` | PyQt6's `State` flags reject `int()` **and** `& int` |
| `state & TOUCH_CANCELLED` (0x20) | `QEvent.Type.TouchCancel` | Qt6 has no cancelled *state*; it is an event type |

The last one never crashed — it just never ran, so a compositor stealing a
gesture left the trigger held down for the rest of the session, which is exactly
what the comment above it claimed to prevent. `TOUCH_UP`/`TOUCH_HELD` were
removed with it: nothing read them, and a "release or cancel" alias is what made
that look handled.

**Why 250 tests missed it.** `TestTouchRouter` drove the router with stand-ins
built on the belief that a real `QTouchEvent` cannot be constructed from Python.
That was true of Qt5's nested `QTouchEvent::TouchPoint` and false of its
replacement: Qt6's `QtGui.QEventPoint` is constructible. The stand-ins therefore
supplied `touchPoints()`, `pos()` and a bare int state — the Qt5 shape — and the
router agreed with them while disagreeing with Qt. The doubles tested the mock.

They now use real `QEventPoint`/`QTouchEvent` objects for single-contact cases,
so the accessor names are an assertion. Stand-ins remain only where two contacts
must be distinguished, because a synthesized real point reports `id() == 0` and
an invalid `uniqueId()` however it is built — two of them are indistinguishable
to Qt, so multi-contact bookkeeping cannot be exercised with real objects.

**Why it aborted rather than erroring.** An exception escaping a Python
`eventFilter` is not reported and swallowed; PyQt aborts the process (verified:
`Aborted (core dumped)` from a raised `AttributeError`). One wrong accessor was
never a dropped touch — it was the overlay and the game session. Touch dispatch
is now contained and the first fault is printed once with a traceback, so a
future mismatch degrades to mouse input and names itself instead of taking the
session down. `touchPoints()`, `pos()` and `int(state())` were each reintroduced
separately to confirm the suite goes red — six tests fail for each, so the
containment does not hide the regression.

### Fixed: an incomplete widget contract aborted the overlay on a hub button

The containment above did its job on hardware — the app printed the fault once and
kept running instead of dying — and the traceback it printed named the next defect:

```
File "glasspad/touch_router.py", line 314, in _move
    target.control_move(QPointF(target.mapFrom(self._window,
AttributeError: 'FabButton' object has no attribute 'control_move'
```

The router drives three methods on whatever it grabs — `control_press`,
`control_move`, `control_release` — but `_claimable()` gated on `control_press`
alone. `FabButton`, the hub's own button, implemented press and release and not
move, so a hub button was grabbed on press and the very next event raised inside
the event filter.

**It was not touch-only, and that was worse.** Both input paths call the same
`_move()`, and only the touch path was wrapped in the `try`/`except` added above.
A mouse click plus a small drag on a hub button therefore aborted the process with
**no containment and no touchscreen involved**. The report string compounded it:
on a mouse fault it printed "falling back to mouse input" about the input that had
just failed, so it now reads "pointer dispatch failed; that input source is
degraded".

Enumerating every claimable widget at runtime rather than reading class
definitions is what established the scope: `FabButton` was the only partial one,
and it was reachable. Three changes, one per half of the defect class:

- `FabButton.control_move()` exists and is a deliberate no-op. A momentary button
  has no position to track; it does **not** un-press when the contact slides off,
  because the hub's buttons are release-to-activate wherever they were pressed,
  and matching `QPushButton`'s grab rule here would change what a slide-off does
  on hardware.
- `_claimable()` requires **all three** members via a new `TouchRouter.CONTRACT`.
  A control that cannot be moved is not claimable at all, so the next momentary
  widget degrades to mouse-only instead of reintroducing the crash.
- The mouse path is now contained like the touch path.

`test_every_claimable_widget_implements_the_whole_contract` asserts the contract
across every target the router is actually offered **with the hub open**, not a
hand-picked list, and `test_dragging_on_a_hub_button_does_not_abort` drives press
→ move → release through real Qt dispatch for both `touch` and `mouse`. Each half
was mutated separately: deleting only `FabButton.control_move` fails the contract
test with `claimable but incomplete: FabButton missing ['control_move']` while the
drag test correctly stays green — the gate is *supposed* to keep that widget
unclaimed. Reverting the gate on top of it reproduces the original fault, failing
both cases with messages naming the path: `touch path still faults on a hub
button` and `mouse path still faults on a hub button`.

### Fixed: every colour in window.py has its alpha and red channel swapped

Found while choosing a second colour for the disable toggle. `MainWindow.xaml`
spells colours `#AARRGGBB`; `QColor`'s four-integer constructor is `RGBA`. Most
of `window.py` copies the XAML digits straight across, so the alpha lands in the
red channel and every hue shifts two places. `layout.py` is unaffected because it
passes the hex as a *string*, which Qt parses alpha-first exactly as WPF does —
and `editor.py` is correct because its author converted from C# `FromArgb(a,r,g,b)`
by hand. The bug is confined to `window.py`'s integer literals.

| XAML | intended | as shipped |
| --- | --- | --- |
| `#F01a1a2e` `PANEL_BG` | `#1a1a2e` a=240 | `#f01a1a` a=46 |
| `#DD1a1a2e` `EDIT_BG` | `#1a1a2e` a=221 | `#dd1a1a` a=46 |
| `#CC208060` `GREEN` | `#208060` a=204 | `#cc2080` a=96 |
| `#CC404055` `GREY` | `#404055` a=204 | `#cc4040` a=85 |
| `#CCFF3333` Quit | `#ff3333` a=204 | `#ccff33` a=51 |
| `#CC4080FF` Edit | `#4080ff` a=204 | `#cc4080` a=255 |
| `#CCA02020` menu open | `#a02020` a=204 | `#cca020` a=32 |

The visible effect: the About, save and edit panels are near-transparent red
rather than solid dark navy — which is also why text on them needs the explicit
`#AAC0D0FF` styling it has — and the menu button's "open" state is a 12%-alpha
olive instead of dark red, so the hub looks unchanged when the menu expands.

**Not fixed in this release.** It is purely cosmetic, it changes how the whole
overlay looks, and no test pins any of these values, so there is no evidence
here about which appearance users currently expect. It is recorded so the next
person picking up a colour does not repeat the investigation.

### Fixed: the Quit button hid the overlay but left the process running

Reported from hardware: clicking the hub's Quit button made the overlay vanish and
`python3 -m glasspad` kept running in the terminal until the user pressed Ctrl-C.
Nothing in the shutdown path was broken — the button is wired to `self.close()`,
the window closed, and `closeEvent` ran `pad.dispose()` and `router.shutdown()`.
The defect is that `MainWindow` is a `Qt::Tool` window, and the attribute behind
`quitOnLastWindowClosed` (`WA_QuitOnClose`) is **False** for Tool windows, which
Qt excludes alongside `Qt::ToolTip` and `Qt::Popup`. So closing the only window
left zero visible windows and `app.exec()` carried on running with no window,
no input and no way out from the desktop — the window was gone, which is exactly
what makes it look like the app had quit.

Measured before fixing, in a subprocess with a watchdog rather than in-process:
a plain `QWidget` whose `close()` is the only action returns from `exec()` on its
own; the same widget given the overlay's exact flags
(`FramelessWindowHint | WindowStaysOnTopHint | Tool`) does not, under **offscreen,
xcb, and with no `DISPLAY` at all**. Setting `WA_QuitOnClose` on the overlay
restores the exit in all three.

`TestQuitOnClose` pins both halves: closing the overlay must end the child process,
and closing the **licenses dialog** must *not* — that dialog is a `Qt::Dialog`,
which already had `quitOnClose` True and was therefore the only window Qt counted
all along, so a fix that made every window count would quit the session whenever a
user shut the license list. Removing the attribute fails both new assertions with
the reported symptom verbatim: `STILL-RUNNING overlay_visible=False`.

**Why 256 tests missed it.** They asserted that `close()` disposes the pad — which
it always did — and none asked whether the *event loop ended*. An earlier entry
here concluded the app's teardown was sound; that was measured on the
SIGINT/SIGTERM path, where `app.quit()` closes windows for you, and the conclusion
was then over-extended to the Quit button, which does not go through `app.quit()`.

### Fixed: hold-to-disable was a one-way switch and needed a restart

Reported from hardware: clicking the hub's `⊘` button hid the overlay and it could
not be brought back — the button had become a way to turn the overlay off once and
then nothing at all.

The cause was re-entrancy, and the trace of one press says it plainly:

```
_set_overlay_enabled(False)  [grabs=1]   <- press hides all 16 elements
  _set_overlay_enabled(True)  [grabs=1]  <- nested, fired from inside release_all()
-> visible=0/16  grabs=0                 <- outer call resumes and hides again
```

Disabling calls `router.release_all()` so nothing is left pressed on the virtual
pad. That iterates the live grabs and calls `control_release()` on each — and the
grab currently held *is* the `⊘` button, whose `released` signal is wired to
`_set_overlay_enabled(True)`. So the release-all released its own driver: enable
ran nested inside disable, then disable resumed and hid every element last, and
`_grabs` had been cleared, so the user's actual button-release arrived to find no
grab and never emitted `released` at all. Every later click hit-tested fine and
reproduced the same nested pair, which is why the button looked dead rather than
merely sticky.

`release_all(keep=...)` now exempts one control and drops each grab *before*
releasing it, so re-entering the router cannot see a grab that is being unwound.
`_set_overlay_enabled` passes the disable button.

Lifting everything else is unchanged and still required — the companion test holds
`A` on one contact and presses `⊘` on another, and `A` must go to 0 or the virtual
pad would keep reporting a pressed button for the rest of the session. Verified
round-tripping on the mouse path, the touch path, and under `TouchCancel` (the
compositor stealing the gesture mid-hold, where no release will ever arrive), plus
the two-finger case with distinct contact ids.

*(The paragraph above originally closed by saying the re-enable had to reach the
window through that button's own release. That described the momentary wiring this
release replaces — see "Changed" below. The re-entrancy guard stays, now so the
button keeps its pressed look through the tap rather than to route a signal.)*

### Changed: hold-to-disable is now a latching toggle

The Windows original is momentary: `TouchDown` hides the overlay and `TouchUp`
shows it, so it only stays away while a finger covers the button. That is awkward
on the device this app exists for — you cannot see what you are uncovering, and
there is no way to park the overlay off for a while. `⊘` now latches: one press
hides it and it stays hidden until pressed again.

The state has to live on the window (`_overlay_enabled`) rather than being
implied by which way the contact is moving, and three consequences needed
decisions:

- **Wired to `pressed`, not `clicked`.** Measured rather than assumed:
  `FabButton.control_move` deliberately does not un-press when the contact leaves
  (WPF `TouchDevice.Capture` parity), so a press that slides 300px away still
  emits `released` *and* `clicked` on release. A `clicked` handler would let a
  finger dragging off on the way out re-show the overlay for a reason the user
  cannot see. Mutating the wiring to `clicked` fails three of the new tests.
- **The button shows its state**, because a hidden overlay leaves only `⊘` and
  the menu on screen: orange means visible, slate means hidden, and the tooltip
  in the hidden state names the way back rather than repeating the action.
- **Entering edit mode un-latches.** Editing positions you cannot see is not
  worth supporting, and the edit panel's Reset calls `GamepadLayout.reset()`,
  which `show()`s every element — so without this, a reset from edit mode would
  reveal the overlay while `⊘` still claimed to be off.

`DISABLE_COLOR` is `QColor("#CCE07030")`, a string, deliberately. `QColor(r, g, b, a)`
is RGBA, so the natural-looking `QColor(0xCC, 0xE0, 0x70, 0x30)` for XAML's
`#CCE07030` yields `#cce070` at 19% alpha — yellow-green and almost invisible,
not orange at 80%. `layout.py` avoids this by passing hex as strings; see the
separate entry below for the sites that still have it wrong.

**Why the old tests missed it.** `test_disable_hides_all_and_releases` called
`window._set_overlay_enabled(False)` and then `(True)` by hand. That exercises the
hide/show and the pad reset perfectly and never once goes through the router, so
the release-all that un-pressed its own driver was never reached. The tests added
here drive `⊘` the way the desktop does — `QApplication.sendEvent` with a real
`QMouseEvent` — driving `control_press()` directly is not enough either, because
that bypasses the grab table where the fault lives.

Both directions are measured, not asserted. Reverting the wiring to the momentary
contract (press hides, release shows) fails exactly three tests —
`test_disable_button_latches_across_presses`,
`test_disable_stays_hidden_after_release`, and
`test_disable_ignores_a_release_off_the_button` — 262 passing, with the user's
symptom verbatim in the failure: 16 of 16 controls visible again the instant the
contact lifts. Dropping `keep=` from the `release_all()` call fails
`test_disable_lifts_a_held_button` on its own, because the toggle releases itself:
`grab_count()` goes 1 → 0 and `released` and `clicked` both fire from inside the
path those signals drive.

### Also fixed: `verify_x11.py` blamed a missing package for an inherited variable

`QT_QPA_PLATFORM=offscreen tools/verify.sh` reported

```
FAIL  tools/verify_x11.py (display :99)   (needs the xorg-server-xvfb package)
```

on a machine that had Xvfb running and had passed the xcb leg two lines earlier.
The `verify_x11` leg was the only Qt leg that did not set `QT_QPA_PLATFORM`, so it
inherited the caller's `offscreen`, Qt selected the offscreen plugin, and the tool
exited 2 with advice about a package nothing was missing. The leg now pins the
platform the way its neighbours do; verified in both directions — with the
variable exported and with it unset the leg reports `RESULT: ALL PASS`.

### Also fixed: the verify tools exited without tearing the overlay down

`verify_x11.py` and `verify_wayland.py` mapped a full-screen click-through
overlay, printed a verdict and called `sys.exit()` with the window still alive.
`closeEvent` is what runs `pad.dispose()` and `router.shutdown()`, so neither
ran: the event filter stayed installed on the `QApplication`, and a live X
window was left for the interpreter to unwind around.

Severity was measured before the fix, because "the tool crashes after
`ALL PASS`" is only release-blocking if the shipping app shares the flaw. It
does not. `app.quit()` does close windows — on xcb with a real mapped window,
SIGTERM produced `pad.dispose` then `router.shutdown`, exit 0, three runs; and
`python3 -m glasspad` started as shipped and terminated by signal returned `rc=0`
three times with no core dump. The gap belonged to the tools alone.

Both now `close()` before exiting and print a `teardown` marker ahead of the
verdict, so a fault during teardown reads as a teardown fault instead of a check
that silently never printed. A tool that skips the teardown cannot catch a bug in
it.

### Added

- `verify.sh` checks a **stale socket is not treated as a display** by making
  one and asking the module what it sees, then confirming a live `:3` is still
  found — so the check cannot pass by vetoing everything.
- `shaping path this session picks`, naming which of the two code paths a session
  will use and which one no automated run covers.
- README: window-rule instructions for stacking (previously only in the
  `--status` output), the native-Wayland empty-region limitation, and a
  coverage table for both shaping paths.
- `LICENSE` at the top level (the MIT text was only in `licenses/`), and
  `.gitignore`.
- `test_version_strings_agree()`: `--version`, the About box and the package
  must agree, and must match the number this file documents.
- `TestPadInitRace` covers `PadService.init()` being disposed mid-startup. The
  behaviour was already correct and remains unmodified; what it lacked was any
  test. Losing needs a slow backend, and every existing test injects
  `NullBackend`, whose constructor cannot lose.

### Tests

265 passing. Measured, not computed, on each environment:

| Environment | Tally |
| --- | --- |
| offscreen, X server on `:99` | 265 passed |
| offscreen, no X server anywhere | 259 passed, 6 skipped |
| offscreen, Wayland session (Xwayland, no `:99`) | 259 passed, 6 skipped |
| xcb on `:99` | 261 passed, 4 skipped |
| xcb on another display, no `:99` anywhere | 259 passed, 6 skipped |

The middle row was measured this revision rather than carried forward, because it
could depend on `open_display(None)`: four of the six libX11 tests fall back to
it, and a Wayland desktop does have a live Xwayland for that fallback to reach.
It does not, and the reason is the platform rather than the server — with
`QT_QPA_PLATFORM=offscreen`, `open_display()` refuses by design (that refusal has
its own test), so the fallback cannot rescue the six and they skip whatever a
session has running. Reproduced with a live server on `:98`, no `:99`, and
`DISPLAY=:98` exported: `259 passed, 6 skipped`.

The last row was re-measured on a server at `:98` with `:99` absent, because an
earlier attempt left a server on `:99` running and reported 252 passed: the skip
that depends on `:99` being *unreachable* passed instead, and the number looked
like the xcb-on-`:99` row. A tally is only evidence for the environment that was
actually true while it ran.

A green suite still does not prove the touch path: it proves the accessors the
tests use. Where a mock can be built, so can the real thing — assert against the
real thing.

Both runners report identical tallies: real `pytest` and the bundled
`tools/run_tests.py`. Each fix above was mutation-tested — the revert of a fix,
and the over-correction of it, both have to fail a test.

### Known limits

- **No automated run covers the native Wayland shaping path.**
  `QWindow.setMask` → `wl_surface.set_input_region` needs a compositor, so
  `tools/verify_wayland.py` cannot run under `Xvfb`. It has now been run by hand
  once on a real compositor (1707×1067 at scale 2.0) and the client half passed
  in full — 670 rectangles placed on the surface, every control centre claimed,
  gaps and the `A` bounding-box corner passing through — but that is one
  machine, not a regression guard. A KDE session with Xwayland running picks xcb,
  which *is* covered; the case nothing tests is Xwayland switched off.
- **The compositor half of Wayland pass-through cannot be checked by any tool.**
  Wayland offers no way to read a surface's input region back, so
  `verify_wayland.py` proving the region is correct on the client side is as far
  as automation goes. Whether a touch on a gap actually reaches the game is a
  one-tap manual check on the target machine.
- **Native Wayland cannot express an empty input region** — Qt has no
  empty-but-not-null `QRegion` — so with every control hidden the overlay still
  covers the game. On xcb the same state passes through cleanly.
- **The bundled runner can segfault on exit when one test is selected in
  isolation.** `tools/run_tests.py -k whole_contract` dies with `SIGSEGV` after
  printing `1 passed` in roughly a third of runs, when that test leaves the hub
  open. It is not the defect above and it is not in the shipping app, all three
  measured over repeated runs: real `pytest` on the same test 0/20, `app.exec()`
  with the hub open then `close()` 0/20, and the full suite under the bundled
  runner 0/6 over a purpose-built repeat loop (the only tally `verify.sh` judges). `os._exit()` -- which skips Python and Qt teardown entirely --
  still crashed, and valgrind reported no GlassPad-side invalid access, so the
  fault surfaces after the verdict, not during the test. Run the whole suite (as
  `verify.sh` does) rather than trusting an isolated `-k` leg's exit code.
- `tools/probe_real_pad.py` is the only check that proves the device exists as
  the kernel sees it, and it needs a writable `/dev/uinput`. In a container or on
  CI, every other check proves logic only, never device creation.
- **A finger-generated touch is still unverified on hardware.** Every test
  synthesises its own `QTouchEvent`, so the events reach the router with ids,
  states and positions *we* chose. What only a real device can confirm: that Qt
  hands the overlay touch events at all on this compositor, that
  `globalPosition()` is in the space `mapFromGlobal()` expects once the window is
  full-screen and scaled (the 2.0 panel here was measured only through
  `QScreen.geometry()`), that per-finger ids stay distinct across a multi-finger
  press, and that a stolen gesture really arrives as `TouchCancel`. A green suite
  proves the accessors exist and are called correctly; it cannot prove the events
  Qt produces match what the tests feed it.
- `xorg-server` is a **test** dependency. The app lazy-loads `libX11`/`libXext`
  and starts no server.
- No `pyproject.toml`/`PKGBUILD` yet, so releases are tarball + `install.sh`
  rather than an AUR package.
