"""GlassPad — touchscreen -> gamepad overlay for Linux.

Original: GlassPad 1.0.2 for Windows (C# / WPF / HIDMaestro) by Dansunmi, MIT
licensed.

Two backends, one of which ships:

  layer  (default, and the only one in the bundles)
      GTK 4 + gtk4-layer-shell, one surface per control. This is the one that
      stays above a fullscreen game, because it asks the compositor for a
      layer instead of winning a stacking contest.

  qt     (kept, needs a checkout and PyQt6)
      The original port. Still the route for a compositor without
      zwlr_layer_shell_v1. glasspad/bundle.py lists the modules, and both
      packaging tools exclude them, so asking for --backend qt inside a bundle
      says so rather than importing half a toolkit.

Module map, Windows original -> here:

    Input/PadService.cs         -> pad.py                 (both backends)
    Input/GamepadAxes.cs        -> pad.py, xd360.py       (both backends)
    Input/StickZone.cs          -> layer_stick.py (layer), zones.py (qt)
    Input/DpadZone.cs           -> layer_dpad.py (layer), zones.py (qt)
    TouchHandler.cs             -> overlay_layer.py gestures (layer);
                                   touch_router.py (qt -- upstream's own class is
                                   never instantiated, so both are re-imaginings
                                   of its intent rather than ports)
    Overlay/GamepadLayout.cs    -> layer_geom.py (layer), layout.py (qt)
    Overlay/LayoutEditor.cs     -> edit mode in overlay_layer.py; editor.py (qt)
    Overlay/Win32Helper.cs      -> passthrough.py (qt only; there is no Win32
                                   equivalent to do this on Wayland, and the
                                   compositor was measured not to offer the
                                   input-region version)
    MainWindow.xaml[.cs]        -> overlay_layer.py + layer_style.py (layer),
                                   window.py + widgets.py (qt)
    LicenseWindow.xaml[.cs]     -> license_window.py (qt only)

Linux-only pieces with no Windows ancestor: bundle.py (where the layer-shell
library lives, and what the payloads exclude), watchdog.py (a control held too
long), layer_lock.py (a release the finger does not know about), layer_edge.py
(the band along the bezel), layer_turbo.py (autofire), layer_profiles.py (a
layout per game).
"""
from __future__ import annotations

__version__ = "1.1.0"
__all__ = ["__version__"]
