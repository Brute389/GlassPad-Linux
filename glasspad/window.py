"""MainWindow — ported from GlassPad 1.0.2 (MainWindow.xaml + .xaml.cs).

Behaviour kept 1:1 with the Windows original:

  * full-screen transparent always-on-top, no chrome, not in the taskbar, and
    never taking focus from the game (WindowStyle=None + Topmost +
    ShowInTaskbar=False + WS_EX_NOACTIVATE),
  * the top-centre FAB hub: Quit / Edit / About fan out with the same 0/60/120ms
    stagger and 180ms cubic ease-out, and collapse over 140ms,
  * the disable button (`⊘`) hides every gamepad element and releases all
    input -- a latching toggle here, momentary in the original,
  * opacity slider drives every element, 4/8-way dpad switch, save then confirm,
  * About box with backend/app version lines, controller status and licenses.

Platform plumbing lives in passthrough.py (click-through) and touch_router.py
(per-contact capture).
"""
from __future__ import annotations

import os

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PyQt6.QtGui import QColor, QGuiApplication
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import passthrough
from .editor import EditOverlay
from .layout import TAG_TO_BUTTON, TAG_TO_TRIGGER, GamepadLayout
from .license_window import LicenseWindow
from .pad import PadService
from .touch_router import TouchRouter
from .widgets import FabButton, MONO_FONTS, Panel, UI_FONTS
from .zones import DpadZone
from . import __version__

# The About box must not be able to show a different release number from
# `glasspad --version`, so this follows the package rather than being a
# fourth literal to remember.
APP_VERSION = __version__
#: Shown where the original prints "HIDMaestro SDK vX". On Linux the virtual pad
#: is created by the kernel's uinput rather than a userspace SDK.
BACKEND_LABEL = "Linux uinput backend (ABI v5)"

HUB_WIDTH, HUB_HEIGHT = 230, 200
#: WPF EditPanel Margin="0,212,0,0": 8 top margin + 200 hub height + 4 gap.
EDIT_PANEL_TOP = 212

PANEL_BG = QColor(0xF0, 0x1A, 0x1A, 0x2E)
EDIT_BG = QColor(0xDD, 0x1A, 0x1A, 0x2E)
GREEN = QColor(0xCC, 0x20, 0x80, 0x60)
GREY = QColor(0xCC, 0x40, 0x40, 0x55)
GREY_DIM = QColor(0x80, 0x40, 0x60, 0x90)

#: The disable button's two states, spelled the way the XAML spells them: a
#: #AARRGGBB string, which Qt parses in the same alpha-first order WPF uses.
#: The orange is MainWindow.xaml's DisableButton background verbatim and the
#: slate is its desaturated counterpart, both at the original's 80% alpha so the
#: opacity slider still reads true.
#:
#: Not QColor(r, g, b, a). That constructor is RGBA, so the natural-looking
#: QColor(0xCC, 0xE0, 0x70, 0x30) for XAML's #CCE07030 yields #cce070 at 19%
#: alpha -- yellow-green and nearly transparent, not orange at 80%. layout.py
#: gets this right because it passes the hex as a string; the four-int form here
#: is the odd one out. String form is therefore mandatory in this pair.
DISABLE_COLOR = QColor("#CCE07030")
DISABLED_COLOR = QColor("#CC55606E")
#: Tooltips for the same two states. While the overlay is off this button is
#: one of only two things left on screen, and the only one that explains why,
#: so the disabled wording has to name the way back rather than just the action.
TOOLTIP_ENABLED = "Hide the overlay (click again to bring it back)"
TOOLTIP_DISABLED = "Overlay hidden - click to bring the controls back"


def layout_path() -> str:
    """GlassPad's %AppData%\\GlassPad\\layout.json -> $XDG_CONFIG_HOME/GlassPad."""
    base = os.environ.get(
        "XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")
    )
    return os.path.join(base, "GlassPad", "layout.json")


class MainWindow(QWidget):
    def __init__(self, pad: PadService | None = None) -> None:
        super().__init__()
        self.setWindowTitle("GlassPad")
        self.setObjectName("GlassPadOverlay")
        self._set_window_flags()

        self._pad = pad or PadService()
        self._fab_open = False
        self._edit_active = False
        #: The disable button latches, so the state it toggles has to live on the
        #: window rather than being implied by which way the contact is moving.
        self._overlay_enabled = True
        self._opacity_value = 1.0
        self._anims: list[QPropertyAnimation] = []
        self._shape_timer: QTimer | None = None

        self.resize(*self._screen_size())
        self._root = QWidget(self)
        self._root.setGeometry(self.rect())

        # Gamepad canvas, then the edit overlay above it (hidden by default).
        self._canvas = QWidget(self._root)
        self._canvas.setGeometry(self.rect())

        self._layout = GamepadLayout(self._canvas)
        self._layout.build(self.width(), self.height())

        self._edit_overlay = EditOverlay(self._root)
        self._edit_overlay.setVisible(False)

        self._hub_buttons = self._build_hub()
        self._edit_panel = self._build_edit_panel()
        # Info before save-confirm: MainWindow.xaml gives them Panel.ZIndex 50
        # and 60, so if both are ever up the save dialog is the one on top. Qt
        # has no ZIndex, so creation order is what decides it.
        self._info_panel = self._build_info_panel()
        self._save_confirm = self._build_save_confirm()

        self._wire_layout_input()
        self._restore_layout()
        self._apply_fade()

        # Visibility of any part of the overlay is reported through the router's
        # application-level filter, because that visibility is what decides
        # whether a touch reaches us at all: the input region has to be re-cut
        # when the hub opens or a panel appears, or the freshly drawn dialog
        # swallows nothing and every tap in it goes through to the game behind.
        # There is no cheaper way to be sure none is missed -- PyQt6 exposes no
        # QWidget.visibilityChanged, and show()/hide() are called from the hub
        # animations, the panels' own buttons and _set_overlay_enabled alike.
        self._router = TouchRouter(self, self._claimable_targets,
                                   self._input_blockers,
                                   self._schedule_shape_refresh)
        self._pad.init(self._on_pad_status)

    # -- accessors for tests ---------------------------------------------
    @property
    def layout(self) -> GamepadLayout:
        return self._layout

    @property
    def pad(self) -> PadService:
        return self._pad

    @property
    def edit_overlay(self) -> EditOverlay:
        return self._edit_overlay

    @property
    def hub_buttons(self) -> dict[str, FabButton]:
        return self._hub_buttons

    # -- window setup -----------------------------------------------------
    def _set_window_flags(self) -> None:
        # Qt.Tool gives "no taskbar entry" on X11 and Wayland; paired with
        # WA_ShowWithoutActivating the game keeps keyboard focus, which is the
        # behaviour WS_EX_NOACTIVATE provides on Windows.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, True)

    @staticmethod
    def _screen_size() -> tuple[int, int]:
        screen = QGuiApplication.primaryScreen()
        if screen is None:  # pragma: no cover - headless
            return 1920, 1080
        geo = screen.geometry()
        return geo.width(), geo.height()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.setGeometry(0, 0, *self._screen_size())
        self._root.setGeometry(self.rect())
        self._canvas.setGeometry(self.rect())
        self._edit_overlay.setGeometry(self.rect())
        self._relocate_hub()
        self._sync_centered_panels()
        self._refresh_passthrough()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._root.setGeometry(self.rect())
        self._canvas.setGeometry(self.rect())
        self._edit_overlay.setGeometry(self.rect())
        self._relocate_hub()
        self._sync_centered_panels()
        self._refresh_passthrough()

    # -- hub --------------------------------------------------------------
    def _build_hub(self) -> dict[str, FabButton]:
        self._hub = QWidget(self._root)
        self._hub.setFixedSize(HUB_WIDTH, HUB_HEIGHT)
        self._hub.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        specs = [
            # (key, glyph, colour, left, top, always visible, tooltip)
            ("quit", "\u2715", QColor(0xCC, 0xFF, 0x33, 0x33), 121, 28, False,
             "Quit GlassPad"),
            ("edit", "\u270E", QColor(0xCC, 0x40, 0x80, 0xFF), 181, 88, False,
             "Edit button layout"),
            ("info", "\u2139", QColor(0xCC, 0x20, 0x80, 0x60), 121, 148, False,
             "About GlassPad"),
            ("disable", "\u2205", QColor(DISABLE_COLOR), 65, 88, True,
             TOOLTIP_ENABLED),
            ("fab", "\u2261", QColor(0xCC, 0x50, 0x50, 0xA0), 121, 88, True,
             "Menu"),
        ]
        buttons: dict[str, FabButton] = {}
        for key, glyph, color, left, top, visible, tip in specs:
            btn = FabButton(glyph, color, tip, self._hub)
            btn.move(left, top)
            btn.setVisible(visible)
            buttons[key] = btn

        buttons["fab"].clicked.connect(self._toggle_fab)
        buttons["quit"].clicked.connect(self.close)
        buttons["edit"].clicked.connect(self._toggle_edit)
        buttons["info"].clicked.connect(self._open_info)
        # DisableButton latches: one press hides the overlay and it stays hidden
        # until pressed again. The Windows original was momentary -- TouchDown
        # hid and TouchUp showed, so the overlay vanished only while a finger
        # covered the button and the button could not see its own release -- but
        # on a touchscreen that is what makes it unusable: you cannot see what
        # you are uncovering, and a long press in a game does not survive being
        # lifted. Deviating from the original is deliberate; see CHANGELOG.
        #
        # Wired to `pressed`, not `clicked`: FabButton emits clicked on a
        # release wherever the contact ended (control_move deliberately does not
        # un-press, for WPF Capture parity), so a finger that slid off the
        # circle on the way out would toggle the overlay back on for reasons the
        # user cannot see. Pressing is also immediate, as the original was.
        buttons["disable"].pressed.connect(self._toggle_overlay_enabled)
        # The hub alone: _edit_panel does not exist yet at this point in __init__.
        self._hub.move((self.width() - HUB_WIDTH) // 2, 8)
        return buttons

    def _relocate_hub(self) -> None:
        self._hub.move((self.width() - HUB_WIDTH) // 2, 8)
        panel = getattr(self, "_edit_panel", None)
        if panel is not None:
            panel.move((self.width() - panel.width()) // 2, EDIT_PANEL_TOP)

    # -- hub animation ----------------------------------------------------
    def _toggle_fab(self) -> None:
        self._fab_open = not self._fab_open
        if self._fab_open:
            self._set_fab("\u00D7", QColor(0xCC, 0xA0, 0x20, 0x20))
            # The edit button is tinted brighter while edit mode is active.
            self._hub_buttons["edit"].color = (
                QColor(0xCC, 0x00, 0xCC, 0xFF)
                if self._edit_active
                else QColor(0xCC, 0x40, 0x80, 0xFF)
            )
            self._hub_buttons["edit"].update()
            self._animate_in(self._hub_buttons["quit"], 0)
            self._animate_in(self._hub_buttons["edit"], 60)
            self._animate_in(self._hub_buttons["info"], 120)
        else:
            self._close_fab()

    def _set_fab(self, glyph: str, color: QColor) -> None:
        btn = self._hub_buttons["fab"]
        btn.glyph = glyph
        btn.color = color
        btn.update()

    def _close_fab(self) -> None:
        self._fab_open = False
        self._set_fab(
            "\u2261",
            QColor(0xCC, 0x20, 0x70, 0xA8)  # blue tint during edit mode
            if self._edit_active
            else QColor(0xCC, 0x50, 0x50, 0xA0),
        )
        for key in ("quit", "edit", "info"):
            self._animate_out(self._hub_buttons[key])

    def _animate_in(self, widget: QWidget, delay_ms: int) -> None:
        """WPF AnimateIn: opacity 0->1 and scale 0.5->1, 180ms cubic ease-out.

        QPropertyAnimation has no BeginTime equivalent (that is QML's), so the
        stagger is a single-shot timer that starts both animations together.
        """
        widget.show()
        widget.set_fade(0.0)
        widget.set_pop(0.5)

        def start() -> None:
            if not widget.isVisible():
                return  # hub was collapsed again before this delay elapsed
            fade = QPropertyAnimation(widget, b"fade", widget)
            fade.setDuration(180)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QEasingCurve.Type.OutCubic)
            fade.start()
            self._anims.append(fade)

            pop = QPropertyAnimation(widget, b"pop", widget)
            pop.setDuration(180)
            pop.setStartValue(0.5)
            pop.setEndValue(1.0)
            pop.setEasingCurve(QEasingCurve.Type.OutCubic)
            pop.start()
            self._anims.append(pop)

        if delay_ms > 0:
            QTimer.singleShot(delay_ms, start)
        else:
            start()

    def _animate_out(self, widget: QWidget) -> None:
        """WPF AnimateOut: opacity ->0 over 140ms, then Collapsed.

        Cancels the paired fade/pop animations first: the next fade animation
        must own the property, or the pop animation could re-pin a value.
        """
        for anim in self._anims:
            if anim.targetObject() is widget and anim.state() \
                    == QAbstractAnimation.State.Running:
                anim.stop()
        start = widget.fade if widget.fade > 0.0 else 1.0
        anim = QPropertyAnimation(widget, b"fade", widget)
        anim.setDuration(140)
        anim.setStartValue(start)
        anim.setEndValue(0.0)
        anim.finished.connect(widget.hide)
        anim.start()
        self._anims.append(anim)

    # -- input region upkeep ----------------------------------------------
    def _schedule_shape_refresh(self) -> None:
        """Re-apply the input region at most once per event-loop turn.

        Visibility changes arrive in bursts -- the hub's three buttons appear on
        0/60/120ms delays, and a panel hide takes its card and its controls out
        together. The region has to describe what is on screen *after* the burst,
        and re-cutting per change would mean several full XShape round trips for
        one user tap. showEvent/resizeEvent still refresh synchronously because
        the geometry itself changed and nothing is scheduled yet.
        """
        if self._shape_timer is None:
            self._shape_timer = QTimer(self)
            self._shape_timer.setSingleShot(True)
            self._shape_timer.timeout.connect(self._refresh_passthrough)
        if not self._shape_timer.isActive():
            self._shape_timer.start(0)

    # -- disable (latching toggle) ----------------------------------------
    def _toggle_overlay_enabled(self) -> None:
        self._set_overlay_enabled(not self._overlay_enabled)

    def _set_overlay_enabled(self, enabled: bool) -> None:
        """Port of SetOverlayEnabled: hide every element, release all input.

        Latching now, rather than momentary as in the original, so the state has
        to be remembered: `_overlay_enabled` is what the next press toggles
        against and what the button's colour and tooltip report.

        The disable button is excluded from the release so it keeps the pressed
        look while it is held and does not emit a `clicked` the user never
        meant; every other grab is lifted, which still matters -- a gamepad
        button held on another contact would otherwise stay pressed on the
        virtual pad for the rest of the session. See TouchRouter.release_all().
        """
        self._overlay_enabled = enabled
        if not enabled:
            self._router.release_all(keep=self._hub_buttons["disable"])
        for el in self._layout.all_editable:
            el.setVisible(enabled)
        if not enabled:
            self._pad.reset()
        self._refresh_passthrough()
        self._sync_disable_button()

    def _sync_disable_button(self) -> None:
        """Reflect the latched state on the button that controls it.

        The overlay's controls are what normally orient the user, and in the
        disabled state every one of them is hidden -- so the button has to say
        "off" and "click to bring them back" on its own, or a hidden overlay
        reads as a crash.
        """
        btn = self._hub_buttons.get("disable")
        if btn is None:
            return
        btn.color = QColor(DISABLE_COLOR if self._overlay_enabled
                           else DISABLED_COLOR)
        btn.setToolTip(TOOLTIP_ENABLED if self._overlay_enabled
                       else TOOLTIP_DISABLED)
        btn.update()

    # -- panels -----------------------------------------------------------
    @staticmethod
    def _font_css() -> str:
        return "font-family: " + ",".join(f'"{f}"' for f in UI_FONTS) + ";"

    @staticmethod
    def _mono_css() -> str:
        return "font-family: " + ",".join(f'"{f}"' for f in MONO_FONTS) + ";"

    def _pill_style(self, color: QColor, font_size: int = 12) -> str:
        return (
            f"QPushButton {{ {self._font_css()} color: white;"
            f" font-size: {font_size}px; border: none; border-radius: 8px;"
            f" background: rgba({color.red()},{color.green()},{color.blue()},"
            f"{color.alpha()}); }}"
        )

    def _pill(self, text: str, color: QColor, w: int, h: int,
              font_size: int = 12, parent: QWidget | None = None) -> QPushButton:
        btn = QPushButton(text, parent)
        btn.setFixedSize(w, h)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(self._pill_style(color, font_size))
        return btn

    def _build_edit_panel(self) -> QWidget:
        panel = Panel(EDIT_BG, 12, self._root)
        panel.setMinimumWidth(260)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        caption = QLabel("Button Opacity", panel)
        caption.setStyleSheet(
            f"color: #AAC0D0FF; font-size: 11px; {self._font_css()}")
        layout.addWidget(caption)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal, panel)
        # The WPF slider runs 0.15..1.0 in 0.05 steps; integer 15..100 keeps the
        # same quantisation without float drift.
        self._opacity_slider.setRange(15, 100)
        self._opacity_slider.setSingleStep(5)
        self._opacity_slider.setPageStep(10)
        self._opacity_slider.setValue(100)
        self._opacity_slider.valueChanged.connect(self._on_opacity)
        layout.addWidget(self._opacity_slider)

        mode = QLabel("D-Pad Mode", panel)
        mode.setStyleSheet(f"color: #AAC0D0FF; font-size: 11px; {self._font_css()}")
        layout.addWidget(mode)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._dpad4_btn = self._pill("4-Way", GREEN, 106, 30, parent=panel)
        self._dpad8_btn = self._pill("8-Way", GREY_DIM, 106, 30, parent=panel)
        self._dpad4_btn.clicked.connect(lambda: self._set_dpad_mode(DpadZone.FOUR))
        self._dpad8_btn.clicked.connect(lambda: self._set_dpad_mode(DpadZone.EIGHT))
        row.addWidget(self._dpad4_btn)
        row.addWidget(self._dpad8_btn)
        box = QWidget(panel)
        box.setLayout(row)
        layout.addWidget(box, alignment=Qt.AlignmentFlag.AlignHCenter)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        save = self._pill("\U0001F4BE  Save", GREEN, 106, 32, parent=panel)
        reset = self._pill("\u21BA  Reset", QColor(0xCC, 0x80, 0x40, 0x20), 106, 32,
                           parent=panel)
        save.clicked.connect(self._save_layout)
        reset.clicked.connect(self._reset_layout)
        row2.addWidget(save)
        row2.addWidget(reset)
        box2 = QWidget(panel)
        box2.setLayout(row2)
        layout.addWidget(box2, alignment=Qt.AlignmentFlag.AlignHCenter)

        panel.adjustSize()
        panel.setVisible(False)
        return panel

    def _build_save_confirm(self) -> QWidget:
        panel = Panel(PANEL_BG, 14, self._root)
        panel.setMinimumWidth(280)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(8)

        title = QLabel("\u2713  Layout Saved", panel)
        title.setStyleSheet(
            f"color: #88EEBB; font-size: 16px; font-weight: 600; {self._font_css()}")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        body = QLabel("Close edit mode?", panel)
        body.setStyleSheet(f"color: #AAC0D0FF; font-size: 13px; {self._font_css()}")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(body)

        row = QHBoxLayout()
        row.setSpacing(10)
        yes = self._pill("Yes", GREEN, 100, 34, 13, panel)
        no = self._pill("No", GREY, 100, 34, 13, panel)
        yes.clicked.connect(self._save_confirm_yes)
        no.clicked.connect(panel.hide)
        row.addWidget(yes)
        row.addWidget(no)
        box = QWidget(panel)
        box.setLayout(row)
        layout.addWidget(box, alignment=Qt.AlignmentFlag.AlignHCenter)

        panel.adjustSize()
        panel.setVisible(False)
        return panel

    def _build_info_panel(self) -> QWidget:
        panel = Panel(PANEL_BG, 16, self._root)
        panel.setMinimumWidth(320)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(4)

        def add(text: str, css: str) -> QLabel:
            label = QLabel(text, panel)
            label.setStyleSheet(css)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setMaximumWidth(360)
            layout.addWidget(label)
            return label

        ui, mono = self._font_css(), self._mono_css()
        add("GlassPad", f"color: white; font-size: 24px; font-weight: bold; {ui}")
        add("Touchscreen \u2192 Gamepad Overlay",
            f"color: #AAC0D0FF; font-size: 13px; {ui}")
        add("by Dansunmi", f"color: #AACCE0FF; font-size: 12px; {ui}")
        add("github.com/dansunmi/GlassPad", f"color: #6688AACC; font-size: 11px; {mono}")
        add(BACKEND_LABEL, f"color: #88A0B8C8; font-size: 10px; {mono}")
        add(f"GlassPad v{APP_VERSION} (Linux)",
            f"color: #88A0B8C8; font-size: 10px; {mono}")
        self._status_label = add("Connecting controller...",
                                 f"color: #AACCE0FF; font-size: 12px; {ui}")
        licenses = self._pill("Open-source licenses", GREY, 200, 34, 12, panel)
        licenses.clicked.connect(self._open_licenses)
        layout.addWidget(licenses, alignment=Qt.AlignmentFlag.AlignHCenter)
        close = self._pill("Close", GREY, 120, 34, 13, panel)
        close.clicked.connect(panel.hide)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignHCenter)

        panel.adjustSize()
        panel.setVisible(False)
        return panel

    # -- panel actions ----------------------------------------------------
    def _on_opacity(self, value: int) -> None:
        self._opacity_value = value / 100.0
        self._apply_fade()

    def _apply_fade(self) -> None:
        """Port of OpacitySlider_ValueChanged -> every element's Opacity."""
        for el in self._layout.all_editable:
            self._set_element_opacity(el, self._opacity_value)
        # XAML binds only the disable button and the FAB to the slider.
        self._hub_buttons["disable"].set_fade(self._opacity_value)
        self._hub_buttons["fab"].set_fade(self._opacity_value)
        self._refresh_passthrough()

    @staticmethod
    def _set_element_opacity(widget: QWidget, value: float) -> None:
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
        effect.setOpacity(max(0.0, min(1.0, value)))

    def _set_dpad_mode(self, mode: int) -> None:
        if self._layout.dpad is None:
            return
        self._layout.dpad.mode = mode
        self._sync_dpad_buttons()

    def _sync_dpad_buttons(self) -> None:
        dpad = self._layout.dpad
        is8 = dpad is not None and dpad.mode == DpadZone.EIGHT
        self._dpad4_btn.setStyleSheet(self._pill_style(GREY_DIM if is8 else GREEN))
        self._dpad8_btn.setStyleSheet(self._pill_style(GREEN if is8 else GREY_DIM))

    def _save_layout(self) -> None:
        dpad = self._layout.dpad
        mode = 8 if (dpad is not None and dpad.mode == DpadZone.EIGHT) else 4
        self._layout.save_layout(layout_path(), self._opacity_value, mode)
        self._show_centred(self._save_confirm)

    def _save_confirm_yes(self) -> None:
        self._save_confirm.setVisible(False)
        self._edit_active = False
        self._edit_overlay.exit()
        self._edit_panel.setVisible(False)
        if self._fab_open:
            self._close_fab()
        self._refresh_passthrough()

    def _reset_layout(self) -> None:
        self._layout.reset()
        self._opacity_slider.setValue(100)
        self._edit_overlay.set_elements(self._layout.all_editable)
        self._refresh_passthrough()

    def _toggle_edit(self) -> None:
        self._close_fab()
        self._router.release_all()
        # Entering edit mode brings the controls back. Editing positions you
        # cannot see is not a thing worth supporting, and the edit panel's Reset
        # calls GamepadLayout.reset(), which show()s every element -- so without
        # this, resetting from edit mode would reveal the overlay while the
        # disable button still claimed it was hidden and showed itself as off.
        if not self._edit_active and not self._overlay_enabled:
            self._set_overlay_enabled(True)
        self._edit_active = not self._edit_active
        self._edit_overlay.toggle()
        if self._edit_active:
            self._edit_overlay.set_elements(self._layout.all_editable)
            self._edit_panel.adjustSize()
            self._edit_panel.show()
            self._relocate_hub()
            self._sync_dpad_buttons()
        else:
            self._edit_panel.setVisible(False)
        self._refresh_passthrough()

    def _open_info(self) -> None:
        self._close_fab()
        self._show_centred(self._info_panel)

    def _open_licenses(self) -> None:
        LicenseWindow(self).show()

    def _show_centred(self, panel: QWidget) -> None:
        panel.adjustSize()
        panel.move(
            (self.width() - panel.width()) // 2,
            (self.height() - panel.height()) // 2,
        )
        panel.show()
        panel.raise_()

    def _sync_centered_panels(self) -> None:
        for panel in (self._save_confirm, self._info_panel):
            if panel.isVisible():
                panel.move(
                    (self.width() - panel.width()) // 2,
                    (self.height() - panel.height()) // 2,
                )

    # -- pad wiring -------------------------------------------------------
    def _wire_layout_input(self) -> None:
        for el, tag in self._layout.buttons:
            self._wire_button(el, tag)
        for stick in self._layout.sticks:
            if stick.name == "LS":
                stick.changed.connect(lambda z: self._pad.set_left_stick(z.X, z.Y))
            else:
                stick.changed.connect(lambda z: self._pad.set_right_stick(z.X, z.Y))
        if self._layout.dpad is not None:
            self._layout.dpad.hat_changed.connect(self._pad.set_dpad)

    def _wire_button(self, el: QWidget, tag: str) -> None:
        """Port of the per-button TouchDown / TouchUp / LostTouchCapture set."""
        trigger = TAG_TO_TRIGGER.get(tag)
        button = TAG_TO_BUTTON.get(tag)

        def down() -> None:
            el.set_press_scale(1.08)
            if trigger == "left":
                self._pad.set_left_trigger(1.0)
            elif trigger == "right":
                self._pad.set_right_trigger(1.0)
            elif button:
                self._pad.press_button(button)

        def up() -> None:
            el.set_press_scale(1.0)
            if trigger == "left":
                self._pad.set_left_trigger(0.0)
            elif trigger == "right":
                self._pad.set_right_trigger(0.0)
            elif button:
                self._pad.release_button(button)

        el.pressed.connect(down)
        el.released.connect(up)

    def _restore_layout(self) -> None:
        """Port of the restore block in OnSourceInitialized."""
        path = layout_path()
        if not os.path.exists(path):
            return
        opacity, dpad_mode = self._layout.load_layout(path)
        self._opacity_slider.setValue(int(round(opacity * 100)))
        for el in self._layout.all_editable:
            self._set_element_opacity(el, opacity)
        if self._layout.dpad is not None:
            self._layout.dpad.mode = (
                DpadZone.EIGHT if dpad_mode == 8 else DpadZone.FOUR
            )

    def _on_pad_status(self, text: str) -> None:
        """Status arrives on the pad's init thread; hop to the GUI thread."""
        QTimer.singleShot(0, lambda: self._status_label.setText(text))

    # -- input routing and click-through ----------------------------------
    def _claimable_targets(self) -> list[QWidget]:
        """Controls the router may claim, in z-order (bottom first).

        The router scans this in reverse, so anything listed later wins a
        contested pixel -- the list must therefore be ordered exactly like the
        XAML tree: TouchCanvas first, then EditCanvas, then HubCanvas.

        While edit mode is on the overlay covers the window, which is the
        Windows behaviour (the tinted EditCanvas swallows every touch aimed at
        the gamepad). The hub buttons must still be listed *above* it: in
        MainWindow.xaml HubCanvas is a later sibling than EditCanvas, so the
        pencil is what a touch there reaches, and that is the only way out of
        edit mode. Omitting them here would hand every press to the tint.

        Panels are deliberately absent in both modes so Qt's touch-to-mouse
        synthesis still drives the QPushButtons, QSlider and mode buttons inside
        them -- the hub container itself is a plain QWidget for the same reason;
        only its buttons are claimable, and hidden ones drop out in the router.
        """
        targets: list[QWidget] = []
        if self._edit_active:
            targets.append(self._edit_overlay)
        else:
            targets += self._layout.all_editable
        targets += list(self._hub_buttons.values())
        return targets

    def _input_blockers(self) -> list[QWidget]:
        """Panels stacked above the canvas and the edit tint, in z-order.

        Everything in here is a Panel whose own QPushButtons are driven by Qt's
        touch-to-mouse synthesis, so the router must stay off their pixels.
        """
        return [self._edit_panel, self._info_panel, self._save_confirm]

    def _refresh_passthrough(self) -> None:
        """Restrict the window's input region to what is actually drawn.

        In edit mode the window takes input everywhere: the tint must swallow
        touches aimed at the game, and the hub and edit panel sit inside that
        same region so their controls stay reachable.
        """
        if self._edit_active:
            passthrough.block_whole_window(self)
            return
        blockers = list(self._layout.all_editable)
        blockers += list(self._hub_buttons.values())
        blockers += self._input_blockers()
        passthrough.apply(self, blockers)

    # -- lifecycle --------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        self._pad.dispose()
        self._router.shutdown()
        super().closeEvent(event)
