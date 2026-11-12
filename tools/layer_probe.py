#!/usr/bin/env python3
"""layer_probe - is layer-shell the fix for GlassPad on KWin/Wayland?

Tests the two load-bearing assumptions behind a rewrite:

  1. STACKING.  Surfaces sit on the layer-shell OVERLAY layer, which KWin
     composites above its fullscreen layer.  If they stay visible over a
     fullscreen game, layer-shell is the answer and window rules never were.
  2. PASS-THROUGH BY CONSTRUCTION.  The default mode is two small surfaces, not
     one full-screen canvas with an input region.  A tap between them has no
     surface to hit, so it reaches the game with no mask, no XShape and no alpha
     hit-testing -- WPF's "alpha == 0 is not hit" rule, rebuilt as geometry.

Run it, then start a fullscreen game and look at the squares:

    LD_PRELOAD=/usr/lib/liblayer-shell-preload.so python3 tools/layer_probe.py
    ... same, one full-screen surface:               ... --full

--full is the contrast case: it should swallow every tap. That difference is
the argument for per-control surfaces over porting passthrough.py.

LD_PRELOAD is required, not cosmetic: gtk4-layer-shell must be linked before
libwayland-client, and an interpreter loads libwayland first. Preload the real
library, NOT the liblayer-shell-preload.so shim its own error message suggests -
the shim leaves is_supported() False here, while the .so.0 gives True.

KWin 6.8.2 does advertise zwlr_layer_shell_v1 v5 (verified by enumerating the
registry directly), so layer-shell is available to any client that can bind it.

Ctrl-C quits; tapping a square quits too.
"""
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell as LayerShell  # noqa: E402

PRELOAD = "/usr/lib/libgtk4-layer-shell.so.0"

CSS = b"""
window.probe { background: alpha(#ff2d55, 0.45); }
window.probe label { color: white; font-size: 15px; }
"""

LOOP = None  # set by main(); handlers call LOOP.quit()


def die(msg, code=1):
    print(f"  {msg}")
    sys.exit(code)


def check_environment():
    """Fail loudly and early rather than drawing ordinary windows that lie."""
    if not os.environ.get("WAYLAND_DISPLAY"):
        die("no WAYLAND_DISPLAY - this probe only tests the Wayland path.")
    if not os.environ.get("LD_PRELOAD") or "layer-shell" not in os.environ.get("LD_PRELOAD", ""):
        die(
            "gtk4-layer-shell is not preloaded, so init would silently fall back "
            f"to an ordinary window.\n  rerun as:\n    LD_PRELOAD={PRELOAD} python3 tools/layer_probe.py",
            3,
        )
    try:
        if not LayerShell.is_supported():
            die("LayerShell.is_supported() is False on this compositor.", 3)
    except Exception as exc:  # noqa: BLE001
        print(f"  (is_supported() unavailable: {exc}; continuing)")


def geometry():
    """Screen size, used only to place the second square. Never fatal."""
    try:
        model = Gdk.Display.get_default().get_monitors()  # a Gio.ListModel
        if model.get_n_items():
            g = model.get_item(0).get_geometry()
            return g.width, g.height
    except Exception as exc:  # noqa: BLE001
        print(f"  (monitor query failed: {exc}; using 1920x1200)")
    return 1920, 1200


def layer_surface(x, y, w, h, text):
    win = Gtk.Window()
    win.add_css_class("probe")
    win.set_default_size(w, h)
    win.set_size_request(w, h)

    LayerShell.init_for_window(win)
    if not LayerShell.is_layer_window(win):
        die("init_for_window() did not produce a layer surface - preload failed.")

    LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
    # NONE is the WA_ShowWithoutActivating equivalent: the game keeps keyboard
    # focus, so a keyboard-driven game is undisturbed by the overlay existing.
    LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
    LayerShell.set_exclusive_zone(win, -1)                      # reserve nothing
    for edge in (LayerShell.Edge.LEFT, LayerShell.Edge.TOP):
        LayerShell.set_anchor(win, edge, True)
    LayerShell.set_margin(win, LayerShell.Edge.LEFT, x)
    LayerShell.set_margin(win, LayerShell.Edge.TOP, y)

    label = Gtk.Label(label=text)
    label.set_size_request(w, h)
    tap = Gtk.GestureClick.new()
    tap.connect("pressed", lambda *_: LOOP.quit())
    label.add_controller(tap)
    win.set_child(label)
    win.connect("destroy", lambda *_: LOOP.quit())
    return win


def main():
    global LOOP
    check_environment()

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    sw, sh = geometry()
    print(f"  backend: {type(Gdk.Display.get_default()).__name__}  screen {sw}x{sh}")

    if "--full" in sys.argv:
        wins = [layer_surface(0, 0, sw, sh,
                              "one full-screen layer OVERLAY surface\n"
                              "taps should NOT reach the game here")]
    else:
        wins = [
            layer_surface(60, 60, 200, 200, "A\nlayer OVERLAY"),
            layer_surface(max(60, sw - 260), max(60, sh - 260), 200, 200, "B\nquit"),
        ]
    for w in wins:
        w.present()

    LOOP = GLib.MainLoop()
    print(f"  {len(wins)} layer surface(s) up. Start a FULLSCREEN game:")
    print("  still visible?  and do taps between them reach the game?  Ctrl-C to quit.")
    try:
        LOOP.run()
    except KeyboardInterrupt:
        pass
    print("  probe closed.")


if __name__ == "__main__":
    main()
