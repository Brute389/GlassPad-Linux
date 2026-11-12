"""GamepadLayout — ported from GlassPad 1.0.2 (Overlay/GamepadLayout.cs).

Geometry is reproduced to the pixel and the element order is preserved, which
is what makes a layout.json written by the Windows app load correctly here:
the file stores an ordered array with no names, matched positionally against
``all_editable``, which is *all 13 buttons first, then the dpad and sticks* --
the C# original concatenates two separate lists, so interleaved build order is
not the index order:

    0  LT    1  RT    2  LB    3  RB     4  Y      5  X
    6  B     7  A     8  L3    9  R3    10  back  11  guide
   12  start 13  dpad 14  LS             15  RS

Build() still adds the dpad before LS and RS before the centre cluster, exactly
as the original does; only the flattened index order differs from add order.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget

from .widgets import CircleButton, Pill
from .zones import DpadZone, StickZone


@dataclass
class Frame:
    """Recorded default: (element, left, top, width, height)."""

    element: QWidget
    left: float
    top: float
    width: float
    height: float


class GamepadLayout:
    def __init__(self, canvas: QWidget) -> None:
        self._canvas = canvas
        self._buttons: list[tuple[QWidget, str]] = []
        self._sticks: list[StickZone] = []
        self._stick_elements: list[QWidget] = []
        self._defaults: list[Frame] = []
        self.dpad: DpadZone | None = None

    # -- accessors --------------------------------------------------------
    @property
    def buttons(self) -> list[tuple[QWidget, str]]:
        return list(self._buttons)

    @property
    def sticks(self) -> list[StickZone]:
        return list(self._sticks)

    @property
    def all_editable(self) -> list[QWidget]:
        """WPF: [..buttons, ..stickElements] — order is load-bearing."""
        return [el for el, _ in self._buttons] + list(self._stick_elements)

    @property
    def defaults(self) -> list[Frame]:
        return list(self._defaults)

    # -- build ------------------------------------------------------------
    def build(self, width: float, height: float) -> None:
        """Port of GamepadLayout.Build(w, h)."""
        for child in self._canvas.children():
            if isinstance(child, QWidget):
                child.deleteLater()
        self._buttons.clear()
        self._sticks.clear()
        self._stick_elements.clear()
        self._defaults.clear()
        self.dpad = None

        # Triggers
        self.pill("LT", 40, 35, 110, 55, "#B3505060")
        self.pill("RT", width - 150, 35, 110, 55, "#B3505060")

        # Shoulder buttons
        self.pill("LB", 40, 100, 120, 48, "#B3607080")
        self.pill("RB", width - 160, 100, 120, 48, "#B3607080")

        # D-Pad
        self.dpad_at(175, height * 0.52)

        # Left stick
        self.stick("LS", 260, height * 0.78)

        # A / B / X / Y
        fcx, fcy, fd = width - 175, height * 0.52, 83.0
        self.circle("Y", "#B3D4AC00", fcx, fcy - fd, 75)
        self.circle("X", "#B31A5FB0", fcx - fd, fcy, 75)
        self.circle("B", "#B3C02020", fcx + fd, fcy, 75)
        self.circle("A", "#B3108040", fcx, fcy + fd, 75)

        # L3 / R3 — next to the shoulder cluster
        self.circle("L3", "#B3304060", 190, 124, 44)
        self.circle("R3", "#B3304060", width - 190, 124, 44)

        # Right stick
        self.stick("RS", width - 300, height * 0.78)

        # Centre cluster: back / guide / start
        self.circle("\u229f", "#B3404055", width / 2 - 115, height * 0.90, 52)
        self.circle("\u2299", "#B3908020", width / 2, height * 0.88, 62)
        self.circle("\u229e", "#B3404055", width / 2 + 115, height * 0.90, 52)

        # Record defaults in AllEditable order.
        for el in self.all_editable:
            self._defaults.append(
                Frame(el, el.x(), el.y(), el.width(), el.height())
            )

    # -- reset / save / load ---------------------------------------------
    def reset(self) -> None:
        for frame in self._defaults:
            el = frame.element
            el.setGeometry(
                int(round(frame.left)), int(round(frame.top)),
                int(round(frame.width)), int(round(frame.height)),
            )
            el.show()

    def save_layout(self, path: str, opacity: float, dpad_mode: int = 4) -> None:
        """Same JSON shape the Windows app writes, so files move both ways.

        _defaults supplies ordering and identity only; the written values are
        each element's *current* geometry, exactly as the C# does with
        Canvas.GetLeft(d.El). Reading f.left instead would silently discard
        every drag and resize the user just performed.
        """
        elements = [
            {
                "left": float(f.element.x()),
                "top": float(f.element.y()),
                "width": float(f.element.width()),
                "height": float(f.element.height()),
            }
            for f in self._defaults
        ]
        root = {"opacity": opacity, "dpadMode": dpad_mode, "elements": elements}
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(root, fh, indent=2)

    def load_layout(self, path: str) -> tuple[float, int]:
        """Returns (opacity, dpad_mode); missing/corrupt file -> (1.0, 4)."""
        opacity, dpad_mode = 1.0, 4
        if not os.path.exists(path):
            return opacity, dpad_mode
        try:
            with open(path, encoding="utf-8") as fh:
                root = json.load(fh)
        except Exception:
            return opacity, dpad_mode
        if not isinstance(root, dict):
            return opacity, dpad_mode
        if isinstance(root.get("opacity"), (int, float)):
            opacity = float(root["opacity"])
        if isinstance(root.get("dpadMode"), int):
            dpad_mode = int(root["dpadMode"])
        elements = root.get("elements")
        if not isinstance(elements, list):
            return opacity, dpad_mode
        for index, item in enumerate(elements):
            if index >= len(self._defaults):
                break
            if not isinstance(item, dict):
                continue
            try:
                left = float(item["left"])
                top = float(item["top"])
                w = float(item["width"])
                h = float(item["height"])
            except (KeyError, TypeError, ValueError):
                continue
            el = self._defaults[index].element
            el.setGeometry(int(round(left)), int(round(top)),
                           int(round(w)), int(round(h)))
        return opacity, dpad_mode

    # -- builders ---------------------------------------------------------
    def pill(self, tag: str, x: float, y: float, w: float, h: float,
             color: str) -> None:
        el = Pill(tag, QColor(color), h, self._canvas)
        el.setGeometry(int(round(x)), int(round(y)), int(round(w)), int(round(h)))
        el.show()
        self._buttons.append((el, tag))

    def circle(self, tag: str, color: str, cx: float, cy: float, d: float) -> None:
        el = CircleButton(tag, QColor(color), self._canvas)
        el.setGeometry(int(round(cx - d / 2)), int(round(cy - d / 2)),
                       int(round(d)), int(round(d)))
        el.show()
        self._buttons.append((el, tag))

    def stick(self, name: str, cx: float, cy: float) -> None:
        zone = StickZone(name, self._canvas)
        self._sticks.append(zone)
        outer = StickZone.OUTER
        zone.setGeometry(
            int(round(cx - outer / 2)), int(round(cy - outer / 2)),
            int(round(outer)), int(round(outer)),
        )
        zone.show()
        self._stick_elements.append(zone)

    def dpad_at(self, cx: float, cy: float) -> None:
        size = DpadZone.BASE
        self.dpad = DpadZone(self._canvas)
        self.dpad.setGeometry(
            int(round(cx - size / 2)), int(round(cy - size / 2)),
            int(round(size)), int(round(size)),
        )
        self.dpad.show()
        self._stick_elements.append(self.dpad)


# Tag -> PadService button name. Mirrors MainWindow.OnButtonDown's switch.
TAG_TO_BUTTON = {
    "A": "A",
    "B": "B",
    "X": "X",
    "Y": "Y",
    "LB": "LB",
    "RB": "RB",
    "\u229f": "BACK",
    "\u229e": "START",
    "\u2299": "GUIDE",
    "L3": "L3",
    "R3": "R3",
}

# Tags handled as analog triggers rather than digital buttons.
TAG_TO_TRIGGER = {"LT": "left", "RT": "right"}
