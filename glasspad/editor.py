"""LayoutEditor — ported from GlassPad 1.0.2 (Overlay/LayoutEditor.cs).

Edit mode: drag any button or stick to move it, or drag the orange handle
straddling the bottom-right corner to resize it (44x44 minimum, same floor as
the original).

The Windows original raises a full-screen EditCanvas that swallows every touch
while active, so XInput and editing can never collide. The equivalent here is a
full-window child that the touch router targets exclusively while edit mode is
on; the gamepad widgets underneath receive nothing.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

HANDLE_SIZE = 26.0
#: LayoutEditor.OnMove clamps both axes to this.
MIN_SIZE = 44.0


class EditOverlay(QWidget):
    """Full-window tint plus per-element drag borders and resize handles."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setVisible(False)
        self.active = False
        self._elements: list[QWidget] = []
        self._tint = QColor(0, 0, 0, 0)
        self._drag_el: QWidget | None = None
        self._drag_origin = QPointF()
        self._left_origin = 0.0
        self._top_origin = 0.0
        self._resize_el: QWidget | None = None
        self._resize_origin = QPointF()
        self._w_origin = 0.0
        self._h_origin = 0.0

    # -- activation -------------------------------------------------------
    def toggle(self) -> None:
        self.active = not self.active
        if self.active:
            self._enter()
        else:
            self._exit_internal()

    def exit(self) -> None:
        if not self.active:
            return
        self.active = False
        self._exit_internal()

    def _enter(self) -> None:
        # Pale blue tint = "you are in edit mode" (WPF ARGB 0x18 00 60 C0).
        self._tint = QColor(0x00, 0x60, 0xC0, 0x18)
        self.setVisible(True)
        self.update()

    # Stacking is deliberately NOT raised here. In WPF the EditCanvas is a
    # sibling of the hub and the panels with no Panel.ZIndex, so it covers the
    # gamepad (TouchCanvas) and nothing else; MainWindow.xaml keeps the hub and
    # both dialogs clickable *while* the tint is up -- that is how edit mode gets
    # exited. Raising this full-window widget put it above the hub and the edit
    # panel, and because the touch router may claim it, every press landed on the
    # tint instead: the pencil could not be pressed and the edit panel's slider,
    # 4/8-way and Save buttons were unreachable, so edit mode could only be left
    # by killing the app. MainWindow orders the children explicitly instead.

    def _exit_internal(self) -> None:
        self._drag_el = None
        self._resize_el = None
        self._tint = QColor(0, 0, 0, 0)
        self.setVisible(False)
        self.update()

    def set_elements(self, elements: list[QWidget]) -> None:
        self._elements = list(elements)
        self._drag_el = None
        self._resize_el = None
        self.update()

    # -- painting ---------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self._tint.alpha():
                p.fillRect(self.rect(), self._tint)
            for el in self._elements:
                rect = QRectF(el.x(), el.y(), el.width(), el.height())
                # Blue drag border: fill ARGB 45/00/B4/FF, pen 200/00/DC/FF.
                p.setPen(QPen(QColor(0x00, 0xDC, 0xFF, 200), 2))
                p.setBrush(QBrush(QColor(0x00, 0xB4, 0xFF, 45)))
                p.drawRoundedRect(rect, 6, 6)
                # Orange handle straddling the bottom-right corner.
                hx = rect.right() + 1 - HANDLE_SIZE / 2.0
                hy = rect.bottom() + 1 - HANDLE_SIZE / 2.0
                p.setPen(QPen(QColor(255, 255, 255), 1.5))
                p.setBrush(QBrush(QColor(255, 130, 0, 220)))
                p.drawEllipse(QPointF(hx + HANDLE_SIZE / 2.0,
                                      hy + HANDLE_SIZE / 2.0),
                              HANDLE_SIZE / 2.0, HANDLE_SIZE / 2.0)

    # -- hit testing (window coordinates; the overlay fills the window) ---
    def handle_rect(self, el: QWidget) -> tuple[float, float, float, float]:
        rect = QRectF(el.x(), el.y(), el.width(), el.height())
        return (rect.right() + 1 - HANDLE_SIZE, rect.bottom() + 1 - HANDLE_SIZE,
                HANDLE_SIZE, HANDLE_SIZE)

    def hit_test(self, pos: QPointF) -> tuple[str, QWidget | None]:
        """Resize handles are checked first, exactly as the original does."""
        for el in self._elements:
            x, y, w, h = self.handle_rect(el)
            if x <= pos.x() <= x + w and y <= pos.y() <= y + h:
                return "resize", el
        for el in self._elements:
            rect = QRectF(el.x(), el.y(), el.width(), el.height())
            if rect.left() <= pos.x() <= rect.right() and \
               rect.top() <= pos.y() <= rect.bottom():
                return "drag", el
        return "", None

    # -- router contract. The overlay fills the window, so its local
    #    coordinates are the window coordinates the router maps to.
    def control_press(self, pos: QPointF) -> None:
        kind, el = self.hit_test(pos)
        if el is None:
            return
        if kind == "resize":
            self._resize_el = el
            self._resize_origin = pos
            self._w_origin = float(el.width())
            self._h_origin = float(el.height())
        else:
            self._drag_el = el
            self._drag_origin = pos
            self._left_origin = float(el.x())
            self._top_origin = float(el.y())

    def control_move(self, pos: QPointF) -> None:
        if self._resize_el is not None:
            new_w = max(MIN_SIZE, self._w_origin + (pos.x() - self._resize_origin.x()))
            new_h = max(MIN_SIZE, self._h_origin + (pos.y() - self._resize_origin.y()))
            self._resize_el.resize(int(round(new_w)), int(round(new_h)))
            self.update()
        elif self._drag_el is not None:
            new_l = self._left_origin + (pos.x() - self._drag_origin.x())
            new_t = self._top_origin + (pos.y() - self._drag_origin.y())
            self._drag_el.move(int(round(new_l)), int(round(new_t)))
            self.update()

    def control_release(self) -> None:
        self._resize_el = None
        self._drag_el = None
