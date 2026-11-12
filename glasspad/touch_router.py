"""Per-contact input routing for the overlay.

The Windows original calls ``TouchDevice.Capture(el)`` on every button so that
a finger which slides off a button still releases it. Its README then records a
known limitation: two buttons pressed at the *same instant* can be missed,
because WPF's touch routing serialises them.

That limitation matters on a gamepad overlay -- games want LT + A + a held stick
at once. Qt hands the whole touch sequence to whichever widget the first contact
landed on only if that widget accepts touches, and otherwise routes each contact
independently, which reintroduces the same class of bug. This router restores
what the original's Capture() calls intended and removes the limitation: it owns
every contact on the overlay window and maps each id to the control it landed
on, so N fingers drive N controls independently in any order, and a release
always reaches the control that was pressed.

Controls implement one contract, positions in control-local coordinates:

    control_press(pos) / control_move(pos) / control_release()

The filter is installed on the application, not just the window: mouse events go
to the child widget under the cursor, so a window-only filter could not capture
a drag that leaves a button. Touch is only claimed for the overlay window; the
panels (edit panel, About box, save dialog) are left alone so Qt's normal
touch-to-mouse synthesis drives the QPushButtons inside them.
"""
from __future__ import annotations

import sys
from typing import Callable

from PyQt6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    Qt,
    QCoreApplication,
)
from PyQt6.QtWidgets import QWidget

from .widgets import (
    MOUSE_ID,
    shape_hit,
    TOUCH_MOVED,
    TOUCH_PRESSED,
    TOUCH_RELEASED,
    TOUCH_STATIONARY,
)


_touch_error_reported = False


def _report_touch_failure(exc: BaseException) -> None:
    """Print the first pointer-dispatch fault once, with a traceback.

    Once rather than per event: a broken accessor fires on every contact of every
    frame, and writing a traceback to stderr at pointer rates makes the terminal
    unreadable while hiding the one line that names the cause. This covers the
    touch path and the mouse path, so the wording must not claim the overlay fell
    back to mouse input when it was mouse input that failed -- it only says the
    overlay survived and something in it is wrong.
    """
    global _touch_error_reported
    if _touch_error_reported:
        return
    _touch_error_reported = True
    import traceback
    print("GlassPad: pointer dispatch failed; that input source is degraded, "
          "the overlay is still running.\n" + traceback.format_exc(),
          file=sys.stderr, flush=True)


def _contact_id(point) -> int:
    """Stable per-contact key, preferring the device-authoritative one.

    QEventPoint exposes both id() and uniqueId(). uniqueId() is what Qt
    guarantees to be distinct per contact, so it wins when the point carries a
    valid one; id() is the fallback. Both are read the same way on press and on
    release, so a grab always matches the control it started on.

    Synthesized points -- what a test builds -- have an invalid uniqueId and an
    id() of 0 regardless of the constructor argument (measured on PyQt6 6.11),
    so two hand-made contacts are indistinguishable to Qt and cannot be used to
    exercise multi-contact bookkeeping. That is why TestTouchRouter drives
    single-contact cases through real Qt objects and uses interface-faithful
    stand-ins only where distinct ids are required.
    """
    uid = point.uniqueId()
    if uid.isValid():
        return uid.numericId()
    return point.id()


class TouchRouter(QObject):
    """Gives the overlay WPF-style per-contact capture.

    ``targets_fn`` returns the claimable controls in z-order, bottom first (Qt
    child order), so scanning in reverse finds the topmost hit.

    ``blockers_fn`` returns widgets stacked *above* every claimable target that
    take input themselves -- the edit panel, the save dialog and the About box.
    WPF hit-tests the visual tree top-down, so a panel opaque at that pixel eats
    the touch and neither EditCanvas nor TouchCanvas ever sees it; a full-window
    claimable target (the edit overlay's tint) would otherwise swallow presses
    aimed at the controls inside those panels, which the router must leave to
    Qt's touch-to-mouse synthesis.
    """

    def __init__(self, window: QWidget,
                 targets_fn: Callable[[], list[QWidget]],
                 blockers_fn: Callable[[], list[QWidget]] | None = None,
                 visibility_fn: Callable[[], None] | None = None) -> None:
        super().__init__(window)
        self._window = window
        self._targets_fn = targets_fn
        self._blockers_fn = blockers_fn
        self._visibility_fn = visibility_fn
        self._grabs: dict[int, QWidget] = {}
        # Qt only ever sends TouchBegin/Update/End to a widget that has opted
        # in; without this attribute a finger on the overlay produces mouse
        # events alone, the branches below never run, and multi-touch -- the one
        # thing this router exists to provide -- is silently unavailable. It is
        # set here rather than by the window because this class is the consumer:
        # any widget given a router accepts touches by construction.
        self._window.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        QCoreApplication.instance().installEventFilter(self)

    def shutdown(self) -> None:
        app = QCoreApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    # -- geometry ---------------------------------------------------------
    def hit_test(self, local: QPointF) -> QWidget | None:
        """Topmost claimable control whose *drawn shape* covers the point.

        Bounding boxes alone would contradict the input region: passthrough.py
        cuts round shapes out of the surface, so the corner pixels of a round
        button already belong to the game underneath. A box test would still
        press that button on a touch there -- which the user can neither see nor
        press again to release, since the next touch at the same place goes
        through to the game too. Both sides must use widgets.shape_hit().
        """
        if self._over_panel(local):
            return None
        for widget in reversed(self._targets_fn()):
            if not self._claimable(widget):
                continue
            origin = widget.mapTo(self._window, QPoint(0, 0))
            if not (origin.x() <= local.x() < origin.x() + widget.width() and
                    origin.y() <= local.y() < origin.y() + widget.height()):
                continue
            inside = widget.mapFrom(self._window, local.toPoint())
            if shape_hit(widget, float(inside.x()), float(inside.y())):
                return widget
        return None

    def _is_in_overlay(self, widget: QWidget) -> bool:
        """True when `widget` is this overlay or one of its descendants.

        The filter is installed on the application, so it also sees the license
        window and any foreign widget; only the overlay's own surface is shaped.
        """
        node: QWidget | None = widget
        while node is not None:
            if node is self._window:
                return True
            node = node.parentWidget()
        return False

    def _over_panel(self, local: QPointF) -> bool:
        """True when a visible panel above the canvas already owns this pixel."""
        if self._blockers_fn is None:
            return False
        for widget in self._blockers_fn():
            if not widget.isVisible() or widget.width() <= 0 \
                    or widget.height() <= 0:
                continue
            origin = widget.mapTo(self._window, QPoint(0, 0))
            if not (origin.x() <= local.x() < origin.x() + widget.width() and
                    origin.y() <= local.y() < origin.y() + widget.height()):
                continue
            inside = widget.mapFrom(self._window, local.toPoint())
            if shape_hit(widget, float(inside.x()), float(inside.y())):
                return True
        return False

    #: Every control this router drives must implement all three, because a
    #: grab can be moved and released whatever it was: _press, _move and
    #: _release are reached from the touch path AND the mouse path.
    CONTRACT = ("control_press", "control_move", "control_release")

    @classmethod
    def _claimable(cls, widget: QWidget) -> bool:
        if not widget.isVisible() or widget.width() <= 0 or widget.height() <= 0:
            return False
        effect = widget.graphicsEffect()
        if effect is not None and hasattr(effect, "opacity") \
                and effect.opacity() <= 0.0:
            return False  # WPF: alpha 0 is neither drawn nor hit-tested
        # All three, not just control_press. Checking one member is what let a
        # hub button be claimed and then crash on control_move: the press
        # succeeded, the very next event raised AttributeError inside the event
        # filter, and PyQt aborts rather than reporting it. A control that
        # cannot be moved must not be claimable at all.
        return all(hasattr(widget, name) for name in cls.CONTRACT)

    # -- event filter -----------------------------------------------------
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind in (QEvent.Type.Show, QEvent.Type.Hide):
            # A control appearing or vanishing changes what the window's input
            # region has to describe, so the owner gets told about every one of
            # them. Watching Show/Hide here is the only route that cannot miss a
            # transition: PyQt6 exposes no QWidget.visibilityChanged signal, and
            # the changes come from too many places to hook individually -- the
            # FAB fade hides its own button from an animation's finished()
            # callback, and each panel hides itself from its Close/No button.
            # Never consumed: the widget still has to do its own bookkeeping.
            if self._visibility_fn is not None \
                    and isinstance(obj, QWidget) \
                    and self._is_in_overlay(obj):
                self._visibility_fn()
            return False
        if kind == QEvent.Type.TouchCancel:
            # Qt6 reports a compositor stealing the gesture as its own event
            # type rather than as a state on each point (Qt5 had
            # TouchPointCancelled). Every contact in flight has to be lifted:
            # leaving one grabbed holds that button down forever, because the
            # release Qt would have sent will never arrive.
            if obj is self._window:
                had = self.grab_count()
                self.release_all()
                if had:
                    event.accept()
                return had > 0
            return False
        if kind in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate,
                    QEvent.Type.TouchEnd):
            if obj is self._window:
                # An exception escaping a Python event filter does not get
                # reported and swallowed -- PyQt aborts the process (verified:
                # "Aborted (core dumped)" from a raised AttributeError). On the
                # touch path that turns one wrong accessor into a dead overlay
                # mid-game, so the failure is contained here and printed once
                # rather than allowed to take the session down. Touch degrades,
                # mouse parity keeps working, and the traceback names the cause.
                try:
                    return self._handle_touch(event)
                except Exception as exc:  # noqa: BLE001
                    _report_touch_failure(exc)
                    return False
            return False
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                    QEvent.Type.MouseButtonRelease):
            # Same containment as touch, and for the same reason: an exception
            # here is a core dump, not a dropped click. The mouse path reached
            # the identical FabButton fault with no guard at all, so a click and
            # a small drag on a hub button killed the overlay without a
            # touchscreen being involved.
            try:
                return self._handle_mouse(obj, event)
            except Exception as exc:  # noqa: BLE001
                _report_touch_failure(exc)
                return False
        return False

    def _handle_touch(self, event) -> bool:
        """Dispatch one touch event's contacts.

        Three Qt6 spellings differ from the Qt5 names this port was written
        against, and each one aborted the app rather than misbehaving quietly:

          * ``points()``, not ``touchPoints()`` -- the latter does not exist on
            QTouchEvent in PyQt6 at all.
          * ``state()`` returns a flag whose members reject both ``int()`` and
            ``& int``; read ``.value``.
          * positions come from ``globalPosition()`` and are mapped through
            ``_window_pos()``, exactly as the mouse path does, so the router is
            correct whatever the window's placement rather than assuming the
            overlay sits at 0,0.
        """
        handled = False
        for tp in event.points():
            state = tp.state().value
            ident = _contact_id(tp)
            local = self._window_pos(QPointF(tp.globalPosition()))
            if state & TOUCH_PRESSED:
                handled |= self._press(ident, local)
            elif state & TOUCH_RELEASED:
                handled |= self._release(ident)
            elif state & (TOUCH_MOVED | TOUCH_STATIONARY):
                handled |= self._move(ident, local)
        if handled:
            event.accept()
        return handled

    def _handle_mouse(self, obj, event) -> bool:
        """Desktop parity. Claimed controls are routed; panels are not."""
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            local = self._window_pos(QPointF(event.globalPosition()))
            return self._press(MOUSE_ID, local)
        if kind == QEvent.Type.MouseMove:
            if MOUSE_ID not in self._grabs:
                return False
            return self._move(MOUSE_ID, self._window_pos(QPointF(event.globalPosition())))
        if kind == QEvent.Type.MouseButtonRelease:
            return self._release(MOUSE_ID)
        return False

    def _window_pos(self, global_pos: QPointF) -> QPointF:
        return QPointF(self._window.mapFromGlobal(global_pos.toPoint()))

    # -- grab bookkeeping -------------------------------------------------
    def _press(self, ident: int, window_local: QPointF) -> bool:
        if ident in self._grabs:
            return False
        target = self.hit_test(window_local)
        if target is None:
            return False  # unclaimed -> panels get Qt's synthesized events
        self._grabs[ident] = target
        target.control_press(QPointF(target.mapFrom(self._window,
                                                    window_local.toPoint())))
        return True

    def _move(self, ident: int, window_local: QPointF) -> bool:
        target = self._grabs.get(ident)
        if target is None:
            return False
        target.control_move(QPointF(target.mapFrom(self._window,
                                                   window_local.toPoint())))
        return True

    def _release(self, ident: int) -> bool:
        target = self._grabs.pop(ident, None)
        if target is None:
            return False
        target.control_release()
        return True

    def release_all(self, keep: QWidget | None = None) -> None:
        """Lift every grab except `keep`.

        Drop-before-release, in that order: control_release() runs arbitrary
        application code that may re-enter the router, and it must never find
        the grab it is releasing still in the table.

        `keep` is for the control that asked for the release. The disable toggle
        calls this from its own press handler, so releasing the disable button
        there would both drop its pressed look mid-press -- while it is one of
        the only two things still on screen, and its pressed look is the sole
        acknowledgement the tap registered -- and emit `released` and `clicked`
        from inside the path those signals would drive. That is the re-entrancy
        class that once made the toggle one-way; see the history in CHANGELOG.md.
        """
        for ident, target in list(self._grabs.items()):
            if target is keep:
                continue
            del self._grabs[ident]
            target.control_release()

    def grab_count(self) -> int:
        return len(self._grabs)
