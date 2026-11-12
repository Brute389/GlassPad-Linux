"""Canvas widgets that reproduce GlassPad's WPF visuals.

Coordinate system is device-independent pixels, identical to the WPF original,
so a layout.json saved on Windows drops in unchanged.

WPF -> Qt mapping used throughout:
  Canvas.SetLeft/Top(el) + el.Width/Height   ->  setGeometry()
  el.Opacity                                 ->  QGraphicsOpacityEffect
  RenderTransformOrigin 0.5 + ScaleTransform ->  press scale in paintEvent
  Border CornerRadius/BorderBrush/Thickness  ->  drawRoundedRect
  Ellipse Fill/Stroke/StrokeThickness        ->  drawEllipse with a QPen

Input arrives from touch_router.py, never from per-widget Qt events, so a
finger cannot wander between controls mid-press.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QWidget

# ---------------------------------------------------------------------------
# Touch point state bits (Qt::TouchPointState).
#
# These are QEventPoint.State.value, measured on PyQt6 6.11 rather than copied
# from Qt5's Qt::TouchPointState: Pressed 0x1, Updated 0x2, Stationary 0x4,
# Released 0x8. Updated replaced Qt5's Moved, and the numbers still line up, so
# TOUCH_MOVED keeps the old name for the bit that means "this contact moved".
#
# There is NO cancelled bit in Qt6, and that is not an omission to paper over:
# Qt6 delivers a stolen gesture as the *event type* QEvent.Type.TouchCancel,
# not as a state on each point. The 0x20 this file used to define was Qt5's
# TouchPointCancelled, so the branch that tested for it could never run --
# a compositor taking the gesture away left every held trigger down, which is
# precisely the failure the comment claimed to prevent. touch_router.py handles
# it off the event type instead; see TestTouchRouter.test_touch_cancel_event.
# ---------------------------------------------------------------------------
TOUCH_PRESSED = 0x01
TOUCH_MOVED = 0x02
TOUCH_STATIONARY = 0x04
TOUCH_RELEASED = 0x08

# No TOUCH_UP / TOUCH_HELD aggregates: nothing ever read them, and a "covers a
# release or a cancel" alias is what let the Qt5 cancel bit look handled here
# while the event type that actually carries a cancel went unhandled in
# touch_router.py. Adding an alias again invites the same mistake back.

#: Synthetic contact id for the mouse pointer (one implicit touch).
MOUSE_ID = -1

# Segoe UI ships with Windows only. KDE on the Flow Z13 normally has Noto Sans;
# DejaVu Sans has the widest glyph coverage of the symbols drawn here.
UI_FONTS = ("Segoe UI", "Noto Sans", "DejaVu Sans", "Sans Serif")
MONO_FONTS = ("Consolas", "DejaVu Sans Mono", "monospace")


def has_glyph(char: str) -> bool:
    """True when a font in the fallback chain actually renders `char`."""
    for family in UI_FONTS:
        if QFontMetrics(QFont(family)).inFont(char):
            return True
    return False


def label_font(size: float, bold: bool = True) -> QFont:
    font = QFont(UI_FONTS[0])
    font.setPixelSize(max(1, int(round(size))))
    font.setBold(bold)
    return font


def argb(a: int, r: int, g: int, b: int) -> QColor:
    return QColor(r, g, b, a)


#: Border shared by every button: white at alpha 200, 1.5px (WPF original).
STROKE = QPen(argb(200, 255, 255, 255), 1.5)
#: Button label colour: white at alpha 220.
LABEL_COLOR = argb(220, 255, 255, 255)


class CanvasWidget(QWidget):
    """Base widget positioned by its parent canvas, like a WPF Canvas child."""

    #: WPF TouchDown / TouchUp, emitted by the router contract below.
    pressed = pyqtSignal()
    released = pyqtSignal()

    def __init__(self, tag: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tag = tag
        self.is_pressed = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._press_scale = 1.0

    # -- router contract. `pos` is widget-local. --------------------------
    def control_press(self, pos: QPointF) -> None:
        self.is_pressed = True
        self.pressed.emit()

    def control_move(self, pos: QPointF) -> None:
        pass

    def control_release(self) -> None:
        if self.is_pressed:
            self.is_pressed = False
            self.released.emit()

    # -- WPF-parity geometry API -----------------------------------------
    def set_left(self, value: float) -> None:
        self.move(int(round(value)), self.y())

    def set_top(self, value: float) -> None:
        self.move(self.x(), int(round(value)))

    def set_press_scale(self, scale: float) -> None:
        """WPF: el.RenderTransform = new ScaleTransform(1.08, 1.08)."""
        if scale != self._press_scale:
            self._press_scale = scale
            self.update()

    # -- painting ---------------------------------------------------------
    def paint_scaled(self, painter: QPainter, draw) -> None:
        """Run `draw` about the widget centre with the press scale applied."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._press_scale != 1.0:
            cx, cy = self.width() / 2.0, self.height() / 2.0
            painter.translate(cx, cy)
            painter.scale(self._press_scale, self._press_scale)
            painter.translate(-cx, -cy)
        draw(painter)

    def draw_centered_text(self, painter: QPainter, text: str, size: float,
                           color: QColor = LABEL_COLOR) -> None:
        painter.setFont(label_font(size))
        painter.setPen(QPen(color))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)


class Pill(CanvasWidget):
    """GamepadLayout.Pill — rounded-rect trigger / shoulder button."""

    #: CornerRadius is bh/2 in WPF, i.e. a stadium shape.
    shape_kind = "pill"

    def __init__(self, tag: str, color: QColor, height: float,
                 parent: QWidget | None = None) -> None:
        super().__init__(tag, parent)
        self._fill = color
        self._corner = height / 2.0
        self._label_size = height * 0.38

    def resizeEvent(self, event) -> None:  # noqa: N802
        # WPF derives CornerRadius = bh/2 and the label size from the button
        # height, so an edit-mode resize must re-derive both.
        self._corner = self.height() / 2.0
        self._label_size = self.height() * 0.38
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        with QPainter(self) as p:
            def draw(p: QPainter) -> None:
                rect = self.rect().adjusted(1, 1, -1, -1)
                p.setPen(STROKE)
                p.setBrush(QBrush(self._fill))
                p.drawRoundedRect(rect, self._corner, self._corner)
                self.draw_centered_text(p, self.tag, self._label_size)

            self.paint_scaled(p, draw)


class CircleButton(CanvasWidget):
    """GamepadLayout.Circle — face buttons, L3/R3, and the centre cluster."""

    shape_kind = "circle"

    def __init__(self, tag: str, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(tag, parent)
        self._fill = color

    def paintEvent(self, event) -> None:  # noqa: N802
        with QPainter(self) as p:
            def draw(p: QPainter) -> None:
                rect = self.rect().adjusted(1, 1, -1, -1)
                p.setPen(STROKE)
                p.setBrush(QBrush(self._fill))
                p.drawEllipse(rect)
                # WPF sizes the label from the circle diameter: d * 0.32.
                self.draw_centered_text(
                    p, self.tag, min(self.width(), self.height()) * 0.32)

            self.paint_scaled(p, draw)


class Panel(CanvasWidget):
    """A rounded translucent card: edit panel, save dialog, About box."""

    shape_kind = "rect"  # cards swallow touches across their whole bounds

    def __init__(self, color: QColor, radius: float,
                 parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._fill = color
        self._radius = radius

    def paintEvent(self, event) -> None:  # noqa: N802
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(self._fill))
            p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1),
                              self._radius, self._radius)


class FabButton(QWidget):
    """MainWindow.xaml FabBtn — a 44px circular button with fade + pressed.

    `fade` is a real Qt property so QPropertyAnimation can drive it the way
    WPF's el.BeginAnimation(OpacityProperty, ...) did.
    """

    shape_kind = "circle"

    clicked = pyqtSignal()
    pressed = pyqtSignal()
    released = pyqtSignal()

    def __init__(self, glyph: str, color: QColor, tooltip: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.glyph = glyph
        self.color = color
        self.setToolTip(tooltip)
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._down = False
        self._opacity = 1.0
        self._pop = 1.0

    # -- router contract --------------------------------------------------
    def control_press(self, pos: QPointF) -> None:
        if not self._down:
            self._set_down(True)
            self.pressed.emit()

    def control_move(self, pos: QPointF) -> None:
        # Momentary button: it has no position to track, so a drag across it
        # changes nothing. The method exists because the router's contract is
        # press/move/release for every claimable control -- omitting it meant a
        # finger or a dragged click anywhere on a hub button raised
        # AttributeError inside the event filter, which PyQt turns into SIGABRT.
        #
        # Deliberately does NOT un-press when the contact leaves the circle, as
        # a QAbstractButton would: the hub's buttons are release-to-activate
        # wherever they were pressed, and matching QPushButton's grab behaviour
        # here would change what a slide-off does on hardware. That is parity --
        # MainWindow.xaml calls TouchDevice.Capture(el) on every button so a
        # finger that slides off still releases it, quoted in the module
        # docstring of touch_router.py.
        pass

    def control_release(self) -> None:
        if self._down:
            self._set_down(False)
            self.released.emit()
            self.clicked.emit()

    # -- fade (XAML binds this to the edit-mode opacity slider) ----------
    def set_fade(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if value != self._opacity:
            self._opacity = value
            self.update()

    def _get_fade(self) -> float:
        return self._opacity

    fade = pyqtProperty(float, fget=_get_fade, fset=set_fade)

    # -- pop (WPF AnimateIn also scales 0.5 -> 1) -------------------------
    def set_pop(self, value: float) -> None:
        if float(value) != self._pop:
            self._pop = float(value)
            self.update()

    def _get_pop(self) -> float:
        return self._pop

    pop = pyqtProperty(float, fget=_get_pop, fset=set_pop)

    # -- painting ---------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            color = QColor(self.color)
            color.setAlpha(int(color.alpha() * self._opacity))
            # The template's IsPressed trigger sets Opacity 0.7.
            if self._down:
                color.setAlpha(int(color.alpha() * 0.7))
            if self._pop != 1.0:
                cx, cy = self.width() / 2.0, self.height() / 2.0
                p.translate(cx, cy)
                p.scale(self._pop, self._pop)
                p.translate(-cx, -cy)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(color))
            p.drawEllipse(self.rect().adjusted(0, 0, -1, -1))
            p.setFont(label_font(18))
            p.setPen(QPen(QColor(255, 255, 255, int(255 * self._opacity))))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.glyph)

    def _set_down(self, down: bool) -> None:
        if down != self._down:
            self._down = down
            self.update()


# ---------------------------------------------------------------------------
# Shape test
#
# WPF hit-tests the rendered geometry, so a touch just outside a round button's
# circle falls through to the game. A rectangle-only hit test would both eat
# those corner pixels and disagree with the XShape input region, opening dead
# zones. Both the router and the XShape region use this one predicate so they
# can never diverge.
# ---------------------------------------------------------------------------
def _in_ellipse(px: float, py: float, cx: float, cy: float,
                rx: float, ry: float) -> bool:
    if rx <= 0 or ry <= 0:
        return False
    dx, dy = (px - cx) / rx, (py - cy) / ry
    return dx * dx + dy * dy <= 1.0


def triangle(cx: float, cy: float, size: float, up: bool) -> QPolygonF:
    """Arrow glyph fallback for fonts without the geometric triangles."""
    half = size / 2.0
    if up:
        return QPolygonF([QPointF(cx, cy - half), QPointF(cx - half, cy + half),
                          QPointF(cx + half, cy + half)])
    return QPolygonF([QPointF(cx, cy + half), QPointF(cx - half, cy - half),
                      QPointF(cx + half, cy - half)])


def side_triangle(cx: float, cy: float, size: float, left: bool) -> QPolygonF:
    half = size / 2.0
    if left:
        return QPolygonF([QPointF(cx - half, cy), QPointF(cx + half, cy - half),
                          QPointF(cx + half, cy + half)])
    return QPolygonF([QPointF(cx + half, cy), QPointF(cx - half, cy - half),
                      QPointF(cx - half, cy + half)])


def shape_hit(widget, local_x: float, local_y: float) -> bool:
    """Is a widget-local point inside the widget's drawn shape?"""
    w, h = float(widget.width()), float(widget.height())
    if w <= 0 or h <= 0:
        return False
    if not (0 <= local_x < w and 0 <= local_y < h):
        return False
    kind = getattr(widget, "shape_kind", "rect")
    if kind == "circle":
        return _in_ellipse(local_x, local_y, w / 2.0, h / 2.0, w / 2.0, h / 2.0)
    if kind == "pill":
        r = min(w, h) / 2.0
        # Straight middle span, then the two semicircular caps.
        if r <= local_x <= w - r:
            return True
        cx = r if local_x < r else w - r
        return _in_ellipse(local_x, local_y, cx, h / 2.0, r, h / 2.0)
    return True
