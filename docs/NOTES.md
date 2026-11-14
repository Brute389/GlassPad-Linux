# Notes: how this got the way it is

The README is the download-facing document: what it is, what it needs, how to install it,
what to do when it does not work. This file is everything else — the archaeology, the
measurements, the failure each rule exists to prevent.

It is not optional reading for anyone changing this code. The paragraphs here are the
reason the README's instructions are shaped the way they are: why one file rather than a
script beside it, why the packaging refuses to bundle your GTK, why the suite runs headless,
why a particular window rule is documented under Known limits. Each one records a specific
machine and a specific failure. A summary loses the failure, and a rule whose failure is
forgotten gets deleted.

Split out of `README.md`, verbatim — nothing here was rewritten for the move.

## Contents

* [Two ways in: run the file, or drop it on a manager](#two-ways-in-run-the-file-or-drop-it-on-a-manager)
* [The package names, and which ones are guesses](#the-package-names-and-which-ones-are-guesses)
* [Packaging: one AppImage, and what a file cannot carry](#packaging-one-appimage-and-what-a-file-cannot-carry)
* [Testing, and how to get a misleading result out of it](#testing-and-how-to-get-a-misleading-result-out-of-it)
* [Input edge cases, and what this hardware will not tell us](#input-edge-cases-and-what-this-hardware-will-not-tell-us)
* [Profiles, feedback, and why the compositor is not asked](#profiles-feedback-and-why-the-compositor-is-not-asked)
* [Stacking, and what this device is bound to](#stacking-and-what-this-device-is-bound-to)
* [Device details worth knowing](#device-details-worth-knowing)
* [Why the licence reader is inside the app](#why-the-licence-reader-is-inside-the-app)
* [Why GlassPad does not install packages for you](#why-glasspad-does-not-install-packages-for-you)
* [The grant does not end when the terminal closes](#the-grant-does-not-end-when-the-terminal-closes)
* [A traceback is not a user interface, and a buffered log is not a record](#a-traceback-is-not-a-user-interface-and-a-buffered-log-is-not-a-record)
* [The Debian split CI found](#the-debian-split-ci-found)
* [FUSE is not one question but two](#fuse-is-not-one-question-but-two)
* [A second copy of the binary, on purpose](#a-second-copy-of-the-binary-on-purpose)

---

### Two ways in: run the file, or drop it on a manager

There are two legitimate first-run routes and the installer has to behave in both,
because they leave different things behind.

**Run it.** `./GlassPad-1.1.1-x86_64.AppImage --install` does the whole job: checks the
library, grants `/dev/uinput` (one password prompt), writes the menu entry, the icon and
the `glasspad` command. Everything it prints is copy-pasteable, because you are in a
terminal.

**Drop it on a manager** — Shelly on CachyOS, AppImageLauncher, Discover's AppImage
backend. This is a *better* install for parts of the job and it does not overlap with the
part that matters most:

| | a manager | `--install` |
|---|---|---|
| moves the file somewhere sensible | yes | no (it installs from wherever it is) |
| menu entry, icon, caches | yes, its own | yes, ours |
| updates the entry when the file moves | yes | no |
| `/dev/uinput` — a udev rule and group membership | **no** | yes |
| `glasspad` on PATH | no | yes |

Checked rather than assumed: no udev/group strings in Shelly's binary, and it has no
appstream/update URL to consult, so it cannot deliver a gamepad permission it has no code
for. What it *can* do — moving the file — is the one thing that breaks things quietly, so
three behaviours exist for it:

**A menu entry that already launches your file is not duplicated.** `--install-desktop`
scans the applications dirs for an `Exec=` that resolves to the same file. If one exists
it prints which file and stands down, because two menu items for one application means
one of them goes stale the next time the manager moves the file — and a menu item that
fails reads as a broken app. The `glasspad` command is still installed, because no
manager writes that and every diagnostic in this program is a sentence starting with
`glasspad …`. `--force` writes ours anyway.

**A `glasspad` command left dangling by a move is recognised as ours and repaired.** An
AppImage manager relocates the file; the symlink we wrote then points at nothing, and
neither the realpath comparison nor the marker-in-file test can prove ownership of a file
that does not exist. Until this existed the message was *"refused: something else owns
it"* about our own link. `--status` now reports the state as its own line:

```
glasspad cmd    : BROKEN: ~/.local/bin/glasspad -> /old/GlassPad-1.1.0… — that
                  file is gone (a manager moved it?). Re-point with:  --install-desktop
menu entry      : 1 from another launcher: ~/.local/share/applications/appimage-GlassPad.desktop
```

**The privileged step is reachable with no terminal.** Drop a file on a manager, get a
menu icon, and there is no terminal anywhere: stdout goes nowhere, and *"run
`glasspad --install-uinput`"* is a note on a door with no handle. So when there is no pad
the About box carries a **Grant gamepad access** button. It opens a terminal window
running our own `--install-uinput` — the plan, the polkit prompt, the installer's output,
and the exit code, with the window held open so the verdict can actually be read
(`glasspad/terminal.py`; konsole, gnome-terminal, kitty, alacritty, foot, wezterm,
xfce4-terminal, xterm, overridable with `$GLASSPAD_TERMINAL`). It escalates nothing
itself: the password prompt stays inside `pkexec`, where it can be refused. If no
terminal is installed, the toast says so and prints the exact command on stderr.

What a manager user still has to do once, by hand or by that button: grant `/dev/uinput`.
Nothing about being a manager makes it able to write `/etc/udev/rules.d`.

---

## The package names, and which ones are guesses

The *library* is `libgtk4-layer-shell.so.0` on every distro; the *package* is not. On
Arch and CachyOS it is `gtk4-layer-shell`, and `libgtk4-layer-shell` — which earlier
drafts of this file told people to `pacman -S`, and which `--status` printed in five
places — matches no package at all, `Provides:` included. Only the Arch name has been
checked here, by asking `pacman -Qo` who owns the installed library; the Debian and
Fedora names are unverified, and `--status` labels them as such on screen rather than
presenting three guesses as equals. Trust the command printed for the machine you are
on: `glasspad/bundle.py` generates it from `/etc/os-release`, and
`tests/test_one_file_install.py` re-derives the Arch row from the package manager so
it cannot rot a second time.
`PyQt6` used to be the first line of this list. The Qt/X11 backend it powered is gone —
it was a second GUI toolkit, a third of the package bytes, and nothing inside a shipped
artefact that exercised it. Its module names survive only in `glasspad/bundle.py`, as the
list the artefact tests refuse to ship.

---

## Packaging: one AppImage, and what a file cannot carry

```bash
./GlassPad-1.1.1-x86_64.AppImage --status              # read this first
./GlassPad-1.1.1-x86_64.AppImage --install-uinput      # one password, once
./GlassPad-1.1.1-x86_64.AppImage --install-desktop     # menu entry + icon, no root
./GlassPad-1.1.1-x86_64.AppImage                       # the overlay
./GlassPad-1.1.1-x86_64.AppImage --uninstall-uinput    # take the rule back
```

Every one of those works from the downloaded file alone, with no repository and no
package. `--dry-run` rehearses the two that touch the system, and elevates in neither
case — asking for a password to read a plan is absurd.

### From a virgin system, what one file can and cannot do

| | |
| --- | --- |
| **Virtual gamepad** | `--install-uinput`, one password. It carries `packaging/glasspad-udev.rules` and the script that installs it, escalates through `pkexec`, adds your account to `input`, and then asks the *account* — not root — whether the device is writable, because root can always write it and a root-side verdict would be a lie. |
| **A `glasspad` command** | Written by `--install-desktop` alongside the menu entry: a symlink to the AppImage, or a wrapper for a checkout, in `~/.local/bin`. Every diagnostic here is imperative — *run this*, *confirm with that* — and none of them are followable without it. It will not install where `PATH` cannot see it, and will not overwrite a `glasspad` belonging to a package or another build unless told to. |
| **A menu entry** | `--install-desktop`. Writes `org.glasspad.GlassPad.desktop` with `Exec=` from `$APPIMAGE`, the runtime's own record of the file you ran, and puts the icon in your own icon theme. |
| **Undoing either** | `--uninstall-uinput`, `--uninstall-desktop`. Both are narrow on purpose: neither touches your layout, your profiles, the `input` group, or the loaded kernel module. |
| **`libgtk4-layer-shell`** | **The one thing no packaging format can bring.** It has to match the host's `libwayland` *and* GTK 4; a bundled copy that mismatches gives a blank overlay instead of an error. So the app finds it, says which rule matched, and if it is missing prints the package command for your distro — generated from `/etc/os-release`, with the rows nobody has verified labelled as unverified — and exits nonzero rather than starting something invisible. |
| **A compositor with `zwlr_layer_shell_v1`** | KWin, Hyprland, Sway, wlroots yes; stock GNOME no. Nothing installs this. |

So the honest instruction for a new user is: **one file, plus one package-manager
command if their distro lacks `libgtk4-layer-shell`** — and on many gaming machines even
the udev step is redundant, because KDE Connect (`40-kdeconnect-uinput.rules`) and Steam
(`60-steam-input.rules`) both ship `TAG+="uaccess"` for `/dev/uinput`, which hands the
node to whoever is logged in at the console. That is why `--uninstall-uinput` explains a
still-writable device instead of claiming success in both directions.

### What went into this exact file

`tools/mk_appimage_offline.py` writes two artefacts beside the image, and a release should
carry both:

```bash
sha256sum -c GlassPad-1.1.1-x86_64.AppImage.sha256    # from the directory holding it
less dist/BUILD-INFO.txt
```

BUILD-INFO records the image's sha256 and size, the runtime's sha256 *and where it came
from*, the squashfs flags, the source commit and tag, and the `mksquashfs` version plus its
owning package. One line is the reason the file exists:

```
source tree    : DIRTY -- uncommitted changes were packed
```

A release built from a working tree with uncommitted edits is unreproducible in a way nobody
notices until a bug report says the binary does not match the repository. The build does not
refuse to do it -- sometimes you need exactly that binary -- it **says so**, and a test
insists the line is one of two honest answers rather than blank.

### Where the privileged half lives

A bundle cannot contain `/etc/udev/rules.d`, so it carries the rule and the code that
writes it, and `--install-uinput` is a thin front end that runs that code under `pkexec`.
Delete either one and the one-file install loses the ability to grant `/dev/uinput` —
precisely the failure that leaves a newcomer with controls that light up and convey
nothing.

It is Python now, at `glasspad/uinput_setup.py`, and it is **stdlib only** on purpose.
Root cannot read an AppImage payload — the FUSE mount is restricted to the account that
mounted it — so what runs elevated has to be copied out to an ordinary filesystem first.
Copying a script is easy; copying a *package* so that `import glasspad.something` still
works means handing root a `PYTHONPATH` into a temporary directory. A self-contained file
plus the rule, side by side, is the smallest thing that can be staged, and staging is
where the size has to be judged.

The modes are `--uinput`, `--uninstall-uinput` and `--check`. The last is what
`glasspad --status` prints rows from, so the two cannot disagree about whether your
machine is granted — one owner, one rendering. `--uninstall-uinput` stays narrow: the two
files this installer writes, and it says in as many words that your layout, your desktop
entry and `input` membership are none of its business.

`--install-desktop` writes `org.glasspad.GlassPad.desktop` into `$XDG_DATA_HOME` and the
icon into the user's own icon theme, pointing `Exec=` at `$APPIMAGE` — the runtime's own
record of the file you ran. `--uninstall-desktop` removes it, under either name this
project has ever used, and leaves the app, the icon and `~/.config/GlassPad` alone.

**The build tool runs what it wrote.** The staged AppDir's `AppRun --status` executes with
bytecode writing disabled before packing is allowed, and the suite is the gate: a red test
stops the artefact. That discipline came from the first bundle this project shipped — a
self-extracting `.run` whose build ran `--version`, `status`, a repeat run and
`install-desktop` in a sandboxed `$HOME`, and caught a launcher bug that would otherwise
have shipped: `\${XDG_DATA_HOME}` reached the shell escaped and stayed literal, so
`install-desktop` tried to `mkdir` a directory named `${XDG_DATA_HOME:-`.

### What was dropped, and what it actually cost

`tools/make_bundle.py` and its `.run` are gone. Two things did not transfer, and neither
was load-bearing:

* **Menu-entry installation.** It lived only in the `.run` launcher. It is now
  `glasspad --install-desktop`, in the app, where the AppImage can use it too — which was
  the point: an AppImage previously had no way to become an application, only a thing you
  run from a terminal. Its `Icon=` also stops pointing into the payload, which for an
  AppImage is a FUSE mount that vanishes on exit and leaves a broken icon in the menu.
* **Running without FUSE.** True, and worth nothing here: GlassPad needs a Wayland
  compositor advertising `zwlr_layer_shell_v1` and a writable `/dev/uinput`, so every
  environment where the lack of FUSE would have rescued the `.run` is an environment where
  the overlay cannot work anyway. Where FUSE genuinely is missing, the AppImage's own
  `--appimage-extract-and-run` (or `--appimage-extract`, then `./squashfs-root/AppRun`)
  does the same job with no mount.

What keeping both cost was every packaging change written twice — the udev rule and the
installer went into both builders in a single day — plus a third payload layout for the
path resolvers to keep handling. The shell installer eventually cost the same thing to
itself: `install.sh` and `--install-uinput` were one install wearing two clothes, and the
day the privileged half became Python the shell had nothing left that the AppImage could
not do.

### Type-2 internals, and borrowing tools offline

```bash
./tools/make_appimage.sh                        # AppDir, then a real .AppImage
./tools/make_appimage.sh --dir                  # stage and run it, no packing
./tools/make_appimage.sh                        # GLASSPAD_NO_BORROW=1 to refuse borrowed tools
./tools/make_appimage.sh --clean                # drop the scratch; keeps image, sidecar, BUILD-INFO
dist/GlassPad-1.1.1-x86_64.AppImage --appimage-extract-and-run status
dist/GlassPad-1.1.1-x86_64.AppImage status      # mounted, via squashfuse
```

`dist/` is meant to answer "which part of this is necessary?" with one answer: the image,
its `.sha256` sidecar, its `BUILD-INFO.txt`. The other things a build makes -- the AppDir,
the intermediate `payload.sqsh`, the borrowed `runtime-active` -- are scratch. They used to
be left behind by the offline route, because only the `appimagetool` path called
`cleanup_staging`, and an audit of the folder found ~1.9 MB of them sitting next to the
deliverable. Both routes clean now; `--clean` exists because `--dir` leaves the AppDir on
purpose (it is the thing you carry to a machine that has `appimagetool`), and `GLASSPAD_KEEP_STAGING=1`
reverses the default when you want to look inside what got packed.

A type-2 AppImage is `[runtime ELF][squashfs]`, so two binaries are needed and neither is
written here. Both are now **named, versioned and hash-checked**:

| piece | normally | as the last resort |
| --- | --- | --- |
| `mksquashfs` | `/usr/bin/mksquashfs`, package-owned (`squashfs-tools`, 4.7.5 here); BUILD-INFO records `pacman -Qo` | found anywhere on disk, announced as *borrowed* |
| runtime ELF | the URL, sha256 and size in `packaging/runtime-pin.json` -- AppImageKit `continuous`, fetched to `~/.cache/glasspad-build/` and hash-verified on every build | carved from a local `.AppImage`, announced as *BORROWED* |
| `appimagetool` | not needed | it glues the same two halves; if it is installed the script still does the same job directly |

The runtime was previously the first 944632 bytes of some other application's AppImage on
the build machine. That is two problems wearing one hat: the deliverable contained an
unversioned binary of unknown origin, and rebuilding required this particular laptop with
those particular apps installed. The official runtime turned out to be 193728 bytes, so the
same payload now ships in **820 KB where it used to take 1.57 MB** -- the download was
mostly somebody else's runtime.

`$GLASSPAD_RUNTIME` is the override, and it **refuses** a runtime that contradicts the pin:
an escape hatch that also silences the check would make the pin decoration. Change the pin
deliberately instead -- hash, release, date.

`tools/mk_appimage_offline.py` prints which rung of each ladder it used, and writes it into
`dist/BUILD-INFO.txt`. Two things learned doing it, both encoded in tests:

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
"$HOME/Downloads/GlassPad-Linux/dist/GlassPad-1.1.1-x86_64.AppImage" \
    --appimage-extract-and-run profile fighter && %command%
```

### Why nothing from the host is bundled

Three things decide whether this app works, and a payload cannot carry any of them:

| | |
| --- | --- |
| **libgtk4-layer-shell** | Bundleable in principle. Wrong in practice: the copy must match the host's `libwayland` *and* GTK 4, and when it does not you get a blank overlay rather than an error. So `glasspad/bundle.py` **finds** it instead — the bundle's own `lib/` first, then `ld.so`, then Arch, Fedora and Debian-multiarch paths — and says which rule matched. `GLASSPAD_LAYER_SHELL=/path` overrides. |
| **A compositor with `zwlr_layer_shell_v1`** | KWin, Hyprland, Sway, wlroots: yes. Stock GNOME: no. No packaging format supplies this, which is why a Flatpak of this app would be worse, not better: the sandbox filters layer-shell off the session bus *and* denies `/dev/uinput`. |
| **Write access to `/dev/uinput`** | A permission, not a library. AppImage sandboxes nothing, so this is the same question as running from a checkout — but it is the one thing that fails on a machine that has never had GlassPad — so `--status` says it out loud, and **`--install-uinput` fixes it**: both artefacts ship `install.sh` and the udev rule, and escalate through `pkexec` once. A bundle cannot carry `/etc/udev/rules.d`; a udev rule inside a squashfs image is not a rule. What it can carry is the means. |

One finding from building this, since it bites anything that asks the question:

> Loading the typelib normally makes `Gtk4LayerShell.is_supported()` return **False for the
> lifetime of the process** — same display, same compositor, pumping the GLib context does
> not help. Load the same file `RTLD_GLOBAL` first and it returns True. So `--status` now
> performs the same preload the app does. Before that it was reporting this machine's KWin
> as having no layer-shell, over the exact overlay that was up on screen.

`--status` reports what this backend actually needs: where `libgtk4-layer-shell` was found
and from where, whether the compositor offers `zwlr_layer_shell_v1` (measured *after* the
same `RTLD_GLOBAL` preload the app performs — see the note above), `/dev/uinput`, and the
profile store. Exit status is 0 only when the overlay could actually run — a diagnostic that
always exits 0 is not a diagnostic.

`--backend layer` is still accepted, for launch options that were written when there was a
choice; it selects nothing. `--backend qt` is refused with a sentence explaining what happened
to it, because the alternative — a traceback, or silence — teaches the reader nothing.

---

## Testing, and how to get a misleading result out of it

What runs automatically, and what no runner can check, are both in
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — but the second half is worth
saying here too, because a green badge that quietly tests less than the README claims is
the failure mode this project keeps running into:

* **`suite`, headless.** `tools/check_all.py --no-live --no-artifacts` on `ubuntu-24.04`
  with the runner's own GTK 4 and `python3-gi`. There is no compositor, so the display-
  dependent tests report **skipped** and the job summary prints the skip counts: a suite
  that silently stops asserting things is worse than one that is missing. The workflow pins
  the runner image rather than `-latest`, because a moving image turns a green pipeline red
  with no commit in between, and the bug you then chase is GTK's.
* **`artifact`.** Builds the AppImage — which re-runs the whole suite as a gate, executes
  the staged tree before `mksquashfs` is allowed to touch it, and refuses to pack a red
  tree — then extracts the finished image and checks the payload *is* the source it claims,
  that the licence texts and the privileged pair are inside it, and that the runtime offset
  points at our squashfs. `BUILD-INFO.txt` and the `.sha256` sidecar are uploaded with it.
* **What CI cannot check, on any runner:** whether the overlay is visible above a
  fullscreen game, whether a touch lands on the control you aimed at, and whether the
  compositor's stacking puts the surface where you expect. Those three have been the
  actual bugs
  during development of this port — all of them — and they need a screen and a finger.

(The README keeps the short version of this; the wording above is the one written against the workflow file, and both are checked against it by `tests/test_release_files.py`.)

There are two runners and they are **not** duplicates. The stdlib suites each run on their
own; `tests/test_glasspad.py` is written in pytest style, and `tools/run_tests.py` executes
it through its own small pytest stand-in (session and function fixtures with yield
teardown, `parametrize`, `tmp_path`, `monkeypatch`, `approx`, `skip`). pytest is not
installed here and cannot be fetched, and a safety net that needs a package you lack is not
a safety net. This is also why `check_all.py` routes that one file to the other runner
instead of executing it: run directly it can only print *No module named pytest*, which is
how several hundred tests sit quietly inside a green-looking tree.

Current tallies are printed in the README's Tests section and nowhere else: the numbers are
the runner's output, not an assertion, and a second copy of a number that changes every time
a test is added is a second thing to forget to update.

Exit codes are three-state, because "everything passed" and "everything that *ran* passed"
are different claims: `0` all green, `1` something failed, `2` something could not start at
all. `--strict` folds the last case into a failure; the packaging gate treats `1` as fatal
and prints `2` as a warning rather than pretending the tree is fully covered.

**The suite needs no display at all.** It used to want an X server: six tests called
`open_display(":99")` by name against the Qt backend's XShape path, and a Wayland-only
desktop skipped them. Those tests went with the backend they tested, and the result is worth
stating plainly because it changed what a green run means:

```bash
env -u DISPLAY -u WAYLAND_DISPLAY python3 tools/run_tests.py   # 87 passed, 0 skipped
```

Nothing skips, so nothing is quietly unmeasured on the machine most users have. The live
self-test is the part that *does* need a Wayland session, and `check_all.py` reports it as
skipped rather than passed when there isn't one.

`tools/run_tests.py` also drives the two uinput-safety legs: it runs with opening a real
pad made impossible and counts how many times the code tried, which is the only way to see
a test that passes *because* the backend swallowed an error.

### Constants that read like a spec

`layer_style.py` carried seventeen module-level constants that no code referenced —
colours transcribed from `MainWindow.xaml` and never wired to a renderer. They looked
like the spec and were not: three were `"#AAC0D0FF"`, which in WPF is alpha `0xAA` over
`#C0D0FF`, i.e. `rgba(192,208,255,0.667)` — exactly what the live CSS says. An unreferenced
constant is a second opinion, and this one was wrong in a way only the Windows tree could
adjudicate. All seventeen are gone; `tests/test_orphan_constants.py` now fails the package
if a new one appears, with an allowlist for anything that genuinely documents — each entry
requiring a sentence about who reads it.

Two things the sweep found, both fixed and attributed in the CSS sheet:

* `InfoPanel CornerRadius="16"`, `SaveConfirmPanel CornerRadius="14"` — one rule gave both
  14, so the About box was drawn with the confirm dialog's corners.
* `EditPanel Padding="16,12"` while the sheet paints `20px` across, `14px` down. **Left
  alone deliberately**: it predates the note, the panel's on-screen size was tuned against
  captures, and closing the gap is a visible 8×4 px change that wants eyes on the result
  rather than a tidy number in a file.

`xd360.UINPUT_VERSION` went the other way — from unreferenced to load-bearing. The About
box printed `ABI v5` as prose next to a struct built from that constant, and the number a
user pastes into a bug report is the number that must not drift.

### Layer-backend suites

The layer-shell backend is covered by standalone files, each of which runs on its own
without pytest:

| File | Covers |
| --- | --- |
| `test_layer_geom.py` | geometry, the layout file, and the element order and default rectangles pinned to the Windows numbers |
| `test_resize_geom.py` | edit-mode sizing: grab zone, floors, clamping, reset |
| `test_fit_screen.py` | rotation fit, the design-size field, `apply_screen` |
| `test_turbo.py` | the square wave, arming rules, the watchdog exemption |
| `test_watchdog.py` | deadlines and the release each one causes |
| `test_stick_tuning.py` | parity with `StickZone.Apply()`, monotonicity, the nub following the deflection |
| `test_lockout.py` | a contact we released cannot press again until it ends; the next touch works |
| `test_edge.py` | the band refuses the bezel and never the button, on the real layouts and after a rotation |
| `test_orphan_constants.py` | nothing in the package declares a constant no code reads — see "Constants that read like a spec" |
| `test_profiles.py` | name validation, fallback order, the claim file, and when a switch is allowed |
| `test_bundle.py` | finding `libgtk4-layer-shell` on Arch, Fedora, Debian, or inside a bundle — and a wrong override named, not swallowed |
| `test_status_cli.py` | `--status` describes the backend you run, and exits nonzero when the overlay could not |
| `test_desktop_entry.py` | where a bundle's menu entry points, that `Icon=` is never a mount path, and that an uninstall leaves the saved layout alone |
| `test_uinput_installer.py` | the rule's contents, both refusal paths, that `--dry-run` never elevates and a real run always does, `PKEXEC_UID` resolution, and that the undo names two files and not the user's layout |
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

**Nothing in the test path is hash-pinned.** The retired `tools/verify.sh` identified the
tree by the md5 of `tests/test_glasspad.py` and, when a docstring edit broke the pin,
quietly dropped to asserting "zero failures only" — a regression that added skips still
passed. A guard whose failure mode is a weaker check is worse than no guard, so the current
runners pin nothing: they report what actually ran, and the tallies above are documentation
rather than an assertion.

What replaced it is a **content** check that does survive edits:
`TestDocPointers::test_no_dangling_file_references` resolves every filename the shipped
files mention in prose — comments and docstrings, not code — against the tree. It is what
caught this session's `NOTES.md`-style references, and it still has teeth: planting
"see NOTES.md and thing.py" in a comment fails the suite.

### A third way: a screen pixel diff measures the compositor

A full-screen before/after diff of the overlay is not evidence of anything. Measured on
this machine: two frames **0.6 s apart in the same run** differ by **143k pixels** when
a layer surface maps or a wallpaper slideshow advances, and by **0 pixels** when nothing
maps. The overlay's own ink is 1–3k pixels, so the noise is fifty times the signal and
the sign of the difference is whatever the desktop felt like.

What works instead:

1. **Measure inside the box you changed**, not the screen. The toast is 38×23 logical
   for one word; the ring around a 75 px button is ~1.4k pixels.
2. **Toggle inside one run**, and report the noise floor beside the signal. Comparing two
   separate launches measures the wallpaper slideshow as readily as the thing you changed —
   a 178k-pixel whole-screen diff is mostly compositor. The measurement hooks this was done
   with (`GLASSPAD_TOASTCYCLE` and friends) were removed once the questions they answered
   were answered; the method outlives them, and the code is in git history if you need to
   re-measure. What the method produced stands: same-state frames differ by **0 px** inside
   the toast's box, toggling the toast changes **332 px** of it. The zero beside the number
   is what makes the number mean something.
3. **Capture with `spectacle -b -n -o file.png`.** There is no `grim` on this machine and
   there never was — no package owns `/usr/bin/grim`, and `/usr/bin`'s mtime predates the
   session — so a script that shells out to it fails with `2>/dev/null` and leaves you
   diffing files that were never written.

---

## Input edge cases, and what this hardware will not tell us

### Holding, and what happens when nobody tells us to release

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

### A release the finger does not know about

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

### Rotation, docks and display scale

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

---

## Profiles, feedback, and why the compositor is not asked

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

### Feedback you can see, and a layout you cannot lose

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

---

## Stacking, and what this device is bound to

### Stacking: the overlay may need a window rule

The layer backend asks to be an `OVERLAY` surface with `keep-above`, which is the whole
reason it exists: the compositor is told which layer to draw in, instead of being raced for
the top of a stack it owns. That request is still a request. On a compositor that treats it
as advisory, the overlay ends up under the game and you need a rule:

```
Plasma (Wayland)   System Settings > Window Management > Window Rules >
                   New Rule from an open window (WM_CLASS "glasspad"/"GlassPad"),
                   then "Keep above windows" -> Force -> Yes.
Hyprland           windowrulev2 = float,windowtitle:^GlassPad$
                   windowrulev2 = pin,windowtitle:^GlassPad$
Others             any rule matching class GlassPad that forces always-on-top.
```

`glasspad --status` prints the same note, so it is available without this file.

`⊘` hides every surface (`set_visible(False)` on each one), so with the pad hidden the game
gets the touches, and nothing stays pressed on the virtual pad: all active contacts are
released on the way out, and a lockout keeps a finger that was mid-press from pressing again
when the controls come back.

There is no input-region trick here, because none is available. `set_input_region` on a KWin
layer surface was measured to be **inert** — the call succeeds and changes nothing — so the
shape rules live in the painters instead: `inside_shape()` refuses a tap that lands on the
corner of a circular control's bounding box. That is the honest version of "the compositor
will do this for us", which it will not.

The caveat that used to sit in this section belonged to the Qt hub, which had no
empty-but-not-null `QRegion`: hiding its controls left an invisible sheet over the game that
swallowed every touch. That behaviour left with the backend.

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

---

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

---

## Why the licence reader is inside the app

`licenses/THIRD-PARTY-NOTICES.txt` covers what the port actually stands on, and keeps
three things apart that a casual notice tends to blur:

* **loaded from your system, not redistributed here** — GTK 4, GLib, Cairo, Pango,
  PyGObject, gtk4-layer-shell, Python. Nothing is bundled by design, so replacing any
  of it is your business, and nothing is linked statically.
* **numbers taken from the kernel headers** — the uinput ABI constants in `xd360.py`,
  from `input-event-codes.h` and `uinput.h`.
* **shipped inside the AppImage** — the AppImageKit runtime, pinned by URL, sha256 and
  size in `packaging/runtime-pin.json`, and hash-verified on every build. Where a machine
  cannot fetch it, the build falls back to one borrowed from a local AppImage and says so
  loudly in `dist/BUILD-INFO.txt` rather than quietly shipping a binary nobody can name.

HIDMaestro's licence and its notices for usbip-win2 and DsHidMini used to sit in
`licenses/windows-original/`. None of it is part of this build — the port creates
its device through the kernel's `uinput`, links none of that code, and ships none of
it in either artefact — so the directory was trimmed with the rest of the
non-essential material. It is in `git log` if the Windows original ever needs
rebuilt. What *is* load-bearing stays enforced rather than asserted: the MIT notice
and `licenses/THIRD-PARTY-NOTICES.txt` are conditions of shipping, both builders
abort if either is missing, and nothing Windows-only may appear above the notices
file's *Not part of this build* heading — checked from inside each packed artefact.

**In the app**, the About box's `Open-source licenses` pill opens a scrollable reader
showing both documents inside the overlay. It deliberately does not launch a text
editor: a layer surface lives on the OVERLAY layer, so anything launched lands *under*
the controls, which from a tablet reads as a crash. If a document is missing, the panel
names it and says how many places it looked, rather than rendering an empty box — which
is what the pill used to do, one step better than reporting success while opening
nothing.

Both documents are conditions of shipping, so `tools/make_appimage.sh` aborts when either
is absent, and the artefact suite imports the packed `bundle.py` from inside the AppImage to
prove the panel resolves them in the layout that only exists after packing.

---

## Why GlassPad does not install packages for you

Three reasons, in descending order of how much they hurt to get wrong.

**The instruction was wrong for the audience most likely to need it.** `DEPENDENCY_PACKAGES`
carried a row labelled "Debian / Ubuntu / SteamOS". SteamOS reports `ID=steamos
ID_LIKE=arch`, so that row never matched a Deck; the *Arch* row matched, and a Deck user was
handed `sudo pacman -S gtk4-layer-shell` against a read-only base image -- an error naming
neither GlassPad nor the reason. Deck and RetroDeck users are precisely the people who install
through Shelly or Big Picture, because that is how you get an app onto a machine you drive with
a controller. A label naming a distro whose package manager it does not use is not a typo; it
is an instruction.

**"No package command would work here" is a fact about the machine, and it is measurable.**
`glasspad/deps.py` measures rather than guessing from a name: `/run/ostree-booted`
(Bazzite, Bluefin), `steamos-readonly` or `ID=steamos`, `/nix/store`, the options `/` is
mounted with, and which manager binary exists. Every one is injectable, so the suite asserts
against eight synthetic systems -- CachyOS, Fedora, Bazzite, SteamOS, NixOS, Debian, a
read-only root and something unrecognisable -- rather than against the one machine that
happened to write the test. One of the two seams needed fixing to get there:
`package_name()` reached through `dependency_rows()` into the *host's* /etc/os-release, so a
Debian fixture was quietly handed the Arch package name, in a test written to prove package
naming was correct.

**Even on a machine where it would work, the transaction is not ours to run.** Installing a
package pulls a dependency closure into somebody's system. What GlassPad does instead is open
a terminal with the exact command displayed and `read` waiting for Enter -- `staged_run()`,
which the suite tests by running it: Enter executes, EOF exits 130 and nothing changes, so
handing it to a non-interactive terminal-open is safe by construction rather than by hope.

### The failure this all replaced

Not a bad message -- *no* message. On a machine without the library, `overlay_layer` raises
while importing:

```
ValueError: Namespace Gtk4LayerShell not available
```

and that happens before `__main__` could print the dependency hint it had, so the process died
with a traceback into a stdout a manager-launched app does not have. Every word of guidance in
`dependency_hint()` was written for a situation it could never be shown in. Reproducing it took
hiding the typelib, because no machine in the development path had the real condition.

### Two ways this went wrong while it was being built, recorded because both are generic

* **`set_keep_above()` does not exist in GTK 4.** It raised inside the `activate` handler,
  where an exception does not reach the caller's `try`; GTK printed a warning and carried on
  running an application with no window. So "we show a notice" described a live process
  displaying nothing -- and the entire headless suite stayed green, because none of it builds
  a widget. The live suite now asks the toolkit directly (`get_mapped()`, size) rather than
  trusting that reaching the code means anything appeared.
* **A 46% pixel difference was not the window.** The first measurement of the notice compared
  two frames and reported a large, screen-wide delta -- and meant nothing of the kind. Verify
  with an instrument that answers the question you asked, and know what your instrument returns
  when the answer is "nothing".

---

## The grant does not end when the terminal closes

About → **Grant gamepad access** opens a terminal, and then GlassPad knew nothing more. The
terminal closes, the overlay keeps its null pad, and the user is left choosing between
restarting the app, logging out, or giving up -- in that order, because restarting is the
obvious thing and, in one of the three cases, the one thing that cannot possibly work.

The three cases are distinguishable from inside the running process, which is what turns this
from an apology into a state machine (`pad.grant_state`):

| Question asked | Answer | Verdict shown |
| --- | --- | --- |
| can we open `/dev/uinput` now? | yes | **restart GlassPad** -- no log out needed |
| ...no, but does the *account* have `input` now? | yes | **log out and back in** -- restarting will not help |
| neither | | **not granted yet** -- the terminal has the reason |

Two details carry the weight:

* **A process keeps the groups it started with.** `os.getgroups()` answers about the process
  and will keep saying "no `input`" until the session is replaced, so membership has to be
  asked of the account out of process (`id -nG`). That is the only way to tell the
  log-out case apart from the nothing-happened case, and it is why the wording says so
  explicitly rather than leaving "restart and see" as the advice.
* **`os.access` is a question about permissions; `os.open` is the question the pad asks.**
  ACL-driven `uaccess` rules and mandatory access control are exactly where the two disagree,
  and a verdict built on `access` could announce "granted" while the pad still could not be
  created. The probe opens for writing and closes immediately -- the device itself is not
  created until `UINPUT_DEV_SETUP`/`UI_DEV_CREATE` are ioctl'd, and the suite checks the
  probe never does that: a probe that created a device would hand Steam a new phantom
  controller every two seconds, for as long as the panel is open.

No self-relaunch button, deliberately: an app that execs itself escapes the environment a
manager set up (Steam and GameScope hand over variables the overlay depends on), and there is
a window in which the old pad and the new one both exist, which is the double-input failure the
desktop entry goes out of its way to prevent. One click on the thing they launched already is
cheaper than explaining that.

Tests: `tests/test_grant_state.py` (11) covers the decision, the wording constraints (no shell
commands in text read on a tablet; the pending verdict promises nothing) and the polling bound
-- five minutes, not the rest of a session. The part no headless suite can see is asserted
live, in the selftest (`step_grant_verdict`): that the label on the real panel actually
changes for all three states, that the toast repeats it, and that the timer stops at a verdict.

## A traceback is not a user interface, and a buffered log is not a record

Two defects found by running the app as someone who had never run it, both in the first
minutes of a virgin install, both invisible on the machine that wrote them.

### `Gdk.Display.get_default()` returns None; the app assumed it never would

```
File "glasspad/overlay_layer.py", in screen_size
    model = Gdk.Display.get_default().get_monitors()
AttributeError: 'NoneType' object has no attribute 'get_monitors'
```

That is what a first run over ssh looks like, and what it looks like from any environment
whose `XDG_RUNTIME_DIR` is not the one holding the compositor's socket -- which is exactly how
the foreign-account walkthrough failed twice. The library was installed, the layer-shell
global was advertised, and the app died four frames below its entry point with a stack trace
in a build where the user has no terminal open to read it.

`screen_size()` now raises `NoDisplay` with the environment in the message, `main()` routes it
into the same state vocabulary the dependency notices use (`deps.print_notice("nodisplay")`),
and the exit code is its own (`EXIT_NO_DISPLAY`, 5) so `__main__` does **not** append the
libgtk4-layer-shell install hint underneath a message that is not about that library. The
stderr copy names the socket path GDK resolved and whether it exists, because that is the
question that took thirty minutes to answer by hand:

> A Wayland client looks for its socket at `$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY`. For this
> process that is `/run/user/4321/wayland-9`, which does not exist.

The first version of that sentence substituted a default `wayland-1` and cheerfully reported
the socket *exists* on a machine with no display at all. When a variable is missing, saying so
beats guessing what it would have been.

Two things were deliberately not done. The check is **not** at the top of `main()` -- `--help`
and `--version` have to answer over ssh, and a test now pins that. And no notice *window* is
attempted: with no display there is nothing to draw a window on, which is why this state goes
to stderr while its cousins go to `notice.py`.

### Redirected stdout is a pipe, and a pipe is block-buffered

The overlay's startup summary -- how many surfaces came up, what screen it fitted, which
profile resolved, whether the pad is null -- was plain `print`. In a terminal you never see
the difference. Redirected (`nohup … > file`, a manager, a systemd unit) it goes into a 4 KiB
buffer, and the run that is closed early, killed, or hung leaves a log that stops
mid-sentence. For a GUI app launched by something else, that log is the only record that
exists.

So it happened for real: the overlay was up -- sixteen controls measured on screen, GPU
dmabufs open -- and its own log never said so, which is how an hour went into wondering
whether layer-shell had mapped anything for a foreign user.

`glasspad/stdio.py:line_buffer_stdout()` is the first thing `main()` does, before any line is
written. It never raises: stdout arrives as a terminal, a pipe, a file, `None`, a
`BytesIO`, or a test's capture object, and block-buffered logging is better than crashing on
the way to drawing the controls.

Its test ships with a **control**: the same non-blocking read, with the helper disabled, must
see nothing until the child exits. Without that control the test passes on every machine
forever, because the usual reason stdout arrives is the process exiting -- precisely the event
that does not occur when a user closes an overlay. Tests for buffering that lack a control
prove nothing, and this repository has now been bitten by a suite that could not fail often
enough to list it as a house rule.

Tests: `tests/test_no_display.py` (9, including four headless subprocess runs) and
`tests/test_stdio.py` (6, including the control). Both run in CI on a machine with no
display, which is the only kind of machine that can test this.

## The Debian split CI found

The first push of this repository ran CI on `ubuntu-24.04`, and two things that had been
written from Arch-shaped assumptions fell over within a minute of each other.

**`libgtk4-layer-shell0` does not exist.** The workflow tried to install that package name,
got `E: Unable to locate package`, warned, and continued -- after which 12 of 30 suites died
on `Namespace Gtk4LayerShell not available`. The warning was the repository's own policy at
the time ("a narrower run is still a run"), sitting in the same codebase as `check_all`'s
rule that *a suite that cannot start is not a passing suite*. Both were written deliberately
and they cannot both be true. The pipeline was red either way, so the warning only bought a
green dependency step and a comment making a claim the run disproved.

CI now provides the dependency: distro package if there is one, otherwise the same 1.3.0
release Arch ships, fetched from the URL in Arch's PKGBUILD and checked against the hash Arch
checks, with a build failure allowed to stop everything. Version and checksum came from
`gitlab.archlinux.org/archlinux/packaging/packages/gtk4-layer-shell` and were compared
against the tarball after download, not recalled:

```
sha256  1ebb01ab14e98afd1727f68f64981c37bd23305b1f131f5667c02b94cf593192
```

Two details in that build are load-bearing. `-Dintrospection=true`, because GlassPad reaches
the library through the GIR namespace `Gtk4LayerShell` and a build that satisfies the dynamic
loader with no typelib would pass a "does the .so load" check while failing every suite. And
`--prefix=/usr`, because the typelib then lands on GI's default search path and in one of the
directories `bundle.LAYER_SHELL_DIRS` names; `/usr/local` would have been tidier and
invisible. The dependency step finishes by asking **glasspad's own resolver** whether it can
find the result, because a library installed where the app does not look is indistinguishable
from one that is missing -- and that is the user's problem, not a CI curiosity.

**`python3-gi-cairo` is a separate package.** With `python3-gi`, `python3-cairo` and
`gir1.2-gtk-4.0` all installed, the paint tests failed with:

```
KeyError: 'could not find foreign type Context'
```

raised inside `PangoCairo.create_layout`, naming no package, on a machine whose `--status`
output looked entirely healthy. Arch folds PyGObject's cairo bridge into `python-gobject`.
Debian and Ubuntu ship it separately, and every control in this overlay is drawn by handing a
`cairo.Context` to PangoCairo, so on those distros the missing package is a missing
application, not a missing nicety.

Minimal reproduction, worth knowing because the error text points nowhere near the cause:

```python
import gi, cairo
gi.require_foreign("cairo", "Context")   # ImportError: Couldn't find foreign struct converter
```

So: the package is in the CI dependency list, the README's requirements table gained a cairo
row it did not have, and `--status` prints a `cairo bridge` row. The last of those is the
point -- a failure mode discovered by a test suite should end up as a line the user can run,
not just a green checkmark in a workflow.

Both fixes went into one composite action (`.github/actions/setup-deps`) used by both jobs.
The drift that caused half of this was two copies of the same apt block: the suite job had a
guard, the build job had `|| true`, and the two jobs disagreed about whether the dependency
was optional.

The loop for all of it was `podman run ubuntu:24.04` with the repo bind-mounted, not the
runner: about 20 seconds a pass against four minutes and a log download. When CI's logs
stopped downloading mid-debug, the container was the only way to keep working -- which is
worth writing down, because the instinct was to re-run CI and wait.

## FUSE is not one question but two

CI's build job produced an AppImage on ubuntu-24.04 and then could not run it:

```
dlopen(): error loading libfuse.so.2
AppImages require FUSE to run.
```

The artefact test skipped the mounted case when `/dev/fuse` was missing. `/dev/fuse` was
present; the library was not, and Ubuntu 24.04 has no candidate for `libfuse2` at all.
Checking the device was measuring a different question from the one the runtime asks --
the type-2 runtime `dlopen`s `libfuse.so.2` and has no fallback of its own.

Two environments, two real paths, and both are now asserted instead of one asserted and
one skipped:

* **libfuse2 present** (Arch, Fedora, Debian 12): run the image mounted, for real.
* **libfuse2 absent** (Ubuntu 24.04): the direct route must fail *loudly* -- that message
  is the only thing a user will ever see -- and `APPIMAGE_EXTRACT_AND_RUN=1` must produce
  a working program. The variable makes the runtime unpack to a temp dir rather than
  mount: one extra unpack per launch, and it works everywhere.

The user-visible version of this is worse than the CI failure, which is why the second fix
is in the installer rather than the test: a `.desktop` entry with `Exec=/path/GlassPad.AppImage`
on Ubuntu 24.04 launches something that writes one line of complaint into a terminal no
one opened, and the icon simply reads as broken. `_exec_line_for()` decides at install
time, on the machine being installed to, prints its reasoning, and says how to undo it.
Both branches are tested, because the failure mode of applying the wrapper everywhere is
invisible -- an unpack per launch on machines that mount fine, noticed months later as
unexplained sluggishness.

Reproduce the missing-library case without a VM:

```bash
podman run --rm -v "$PWD":/src:ro ubuntu:24.04
apt-cache policy libfuse2          # Candidate: (none)
```

The shape repeats, and this was the third instance in a week of CI: **a probe written for
one distribution, run on another, answers confidently and wrongly.** `pacman -Qo` on Debian
(crash), `libgtk4-layer-shell0` on Ubuntu (no such package), `/dev/fuse` standing in for
libfuse2 (present, and irrelevant). Each was fixed by asking the question the runtime
actually asks, and by making the fallback text report what it measured instead of what the
author's machine happened to lack.

## A second copy of the binary, on purpose

The AppImage now lives at the top of the repository as well as in Releases. That is a
deliberate exception to two rules this project has been enforcing all week: binaries do not
belong in git, and a file that exists twice goes stale.

The reason it is here anyway is reachability. Someone who lands on the repository reads the
file list; the Releases tab is a thing you already know exists. `download the app` should
not depend on knowing GitHub's split, and a project this size has one binary.

The cost is real, so the exception is narrow and machine-enforced:

* **The build writes it.** `mirror_to_repo_root()` runs at the end of
  `tools/mk_appimage_offline.py`, alongside the sidecar. There is no "remember to copy it"
  step, because those rot within a release.
* **`.gitignore` exempts one path**, not a pattern that also matches scratch:
  `!/GlassPad-*.AppImage`, with the guard named in the comment above it.
* **The suite treats staleness as a failure**: exactly one image at the root, named for
  `glasspad.__version__`, its sidecar matching byte for byte with a *bare* filename (so
  `sha256sum -c` works in whatever directory the user dropped them in), and it must
  actually run and print that version. That last one is the only check that catches a file
  named 1.1.1 which is really last week's build.
* **Provenance stays honest.** The mirror is tracked, so every build dirties the tree, and
  `source tree : DIRTY` on every clean release would make the line mean nothing -- the line
  exists to catch a release built from private edits. `_tree_state()` therefore discounts
  the mirror *by exact name* and still names everything else. Discounting a file is not the
  same as looking away: a build with real uncommitted edits prints them.

Version numbering follows the same logic: this is still 1.1.1. The payload differs from the
first 1.1.1 by documentation and comments, so the release was re-tagged and its assets
replaced rather than inventing a 1.1.2 for hygiene. The digest changed, which is the part a
reader is entitled to know, and the release notes say so instead of pretending the file was
always this one.

v1.1.0 itself is gone -- release *and* tag, on the server and in the local clone. A release page
listing a version whose artefacts were superseded by a re-tagged one invites someone to install
the broken build, and one line in the v1.1.1 notes carries the history better than a dangling
entry does. The commit it pointed at is still an ancestor of `main`, so the tag can be recreated
in one command if it is ever needed; what matters is that nothing publishes it by accident.

**Which is why the local tag went too.** `git push --follow-tags` pushes the tags in the local
clone, so a tag deleted on the server and left behind at home comes back on the next ordinary
push -- a zombie release, resurrected by a flag that looks like hygiene. Either delete the tag
where both copies live, or don't delete it at all. (This repo has used `--follow-tags`; from here
on the release flow is `git push` then `git push origin <tag>`, which does exactly what you
meant and nothing else.)

