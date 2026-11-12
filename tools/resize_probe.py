#!/usr/bin/env python3
"""Can a mapped layer surface change size in place, or must it be remapped?

Resizing controls in edit mode depends entirely on the answer, and it is a
compositor question rather than a code question: gtk4-layer-shell negotiates the
surface size through the layer protocol, and KWin may or may not honour a size
change on an already-mapped layer surface. Measured here, on the machine that has
to run it, rather than assumed.

Three strategies are tried in sequence on the same surface and the drawn extent is
photographed after each:

    1  set_size_request + set_default_size + the drawing area's content size
    2  the above, then unmap and remap (hide -> set -> present)
    3  destroy the surface and build a new one at the new size

Run:  python3 tools/resize_probe.py          (needs the compositor, obviously)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad.overlay_layer import LayerShell, _HOW  # noqa: E402  (binds lib too)

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

SIZE0 = 60
SIZE1 = 130
X, Y = 400, 300


def make(size: int) -> tuple[Gtk.Window, Gtk.DrawingArea]:
    win = Gtk.Window()
    win.add_css_class("ctrl")
    LayerShell.init_for_window(win)
    LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
    LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
    LayerShell.set_exclusive_zone(win, -1)
    LayerShell.set_anchor(win, LayerShell.Edge.LEFT, True)
    LayerShell.set_anchor(win, LayerShell.Edge.TOP, True)
    win.set_default_size(size, size)
    win.set_size_request(size, size)
    area = Gtk.DrawingArea()
    area.set_content_width(size)
    area.set_content_height(size)
    area.set_hexpand(True)
    area.set_vexpand(True)

    def paint(_a, cr, w, h):
        cr.set_source_rgba(0.2, 0.85, 0.5, 0.85)
        cr.arc(w / 2.0, h / 2.0, min(w, h) / 2.0 - 2, 0, 6.2832)
        cr.fill()

    area.set_draw_func(paint)
    win.set_child(area)
    LayerShell.set_margin(win, LayerShell.Edge.LEFT, X)
    LayerShell.set_margin(win, LayerShell.Edge.TOP, Y)
    win.present()
    return win, area


def main() -> int:
    print(f"  preload: {_HOW}")
    if not LayerShell.is_supported():
        print("  layer-shell unusable here")
        return 3
    app = Gtk.Application()
    loop = GLib.MainLoop()
    state = {}

    def do_resize_request(win, area, size):
        win.set_size_request(size, size)
        win.set_default_size(size, size)
        area.set_content_width(size)
        area.set_content_height(size)
        area.queue_resize()
        win.queue_resize()

    def stage1():
        win, area = state["win"], state["area"]
        do_resize_request(win, area, SIZE1)
        GLib.timeout_add(700, report1)
        return False

    def report1():
        win, area = state["win"], state["area"]
        print(f"  after 1 (request sizes):   window {win.get_width()}x{win.get_height()} "
              f"area {area.get_width()}x{area.get_height()} "
              f"mapped={win.get_mapped()}")
        os.system("spectacle -b -n -o /tmp/rsz1.png >/dev/null 2>&1")
        GLib.timeout_add(300, stage2)
        return False

    def stage2():
        win, area = state["win"], state["area"]
        win.set_visible(False)
        do_resize_request(win, area, SIZE1)
        GLib.timeout_add(200, lambda: (win.present(), False)[1])
        GLib.timeout_add(900, report2)
        return False

    def report2():
        win, area = state["win"], state["area"]
        print(f"  after 2 (unmap + remap):   window {win.get_width()}x{win.get_height()} "
              f"area {area.get_width()}x{area.get_height()} "
              f"mapped={win.get_mapped()}")
        os.system("spectacle -b -n -o /tmp/rsz2.png >/dev/null 2>&1")
        GLib.timeout_add(300, stage3)
        return False

    def stage3():
        win = state["win"]
        win.set_visible(False)
        new_win, new_area = make(SIZE1)
        state["win3"] = (new_win, new_area)
        GLib.timeout_add(900, report3)
        return False

    def report3():
        win, area = state["win3"]
        print(f"  after 3 (fresh surface):   window {win.get_width()}x{win.get_height()} "
              f"area {area.get_width()}x{area.get_height()} "
              f"mapped={win.get_mapped()}")
        os.system("spectacle -b -n -o /tmp/rsz3.png >/dev/null 2>&1")
        GLib.timeout_add(400, lambda: loop.quit())
        return False

    def setup():
        state["win"], state["area"] = make(SIZE0)
        os.system("spectacle -b -n -o /tmp/rsz0.png >/dev/null 2>&1")
        GLib.timeout_add(900, stage1)
        return False

    GLib.idle_add(setup)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
