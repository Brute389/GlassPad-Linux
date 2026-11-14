"""GlassPad — touchscreen -> gamepad overlay for Linux.

Original: GlassPad 1.0.2 for Windows (C# / WPF / HIDMaestro) by Dansunmi, MIT
licensed.

One backend: **GTK 4 + gtk4-layer-shell**, one surface per control. It stays
above a fullscreen game because it asks the compositor for a layer instead of
trying to out-stack one. The Qt/X11 port that used to sit behind `--backend qt`
was removed in the orphan-code pass -- a second toolkit, a third of the package
bytes, and nothing in the shipped artefacts that exercised it. It is in git
history; `glasspad/bundle.py` keeps the list of its module names purely so the
artefact tests can refuse to ship them again by accident.

Module map, Windows original -> here:

    Input/PadService.cs         -> pad.py
    Input/GamepadAxes.cs        -> pad.py, xd360.py
    Input/StickZone.cs          -> layer_stick.py
    Input/DpadZone.cs           -> layer_dpad.py
    TouchHandler.cs             -> the gesture handlers in overlay_layer.py
                                   (upstream's own class is never instantiated,
                                   so this is a re-imagining of its intent rather
                                   than a port)
    Overlay/GamepadLayout.cs    -> layer_geom.py
    Overlay/LayoutEditor.cs     -> edit mode in overlay_layer.py
    Overlay/Win32Helper.cs      -> nothing. There is no Wayland equivalent of
                                   click-through-by-geometry, and the compositor
                                   was measured not to honour the input-region
                                   version either -- see inside_shape() in
                                   layer_geom.py, which refuses taps outside a
                                   control's figure instead.
    MainWindow.xaml[.cs]        -> overlay_layer.py + layer_style.py

Linux-only pieces with no Windows ancestor: bundle.py (where the layer-shell
library lives), watchdog.py (a control held too long), layer_lock.py (a release
the finger does not know about), layer_edge.py (the band along the bezel),
layer_turbo.py (autofire), layer_profiles.py (a layout per game).
"""
from __future__ import annotations

__version__ = "1.1.1"
__all__ = ["__version__"]
