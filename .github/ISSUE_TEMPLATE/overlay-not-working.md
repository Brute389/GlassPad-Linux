---
name: Overlay not working
about: Controls do not appear, do not respond, or do not reach the game
title: "[overlay] "
labels: [bug]
body:
  - type: markdown
    attributes:
      value: |
        The overlay has four independent ways to fail, and from the couch they are
        identical: controls on screen, game unresponsive. They are

        1. no Wayland session (or an exclusive-fullscreen game holding the screen),
        2. `libgtk4-layer-shell` missing, so the overlay starts blank,
        3. a compositor that does not advertise `zwlr_layer_shell_v1`,
        4. `/dev/uinput` your account cannot write, so a pad is drawn and nothing is sent.

        `--status` tells all four apart in about a second, which is why it is the first
        field below and not a suggestion. Please paste it verbatim rather than
        summarising it — the wording of a line is often the answer.

  - type: textarea
    id: status
    attributes:
      label: 'Output of `glasspad --status`'
      description: >
        Run it in the same session you run the overlay in. If you launched it from a
        manager (Steam Big Picture, RetroDeck, GameScope), say so below — a manager can
        start it with an environment that a terminal does not have, and `--status` shows
        the difference.
      render: text
      placeholder: |
        session         : ...
        layer-shell lib : ...
        compositor      : ...
        /dev/uinput     : ...
        grant           : ...
    validations:
      required: true

  - type: textarea
    id: selftest
    attributes:
      label: 'Output of `glasspad --selftest`'
      description: >
        Runs the whole chain against a null pad and prints ~95 checks. This one needs a
        Wayland session; if it cannot run, say what it printed instead.
      render: text
    validations:
      required: true

  - type: dropdown
    id: symptom
    attributes:
      label: What happens
      options:
        - Nothing appears at all
        - Overlay appears, taps do nothing
        - Taps register on the overlay, the game ignores them
        - Overlay appears *under* the game
        - Appears, then stops responding after a while
        - Crashes or exits immediately
        - Something else
    validations:
      required: true

  - type: dropdown
    id: compositor
    attributes:
      label: Compositor
      options:
        - KDE Plasma (KWin)
        - Hyprland
        - Sway
        - wlroots-based (other)
        - KDE with XWayland/X11 app
        - GNOME
        - Other / unknown
    validations:
      required: true

  - type: input
    id: distribution
    attributes:
      label: Distribution and how GlassPad was started
      description: >
        e.g. `CachyOS, ./GlassPad-1.1.1-x86_64.AppImage from a terminal`, or
        `Fedora 41, python3 -m glasspad from a checkout, launched by Steam as a
        non-Steam game`. A menu entry and a terminal are different environments.
    validations:
      required: true

  - type: input
    id: game
    attributes:
      label: Game, and how it is running
      description: >
        Proton or native, and whether the game is exclusive fullscreen, borderless or
        windowed. Exclusive fullscreen is a different code path in every compositor.

  - type: textarea
    id: logs
    attributes:
      label: Relevant log output
      description: >
        If you started it from a terminal, paste what it printed. If a manager started it,
        the journal is the only copy:
        `journalctl --user -b -0 --since "-10 min" | grep -i glasspad`.
        `GLASSPAD_LOG=full` disables the status-line folding, if the missing line is a
        per-event coordinate.
      render: text

  - type: checkboxes
    id: tried
    attributes:
      label: Things that are worth ten seconds before writing
      options:
        - label: '`glasspad --status` was run in the same session as the overlay'
          required: true
        - label: I ran `glasspad --install-uinput`, and logged out and back in if it said the group was newly added
        - label: I tried a windowed or borderless game, to rule out exclusive fullscreen
        - label: I read the Troubleshooting section of the README

  - type: textarea
    id: notes
    attributes:
      label: Anything else
      description: What you expected, what changed just before it stopped, and anything you already tried and ruled out.
