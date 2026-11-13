# GlassPad (Linux)

Touchscreen → virtual Xbox 360 gamepad overlay for Linux, ported from GlassPad
1.0.2 (Windows/WPF + HIDMaestro). The overlay draws touch controls on top of a
game and forwards touches to a kernel `uinput` device that identifies itself as
a wired Microsoft 360 pad, so anything that accepts a controller accepts it.

**Current version: 1.1.0** (Linux port). The reasoning behind each change is in
`git log`, not a changelog file — the history carries the *why* alongside the diff,
and keeping a second copy in sync is a chore that goes stale within a release.
`glasspad --version` and the About box
report the same number, and `test_version_strings_agree()` fails if they
ever stop. The Windows original stays 1.0.2; the docstrings naming it are
provenance, not this version.

HIDMaestro has no Linux build, so the transport is `uinput`; everything else —
zone mapping, `ToHM()` normalisation, layout format, resting state — is
preserved from the original. A `layout.json` written by the Windows app loads
here unchanged.

## Requirements

* **Python 3 with GObject introspection, GTK 4, and `libgtk4-layer-shell`.** These are
  the ones the shipped overlay cannot start without — the last is the library that puts a
  surface above a fullscreen game, and `--status` prints where it found it or names the
  package to install:

  | | Arch / CachyOS | Debian / Ubuntu | Fedora |
  | --- | --- | --- | --- |
  | bindings | `python-gobject` | `python3-gi` | `python3-gobject` |
  | GTK 4 | `gtk4` | `gir1.2-gtk-4.0` | `gtk4` |
  | layer shell | `gtk4-layer-shell` | `libgtk4-layer-shell0` | `gtk4-layer-shell` |

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

* **A compositor offering `zwlr_layer_shell_v1`** — KWin, Hyprland, Sway, wlroots. Stock
  GNOME is the common one that does not.
* Developed on CPython 3.12–3.14. Nothing uses a runtime-evaluated PEP 604/585 annotation
  (every module defers them via `from __future__ import annotations`), so an older 3.x will
  very likely work, but it has not been checked.
* Avoid `return` inside a `finally` block. It is a `SyntaxWarning` on newer
  CPython and is slated to become a `SyntaxError`, and it also silently swallows
  any in-flight exception — teardown belongs after the `try`/`except` chain so
  the handlers set a return code.
* Linux with the `uinput` module, writable to your user (the installer enables both)
* Optional: `python-evdev` for reading the virtual pad back (`tools/probe_real_pad.py`).

`PyQt6` used to be the first line of this list. The Qt/X11 backend it powered is gone —
it was a second GUI toolkit, a third of the package bytes, and nothing inside a shipped
artefact that exercised it. Its module names survive only in `glasspad/bundle.py`, as the
list the artefact tests refuse to ship.

## Tested on

One column says *measured*, the other says *reasoned about*, and the difference is the
whole point of writing this down: a compatibility list that does not distinguish them is
advertising.

| Compositor | Status | What that actually means |
| --- | --- | --- |
| **KDE Plasma, KWin on Wayland** | **measured** | Everything in this README — every pixel measurement, every stacking claim, the in-game tests, the four-simultaneous-touch checks, the udev grant on a virgin system — was produced on one machine running KWin. This is the reference environment. |
| **Steam Big Picture / GameScope session** | **measured** | Installed through a controller-oriented manager, launched without a terminal, overlay above a fullscreen game, mapped through Steam Input to the game's own bindings. |
| Hyprland | reasoned, not run | Needs `zwlr_layer_shell_v1` and a writable `/dev/uinput`, both of which it has. What has not been checked here is *stacking*: Hyprland's layer rules and its fullscreen handling differ from KWin's, and stacking is the thing that has actually gone wrong in development. `glasspad --status` will tell you the protocol is there; it cannot tell you the overlay lands above your game. |
| Sway, other wlroots compositors | reasoned, not run | Same protocol, same reasoning, same caveat. Per-control touch-through under a wlroots compositor with a window rule of its own is an open question. |
| GNOME (mutter) | **not supported** | mutter does not implement `zwlr_layer_shell_v1`. `--status` says so plainly rather than starting an overlay you cannot see. A shell extension that provides the protocol may change that; nobody has tried one. |
| X11, on any desktop | **not supported** | There is no layer-shell on X11. The Qt/X11 experiment that existed early in this port is deleted, along with PyQt6. |

If you get it working somewhere in the "reasoned" row, that is worth an issue — not a bug
report, just a sentence saying which compositor and whether the overlay stayed above the
game. The row is the honest claim; a report is what turns it into the row above it.

## Install

There is one install, and it is the AppImage. Everything a file can do by itself it does
by itself; the one step that needs a password is *inside* the file rather than beside it,
so there is nothing to download twice and no directory to keep the parts in.

```bash
chmod +x GlassPad-1.1.0-x86_64.AppImage
./GlassPad-1.1.0-x86_64.AppImage --install-desktop    # menu entry + icon; no root
./GlassPad-1.1.0-x86_64.AppImage --install-uinput     # one password, once -- read what it prints
./GlassPad-1.1.0-x86_64.AppImage                      # the overlay
```

From a checkout, the same commands are `python3 -m glasspad --install-desktop` and so on.
Nothing else in this repository is an installer.

There used to be a shell one. It did a third of its job redundantly, could not be used by
the AppImage anyway, and left two files to keep in step where one would do; its privileged
half is now [`glasspad/uinput_setup.py`](glasspad/uinput_setup.py), packed inside the
payload — see [Where the privileged half lives](#where-the-privileged-half-lives). What
its `--autostart` mode did is one command of your own, and the menu entry is the reason
nobody needed it:

```bash
cp ~/.local/share/applications/glasspad.desktop ~/.config/autostart/
```

### First run on a machine that has never had GlassPad

Three things stand between a downloaded file and a pad that reaches a game. Two need
a password; one needs a package manager, which is the single thing this program will
not run on your behalf. `--install` does the two it can and names the one it cannot:

```bash
chmod +x GlassPad-1.1.0-x86_64.AppImage
./GlassPad-1.1.0-x86_64.AppImage --install           # three steps, one password
./GlassPad-1.1.0-x86_64.AppImage                     # done
```

The `./` is not decoration: bash does not search the current directory, so the bare
filename answers `command not found` about a file that is perfectly fine — which is
exactly what the last person to install this typed.

`--install` (alias `--setup`) runs, in this order:

1. **`libgtk4-layer-shell`** — looked for, not installed. If it is missing, the package
   command for *this* distribution is printed and the run stops there: asking for a
   password for an overlay that cannot be drawn is a bad trade.
2. **`/dev/uinput`** — one password. Loads the `uinput` module at boot, installs the
   udev rule, adds your account to `input`.
3. **A menu entry and a `glasspad` command** — no password. The menu entry makes it
   clickable; `~/.local/bin/glasspad` makes every sentence this app prints — *run this*,
   *confirm with that* — one you can follow. It refuses to write the command where
   `PATH` cannot see it, and refuses to replace one that belongs to something else
   without `--force`, because an install that announces success and then answers
   `command not found` is the specific failure this path exists to remove.

`--install --dry-run` rehearses all three, step 3 included, with no password and
nothing written. Afterwards `--status` ends with one `NEXT:` line, so the report comes
with its conclusion instead of leaving you to derive it from seven facts.

The narrow commands remain for when you want one step rather than three:
`--install-uinput`, `--install-desktop`, and their `--uninstall-` counterparts.

The rule step 2 writes is
[`packaging/glasspad-udev.rules`](packaging/glasspad-udev.rules), copied verbatim —
it is a separate file so you can read it and so `udevadm verify` can check it, and the
grant refuses to run without it rather than falling back to a copy of its own. Add
`--dry-run` to see the plan with no password at all.

If that command says the group was newly added, it takes effect at the **next
login**: a process keeps the groups it was started with, and no amount of
`udevadm trigger` changes that.

Until the permission exists the overlay still appears and every control still
lights up under your finger, and not one press reaches the game. That is now said
out loud — once on stderr, and as `NO PAD` on the overlay itself — because a pad
that silently does nothing is indistinguishable from an overlay that is broken,
which is what a fresh install used to look like.

Note that nothing relocates. `--install-desktop` writes only inside `$XDG_DATA_HOME`;
`--install-uinput` writes two absolute `/etc` paths and names both before asking for a
password. There is no `PREFIX` to set, which removes the trap that came with one: a
"rehearsal" that redirected the harmless half and still edited `/etc`. `--dry-run` is the
only flag that promises not to touch the machine, and it elevates nothing.

Run it from a checkout:

```bash
python3 -m glasspad            # the overlay
python3 -m glasspad --status   # environment + backend report
python3 -m glasspad --null-pad # UI only, no virtual device
```

### Two ways in: run the file, or drop it on a manager

There are two legitimate first-run routes and the installer has to behave in both,
because they leave different things behind.

**Run it.** `./GlassPad-1.1.0-x86_64.AppImage --install` does the whole job: checks the
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

## Packaging: one AppImage, and what a file cannot carry

```bash
./GlassPad-1.1.0-x86_64.AppImage --status              # read this first
./GlassPad-1.1.0-x86_64.AppImage --install-uinput      # one password, once
./GlassPad-1.1.0-x86_64.AppImage --install-desktop     # menu entry + icon, no root
./GlassPad-1.1.0-x86_64.AppImage                       # the overlay
./GlassPad-1.1.0-x86_64.AppImage --uninstall-uinput    # take the rule back
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
sha256sum -c GlassPad-1.1.0-x86_64.AppImage.sha256    # from the directory holding it
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
dist/GlassPad-1.1.0-x86_64.AppImage --appimage-extract-and-run status
dist/GlassPad-1.1.0-x86_64.AppImage status      # mounted, via squashfuse
```

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
"$HOME/Downloads/GlassPad-Linux/dist/GlassPad-1.1.0-x86_64.AppImage" \
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

## Check your tree

Three commands, in order of how much they cost:

```bash
python3 -m glasspad --status      # this machine: library, compositor, grants, profiles
python3 -m glasspad --selftest    # the chain end to end, against /dev/null: 95 checks
python3 tools/check_all.py        # the whole suite: 27 suites, ~35 s
```

`--status` changes nothing. It is the "why is there no overlay on screen" command, and it
walks the chain the overlay actually needs — PyGObject, GTK 4, the typelib,
`libgtk4-layer-shell`, a Wayland session, the compositor's `zwlr_layer_shell_v1` global,
then the device: modules-load config, the udev rule, whether *your account* can write
`/dev/uinput`, and group membership — in the order they fail. Exit status is 0 only when
the overlay could actually run.

The input ABI is checked by **importing** the module, never by grepping it, because this
port's bug class was constants whose names were right while their numbers were off by one —
a grep for `ABS_HAT0X` matches whether it is `0x10` (a hat) or `0x39`
(`ABS_MT_TRACKING_ID`). `TestXbox360Abi` in `tests/test_glasspad.py` asserts the numbers
against the kernel headers.

## Troubleshooting

Start with the two commands that were written for this. Both print a verdict, both are
safe on a live machine, and together they cover most of what can be wrong:

```bash
glasspad --status       # library, compositor, /dev/uinput, who grants it, menu entry
glasspad --selftest     # 95 checks: geometry, mapping, ABI numbers, teardown, licences
```

If you are reporting a problem, paste both in full. A symptom without them is a
conversation that starts with "and what does `--status` say".

| What you see | What it is |
| --- | --- |
| Controls draw, animate, and nothing happens in the game | No `/dev/uinput` write access. `--status` says so. Run `glasspad --install-uinput`, then **log out and back in** — a process keeps the groups it started with, and no `udevadm trigger` fixes that. This is the most confusing failure this program has, because everything on screen looks correct. |
| Overlay appears on the desktop but vanishes in a game | The game has an **exclusive fullscreen** grab, which takes the screen from the compositor, and a layer surface lives in the compositor. Run the game in borderless/windowed fullscreen. |
| No overlay at all, and `--status` mentions `zwlr_layer_shell_v1` | Your compositor does not offer layer-shell. Stock GNOME is the usual one; KWin, Hyprland, Sway and wlroots do. |
| No overlay, and the library was not found | `libgtk4-layer-shell` is not installed, or is a different GTK 4 minor than the one linked. `--status` names the package for your distro; it is the line to trust over any table in a README. |
| Menu shows an old icon after a reinstall | The artwork changed but the version did not, so the integrated icon **name** did not — and KDE caches rendered pixmaps by name inside `plasmashell`, for the life of the process. The files on disk are right; restart the shell: `systemctl --user restart plasma-plasmashell.service`. Bump the version on artwork changes and the name changes with it. |
| Second Ctrl-C kills it outright | Intended. The first requests an orderly shutdown (release every button, destroy the pad); the second exits hard, because `os._exit` drops the last `/dev/uinput` fd and the kernel closes the device. No ghost pad is left either way. |
| A control stays pressed after you let go | Should not happen — the watchdog releases anything held past `--hold-limit`, and a release caused by the watchdog or a hide is not undone by later drag events. If you can reproduce it, capture with `GLASSPAD_LOG=full` (below) and attach `--status`. |
| Buttons respond to taps but the stick feels wrong | Check `--saturate`, `--stick-curve`, `--deadzone` — the parity target is the Windows client's `MaxTravel`, not the drawn radius, so a stick that visually bottoms out early is correct behaviour. |

### Journal and terminal noise

A manager-launched run (AppImage Menu, systemd transient unit) has no terminal, so
stdout is the journal and the journal is the only record the run leaves. Per-control
position and size readouts are therefore folded to one line per control per half second,
and the line says what it stands for:

```
status: A  125x125
status: A  212x212  (+2 updates folded)
```

One-shot messages — profile switches, refusals, why something did *not* happen — are
never folded. To see every event while debugging:

```bash
GLASSPAD_LOG=full glasspad          # or: journalctl --user -f
```

## Environment variables

Every one the code reads. The `--help` flags are the supported interface; these are the
same settings for cases where a flag cannot reach — a launcher, a manager, a service.

**Support**

| | |
| --- | --- |
| `GLASSPAD_LOG=full` | Stop folding the streaming status readouts (see above). |
| `GLASSPAD_CONFIG_DIR` | Where `layout.json` and `profiles/` live. Default `~/.config/GlassPad`; `--status` prints the resolved path. |
| `GLASSPAD_SELFTEST=1` | Run the 95-check selftest instead of an ordinary session. `glasspad --selftest` sets this and adds `--null-pad`. |
| `GLASSPAD_DEBUG` | Extra diagnostics on stderr at a few decision points (library resolution, teardown). |
| `GLASSPAD_TRACE` | Per-event trace, same as `--trace`: every touch, grab, release, fold. Loud; meant for one reproduce. |

**Behaviour**

| | |
| --- | --- |
| `GLASSPAD_HOLD_LIMIT` | Default for `--hold-limit`, the watchdog ceiling in seconds. Clamped like the flag. |
| `GLASSPAD_TERMINAL` | Which terminal to open when a privileged step needs a window (an AppImage launched from a menu has none). Validated against the known names; unknown values are refused rather than exec'd. |
| `GLASSPAD_DPAD_STYLE` | Default for `--dpad-style`. |
| `GLASSPAD_DPAD_CROSS` | Default for `--dpad-cross`. |
| `GLASSPAD_DPAD_PRESS` | Default for `--dpad-press`. |
| `GLASSPAD_PRESS` | Default for `--press`. |
| `GLASSPAD_BACKEND` | Backend selection. An unrecognised value is refused with the valid names, exactly like `--backend` — it cannot silently fall through into starting an overlay. |

**Internals — you should not need any of these**

They exist because a bundle's layout is only known at runtime, and the tests import the
packed copies rather than the checkout.

| | |
| --- | --- |
| `GLASSPAD_LAYER_SHELL` | Absolute path to `libgtk4-layer-shell.so.0`, for a library the loader cannot find. `--status` prints this exact hint when it is the missing piece. |
| `GLASSPAD_BUNDLE`, `GLASSPAD_BUNDLE_LIB` | Bundle root markers set by the launcher. `APPDIR` is honoured as well. |
| `GLASSPAD_BIN_DIR` | Where the `glasspad` command on `PATH` is created. Default `~/.local/bin`. |
| `GLASSPAD_INSTALLER_DIR` | Where the privileged pair (script + rule) is searched for. Put an empty directory here to rehearse "this build ships nothing". |
| `GLASSPAD_DOC_ROOT` | Where `LICENSE` and `licenses/THIRD-PARTY-NOTICES.txt` are looked up by the About box. |
| `GLASSPAD_RUNTIME` | Build-time only. An explicit AppImageKit runtime ELF. **Refused** unless it matches `packaging/runtime-pin.json` — the override that also silenced the check would make the pin decoration. |
| `GLASSPAD_RUNTIME_DONORS` | Build-time only. Colon-separated `.AppImage` files to borrow a runtime from when the pinned download is unreachable. Default: the usual AppImage directories. |
| `GLASSPAD_NO_BORROW=1` | Build-time only. Refuse a borrowed tool (see the packaging section). |
| `GLASSPAD_KEEP_STAGING=1` | Build-time only. Leave the AppDir staged for inspection instead of cleaning it. |
| `GLASSPAD_UINPUT_NODE` | The node the permission checks look at. Default `/dev/uinput`; it exists so the skip-versus-grant decision in `install.sh` can be tested on a machine whose real `/dev/uinput` is already working. |
| `GLASSPAD_ENV_HINT` | The hint string printed when the layer-shell library cannot be found. Override only to reword the message; it is a message, not a path. |

## Tests

What runs automatically, and what no runner can check, are both in
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) — but the second half is worth
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
  The live self-test (`--selftest`, 95 checks) covers as much of that chain as it can
  against `/dev/null`, and it is the one suite CI skips.


```bash
python3 tools/check_all.py                        # everything, ~35 s
python3 tools/run_tests.py                        # the pytest-style suite on its own
python3 tools/check_all.py --no-legacy --no-live  # quick loop while editing
```

There are two runners and they are **not** duplicates. The stdlib suites each run on their
own; `tests/test_glasspad.py` is written in pytest style, and `tools/run_tests.py` executes
it through its own small pytest stand-in (session and function fixtures with yield
teardown, `parametrize`, `tmp_path`, `monkeypatch`, `approx`, `skip`). pytest is not
installed here and cannot be fetched, and a safety net that needs a package you lack is not
a safety net. This is also why `check_all.py` routes that one file to the other runner
instead of executing it: run directly it can only print *No module named pytest*, which is
how several hundred tests sit quietly inside a green-looking tree.

Current tallies here:

| piece | tally |
| --- | --- |
| stdlib suites (25 files) | **311 passed** |
| pytest-style suite, via `tools/run_tests.py` | **91 passed, 0 skipped** |
| in-app self-test (`GLASSPAD_SELFTEST`: real hub, resize, rotation, profiles, turbo, licence reader) | **95 checks** |

Those numbers are what `tools/check_all.py` prints on this machine, not an assertion — no
test compares the README to the runner, because a pin that has to be edited every time a
test is added is a pin people edit rather than read.

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

## Tools

| Tool | What it proves |
| --- | --- |
| `tools/probe_real_pad.py` | Creates the **real** pad and reads identity, `PRODUCT` and every axis range back out of the kernel, then destroys it and verifies nothing was left. Needs a writable `/dev/uinput`. |
| `tools/uinput_watch.py` | Snapshots kernel input devices and reports any pad a test leaked. `run -- <cmd>` wraps the suite so nothing hides between steps. |
| `tools/run_tests.py` | Runs the pytest-style suite with no pytest installed, including the uinput-safety legs. `check_all.py` calls it; you rarely need to. |
| `tools/check_all.py` | Every suite plus the live self-test; exit status is real, and a suite that cannot start counts as failing. |
| `tools/make_appimage.sh` | Stage the AppDir (validated desktop entry, `AppRun`), run it, then pack; cleans its own scratch on success. |
| `tools/make_appimage.sh` | Stage the AppDir (validated desktop entry, `AppRun`), run it, then pack if the tooling exists — and say plainly what is missing when it does not. |
| `tools/glasspad-profile` | Claim a profile for the overlay (`--seed`, `--list`, `--status`, `--clear`). Exits 0 unless the *arguments* were wrong — a launcher must never fail because of the overlay. |
| `tools/mk_appimage_offline.py` | Pack a staged AppDir into a real type-2 `.AppImage`, using the package-owned `mksquashfs` and the pinned AppImageKit runtime (borrowing only as a fallback, and announced when it happens), checking the payload offset and writing BUILD-INFO plus the sha256 sidecar. |

Also here, and deliberately: the tree holds only what is needed to build, install
and verify. The measurement tools that derived this spec — `render_style.py` and
`render_dpad.py` (offscreen renders with numerical checks), `resize_probe.py`
(whether a mapped layer surface can resize in place), `hit_test.py`, `pad_probe.py`,
`layer_probe.py`, `verify_screen.py`, `render_stick_curve.py` — were trimmed out.
Prose in `glasspad/*.py` still names them as the origin of specific numbers, and
`test_no_dangling_file_references` treats them as citable-but-unresolvable for that
reason. All of them are in `git log` if a control ever needs re-measuring on real
hardware.

A green test suite does **not** prove no pad was created: `PadService` turns
backend errors into a status line, so a test that reaches the backend passes
either way. That is why `uinput_watch` exists, and why `tools/run_tests.py` runs the suite
again with pad creation made impossible and counts the attempts.

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
it was measured before it was built on (by `resize_probe.py`, now trimmed from the
tree — see `git log`): a plain size request
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
```

The table below is that curve expressed in millimetres of thumb travel; it was
computed by a renderer that has since been trimmed from the tree, and `git log`
still has it.

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

`glasspad/layer_geom.py` reads the Windows `layout.json` format as-is. The file
holds only geometry, as an ordered array with **no names**, matched positionally
against `all_editable` (all 13 buttons first, then dpad and sticks -- the C#
original concatenates two lists, so flattened index order differs from build
order). Nothing in it refers to kernel event codes, so the ABI corrections above
required no migration of files on disk.

## License

MIT — see `LICENSE` at the repository root. It carries the upstream copyright because
this is a derivative work: the layout geometry, colours and animations are transcribed
from `MainWindow.xaml` of GlassPad 1.0.2 for Windows by Dansunmi, and the thumbstick
response from `Input/StickZone.cs` of the same project.

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
