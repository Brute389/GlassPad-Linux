"""GlassPad — touchscreen → gamepad overlay, Arch Linux port.

Original: GlassPad 1.0.2 for Windows (C# / WPF / HIDMaestro) by Dansunmi,
MIT licensed. This is a behaviour-preserving port to PyQt6 + Linux uinput.

Module map (Windows original -> here):
    MainWindow.xaml[.cs]        -> window.py + widgets.py
    Input/PadService.cs         -> pad.py
    Input/GamepadAxes.cs        -> pad.py (axis table in xd360.py)
    Input/StickZone.cs          -> zones.py::StickZone
    Input/DpadZone.cs           -> zones.py::DpadZone
    Input/TouchHandler.cs       -> touch_router.py (upstream's class is itself
                                    never instantiated, so this is a re-imagining
                                    of its intent rather than a port)
    Overlay/GamepadLayout.cs    -> layout.py
    Overlay/LayoutEditor.cs     -> editor.py
    Overlay/Win32Helper.cs      -> passthrough.py
    LicenseWindow.xaml[.cs]     -> license_window.py
"""
from __future__ import annotations

__version__ = "1.1.0"
__all__ = ["__version__"]
