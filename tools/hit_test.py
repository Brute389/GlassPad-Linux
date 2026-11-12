#!/usr/bin/env python3
"""Does a circular control's transparent corner swallow taps -- or not?

The overlay paints circles on square surfaces, so the corner of every round
button is transparent pixels that still belong to a surface. Whether a tap there
reaches the game is decided by the wl_surface input region, and "the widget looks
round" says nothing about what the compositor was told. This measures it instead
of assuming, which is the mistake that made me write item 1 off as a limitation.

Two of OUR OWN surfaces, no game required:

  SINK   full-screen, mapped first
  BALL   160x160 with a circle drawn in it, mapped second so it stacks on top

Tap the middle of the circle, then tap its visibly-empty top-left corner. The
log says which surface took each one:

  BALL reached        -> the circle works, corner must pass through
  corner -> SINK      -> input region already correct, nothing to fix
  corner -> BALL      -> the square is swallowing taps; needs set_input_region
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys

# Found, not hardcoded: the old /usr/lib constant was an Arch path, and the same
# search the app uses knows about Debian multiarch, Fedora and bundle directories.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from glasspad.bundle import layer_shell_path  # noqa: E402

GTK4_LAYER_SHELL, _GLS_WHY = layer_shell_path()
try:
    if not GTK4_LAYER_SHELL:
        sys.exit("libgtk4-layer-shell not found; set GLASSPAD_LAYER_SHELL=/path/to/it")
    ctypes.CDLL(GTK4_LAYER_SHELL, mode=ctypes.RTLD_GLOBAL)
except OSError:
    pass                                    # fall back to whatever LD_PRELOAD gives us

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell as LayerShell  # noqa: E402

CSS = b"""
window { background: transparent; }
.sink  { background: alpha(#102040, 0.55); color: #dfe6f5; font-size: 16px; }
"""


NORMAL = False      # set by --normal: plain windows instead of layer surfaces


def layer_window(w: int, h: int, x: int, y: int) -> Gtk.Window:
    """A layer surface, or with --normal a plain undecorated window.

    The --normal variant exists to separate two explanations for an input region
    that is accepted but has no effect: GTK ignoring the call, versus KWin
    ignoring input regions specifically on layer surfaces. If a shaped region
    works on an ordinary window and not on a layer surface, no client-side fix
    exists and the question is closed.
    """
    win = Gtk.Window()
    win.set_decorated(False)
    win.set_default_size(w, h)
    win.set_size_request(w, h)
    if NORMAL:
        return win
    LayerShell.init_for_window(win)
    LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
    LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
    LayerShell.set_exclusive_zone(win, -1)
    LayerShell.set_anchor(win, LayerShell.Edge.LEFT, True)
    LayerShell.set_anchor(win, LayerShell.Edge.TOP, True)
    LayerShell.set_margin(win, LayerShell.Edge.LEFT, x)
    LayerShell.set_margin(win, LayerShell.Edge.TOP, y)
    return win


CORNER = 26      # magenta aim-square at the ball's top-left corner


def note(text: str) -> None:
    print(f"  {text}", flush=True)


SETTING_FILE = "/tmp/hit_ab_setting"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", action="store_true",
                    help="apply a circular input region to the BALL (the fix)")
    ap.add_argument("--region-delay", type=float, default=0.0,
                    help="apply the region N seconds after map, to test whether "
                         "GTK is overwriting it during the first commit")
    ap.add_argument("--ab", action="store_true",
                    help="apply it or not at random, and stay silent about which "
                         "until the taps are in -- the honest way to check a fix "
                         "when the person measuring it also wants it to work")
    ap.add_argument("--normal", action="store_true",
                    help="use plain windows for both surfaces (discriminates GTK "
                         "vs KWin when input regions seem inert)")
    args = ap.parse_args()

    global NORMAL
    NORMAL = args.normal

    hidden = None
    if args.ab:
        import random

        hidden = random.random() < 0.5
        args.region = hidden

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    model = Gdk.Display.get_default().get_monitors()
    geom = model.get_item(0).get_geometry()
    sw, sh = geom.width, geom.height

    counts = {"sink": 0, "ball": 0}

    # --- sink: full screen, mapped first ---------------------------------
    sink = layer_window(sw, sh, 0, 0)
    label = Gtk.Label(label="SINK 0 -- tap the circle's middle, then its magenta corner")
    label.add_css_class("sink")
    label.set_hexpand(True)
    label.set_vexpand(True)
    sink.set_child(label)
    revealed = False

    def reveal() -> None:
        """Blind until the first tap, then write the setting down.

        Revealing on Ctrl-C would be useless here -- the process gets killed, so
        the answer would die with it. Writing it only after a tap keeps the
        blinding where it matters (while the taps are being made).
        """
        nonlocal revealed
        if hidden is None or revealed:
            return
        revealed = True
        try:
            with open(SETTING_FILE, "w", encoding="utf-8") as fh:
                fh.write("ON" if hidden else "OFF")
        except OSError:
            pass
        note("trial setting recorded -- ask the assistant to read it")

    sink_g = Gtk.GestureClick.new()
    sink_g.set_button(0)

    def sink_hit(_g, _n, x, y) -> None:
        counts["sink"] += 1
        label.set_text(f"SINK {counts['sink']} -- last ({x:.0f},{y:.0f})")
        note(f"SINK took the tap at ({x:.0f},{y:.0f})")
        reveal()
    sink_g.connect("pressed", sink_hit)
    sink.add_controller(sink_g)
    sink.present()

    # --- ball: a circle on a square surface, mapped second ---------------
    size = 160
    ball = layer_window(size, size, sw // 2 - size // 2, sh // 2 - size // 2)
    area = Gtk.DrawingArea()
    area.set_content_width(size)
    area.set_content_height(size)

    def draw(_area, cr, w, h) -> None:
        cr.set_source_rgba(0.10, 0.72, 0.35, 0.95)
        cr.arc(w / 2.0, h / 2.0, min(w, h) / 2.0 - 2.0, 0.0, 6.2832)
        cr.fill()
        # The decisive target: painted by this surface, deliberately OUTSIDE the
        # input region. Tap inside it and the only way the SINK can report the
        # tap is if the compositor was told to ignore this corner.
        cr.set_source_rgba(1.0, 0.10, 0.85, 0.95)
        cr.rectangle(0, 0, CORNER, CORNER)
        cr.fill()

    area.set_draw_func(draw)
    box = Gtk.Overlay()
    box.set_child(area)
    tag = Gtk.Label(label="BALL 0")
    tag.set_halign(Gtk.Align.CENTER)
    tag.set_valign(Gtk.Align.CENTER)
    box.add_overlay(tag)
    ball.set_child(box)
    ball_g = Gtk.GestureClick.new()
    ball_g.set_button(0)

    def ball_hit(_g, _n, x, y) -> None:
        counts["ball"] += 1
        tag.set_text(f"BALL {counts['ball']}")
        note(f"BALL took the tap at ({x:.0f},{y:.0f})")
        reveal()
    ball_g.connect("pressed", ball_hit)
    ball.add_controller(ball_g)
    ball.present()

    if args.region and not args.ab:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from glasspad.layer_geom import circle_region

        region = circle_region(size, size)

        def attempt(*_a):
            surface = ball.get_surface()
            if surface is None:
                return True                       # not mapped yet
            surface.set_input_region(region)
            note(f"input region applied to BALL: {region.num_rectangles()} spans, "
                 f"extents {region.get_extents().width}x{region.get_extents().height}")
            return False
        if args.region_delay > 0:
            note(f"applying the region after {args.region_delay:.1f}s -- wait for the line before tapping")
            GLib.timeout_add(int(args.region_delay * 1000), attempt)
        else:
            GLib.idle_add(attempt)
    elif args.ab:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from glasspad.layer_geom import circle_region

        if hidden:
            region = circle_region(size, size)

            def attempt_ab(*_a):
                surface = ball.get_surface()
                if surface is None:
                    return True
                surface.set_input_region(region)
                return False
            GLib.idle_add(attempt_ab)
        note("trial running: input region ON or OFF, not saying which until you tap")
    else:
        note("no input region set -- expect the BALL's square to swallow corner taps")

    note(f"screen {sw}x{sh}; ball at ({sw // 2 - size // 2},{sh // 2 - size // 2}) {size}x{size}")
    note("tap 1: middle of the circle")
    note(f"tap 2: INSIDE the magenta square at the ball's top-left corner ({CORNER}x{CORNER}, at screen "
         f"{sw // 2 - size // 2}..{sw // 2 - size // 2 + CORNER})")
    note("each surface shows its own count: if neither number moves, the tap "
         "landed on neither -- say so rather than assuming the region failed")
    note("Ctrl-C to quit")
    try:
        GLib.MainLoop().run()
    except KeyboardInterrupt:
        pass
    if args.ab:
        note(f"trial setting was: input region {'ON' if hidden else 'OFF'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
