---
name: Build, packaging or tests
about: Building from source, the AppImage builder, or the test suite
title: "[build] "
labels: [build]
body:
  - type: markdown
    attributes:
      value: |
        Two facts save a round trip:

        * `pytest` is **not** used. The suite is driven by `tools/check_all.py`, and the
          pytest-style file runs through `tools/run_tests.py`, which carries a small
          stand-in. Invoking `pytest` directly reports a missing module and loses ~90
          tests silently — a suite that never started is not a passing suite.
        * The suites *do* run headless. If one fails in CI or over ssh, that is a bug in
          the suite, not something to work around: the display-dependent tests are meant to
          report `skipped` and the tally is meant to show it.

  - type: textarea
    id: checkall
    attributes:
      label: 'Output of `python3 tools/check_all.py -v`'
      render: text
    validations:
      required: true

  - type: input
    id: distribution
    attributes:
      label: Distribution, and package versions of the dependencies
      description: >
        `python3 --version`, and how you installed `python3-gi`, GTK 4 and
        libgtk4-layer-shell. The layer-shell library is the one most often missing from a
        distribution's repositories, and the error it produces is a blank overlay rather
        than a message.
    validations:
      required: true

  - type: dropdown
    id: where
    attributes:
      label: Where this happens
      options:
        - From a git checkout
        - tools/make_appimage.sh (building the AppImage)
        - Running a built AppImage
        - CI
        - Somewhere else
    validations:
      required: true

  - type: textarea
    id: expected
    attributes:
      label: What you expected, and what happened instead
      description: Include the exact command line you typed.
