# Security

Short version, for the person deciding whether to type a password: GlassPad draws a
touchscreen gamepad on top of your games and creates a **virtual Xbox 360 controller** so
the game believes one is plugged in. Everything else on this page follows from those two
facts.

## What the software is, in capability terms

| Capability | Why it is needed | Where it is granted |
| --- | --- | --- |
| Draw a fullscreen, transparent, click-through surface above other windows | the overlay | `zwlr_layer_shell_v1`, a compositor protocol. Revoke by closing the app |
| Create and write a virtual input device | the gamepad | `/dev/uinput`, one command, one password: `glasspad --install-uinput` |
| Write files in your home directory | layout, menu entry, icon | your own account, no privileges |

It runs as **your** user, not as root, and not inside a sandbox: an AppImage sandboxes
nothing. The privilege it asks for is the one time, in `/etc`, and it names both files
before asking for the password.

## The grant is wider than the app needs, and that is disclosed at the prompt

`/dev/uinput` is owned by group `input`, and `input` is the group that owns **every**
evdev node on the machine. Membership does not only let GlassPad write a gamepad — it lets
anything running as you read every keyboard, mouse and touchpad. The write you want and
the read you did not ask for are the same group, and group membership is permanent and
outlives the application.

Two things in the code are there because of that:

* **The grant is the smallest one that works, decided rather than assumed.** Before
  writing anything, `--install-uinput` asks whether *your account* can already write the
  node. logind puts a `uaccess` ACL on it for the active session, and Steam's
  `60-steam-input.rules` and KDE Connect's rule tag it the same way. On a great many
  machines the answer is yes, and GlassPad then installs **nothing**: no file in `/etc`, no
  group membership. `--force-rule` overrides that, for a machine that wants the policy
  written down.
* **The disclosure is printed at the moment of decision**, at the password prompt — not
  only in a README. If `input` membership is too wide for this machine, do not install the
  rule; the output explains what already covers the node instead.

`--install-uinput` also adds your account to `input` — but only if the node is not already
writable and you are not already a member, and it records whether *this run* added it, so
the "log out and back in" advice is only given when it can help.

## The threat model of a virtual gamepad

A device that looks like an Xbox 360 controller is, from the desktop's point of view, a
way to send input. Two consequences worth stating rather than hiding:

1. **Whatever can write `/dev/uinput` can synthesize controller input as you.** That is
   not unique to GlassPad — Steam and KDE Connect both ship rules for the same node — but
   it is why GlassPad does not install anything when access already exists, and why the
   rule is `MODE="0660"`, group `input`, never `0666`. A world-writable gamepad is a door
   left open for every sandboxed app on the machine.
2. **The overlay is a transparent surface that can cover a game, and a control that is
   drawn is a control that can be pressed.** There is no authentication between a touch and
   a button event: that is the feature. Anyone with physical access to the touchscreen has
   the input. Lock the screen, not the layout.

## Privileged code, and what runs as root

The privileged half is [`glasspad/uinput_setup.py`](glasspad/uinput_setup.py). It is
**standard library only**, and that is a security property rather than a style choice:
root cannot read an AppImage payload (the FUSE mount is restricted to whoever mounted it),
so the script is copied out to a fresh `0700` directory together with the one rule file it
installs, and *that* is what `pkexec` runs. Copying a self-contained script is auditable;
copying a package, and handing root a `PYTHONPATH` into a temporary directory, is not.

Read it before you run it. It is about 400 lines, it takes one password, and it prints
every path it writes.

**There is one policy file.** The rule lives at
[`packaging/glasspad-udev.rules`](packaging/glasspad-udev.rules) and the script copies it
verbatim, or refuses to run. There is deliberately no embedded fallback text, and a test
scans the whole tree for a second definition of the mode — two versions of a permission
policy is how a machine ends up running the one nobody reviewed. The file is checked with
`udevadm verify` in the test suite.

## Removal

```bash
glasspad --uninstall-uinput      # the rule and the modules-load file, and nothing else
glasspad --uninstall-desktop     # the menu entry and icon
```

`--uninstall-uinput` is narrow on purpose: it does not touch your layout, your desktop
entry, the loaded kernel module, or group membership. It removes group membership never,
because `input` predates GlassPad on plenty of machines and other devices depend on it —
and it will tell you, measured as *your* account, if the node stays writable afterwards
because some other rule is granting it. Removing our rule from such a machine changes
nothing about your access, and pretending otherwise would be a claim you could disprove by
opening the overlay.

## What GlassPad does not do

These are properties with tests behind them, not intentions:

* **No network access.** Nothing in the package imports `socket`, `http`, `urllib` or any
  HTTP client; the suite fails if one appears.
* **No telemetry, no update checker, no analytics.** Nothing phones home, so nothing can.
* **Nothing from your system is bundled into the AppImage** — no GTK, no layer-shell
  library, no Python. It runs against your distribution's own libraries, which means your
  distribution's security updates reach the code GlassPad is actually running. (The
  AppImage runtime is the one binary that is fetched rather than bundled; it is pinned by
  SHA-256 in `packaging/runtime-pin.json`, verified on download, and recorded, with its
  hash, in `BUILD-INFO.txt` beside every artefact.)
* **No root at runtime.** `--install-desktop` writes only inside `$XDG_DATA_HOME`; the
  overlay itself writes only `$XDG_CONFIG_HOME/GlassPad`.
* **No world-writable device, ever.** `0660` and group `input`, verified against the
  `udevadm` linter.

## Reporting a vulnerability

Open a **private security advisory** on the repository (Security → Report a vulnerability)
rather than a public issue, so a fix can exist before a description does. Public issues are
the right place for "the overlay does not appear", and the wrong place for anything that
explains how to make a machine press its own keys.

If a report concerns the udev rule or the grant path, include the output of
`glasspad --status` — it prints the whole device chain, including whether the grant came
from GlassPad or from something else on the machine.
