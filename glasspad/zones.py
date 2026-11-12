"""StickZone and DpadZone, ported from GlassPad 1.0.2.

Ported from:
  src/GlassPad/Input/StickZone.cs   (Outer 119 / Inner 57 / travel 31px)
  src/GlassPad/Input/DpadZone.cs    (Base 180 / arm 60, 4-way and 8-way hats)

The hat tables and the dead-zone radius are reproduced exactly, including the
original's inclusive/exclusive comparison boundaries, so a given touch point
selects the same direction on Linux as on Windows.

One deliberate deviation: the stick's centre for the travel maths is the
widget's actual centre rather than the WPF constant ``Outer / 2`` (StickZone.cs
subtracts that literal, so it assumes the widget is exactly ``Outer`` wide).
At the default 119px size -- every default layout, and every layout saved on
Windows that did not resize the stick -- the two are identical; a resized stick
is where they would part, and using the real centre keeps the nub centred there
instead of drifting.
"""
from __future__ import annotations

import math
from typing import Callable

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF

from .widgets import (
    CanvasWidget,
    MOUSE_ID,
    argb,
    has_glyph,
    label_font,
    side_triangle,
    triangle,
)

#: Normalised hat names. Same eight directions as HMHat, plus "None".
HAT_NONE = "None"


# ---------------------------------------------------------------------------
# Analog stick
# ---------------------------------------------------------------------------
class StickZone(CanvasWidget):
    """Port of StickZone: a ring with a nub clamped to MaxTravel pixels."""

    OUTER = 119.0
    INNER = 57.0
    # (119 - 57) / 2 == 31. The original's comment says "28 px"; the expression
    # is what the shipped Windows build actually evaluates, so 31 is kept.
    MAX_TRAVEL = (OUTER - INNER) / 2.0

    changed = pyqtSignal(object)  # emits self, like Action<StickZone>

    def __init__(self, name: str, parent=None) -> None:
        super().__init__(name, parent)
        self.name = name
        self.stick_x = 0.0
        self.stick_y = 0.0
        self._touch_id = -1
        self._nub = QPointF(0.0, 0.0)
        self._outer_fill = QColor("#B8202030")
        self._nub_fill = QColor("#B8909098")

    # -- public -----------------------------------------------------------
    @property
    def X(self) -> float:  # noqa: N802 - WPF naming parity
        return self.stick_x

    @property
    def Y(self) -> float:  # noqa: N802
        return self.stick_y

    # -- painting ---------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        with QPainter(self) as p:
            def draw(p: QPainter) -> None:
                rect = self.rect().adjusted(1, 1, -1, -1)
                p.setPen(QPen(argb(200, 255, 255, 255), 1.5))
                p.setBrush(QBrush(self._outer_fill))
                p.drawEllipse(rect)
                # Nub: fixed INNER size, centred, offset by clamped travel.
                cx, cy = self.width() / 2.0, self.height() / 2.0
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(self._nub_fill))
                p.drawEllipse(self._nub_rect(cx + self._nub.x(),
                                             cy + self._nub.y()))

            self.paint_scaled(p, draw)

    def _nub_rect(self, cx: float, cy: float) -> QRectF:
        half = self.INNER / 2.0
        return QRectF(cx - half, cy - half, self.INNER, self.INNER)

    # -- router contract --------------------------------------------------
    def control_press(self, pos: QPointF) -> None:
        if self._touch_id < 0:
            self._touch_id = MOUSE_ID
            self.is_pressed = True
            self._apply(pos)

    def control_move(self, pos: QPointF) -> None:
        if self.is_pressed:
            self._apply(pos)

    def control_release(self) -> None:
        if self.is_pressed:
            self.reset()

    def _apply(self, pos: QPointF) -> None:
        cx, cy = self.width() / 2.0, self.height() / 2.0
        dx, dy = pos.x() - cx, pos.y() - cy
        dist = math.hypot(dx, dy)
        if dist > self.MAX_TRAVEL:
            scale = self.MAX_TRAVEL / dist
            dx, dy = dx * scale, dy * scale
        self._nub = QPointF(dx, dy)
        self.stick_x = dx / self.MAX_TRAVEL
        self.stick_y = dy / self.MAX_TRAVEL
        self.update()
        self.changed.emit(self)

    def reset(self) -> None:
        self._touch_id = -1
        self._nub = QPointF(0.0, 0.0)
        self.stick_x = 0.0
        self.stick_y = 0.0
        self.is_pressed = False
        self.update()
        self.changed.emit(self)


# ---------------------------------------------------------------------------
# D-pad
# ---------------------------------------------------------------------------
class DpadZone(CanvasWidget):
    """Port of DpadZone: cross base, four arm highlights, 8-way ring."""

    FOUR = 4
    EIGHT = 8

    BASE = 180.0
    BASE_ARM = 60.0
    RING_SIZE = 200.0
    DEAD_ZONE = 12.0

    hat_changed = pyqtSignal(str)  # "N", "SE", "None", ...

    def __init__(self, parent=None) -> None:
        super().__init__("dpad", parent)
        self._mode = self.FOUR
        self._touch_id = -1
        self._cross = self.cross_polygon(self.BASE, self.BASE_ARM)
        # arm key -> current opacity, for the fade-out animation
        self._arms = {"N": 0.0, "S": 0.0, "W": 0.0, "E": 0.0}
        self._fade = QTimer(self)
        self._fade.setInterval(16)
        self._fade.timeout.connect(self._fade_step)
        self._fade_target = dict(self._arms)
        self._c = (self.BASE - self.BASE_ARM) / 2.0  # = 60

    # -- mode -------------------------------------------------------------
    @property
    def mode(self) -> int:
        return self._mode

    @mode.setter
    def mode(self, value: int) -> None:
        self._mode = value
        self.update()

    # -- geometry ---------------------------------------------------------
    @staticmethod
    def cross_polygon(outer: float, arm: float) -> QPolygonF:
        """Port of DpadZone.CrossGeom, as a 12-point closed figure."""
        c = (outer - arm) / 2.0
        pts = [
            (c, 0), (c + arm, 0), (c + arm, c), (outer, c), (outer, c + arm),
            (c + arm, c + arm), (c + arm, outer), (c, outer), (c, c + arm),
            (0, c + arm), (0, c), (c, c),
        ]
        return QPolygonF([QPointF(x, y) for x, y in pts])

    def arm_rect(self, key: str) -> QRectF:
        """Highlight rects, from AddArmHighlight(...)."""
        c, arm = self._c, self.BASE_ARM
        table = {
            "N": (c, 0, arm, c),
            "S": (c, c + arm, arm, c),
            "W": (0, c, c, arm),
            "E": (c + arm, c, c, arm),
        }
        x, y, w, h = table[key]
        return QRectF(x, y, w, h)

    def _scale(self) -> tuple[float, float, float]:
        """WPF Viewbox(Stretch=Uniform): scale by the smaller ratio, centred."""
        sx, sy = self.width() / self.BASE, self.height() / self.BASE
        s = min(sx, sy)
        return s, (self.width() - self.BASE * s) / 2.0, (self.height() - self.BASE * s) / 2.0

    # -- painting ---------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        with QPainter(self) as p:
            def draw(p: QPainter) -> None:
                s, ox, oy = self._scale()
                p.translate(ox, oy)
                p.scale(s, s)

                if self._mode == self.EIGHT:
                    off = (self.BASE - self.RING_SIZE) / 2.0
                    p.setPen(QPen(QColor(0, 220, 255, 200), 2.5))
                    p.setBrush(QBrush(QColor(0, 180, 255, 60)))
                    p.drawEllipse(
                        QRectF(off, off, self.RING_SIZE, self.RING_SIZE)
                    )

                p.setPen(QPen(argb(180, 255, 255, 255), 1.5))
                p.setBrush(QBrush(QColor("#C8404055")))
                p.drawPolygon(self._cross)

                for key, opacity in self._arms.items():
                    if opacity <= 0.0:
                        continue
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QBrush(QColor(0, 220, 255, int(100 * opacity))))
                    rect = self.arm_rect(key)
                    p.drawRoundedRect(rect, 4, 4)

                self._draw_arrows(p)

            self.paint_scaled(p, draw)

    def _draw_arrows(self, p: QPainter) -> None:
        """Port of the four Arrow() labels, drawn as glyphs when available.

        Segoe UI has the geometric triangles; on a font set that lacks them the
        same arrows are drawn as polygons so the dpad never shows tofu boxes.
        """
        base, c = self.BASE, self._c
        positions = {
            "\u25b2": (base / 2 - 7, c / 2 - 9),
            "\u25bc": (base / 2 - 7, base - c / 2 - 9),
            "\u25c0": (c / 2 - 9, base / 2 - 9),
            "\u25b6": (base - c / 2 - 9, base / 2 - 9),
        }
        p.setFont(label_font(16))
        p.setPen(QPen(argb(210, 255, 255, 255)))
        for glyph, (left, top) in positions.items():
            if has_glyph(glyph):
                p.drawText(QRectF(left, top, 20, 20),
                           Qt.AlignmentFlag.AlignCenter, glyph)
            else:
                cx, cy = left + 10, top + 10
                up = glyph in ("\u25b2",)
                down = glyph in ("\u25bc",)
                if up or down:
                    poly = triangle(cx, cy, 11, up)
                else:
                    poly = side_triangle(cx, cy, 11, glyph == "\u25c0")
                p.setBrush(QBrush(argb(210, 255, 255, 255)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawPolygon(poly)

    # -- router contract --------------------------------------------------
    def control_press(self, pos: QPointF) -> None:
        if self._touch_id < 0:
            self._touch_id = MOUSE_ID
            self.is_pressed = True
            self._update_hat(self._to_base(pos))

    def control_move(self, pos: QPointF) -> None:
        if self.is_pressed:
            self._update_hat(self._to_base(pos))

    def control_release(self) -> None:
        if self.is_pressed:
            self._release()

    def _to_base(self, pos: QPointF) -> QPointF:
        """Undo the Viewbox uniform-scale/centre transform."""
        s, ox, oy = self._scale()
        if s == 0:
            return QPointF(self.BASE / 2, self.BASE / 2)
        return QPointF((pos.x() - ox) / s, (pos.y() - oy) / s)

    def _release(self) -> None:
        self._touch_id = -1
        self.is_pressed = False
        self._clear_highlights()
        self.hat_changed.emit(HAT_NONE)

    def _update_hat(self, pos: QPointF) -> None:
        dx, dy = pos.x() - self.BASE / 2.0, pos.y() - self.BASE / 2.0
        dist = math.hypot(dx, dy)
        if dist < self.DEAD_ZONE:
            self._clear_highlights()
            self.hat_changed.emit(HAT_NONE)
            return
        angle = math.degrees(math.atan2(dy, dx))
        hat = to_hat8(angle) if self._mode == self.EIGHT else to_hat4(angle)
        self._update_highlight(hat)
        self.hat_changed.emit(hat)

    # -- highlight --------------------------------------------------------
    def _update_highlight(self, hat: str) -> None:
        """Port of UpdateHighlight: cardinal lights one arm, diagonal two at .7."""
        self._fade.stop()
        self._fade_target = {k: 0.0 for k in self._arms}
        lights = {
            "N": [("N", 1.0)],
            "S": [("S", 1.0)],
            "E": [("E", 1.0)],
            "W": [("W", 1.0)],
            "NE": [("N", 0.7), ("E", 0.7)],
            "SE": [("S", 0.7), ("E", 0.7)],
            "SW": [("S", 0.7), ("W", 0.7)],
            "NW": [("N", 0.7), ("W", 0.7)],
        }.get(hat, [])
        for key, opacity in lights:
            self._arms[key] = opacity  # LitArm sets Opacity immediately
        self.update()

    def _clear_highlights(self) -> None:
        """Port of ClearHighlights: fade every arm to 0 over 120ms."""
        self._fade_target = {k: 0.0 for k in self._arms}
        if any(v > 0 for v in self._arms.values()):
            self._fade.start()
        else:
            self.update()

    def _fade_step(self) -> None:
        step = 16.0 / 120.0
        done = True
        for key in self._arms:
            current = self._arms[key]
            target = self._fade_target[key]
            if current > target:
                self._arms[key] = max(target, current - step)
                if self._arms[key] > target:
                    done = False
        self.update()
        if done:
            self._fade.stop()


# ---------------------------------------------------------------------------
# Direction mapping — reproduced verbatim from DpadZone.cs
# ---------------------------------------------------------------------------
def to_hat4(a: float) -> str:
    """4-way mapping. Boundary comparisons copied exactly from the original."""
    if -135 < a <= -45:
        return "N"
    if -45 < a <= 45:
        return "E"
    if 45 < a <= 135:
        return "S"
    return "W"


def to_hat8(a: float) -> str:
    """8-way mapping, 45-degree sectors centred on the cardinals."""
    norm = ((a % 360) + 360) % 360
    sector = int((norm + 22.5) / 45) % 8
    return (
        "E", "SE", "S", "SW", "W", "NW", "N", "NE",
    )[sector]
