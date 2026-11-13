#!/usr/bin/env python3
"""A plain dark window to photograph the overlay against.

Not part of the product, and not imported by anything. Two README pictures came from it,
and it exists because of the way you get a clean one:

The obvious thing -- screenshot the overlay over your desktop -- puts whatever the desktop
holds into a public repository. On the machine these pictures were taken on that meant a
Steam game's launcher symlink, a scratch file named after a self-hosted service, a
wallpaper, and the pinned-app strip in the panel. Nobody reviews a PNG for that; the
picture is reviewed by looking at it, and the interesting pixels are exactly the ones you
are NOT looking at.

So: cover the desktop, then shoot.

    python3 tools/shot_stage.py 14 &      # a window for 14 seconds
    sleep 2
    python3 -m glasspad --null-pad &      # the overlay on top of it
    sleep 3
    spectacle -b -n -o shot.png

**The finding worth recording:** the first version of this called ``win.fullscreen()``,
and the overlay disappeared. KWin raises a fullscreen window above layer-shell surfaces --
the same stacking rule that makes an exclusive-fullscreen game swallow the overlay, which
is documented under "Stacking" in the README. A maximized, undecorated window covers the
desktop's icons, leaves the compositor's normal stacking alone, and the overlay sits on top
of it exactly as it does over a windowed game. That is why this file is thirty lines of
GTK rather than a fullscreen black rectangle.

Standard library plus PyGObject; no dependency on GlassPad itself, so it runs on a machine
where the overlay will not start.
"""
from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

DEFAULT_SECONDS = 14.0
DEFAULT_COLOUR = "#14161a"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    seconds = float(argv[0]) if argv else DEFAULT_SECONDS
    colour = argv[1] if len(argv) > 1 else DEFAULT_COLOUR

    win = Gtk.Window()
    win.set_decorated(False)          # no titlebar in the frame
    win.maximize()                    # NOT fullscreen(); see the module docstring

    provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(
        win.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    provider.load_from_string(f"window {{ background-color: {colour}; }}")
    win.present()

    # Gtk.main() is gone from GTK 4.10+; the loop is explicit now, and a timed exit means
    # a forgotten stage window does not sit on the screen while you are trying to shoot it.
    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(1, lambda: print("stage up", flush=True) or False)
    GLib.timeout_add_seconds(max(1, int(seconds)),
                             lambda: (win.destroy(), loop.quit(), False)[-1])
    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
