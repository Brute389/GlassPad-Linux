# GlassPad (Linux)

Touchscreen → virtual Xbox 360 gamepad overlay for Linux, ported from GlassPad
1.0.2 (Windows/WPF + HIDMaestro). The overlay draws touch controls on top of a
game and forwards touches to a kernel `uinput` device that identifies itself as
a wired Microsoft 360 pad, so anything that accepts a controller accepts it.

**Current version: 1.1.0** (Linux port) — see
[CHANGELOG.md](CHANGELOG.md). `glasspad --version` and the About box
report the same number, and `test_version_strings_agree()` fails if they
ever stop. The Windows original stays 1.0.2; the docstrings naming it are
provenance, not this version.

HIDMaestro has no Linux build, so the transport is `uinput`; everything else —
zone mapping, `ToHM()` normalisation, layout format, resting state — is
preserved from the original. A `layout.json` written by the Windows app loads
here unchanged.

## Requirements

* Python 3 and PyQt6. Developed on CPython 3.12 / PyQt6 6.11; the suite has also
  been run green against PyQt6 and Qt **6.12.0**, and both files compile clean
  on 3.13. Nothing uses a runtime-evaluated PEP 604/585 annotation (every module
  defers them via `from __future__ import annotations`), so an older 3.x will
  very likely work, but it has not been checked.
* Avoid `return` inside a `finally` block. It is a `SyntaxWarning` on newer
  CPython and is slated to become a `SyntaxError`, and it also silently swallows
  any in-flight exception — teardown belongs after the `try`/`except` chain so
  the handlers set a return code.
* Linux with the `uinput` module (the installer enables it)
* Optional: `python-evdev` for reading the pad back, `Xvfb` for the xcb test leg

## Install

```bash
sudo ./install.sh          # uinput module + udev rule + icons + desktop entry
newgrp input               # or log out and back in — group membership is needed
./install.sh --check       # report state, change nothing
```

Other modes: `--user` (desktop entry + icons into `~/.local`, no root, no `/etc`
writes), `--autostart`, `--dry-run`, `--uninstall`.

Note that `PREFIX` relocates **only** the desktop entry and icons. The module
config and udev rule are absolute `/etc` paths and ignore `PREFIX`, so
`PREFIX=/tmp/x ./install.sh` is not a sandbox. Use `--user` for a run that
touches nothing outside `$HOME`.

Run it from a checkout:

```bash
python3 -m glasspad            # the overlay
python3 -m glasspad --status   # environment + backend report
python3 -m glasspad --null-pad # UI only, no virtual device
```

## Packaging: one file, and what a file cannot carry

```bash
python3 tools/make_bundle.py          # -> dist/glasspad-<version>-<arch>.run  (~615 KiB)
```

The `.run` is one executable file: copy it to another machine, run it. It unpacks once
under `$XDG_CACHE_HOME/glasspad/<version>-<payload hash>` and reuses that tree, so the hash
in the path matters — keying it on the version alone means a rebuild on the same version
keeps running the old payload forever.

```bash
./glasspad-1.1.0-x86_64.run                    # the overlay
./glasspad-1.1.0-x86_64.run status             # what it found, and what is missing
./glasspad-1.1.0-x86_64.run install-desktop    # menu entry pointing at this file
./glasspad-1.1.0-x86_64.run uninstall-desktop
```

**The build tool executes what it wrote** — `--version`, `status`, a repeat run, and
`install-desktop` inside a sandboxed `$HOME` — and refuses to call the artefact done
otherwise. That gate has already caught a launcher bug which would otherwise have shipped:
`\${XDG_DATA_HOME}` reached the shell escaped and stayed literal, so `install-desktop` tried
to `mkdir` a directory named `${XDG_DATA_HOME:-`.

### AppImage

```bash
./tools/make_appimage.sh                        # AppDir, then a real .AppImage
./tools/make_appimage.sh --dir                  # stage and run it, no packing
./tools/make_appimage.sh                        # GLASSPAD_NO_BORROW=1 to refuse borrowed tools
dist/GlassPad-1.1.0-x86_64.AppImage --appimage-extract-and-run status
dist/GlassPad-1.1.0-x86_64.AppImage status      # mounted, via squashfuse
```

It needs **no repository and no package**. A type-2 AppImage is `[runtime ELF][squashfs]`,
and on any disk that has run an AppImage before, both halves already exist:

| piece | where it came from here | |
| --- | --- | --- |
| `mksquashfs` | inside the Spotify flatpak bundle (`files/bin/mksquashfs`) | runs on the host against host libraries; 4.7 |
| runtime ELF | the first 944632 bytes of `StabilityMatrix.AppImage`, up to its squashfs superblock | the donor list is tried in order; one donor had no valid superblock |
| `appimagetool` | not needed | it glues the same two things; if it *is* installed, the script uses it |

`tools/mk_appimage_offline.py` does the borrowing and **prints what it took from where**,
because that is a dependency on someone else's bundle rather than a managed one. Two things
learned doing it, both encoded in tests:

* **no padding between the two halves.** The runtime reports the payload offset as the exact
  end of its own image; 1544 bytes of well-meant alignment produced *"This doesn't look like
  a squashfs image"*.
* **the squashfs superblock's magic is 4 bytes, not 8.** Parsing it as 8 shifts every later
  field by four and rejects every genuine superblock — which is how the first version
  reported "no squashfs found" in files that obviously had one.

With a network, the durable answer is still `sudo pacman -S squashfs-tools` plus a real
`appimagetool` (`APPIMAGE_EXTRACT_AND_RUN=1 appimagetool -n GlassPad.AppDir …`); the offline
route is a way of not being blocked, not a substitute for package management.

Profiles work through the image, which is what makes them usable at all:

```bash
"$HOME/Downloads/GlassPad-Linux/dist/GlassPad-1.1.0-x86_64.AppImage" \
    --appimage-extract-and-run profile fighter && %command%
```

### Why nothing from the host is bundled

Three things decide whether this app works, and a payload cannot carry any of them:

| | |
| --- | --- |
| **libgtk4-layer-shell** | Bundleable in principle. Wrong in practice: the copy must match the host's `libwayland` *and* GTK 4, and when it does not you get a blank overlay rather than an error. So `glasspad/bundle.py` **finds** it instead — the bundle's own `lib/` first, then `ld.so`, then Arch, Fedora and Debian-multiarch paths — and says which rule matched. `GLASSPAD_LAYER_SHELL=/path` overrides. |
| **A compositor with `zwlr_layer_shell_v1`** | KWin, Hyprland, Sway, wlroots: yes. Stock GNOME: no. No packaging format supplies this, which is why a Flatpak of this app would be worse, not better: the sandbox filters layer-shell off the session bus *and* denies `/dev/uinput`. |
| **Write access to `/dev/uinput`** | A permission, not a library. AppImage sandboxes nothing, so this is the same question as running from a checkout — but it is the one thing that fails on a machine that has never had GlassPad, so `--status` says it out loud. |

One finding from building this, since it bites anything that asks the question:

> Loading the typelib normally makes `Gtk4LayerShell.is_supported()` return **False for the
> lifetime of the process** — same display, same compositor, pumping the GLib context does
> not help. Load the same file `RTLD_GLOBAL` first and it returns True. So `--status` now
> performs the same preload the app does. Before that it was reporting this machine's KWin
> as having no layer-shell, over the exact overlay that was up on screen.

`--status` is also backend-aware now: `--backend layer` reports layer-shell, the compositor,
`/dev/uinput` and the profile store; `--backend qt` keeps reporting XShape and pass-through.
Exit status is 0 only when the overlay could actually run — a diagnostic that always exits 0
is not a diagnostic.

## Check your tree

`tools/verify.sh` is self-contained: no root, no edits, no paths to configure.
From the project root:

```bash
sh tools/verify.sh            # full battery
sh tools/verify.sh --fast     # state + tooling, skip the suite
sh tools/verify.sh /some/tree # check a different checkout
```

It starts its own `Xvfb` on `:99` rather than using your desktop — the suite
maps a full-screen transparent overlay and would steal your input. Set
`USE_MY_DISPLAY=1` to force your real compositor instead.

Fixes are judged by **content**, never by hash. File hashes only say *which*
revision a file is, so that an older-but-correct checkout is reported as older
rather than broken. The input-ABI check imports the module rather than grepping
it, because this port's bug class was constants whose names were right while
their numbers were off by one — a grep for `ABS_HAT0X` matches whether it is
`0x10` (a hat) or `0x39` (`ABS_MT_TRACKING_ID`).

## Tests

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest tests -q
python3 tools/run_tests.py            # same suite, no pytest installed
```

Both report the same tallies. Current revision: **265 passed**
offscreen, **261 passed + 4 skipped** under xcb.

Tallies legitimately differ by environment, which is not a failure:

| Situation | Tally |
| --- | --- |
| offscreen, an X server on `:99` (what `verify.sh` starts) | `265 passed` |
| offscreen, no X server anywhere | `259 passed, 6 skipped` |
| offscreen, a Wayland session (Xwayland `:0`, no `:99`) | `259 passed, 6 skipped` |
| xcb on `:99` | `261 passed, 4 skipped` |
| xcb on another display (`USE_MY_DISPLAY=1`) | `259 passed, 6 skipped` |

A Wayland-only desktop hits rows 3 and 5 without installing anything, and that
is not a failure to fix: six tests call `open_display(":99")` by name, four of
them with no fallback, so only a literal `:99` can satisfy them. `xorg-server`
is a **test** dependency, never a runtime one — the app lazy-loads `libX11` /
`libXext` and starts no server — so GlassPad itself runs the same whether or
not the suite can reach `:99`. On Arch it is the package containing `Xvfb` that
you want (often already present as a dependency).

The 6 are not gated by `QT_QPA_PLATFORM`: they call libX11 `open_display()`
directly and skip themselves when no server answers. The 4 under xcb are
offscreen-only tests that skip the other way round.

`tools/run_tests.py` exists because this ships to desktops where PyQt6 is a
system package and pytest often is not; a safety net needing a package you lack
is not a safety net. It reimplements the slice of pytest the suite uses
(session/function fixtures with yield teardown, `parametrize`, `approx`,
`skip`).

### Layer-backend suites

The layer-shell backend is covered by standalone files, each of which runs on its own
without pytest:

| File | Covers |
| --- | --- |
| `test_layer_geom.py` | geometry, the layout file, parity with the Qt path |
| `test_resize_geom.py` | edit-mode sizing: grab zone, floors, clamping, reset |
| `test_fit_screen.py` | rotation fit, the design-size field, `apply_screen` |
| `test_turbo.py` | the square wave, arming rules, the watchdog exemption |
| `test_watchdog.py` | deadlines and the release each one causes |
| `test_stick_tuning.py` | parity with `StickZone.Apply()`, monotonicity, the nub following the deflection |
| `test_lockout.py` | a contact we released cannot press again until it ends; the next touch works |
| `test_edge.py` | the band refuses the bezel and never the button, on the real layouts and after a rotation |
| `test_profiles.py` | name validation, fallback order, the claim file, and when a switch is allowed |
| `test_bundle.py` | finding `libgtk4-layer-shell` on Arch, Fedora, Debian, or inside a bundle — and a wrong override named, not swallowed |
| `test_status_cli.py` | `--status` describes the backend you run, and exits nonzero when the overlay could not |
| `test_bundle_artifact.py` | the built `.run` executes from an empty environment; the launcher header parses as POSIX `sh` |
| `test_press_look.py` | what a press looks like (round stays round, no clipping) |
| `test_press_release.py` | what a press *does* (pad events, gating, easing) |
| `test_dpad_variants.py` | d-pad indicator, cross and press variants |
| `test_toast.py` | on-screen status toasts, and the autofire mark (pixels, not flags) |
| `test_overlay_refs.py` | AST check: every `self.x` has something behind it |

```bash
python3 tools/check_all.py          # every suite + the live self-test, exit 0 only if green
python3 tools/check_all.py --no-live
python3 tools/check_all.py -v       # full output of whatever failed
```

`check_all.py` exists because the loop that used to be documented here could not fail:

```bash
for t in tests/test_*.py; do python3 "$t" | tail -1; done   # exit status: always 0
```

A pipeline reports **tail's** status, so a suite that dies on import printed its
traceback and the loop still reported success. `check_all.py` also treats "could not
start" as a failure rather than silence, which is the difference between a missing
dependency and a passing test suite.

They are separate files because `tests/test_glasspad.py` — the original 212 tests —
imports pytest, which the application does not need and which is not installed here.
It is reported as failing to run, deliberately: dormant coverage is not coverage, so
the parts that guard the layer backend were rewritten to run on their own.

There is also a **live self-test**, which is the only check that can fail a missing
hub handler (pixels cannot):

```bash
GLASSPAD_SELFTEST=1 python3 -m glasspad --null-pad
```

It drives the real hub, edit mode, a resize, a reset, a rotation, a turbo hold and the
quit guard against live layer surfaces, and asserts on what the pad and the compositor
received — currently 50 checks. It prints `selftest done` as its last word; a run that
stops early is a failure, not a shorter pass.

**Two hand-built Overlays broke twice each.** `test_watchdog.py` and `test_turbo.py`
each built an `Overlay` with `__new__` and set the attributes they happened to need, so
every new piece of state on the class broke both -- `lockout` and `edge_margin` produced
fifteen failures from a change that was correct. There is one fixture now,
`tests/_overlay_stub.py`, and `test_overlay_refs.py` compares it against
`Overlay.__init__` by AST: the next new attribute is reported by name and told where to
go, instead of arriving as an `AttributeError` in a test that looked nothing like it.

### Two ways to get a misleading result from the bundled runner

**`-k` matches substrings, not pytest expressions.** `-k "contract or drag"`
selects nothing and prints `0 passed, 0 failed, 0 skipped` — which looks like a
clean run of a filtered set rather than no tests at all. Pass one substring, or
run the whole suite.

**Editing `tests/test_glasspad.py` invalidates two pins in `tools/verify.sh`.**
Section 2 identifies the tree by that file's md5. After any edit — even a
docstring one, which changes no behaviour — add the new hash to
`HASHES_tests_test_glasspad_py` *and* update `WANT_OFF_P` / `WANT_XCB_P` in the
same change. Miss it and `verify.sh` prints `unrecognised revision` and quietly
drops to asserting *zero failures only*, so a regression that adds skips still
passes. This bit twice while writing the 259-test revision.

`install.sh` and `tools/uinput_watch.py` are hash-pinned too, but only
`tests/test_glasspad.py` decides the suite tallies, so only editing the test file
can weaken a run. `tools/verify.sh` pins nothing about itself: a run reports the
tree's hashes and nothing about the checker's own revision, so a change to the
checker is verified by running it, not by a pin.

### A third way: a screen pixel diff measures the compositor

A full-screen before/after diff of the overlay is not evidence of anything. Measured on
this machine: two frames **0.6 s apart in the same run** differ by **143k pixels** when
a layer surface maps or a wallpaper slideshow advances, and by **0 pixels** when nothing
maps. The overlay's own ink is 1–3k pixels, so the noise is fifty times the signal and
the sign of the difference is whatever the desktop felt like.

What works instead:

1. **Measure inside the box you changed**, not the screen. The toast is 38×23 logical
   for one word; the ring around a 75 px button is ~1.4k pixels.
2. **Toggle inside one run**, and report the noise floor beside the signal. The
   `GLASSPAD_TOASTCYCLE` hook exists for this: it shows the toast, hides it, forever, and
   logs which state it is in so the capture can be lined up with it. Same-state frames
   differ by 0 px in that box; toggling the toast changes 332 px of it and adds 196 px of
   bright ink. That is the toast, and the zero next to it is why the number means
   something.
3. **Capture with `spectacle -b -n -o file.png`.** There is no `grim` on this machine and
   there never was — no package owns `/usr/bin/grim`, and `/usr/bin`'s mtime predates the
   session — so a script that shells out to it fails with `2>/dev/null` and leaves you
   diffing files that were never written.

## Tools

| Tool | What it proves |
| --- | --- |
| `tools/probe_real_pad.py` | Creates the **real** pad and reads identity, `PRODUCT` and every axis range back out of the kernel, then destroys it and verifies nothing was left. Needs a writable `/dev/uinput`. |
| `tools/uinput_watch.py` | Snapshots kernel input devices and reports any pad a test leaked. `run -- <cmd>` wraps the suite so nothing hides between steps. |
| `tools/probe_uinput.py` | Runs the suite with opening a real pad made impossible and counts attempts. Matters on a machine where `/dev/uinput` *is* writable. |
| `tools/verify_x11.py` | End-to-end xcb: is the input region right? Maps full-screen, so aim it at a server you own (`Xvfb :99`). |
| `tools/verify_wayland.py` | Same for the Wayland plugin. Cannot use Xvfb — needs a real compositor. |
| `tools/run_tests.py` | The suite with no pytest. |
| `tools/check_all.py` | Every suite plus the live self-test; exit status is real, and a suite that cannot start counts as failing. |
| `tools/render_stick_curve.py` | The stick response as **millimetres of thumb travel**, per preset. |
| `tools/make_bundle.py` | Build **and run** the single-file `.run` bundle; refuses to call it done until the artefact has executed cleanly. |
| `tools/make_appimage.sh` | Stage the AppDir (validated desktop entry, `AppRun`), run it, then pack if the tooling exists — and say plainly what is missing when it does not. |
| `tools/glasspad-profile` | Claim a profile for the overlay (`--seed`, `--list`, `--status`, `--clear`). Exits 0 unless the *arguments* were wrong — a launcher must never fail because of the overlay. |
| `tools/render_style.py`, `tools/render_dpad.py`, `tools/render_press_previews.py` | Offscreen renders of the painters, with numerical checks — the reliable half of visual verification. |
| `tools/pad_probe.py`, `tools/hit_test.py`, `tools/layer_probe.py` | What the kernel receives, which surface took a tap, and whether a layer surface survives at all. |

A green test suite does **not** prove no pad was created: `PadService` turns
backend errors into a status line, so a test that reaches the backend passes
either way. That is why `uinput_watch` and `probe_uinput` exist.

## Using the overlay

The glyphs are the Windows original's, chosen to stay compact at 44 px.

**Always visible, top centre:**

| Glyph | Function |
| --- | --- |
| `≡` | Open the menu. Becomes `×` while open. |
| `⊘` | Hide the overlay. **Latches** — press again to bring the controls back. Orange while visible, slate while hidden. |

**Inside the menu:** `✕` quit · `✎` edit layout · `ℹ` about.

**Bottom centre row** — the three small circles are Back, Guide and Start:

| Glyph | Gamepad button | Appearance |
| --- | --- | --- |
| `⊟` | Back | grey, 52 px |
| `⊙` | Guide | **yellow**, 62 px — the larger middle one |
| `⊞` | Start | grey, 52 px |

`⊙` sends `BTN_MODE`. Nothing on a Linux desktop claims a virtual gamepad's
Guide the way the Windows shell claims it for Game Bar, so it can look inert;
with Steam running it opens the Steam Overlay. It is also the one button the
Windows app could not verify — `PadTest --live` reads back through
`XInputGetState`, which does not expose Guide.

A game that ignores Guide entirely is normal, not a fault in the overlay. To see
the event for yourself, `pip install evdev` and read the node while the overlay
runs:

```bash
python3 - <<'PY'
import evdev
dev = next(d for d in evdev.list_devices()
           if d.phys == "glasspad/virtual0")
print("watching", dev.name, "-- press the yellow circle")
for ev in dev.read_loop():
    if ev.type == evdev.ecodes.EV_KEY and ev.value in (1, 0):
        print(ev.code, evdev.categorize(ev))
PY
```

Expect `316 Key.BTN_MODE` on press.

## Edit mode

Open it from the hub (`✎`), or start there: `python3 -m glasspad --edit`. Every
control grows a small knob in its bottom-right corner.

| Gesture | Result |
| --- | --- |
| drag a control's body | move it |
| drag the knob | resize it — proportions kept, 32 px floor, clamped on screen |
| **tap** the knob | arm or disarm turbo for that control (the knob turns amber, with a `T`) |

A tap and a drag are told apart by travel (4 px), so arming turbo never grows a
control by accident and resizing never arms it. Sizing sits on one corner on
purpose: four knobs on a 75 px button cover a third of its face, and a drag meant to
move would resize instead.

Whether a *mapped* layer surface can change size in place is a compositor question, so
it was measured before it was built on (`tools/resize_probe.py`): a plain size request
works under KWin — no unmap, no replacement surface.

## Holding, and what happens when nobody tells us to release

A press ends on release, on gesture-cancel, on hiding the overlay and on quit. Every
one of those is something the compositor told us. The cases that ruin a game session
are the ones where nobody tells us: a game grabbing input exclusively, the screen
blanking with a thumb on the stick, a suspend while a control is held. On a gamepad a
stuck control is not cosmetic — a held stick is running in circles, a held trigger is
throttle — and the overlay is translucent by design, so you are not looking at it.

So anything held longer than `--hold-limit` seconds (**8** by default, `0` disables)
releases itself and says so in the status line. It is one-shot per press: someone
genuinely holding a charge shot is not cut off again every eight seconds.

Controls armed for turbo are exempt while they fire. A turbo button is held for
minutes deliberately, which is precisely the pattern the watchdog exists to break up;
without the exemption the safety net would switch the feature off eight seconds into
the one thing it is armed for.

## A release the finger does not know about

The watchdog releases a control that has been held too long. For buttons that worked.
For sticks it did nothing at all, and the numbers say so -- left stick axis 0, measured
on the live overlay before the fix:

```
held 1.0   ->   watchdog releases: 0.5   ->   one more drag-update: 1.0
```

GTK keeps sending `drag-update` for a contact it still owns, and each one put the stick
back. The release lasted until the next motion event, i.e. not at all: the thumb stayed
at full deflection for as long as it was on the glass, which is precisely the failure
the watchdog exists to break. The hide button had the same shape -- it drops every
contact, and a finger still down pressed its control back onto a hidden overlay.

One rule fixes both: **a contact that ends is a release; a contact we release *for* is a
release plus a lockout**, and the lockout is lifted when that contact actually ends --
including via `cancel`, and including ending after we already released it. The control is
inert until the finger lifts and the next touch behaves normally, which is what makes it
safe to leave on rather than a feature to be afraid of. Motion for a control that is not
held is dropped in the same place, and `Lockout.dropped` counts them.

### Edges, and what this hardware will not tell us

Real palm rejection needs contact size or pressure. This touchscreen gives neither to a
layer surface: a palm and a fingertip arrive as identical points, so anything claiming to
tell them apart on this machine is guessing. What is real is that the last few pixels of
panel collect thumb roots and heels of hands, and that edit mode makes it easy to park a
control flush against a bezel.

So a press in the outer `--edge-margin` pixels is refused -- **8 px** by default, `0` for
off -- with one hard limit: a band never takes more than **a quarter of a control's face**
on that axis. A rejection rule that swallows a button is worse than no rule, so a small
control sitting flush at the edge stays usable.

The honest part: on the app's default layout, on this machine's saved layout, and on the
defaults after a rotation, **no control comes that close to an edge**, so the gate
refuses nothing in the layout you have. That is asserted against the real frames in
`tests/test_edge.py`, because this feature's failure mode is silently eating legitimate
presses in somebody's hand-made layout.

## Rotation, docks and display scale

Layout rectangles are absolute pixels, and the Windows client never recorded which
screen they were absolute *pixels of*. This port writes that down (`screenWidth` /
`screenHeight`) and uses it: launch on a different screen than the layout was authored
for and everything is refitted, and rotating or docking **while the overlay runs** is
followed live.

- sizes scale **uniformly** (`min` of the two axis ratios), so buttons stay round and
  the drawn figure keeps agreeing with the hit region;
- a control that sat **against an edge stays against that edge**, because it was put
  there to be reachable from the frame of the device;
- nothing is ever left off screen.

Refitting always starts from the authored geometry, never from wherever the controls
currently are, so rotating to portrait and back returns the layout exactly rather than
drifting a rounding step per rotation. That was proven with a real
`kscreen-doctor output.eDP-1.rotation.left`: the overlay reported
`1707x1067 -> 1067x1707 (x0.63)`, and after restoring the rotation its controls were
pixel-identical to before.

A file written before the field existed is taken at face value — no surprise rescaling
of a hand-placed pad. `--screen WxH` simulates a different screen for testing.

## Turbo

Hold an armed control and it fires repeatedly, at `--turbo-hz`, **20 Hz** by
default, with `0` turning turbo off for the whole overlay. Only
buttons and triggers can be armed; the stick and the d-pad are refused, because
rapid-firing those is not a thing any game reads.

20 Hz is the rate USB console controllers shipped with: fast enough that no game polls
past it, slow enough that anything with a one-frame input cooldown still sees both
edges. Rate and arming live in the layout file (`turboHz`, and a per-element
`"turbo": true`), so each game's layout keeps its own.

```bash
python3 -m glasspad --turbo A,X --turbo-hz 30     # arm from the command line
```

## Stick response

Full deflection now arrives **31 px** from the centre of the app's 119 px stick —
52 % of the way to the rim — because that is what the original does. It did not used to.
This port normalised the thumb offset by the disc's *radius* (59.5 px) where
`StickZone.cs` normalises by `MaxTravel`, so full deflection needed **1.9× the travel**
the original asks for: a thumb at the client's own saturation point got 0.52 here.

That also made the picture lie. The nub was clamped to `MaxTravel` on its own, so past
31 px the ring had stopped moving while the pad was still climbing. The nub is now
computed *from* the deflection rather than from the finger, so the two cannot disagree
by construction instead of by two pieces of arithmetic happening to match.

Two notes on where the numbers come from:

* `StickZone.cs` writes `(Outer - Inner) / 2` with a comment saying `28 px`. The
  arithmetic says **31** and the compiled value is 31. Quoting that comment instead of
  evaluating it is how the parity target got guessed wrong here once, so the constant is
  evaluated in code and a test asserts `MAX_TRAVEL == (OUTER - INNER) / 2`.
* Parity is checked point by point against a transcription of `StickZone.Apply()`, on
  axes and diagonals, including positions outside the disc.

Three dials sit on top, and presets for people who cannot feel a saturation fraction:

```bash
python3 -m glasspad --stick-preset crisp
python3 -m glasspad --stick-sat 0.6 --stick-dead 0.05 --stick-curve 0.8
python3 tools/render_stick_curve.py        # the table below, in millimetres
```

```
                                  thumb travel for deflection of
    tuning                     25%     50%     75%    100%   (mm)
    ------------------------------------------------------------------
    app                   1.4      2.7      4.1      5.4  sat 52%  dead 0%  curve 1.00
    soft                  1.4      2.6      4.0      5.4  sat 52%  dead 4%  curve 0.85
    crisp                 1.0      1.8      2.8      4.1  sat 39%  dead 6%  curve 0.62
    wide                  2.0      3.4      4.6      5.7  sat 55%  dead 2%  curve 1.25
```

Measured in millimetres on purpose: a console thumbstick's *entire* travel is about
7 mm, and the app's stick is 20.8 mm across, so the useful question is "how far does
my thumb move", not "what fraction of a radius". `soft` adds a small dead zone because a
resting thumb creeps on glass and a game with a 0.05 threshold reads that as walking;
`crisp` is for games where the only question is run/dodge; `wide` is for driving and
cameras, where snapping to full is worse than being slow. Edit mode offers the four as a
row of pills with the numbers underneath, and the choice is saved in the layout file as
`stickTuning`.

## Profiles: a layout per game

A profile is not a new format. Everything a profile needs is already in the layout file —
the sixteen rectangles, opacity, d-pad mode and style, press treatment, turbo arming and
rate, stick response, and the screen it was authored for — so a profile **is** a layout
file with a name, in `~/.config/GlassPad/profiles/`.

```bash
python3 tools/glasspad-profile --seed      # makes 'default' and 'fighter' from your layout
python3 tools/glasspad-profile --list
python3 tools/glasspad-profile fighter     # claim one, from anywhere
```

Two ways in:

* **A launcher says.** Steam launch options: `glasspad-profile fighter && %command%`, or
  the same in a RetroDECK per-game command. Absolute path works too.
* **The hub.** Open the menu (`≡`) and press `◈`; it steps through the profiles and wraps.

A switch changes *everything in that file*, and it goes through the same path as a
rotation: refit from the profile's own authored screen size, resize the mapped surfaces,
re-place them. A profile laid out in landscape and claimed while the tablet is in portrait
fits instead of landing half off-screen. Turbo arming and stick response come along, so
per-game autofire needs no feature of its own.

**A switch never lands while a control is held.** If something is pressed when the claim
arrives, the change is queued and applied the moment nothing is held — moving a control out
from under a thumb is the one thing profiles must never do, and the cost of waiting is a
second or two.

Saving writes to the **active profile**, and rotates that profile's own backups.
`layout.json` stays as it was the day you started using profiles.

### Why not ask the compositor which window is in front

Because on this machine it cannot be asked, and every route that pretends otherwise is
silently wrong exactly when a game is up. Verified on KWin 6.8 / KDE here:

| Route | Result |
| --- | --- |
| `zwlr_foreign_toplevel_manager_v1` (what `kdotool`, `wlrctl` use) | absent — 68 interfaces in `wayland-info`, that one is not among them |
| KWin legacy JS scripting (`loadScript`) | loads, returns `0`, registers nothing on the bus, logs nothing. A no-op that reports success |
| XWayland (`DISPLAY=:0` is live) | `_NET_ACTIVE_WINDOW` returns a window with no `WM_CLASS` and no `_NET_WM_NAME`; a Wayland-native game is not in XWayland at all |
| Declarative KWin script package | the one remaining route, unproven, and the JS API it would replace was already removed once in 6.0 |

A launcher already knows what it launched. Telling us costs it one command, and the claim
is **explicit** — it cannot fire because a notification stole focus or the Steam overlay
blinked into existence. If a KWin script is ever made to work, it would write the same
claim file, so nothing here changes.

The claim is deliberately sticky: Steam launch options have no exit hook, so a reverted
layout mid-alt-tab is worse than the last game's profile staying up. `--profile-timeout
SECONDS` reverts to `default` after that long instead. `--layout FILE` names a file
directly and turns claiming off for the run, because someone hand-editing a layout must
not have a launcher change it halfway through.

## Feedback you can see, and a layout you cannot lose

**Status messages appear on screen.** The watchdog's `released A after 8s held`, the
`A: turbo armed`, "defaults restored" — all of them used to go only to stdout, which
during a game is a terminal nobody has open. They now toast above… below the hub for a
moment and a half, as shadowed text with no panel behind it, on the same surface as the
resize readout. Messages that repeat every motion event (the live size while dragging)
stay in the terminal, because toasting those replaces a stable readout with a flicker.

**An autofiring control looks like one.** While turbo is firing, the control wears an
amber outline. It is *not* a permanent mark: the knob that says "armed" only exists in
edit mode, so without this there is no way mid-game to tell whether A is autofiring or
stuck, and six controls bearing permanent badges is exactly the ink the d-pad round
argued against.

**A quit cannot swallow unsaved work.** Leaving edit mode with unsaved changes now asks
— *Save / Discard* — instead of closing. The flag behind it (`_dirty`) was being set in
eleven places and read in none, so the question had been silently disabled; when edit
mode could only nudge controls that was a shrug, and since the sizing knobs arrived it
means an hour of layout work per stray press.

**Saving is atomic, and keeps the last three layouts.** `layout.json` is written to a
sibling, fsynced, then `os.replace`d over the target: the file is either the old layout
or the new one, never an empty half-file, which `open(path, "w")` cannot promise because
it truncates before a single new byte exists. Each save also rotates
`layout.json.bak1 → .bak2 → .bak3`, for the failure that is not a crash but regret.
One save is one write — an earlier revision reopened the file to add the turbo rate,
doubling the window in which a crash could eat a layout for the sake of one float.

## Known limits

`tools/probe_real_pad.py` is the only check that proves the device exists as the
kernel sees it; it needs a writable `/dev/uinput`, so it cannot run in a
container or on CI. There, every other check proves *logic* only — parsing,
mapping, normalisation, error resilience — never device creation. Run the probe
on the target hardware before concluding the pad works.

`--hold` defaults to 6 seconds, so even a bare run holds briefly before
destroying. Every path — full run, early Ctrl-C, unexpected exception — now
destroys the device, re-checks that nothing was left behind, and prints a
`RESULT:` line:

```bash
python3 tools/probe_real_pad.py            # ~6s, ends with RESULT: PASS
python3 tools/probe_real_pad.py --hold 0   # no pause
python3 tools/probe_real_pad.py --hold 30  # stay open to poke at
# from another shell, while it holds:
grep -A4 'X-Box 360' /proc/bus/input/devices
cat /sys/class/input/input*/phys | grep glasspad
```

The device exists only while the fd is open, so a second shell sees it in
`/proc` during the hold and it disappears after — that is expected, not a leak.
If it is still there once the probe has printed `destroyed cleanly`, that is a
real bug.

### Stacking: the overlay may need a window rule

KWin gives an X11 client its X11 stacking rules, so on xcb the overlay usually
sits above a fullscreen game on its own. A native Wayland surface only *asks* to
be kept above, and whether that is honoured is up to the compositor. If the
overlay is under the game, force it:

```
Plasma (Wayland)   System Settings > Window Management > Window Rules >
                   New Rule from an open window (WM_CLASS "glasspad"/"GlassPad"),
                   then "Keep above windows" -> Force -> Yes.
Hyprland           windowrulev2 = float,windowtitle:^GlassPad$
                   windowrulev2 = pin,windowtitle:^GlassPad$
Others             any rule matching class GlassPad that forces always-on-top.
```

`glasspad --status` prints the same note, so it is available without this file.

### Native Wayland cannot express an empty input region

Qt has no empty-but-not-null `QRegion`, so with **every** control hidden (the `⊘`
toggle) the overlay still covers the game. Nothing stays pressed; touches simply
do not reach the game until the controls come back. On xcb the equivalent state
is a genuinely empty shape and passes through cleanly. This is a Qt limitation,
not a shaping bug, and the only fix would be to unmap the window while disabled.
Because the toggle now latches, this is no longer a momentary curiosity on
Wayland: the overlay keeps swallowing input for as long as it is hidden, so
press `⊘` again to restore both the controls and pass-through.

### The native Wayland path needs a compositor to verify

`tools/verify_wayland.py` checks the `setMask` input-region path, but it cannot
run against Xvfb — the whole point is to exercise the wayland plugin, which needs
a real compositor. It is therefore the one part of the port no automated run
covers. It has been run by hand once on a real compositor and the client half
passed completely; that is evidence, not a regression guard:

| Shaping path | Selected when | Covered by |
| --- | --- | --- |
| XShape via libX11 | an X server is reachable, so `QT_QPA_PLATFORM=xcb` | `tools/verify_x11.py`, run by `verify.sh` |
| `QWindow.setMask` → `wl_surface.set_input_region` | no X server, native wayland plugin | nothing automated — run it by hand |

A KDE Wayland session normally has Xwayland running, so `DISPLAY` is set and
`_force_xcb()` chooses **xcb**, which *is* covered. The uncovered case is a
session with Xwayland switched off. Run it once on the target machine:

```bash
QT_QPA_PLATFORM=wayland python3 tools/verify_wayland.py
```

It maps the overlay full-screen briefly, so it is a hand-run check rather than
something `verify.sh` would do unprompted to someone's desktop.

### What binds this device — and it is not `xpad`

`xpad` is a **USB** driver: `xpad_probe()` takes a `struct usb_interface`, and
`xpad_table` matches on `USB_DEVICE_ID_MATCH_VENDOR | USB_DEVICE_ID_MATCH_INT_INFO`
(`idVendor` plus interface class/subclass/protocol). A `uinput` device has no USB
parent at all — its sysfs path is `/devices/virtual/input/inputN` — so `xpad`
can never bind it, no matter what identity is declared. Do not treat a missing
`xpad` binding as a defect; it is not reachable by design.

The declared identity is still worth getting right, because it is read back
through `EVIOCGID` (the `I: Bus= Vendor= Product= Version=` line) by userspace —
SDL, Steam Input and Wine query it to decide whether this is an Xbox-style pad.
The consumer that does attach is `joydev`, which matches on `EV_ABS` + `ABS_X`
and creates `/dev/input/jsN`; that `jsN` in `H: Handlers=` is the evidence the
joystick layer accepted the device.

To confirm events actually arrive, `pip install evdev` and read the event node,
or watch it directly:

```bash
cat /dev/input/js0 | head -c 64 | xxd | head    # or: python3 -m evdev.evtest
```

## Device details worth knowing

* **Input ABI** follows the kernel's own `input-event-codes.h`: face buttons on
  the `BTN_GAMEPAD`/`BTN_SOUTH` block (`0x130`+), D-pad keys on `BTN_DPAD_*`
  (`0x220`+), Guide on `BTN_MODE` (`0x13C`), hats on `ABS_HAT0X/Y` (`0x10/0x11`).
  An earlier revision was slid by one through `0x138..0x13B` and had the hats on
  `ABS_MT_*`, which made the pad look like a touchscreen to anything
  classifying by capability. `tests/test_glasspad.py` pins these constants
  against values restated independently of the module.
* **Sticks** are `ABS_X/Y` + `ABS_RX/RY`, **triggers** `ABS_Z/RZ` — matching
  `xpad_set_up_abs()` in `drivers/input/joystick/xpad.c`.
* **Ranges** are a deliberate deviation, the only one: sticks are `-320..320`
  with no dead zone, where a real 360 pad reports `-32768..32767` with
  `fuzz=16, flat=128`. The source is a touchscreen, so there is no analog drift
  for a flat to paper over, and a 128-unit dead zone on a ±320 axis would eat
  ~40% of the travel a finger can cover. Readers normalise by the declared range
  (`EVIOCGABS`), so SDL and Steam Input still see full travel. If a game
  hardcodes 32768-unit maths instead of normalising, sticks will barely move —
  that is `STICK` in `glasspad/xd360.py`, and the rest of the code derives its
  maths from the spec rather than assuming a width.
* **absinfo needs the legacy `write()` path.** `UI_SET_ABSBIT` alone declares an
  axis but describes nothing: `input_alloc_absinfo()` is `kzalloc`'d and evdev
  returns `EVIOCGABS` verbatim, so an undescribed axis reads back `min == max ==
  0` — a *dead* stick, not a wide or fast one. The kernel applies no clamping to
  injected values, so the numbers still reach handlers, but anything dividing by
  `(max - min)` sees nothing. `_create_device()` writes `uinput_user_dev` for
  exactly this reason.
* **Triggers do match `xpad` exactly** (`0..255`), being absolute-press values
  rather than a range a dead zone could damage.

## Layouts

`glasspad/layout.py` reads the Windows `layout.json` format as-is. The file
holds only geometry, as an ordered array with **no names**, matched positionally
against `all_editable` (all 13 buttons first, then dpad and sticks -- the C#
original concatenates two lists, so flattened index order differs from build
order). Nothing in it refers to kernel event codes, so the ABI corrections above
required no migration of files on disk.

## License

See `licenses/` — GlassPad's own license plus `THIRD-PARTY-NOTICES.txt`
(HIDMaestro, whose Windows virtual-controller concept this replaces with
`uinput`).
