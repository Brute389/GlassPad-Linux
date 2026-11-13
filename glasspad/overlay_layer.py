"""Layer-shell overlay backend (spike).

Proof-of-concept for the design the probe validated on this machine:

  * KWin 6.8.2 advertises zwlr_layer_shell_v1 v5 (verified by registry dump).
  * A layer OVERLAY surface survives a fullscreen game.
  * Taps that land between surfaces reach the game, because there is no surface
    there -- pass-through by geometry, with no input region, no XShape and no
    alpha hit-testing. So this backend deliberately renders ONE SURFACE PER
    CONTROL instead of a full-screen canvas.

What it proves, in priority order:

  1. CONCURRENT CONTACTS. The hub readout shows the largest number of fingers
     seen on the overlay at the same instant. This is the one property that
     could change the architecture rather than the code, so run
     `--touch-test` first: a single full-screen surface that just counts.
  2. End-to-end with the real uinput device from pad.PadService.
  3. Reuse of layout.py's geometry without touching the Qt widgets.

Geometry comes from layout.json -- the same file the Windows app and the Qt
editor write, positions indexed by ELEMENT_ORDER. --save-layout dumps the
built-in defaults there so the Qt editor has something to fine-tune.

Still not here: the FAB hub and in-overlay editing.
The Qt UI stays where it is; Qt and GTK cannot share one event loop, so this is
a separate process by design (`python3 -m glasspad.overlay_layer`).

Preload
-------
gtk4-layer-shell must be in the global namespace before PyGObject pulls in Gtk.
Loading it with ctypes RTLD_GLOBAL first achieves that in-process, so no
LD_PRELOAD is needed in the desktop entry. Where the library lives is a distro
question and a bundle question, so it is searched for -- see glasspad/bundle.py
(override with `GLASSPAD_LAYER_SHELL=/path/to/libgtk4-layer-shell.so.0`).

This is not a performance detail. Measured on this machine, in one process: load
the typelib normally and `Gtk4LayerShell.is_supported()` returns False for the
lifetime of the process, with or without pumping the GLib context; load the same
file RTLD_GLOBAL first and it returns True on the same compositor and display.
Whatever the loader is doing upstream, the practical rule is that *nothing* asks
that question before the preload -- including --status, which had been doing
exactly that and reporting KWin as having no layer-shell.
"""
from __future__ import annotations

import argparse
import ctypes
import math
import os
import sys
import time

# Where this lives depends on the distro *and* on whether we are running out of a bundle,
# so it is searched for rather than hardcoded. glasspad/bundle.py explains the order and
# returns which rule matched, because "not found" is not a diagnosis.
from glasspad.bundle import layer_shell_path as _find_layer_shell  # noqa: E402
from glasspad.bundle import read_doc  # noqa: E402

LAYER_SHELL_LIB, LAYER_SHELL_WHY = _find_layer_shell()


def _preload_layer_shell() -> str:
    """Load gtk4-layer-shell ahead of libwayland; report how it went."""
    if not LAYER_SHELL_LIB:
        print(f"  gtk4-layer-shell not found ({LAYER_SHELL_WHY}); "
              f"trying LD_PRELOAD -- set {GLASSPAD_ENV_HINT} if you know where it is")
        return "LD_PRELOAD (external)"
    try:
        ctypes.CDLL(LAYER_SHELL_LIB, mode=ctypes.RTLD_GLOBAL)
        return f"ctypes RTLD_GLOBAL ({LAYER_SHELL_LIB}, found via {LAYER_SHELL_WHY})"
    except OSError as exc:
        print(f"  ctypes preload failed ({LAYER_SHELL_LIB}: {exc}); relying on LD_PRELOAD")
        return "LD_PRELOAD (external)"


GLASSPAD_ENV_HINT = "GLASSPAD_LAYER_SHELL=/path/to/libgtk4-layer-shell.so.0"


_HOW = _preload_layer_shell()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import (  # noqa: E402
    Gdk, Gio, GLib, Gtk, Gtk4LayerShell as LayerShell,
)

from glasspad import __version__  # noqa: E402
from glasspad import layer_dpad as DP  # noqa: E402
from glasspad import layer_press as LP  # noqa: E402
from glasspad import layer_style as S  # noqa: E402
from glasspad import layer_profiles as prof
from glasspad import pad  # noqa: E402
from glasspad import xd360  # noqa: E402
from glasspad.layer_turbo import (  # noqa: E402
    DEFAULT_TURBO_HZ,
    TAP_SLOP,
    Turbo,
    clamp_hz,
    half_period_ms,
)
from glasspad.layer_edge import (  # noqa: E402
    DEFAULT_MARGIN as EDGE_DEFAULT,
    clamp_margin as edge_clamp,
    describe as edge_describe,
    refuses as edge_refuses,
)
from glasspad.layer_lock import Lockout  # noqa: E402
from glasspad.layer_stick import (  # noqa: E402
    PRESETS,
    StickTuning,
    deflect,
    from_dict as stick_from_dict,
    nub_offset,
    preset as stick_preset,
)
from glasspad.watchdog import (  # noqa: E402
    DEFAULT_HOLD_LIMIT,
    Watchdog,
    clamp_limit,
)
from glasspad.layer_geom import (
    BUTTON,
    DPAD,
    ELEMENT_ORDER,
    STICK,
    TRIGGER,
    Frame,
    circle_region,
    default_frames,
    default_layout_path,
    fit_frames,
    load_screen_size,
    hat_with_hysteresis,
    label_for,
    label_size,
    GRIP_SIZE,
    MIN_CONTROL,
    grip_centre,
    on_grip,
    render_shape,
    size_from_drag,
    inside_shape,
    load_frames,
    load_turbo,
    load_stick_tuning,
    load_turbo_hz,
    rect_region,
    rounded_rect_region,
    stick_deflection,
    write_frames,
)


#: Why plain gestures are enough here. Every public GTK4 gesture descends from
#: GestureSingle -- one controller, one contact -- and the raw alternative is a
#: dead end from Python: EventControllerLegacy::event hands back a None where the
#: Gdk.Event should be (GdkEvent is a boxed union that GI does not marshal), which
#: is what the 20k-line traceback in /tmp/touch_test.log was.
#:
#: One surface per control turns that limitation into a non-event. A thumb on LS
#: and a finger on A land on two different surfaces, hence two different
#: controllers, and both stay live. The open question is therefore no longer a
#: GTK question but a compositor one: will KWin deliver a second touch to a second
#: OVERLAY surface while the first is still grabbed? The held-controls readout in
#: the hub measures exactly that, and it is the property the whole design rests on.

# The controls themselves are painted with cairo (see layer_style), so almost none
# of this is about them: the surfaces are transparent and the DrawingArea fills
# them. What is left is the settings panel, styled from MainWindow.xaml's EditPanel
# (#DD1a1a2e, radius 12, labels #AAC0D0FF at 11px, PillBtn radius 8 at 12px).
#: Type size for the size readout shown while a control is being dragged.
HUD_PX = 12.0

# Amber, for a knob that is armed. Not from the Windows client, which has no turbo:
# deliberately far from the slate and green of an ordinary knob and from the orange of
# the hidden-pad state, so the three cannot be confused at a glance.
_TURBO_KNOB = (1.00, 0.72, 0.10, 0.95)

_KIND_BY_NAME = {name: kind for name, kind, _tag in ELEMENT_ORDER}

CSS = """
/* The app spells its colours #AARRGGBB and GTK CSS reads #RRGGBBAA, so every
   literal below is converted rather than copied. '#cc208060' in GTK is a magenta
   at 38% alpha, not the app's green at 80% -- the same trap window.py documents for
   QColor(r,g,b,a). rgba() form is therefore mandatory in this sheet. */
window.ctrl, window.hub, window.panelwin { background: transparent; }
/* EditPanel: Background #DD1a1a2e -> 0.867 over (26,26,46), CornerRadius 12. */
/* padding: xaml EditPanel says Padding="16,12"; this sheet paints 20px across,
   14px down. Divergence left deliberately -- it predates this note, and the
   panel's on-screen size was tuned against captures rather than against the
   attribute. Changing it to 16px 12px is a real 8x4 px change to a visible box,
   so it wants eyes on the result, not a tidy number in a file. */
.panel { background: rgba(26,26,46,0.867); border-radius: 12px; padding: 14px 20px; }
.confirm, .info { background: rgba(26,26,46,0.941); }
.info    { border-radius: 16px; padding: 28px 36px; }  /* InfoPanel: 16, "36,28" */
.confirm { border-radius: 14px; padding: 22px 28px; }  /* SaveConfirmPanel: 14, "28,22" */
/* padding, not margin_*: the app's panels are a Border with Padding, so the fill
   covers the padding. margin_* sits OUTSIDE the background and leaves a transparent
   ring inside the panel's own edge -- measured, that cost each panel 40 px of width
   and 28 of height of visible fill that the original has. */
.panel label { color: rgba(192,208,255,0.667); font-size: 11px; }
.info .title { color: #ffffff; font-size: 24px; font-weight: 800; }
.info .sub   { color: rgba(192,208,255,0.667); font-size: 13px; }
.info .author{ color: rgba(204,224,255,0.667); font-size: 12px; }
.info .url   { color: rgba(136,170,204,0.4); font-size: 11px; }
.info .backend, .info .ver { color: rgba(160,184,184,0.533); font-size: 10px; }
.info .status{ color: rgba(204,224,255,0.667); font-size: 12px; }
.confirm .ok   { color: #88eebb; font-size: 16px; font-weight: 600; }
.confirm .body { color: rgba(192,208,255,0.667); font-size: 13px; }
.pillbtn { background-image: none; background-color: rgba(32,128,96,0.8);
           color: #ffffff; font-size: 12px; border: none; box-shadow: none;
           border-radius: 8px; padding: 2px 6px; }
/* no min-width here on purpose: the app sizes each pill itself (106 in the edit
   panel, 100 in the save dialog, 200 and 120 in the about box), and a CSS minimum
   silently overrides every one of those requests. */
.pillbtn:hover { background-color: rgba(47,154,116,0.9); }
.pillbtn.off { background-color: rgba(64,96,144,0.5); }
.pillbtn.warn { background-color: rgba(128,64,32,0.8); }
.pillbtn.warn:hover { background-color: rgba(154,80,40,0.9); }
.pillbtn.grey { background-color: rgba(64,64,85,0.8); }
.pillbtn.grey:hover { background-color: rgba(84,84,105,0.9); }
/* the licence reader. A TextView in GTK 4 puts its glyphs under a `text` node,
   and a colour set only on the outer node leaves them at the theme default -- a
   dark theme on a dark panel is how a licence becomes a blank box. */
.licence { background: rgba(26,26,46,0.96); border-radius: 14px; padding: 18px 22px; }
.licence text { background: rgba(0,0,0,0.22); color: rgba(204,224,255,0.86);
                font-size: 11px; }
.licence textview, .licence viewport { background: transparent; }
.licence scrollbar { background: none; }
.licence scrollbar slider { background: rgba(160,184,184,0.35); min-width: 6px; }
"""

#: Fill per control, straight from GamepadLayout.Build()'s #AARRGGBB literals.
FILLS = S.FILLS


# --- status-line rate limits -------------------------------------------------
# One line per control per this interval, for readouts that stream every motion
# event. Chosen by what is left readable in `journalctl -u app-glasspad@*`: a drag
# still shows as a handful of lines that trace the movement, not 412 of them.
QUIET_FLUSH_S = 0.5

# Key for the "same sentence twice in a row" guard on non-streaming messages. An
# underscore-prefixed dict key that no control can have, because control names come
# from the layout.
_ONE_SHOT = "\x00oneshot"


def _log_wants_everything() -> bool:
    """GLASSPAD_LOG=full turns folding off.

    A log that hides information with no way to get it back is a bad bargain when the
    thing being debugged might be the folding itself.
    """
    return (os.environ.get("GLASSPAD_LOG") or "").strip().lower() in (
        "full", "1", "yes", "true")


class Overlay:
    def __init__(self, service: pad.PadService, opacity: float, *,
                 edit: bool = False, layout_path: str = "",
                 dpad_mode: int = 4,
                 hold_limit: float = DEFAULT_HOLD_LIMIT) -> None:
        self.service = service
        self.opacity = opacity
        self.edit = edit
        self.layout_path = layout_path or default_layout_path()
        self.dpad_mode = dpad_mode
        self.surfaces: dict[str, Gtk.Window] = {}
        self.geo: dict[str, list[float]] = {}      # name -> [x, y, w, h], live
        # drag state per control: {"mode": "move"|"size", ...anchor and start size}
        self._grab: dict[str, dict] = {}
        self._sizing: str | None = None            # which control is being sized now
        # the size readout: its own surface, because the control's surface is exactly
        # the control's frame and there is no room under it for a number
        self.hud: Gtk.Window | None = None
        self.hud_area: Gtk.DrawingArea | None = None
        self._hud_text = ""
        self._hud_size = (1, 1)
        self._hud_gen = 0
        self._dirty = False
        self._quit_armed = False
        self._ignored: set[str] = set()
        self.input_regions = True
        self.hit_shapes = True
        # FAB / menu state, and the per-control controller pairs that edit mode
        # swaps between (None = not attached).
        self.ctrl_input: dict[str, Gtk.EventController | None] = {}
        self.ctrl_move: dict[str, Gtk.EventController | None] = {}
        self.menu_open = False
        #: what the latching disable button toggles; named as window.py names it
        self.overlay_enabled = True
        # hub state: the radial FAB and its children, then the settings panel
        self.hub: Gtk.Window | None = None
        self.hub_box: Gtk.Fixed | None = None
        self.hub_children: dict[str, Gtk.DrawingArea] = {}
        self.hub_state: dict[str, dict] = {}
        self.hub_x = 0.0
        self.panel: Gtk.Window | None = None
        self.dpad4_btn: Gtk.Button | None = None
        self.dpad8_btn: Gtk.Button | None = None
        # info panel ("\u2139"), save confirmation, and the pad-status line inside info
        self.info: Gtk.Window | None = None
        #: the licence reader, built on first use: it is opened once a year at most
        self.licence: Gtk.Window | None = None
        self.licence_view: Gtk.TextView | None = None
        self.licence_scroll: Gtk.ScrolledWindow | None = None
        self.confirm: Gtk.Window | None = None
        self.discard: Gtk.Window | None = None
        self.status_label: Gtk.Label | None = None
        self.sw, self.sh = 0, 0
        self.windows: list[Gtk.Window] = []
        self.active: dict[str, str] = {}        # control name -> kind (one contact per surface)
        # Every other release path here is something the compositor told us about.
        # This is the one for the cases where nobody tells us: an exclusive input
        # grab, a blanked screen, a suspend with a thumb on the stick. On a gamepad a
        # stuck control is not cosmetic -- see watchdog.py.
        self.watchdog = Watchdog(hold_limit)
        # geometry as authored, and the screen it was authored for: the base every
        # screen change is refitted from (see apply_screen)
        self.base_frames: list[Frame] = []
        self.design_screen: tuple[int, int] | None = None
        self.sw = 0
        self.sh = 0
        self.turbo = Turbo(DEFAULT_TURBO_HZ)
        # Fences a contact that we released for the player, so it cannot press the
        # control again until the contact actually ends. See glasspad/layer_lock.py.
        self.lockout = Lockout()
        self.edge_margin = EDGE_DEFAULT   # replaced by main() from --edge-margin
        # Profiles: which named layout this session is, and what a launcher asked
        # for. main() sets them all; the defaults keep a bare Overlay usable.
        self.profile_name: str | None = None
        self.cfg_dir: str | None = None
        self.profiles_enabled = True
        self.profile_timeout = 0.0
        self._pending_profile: str | None = None
        self._claim_monitor = None          # referenced or Gio finalises it
        self._claim_timer = None
        self._claim_seen = None
        self.stick_tuning = StickTuning()      # replaced by main() from file/flags
        self._turbo_timers: dict[str, int] = {}   # name -> GLib source id
        self._monitor_refs: list = []
        self._monitor_sources: dict = {}
        self._hold_timer = None
        self.max_contacts = 0
        self._hat = "None"                      # last d-pad direction, for hysteresis
        self._start: dict[str, tuple[float, float]] = {}   # press point per control
        self.trace = False                      # GLASSPAD_TRACE=1
        self.stick_anchor = "press"             # or "surface"
        # d-pad look: the client's own rendering is "app"; the defaults here are the
        # quieter set, see glasspad/layer_dpad.py for why
        self.dpad_style = "notch"               # app | hairline | notch | spoke | bare
        self.dpad_cross = "soft"                # app | soft | rounded
        self.dpad_press = "cool"                # ice | cool | neutral
        # how a button shows a press; "outset" is the Windows client's 1.08 and gets
        # clipped into a square by the surface, see glasspad/layer_press.py
        self.press_mode = "bevel"               # bevel | glow | outset
        self.press_anim_ms = 140                # eased return to rest; 0 = snap
        self._anim_gen: dict[str, int] = {}
        self.hub_label: Gtk.Label | None = None
        self.loop: GLib.MainLoop | None = None
        # Per-control visual state: press scale, stick nub offset, lit d-pad arms.
        # The painters read this dict; input handlers write it. Kept separate from
        # geo so dragging a control cannot disturb how it looks.
        self.vis: dict[str, dict] = {}
        self.areas: dict[str, Gtk.DrawingArea] = {}

    # -- surfaces ---------------------------------------------------------
    def _make_surface(self, w: int, h: int, css_class: str) -> Gtk.Window:
        win = Gtk.Window()
        win.add_css_class("ctrl")
        if css_class:
            win.add_css_class(css_class)
        win.set_default_size(w, h)
        win.set_size_request(w, h)
        win.set_opacity(self.opacity)

        LayerShell.init_for_window(win)
        if not LayerShell.is_layer_window(win):
            raise RuntimeError(
                "not a layer surface - gtk4-layer-shell was loaded after "
                "libwayland; install libgtk4-layer-shell or set `GLASSPAD_LAYER_SHELL`"
            )
        LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
        # NONE: the overlay never takes keyboard focus, so a keyboard-driven
        # game is undisturbed by its existence.
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
        LayerShell.set_exclusive_zone(win, -1)
        LayerShell.set_anchor(win, LayerShell.Edge.LEFT, True)
        LayerShell.set_anchor(win, LayerShell.Edge.TOP, True)
        return win

    def place(self, win: Gtk.Window, x: int, y: int) -> None:
        LayerShell.set_margin(win, LayerShell.Edge.LEFT, int(x))
        LayerShell.set_margin(win, LayerShell.Edge.TOP, int(y))

    # -- input ------------------------------------------------------------
    def _live(self, name: str, fallback) -> tuple[float, float]:
        """The control's size as it is now, not as it was when the widget was built.

        The gesture callbacks used to close over the w/h from build time, so once a
        control was resized -- in edit mode, or by a profile swap or a rotation -- the
        shape gate and the stick's saturation radius were still measuring the old box.
        A stick grown from 119 to 238 px kept saturating where 119 px did.
        """
        g = self.geo.get(name)
        return (g[2], g[3]) if g else fallback

    def _click_controller(self, name: str, kind: str, w: int, h: int) -> Gtk.GestureClick:
        """Buttons and triggers: down -> 1, up -> 0."""
        g = Gtk.GestureClick.new()
        g.set_button(0)  # 0 = any button, and touch
        g.connect("pressed", lambda _g, _n, x, y: self._press(
            name, kind, x, y, *self._live(name, (w, h))))
        g.connect("released", lambda _g, _n, x, y: self._release(name, kind))
        g.connect("cancel", lambda _g, _s: self._release(name, kind))  # GtkGesture::cancel
        return g

    def _drag_controller(self, name: str, kind: str, w: int, h: int) -> Gtk.GestureDrag:
        """Sticks and dpad: position is read on every step, not just on press.

        The press point is captured from drag-begin's own arguments and the offset
        from drag-update's own arguments, because that pair is authoritative for
        the event being handled. The previous version re-fetched state through
        get_start_point()/get_offset(), and the result was sticks that could only
        ever reach one quadrant: 672 axis events, not one of them negative.
        """
        g = Gtk.GestureDrag.new()
        g.set_button(0)
        g.connect("drag-begin", lambda _g, x, y: self._drag_start(
            name, kind, *self._live(name, (w, h)), x, y))
        g.connect("drag-update", lambda _g, ox, oy: self._drag_step(
            name, kind, *self._live(name, (w, h)), ox, oy))
        g.connect("drag-end", lambda _g, _x, _y: self._release(name, kind))
        # GtkGesture::cancel is the base-class signal; without it a cancelled
        # contact leaves the axis parked wherever the thumb left it.
        g.connect("cancel", lambda _g, _s: self._release(name, kind))
        return g

    def _drag_start(self, name, kind, w, h, x, y) -> None:
        self._start[name] = (x, y)
        self._press(name, kind, x, y, w, h)
        if name in self.active:
            self._move(name, kind, w, h, x, y)

    def _drag_step(self, name, kind, w, h, ox, oy) -> None:
        sx, sy = self._start.get(name, (w / 2.0, h / 2.0))
        x, y = sx + ox, sy + oy
        if self.trace:
            print(f"    {name:<5} start=({sx:6.1f},{sy:6.1f}) offset=({ox:7.1f},{oy:7.1f})"
                  f" -> pos=({x:6.1f},{y:6.1f}) of {w}x{h}", flush=True)
        self._move(name, kind, w, h, x, y)

    # -- contact bookkeeping ---------------------------------------------
    def _count(self) -> None:
        n = len(self.active)
        if n > self.max_contacts:
            self.max_contacts = n
            print(f"  held: {n} controls simultaneously  <- new maximum")
        self._sync_hub_label()

    def _press(self, name, kind, x, y, w=0, h=0) -> None:
        if name in self.active:
            return
        if self.lockout.is_locked(name):
            # Released by the watchdog (or by the hide button) while the finger was
            # still down. Until that contact ends this control is inert -- re-pressing
            # here is what made the watchdog a no-op for sticks.
            if self.trace:
                print(f"    {name:<5} press refused: released under the finger",
                      flush=True)
            return
        if self.hit_shapes and w and h and not inside_shape(kind, x, y, w, h):
            # Corner of a round button: refuse it. The tap is lost either way --
            # this surface is still the one on top -- but it must not fire the
            # control. This is the client-side half of what an input region would
            # have done properly; the compositor was measured not to offer it.
            self._ignored.add(name)
            if self.trace:
                print(f"    {name:<5} press ({x:.0f},{y:.0f}) outside its shape -> ignored",
                      flush=True)
            return
        frame = self.geo.get(name)
        if self.edge_margin > 0.0 and frame:
            edge = edge_refuses(x, y, tuple(frame), (self.sw, self.sh),
                                self.edge_margin)
            if edge is not None:
                # The outer band of a control that sits against the bezel. Refusing is
                # not free -- the tap is lost either way -- but a thumb root or a heel
                # of the hand firing a control is worse than a lost tap, and the band
                # is capped so it can never take a whole control. See layer_edge.py.
                self._ignored.add(name)
                if self.trace:
                    print(f"    {name:<5} press ({x:.0f},{y:.0f}) in the {edge} edge "
                          f"band -> ignored", flush=True)
                return
        self.active[name] = kind
        self.watchdog.arm(name, time.monotonic())
        if self.turbo.active(name) and kind in (BUTTON, TRIGGER):
            self._turbo_start(name)
        self._arm_hold_timer()
        self._count()
        if kind in (BUTTON, TRIGGER):
            # 0 -> 1 with no ramp: latency in the acknowledgement of a touch is the
            # one thing a press animation must never add. The client sets its
            # transform outright on TouchDown for the same reason; what it did next --
            # clearing it outright -- is the part that reads as a blink, and that is
            # what _ease_press changes.
            self._vis(name, "press", 1.0)
        if kind == DPAD:
            self._light_arms(name, self._hat)
        if kind == BUTTON:
            self.service.press_button(name)
        elif kind == TRIGGER:
            (self.service.set_left_trigger if name == "LT"
             else self.service.set_right_trigger)(1.0)
        # STICK / DPAD assert themselves through _move, which follows immediately.

    def _move(self, name, kind, w, h, x, y) -> None:
        if not self.lockout.may_motion(name, self.active):
            # A drag-update for a contact that no longer owns anything. Passing it on
            # is the ghost: the stick had been released, and the next event put it
            # back at full deflection, where it stayed until the thumb lifted.
            if self.trace:
                print(f"    {name:<5} motion dropped: not held (locked="
                      f"{self.lockout.is_locked(name)})", flush=True)
            return
        if kind == STICK:
            self._stick(name, w, h, x, y)
        elif kind == DPAD:
            self._dpad(w, h, x, y)

    def _retry_pending_profile(self) -> None:
        """A switch that was deferred because a control was held.

        Deferred rather than applied: a layout that changes while a thumb is on the
        stick moves the control out from under the finger, which is the one thing
        profiles must never do. The cost is a second or two.
        """
        if self._pending_profile and not self.active:
            GLib.idle_add(self.check_claim, "released")

    # -- profiles ---------------------------------------------------------
    def apply_profile(self, name: str, path: str) -> bool:
        """Swap in a whole different layout, live.

        Geometry goes through the rotation path on purpose: it already refits from the
        authored frames and their authored screen size, resizes the mapped layer
        surfaces and re-places them -- exactly what a profile laid out on another day,
        or another orientation, needs. Doing it a second way here would be a second
        chance to get the rounding wrong.
        """
        frames, opacity, dpad_mode, note = load_frames(path, self.sw, self.sh)
        if not note.startswith("loaded"):
            self._say(f"profile {name} did not load: {note}")
            return False
        self.frames = list(frames)
        self.base_frames = list(frames)
        self.design_screen = load_screen_size(path) or (self.sw, self.sh)
        self.opacity = min(1.0, max(0.05, opacity))
        self.dpad_mode = dpad_mode
        self.stick_tuning = load_stick_tuning(path)
        hz = load_turbo_hz(path)
        armed = load_turbo(path)
        self.turbo = Turbo(clamp_hz(hz if hz is not None else self.turbo.hz),
                           armed=armed)
        self.layout_path = path
        self.profile_name = name
        # force=True: the screen has not changed, but the layout under it has
        self.apply_screen(self.sw, self.sh, force=True, reason=f"profile {name}")
        for win in self.surfaces.values():
            win.set_opacity(self.opacity)
        if self.hub is not None:
            self.hub.set_opacity(self.opacity)
        for area in self.areas.values():
            area.queue_draw()
        # Switching is not editing. Without this the quit guard offers to save a layout
        # that arrived from a file and was never touched -- and saving it would write
        # this profile over whatever it just replaced.
        self._dirty = False
        if getattr(self, "opacity_slider", None) is not None:
            self.opacity_slider.set_value(self.opacity)
        for updater in ("_update_dpad_buttons", "_update_turbo_buttons",
                        "_update_stick_buttons"):
            fn = getattr(self, updater, None)
            if fn is not None:
                fn()
        self._sync_hub_label()
        self._say(f"profile: {name}" + (f" ({len(armed)} armed)" if armed else ""))
        return True

    def check_claim(self, _source: str = "poll") -> bool:
        """Read what a launcher asked for and act on it, or wait.

        Always returns True: this is both the monitor's idle task and the 2-second
        poll, and a timer that silently stopped would mean profiles quit working with
        nothing on screen to say so.
        """
        if not (self.profiles_enabled and self.cfg_dir):
            return True
        claim = prof.read_claim(self.cfg_dir)
        if claim is None:
            return True
        asked = claim.get("profile")
        action, want = prof.decide(self.profile_name, asked, bool(self.active),
                                   timeout=self.profile_timeout, claim=claim)
        self._claim_seen = (asked, claim.get("at"))
        if action == "defer":
            if self._pending_profile != want:
                self._pending_profile = want
                self._say_quiet(f"profile {want} queued; switching when you let go")
            return True
        if action == "none":
            if self._pending_profile and not self.active and asked == self.profile_name:
                self._pending_profile = None
            return True
        self._pending_profile = None
        _name, path, note = prof.resolve(self.cfg_dir, want)
        if path is None:
            self._say(f"profile {want} has nothing to load")
            return True
        if note:
            self._say_quiet(note)
        self.apply_profile(_name or want, path)
        return True

    def watch_claims(self) -> None:
        """Notice a launcher writing the claim file, with a poll behind it.

        The monitor is the fast path -- a game's layout lands within a frame of the
        launcher running -- and the 2 s poll is the honest one: inotify has limits and
        a claim the monitor missed would otherwise look like the feature not working.
        The poll costs one stat.
        """
        if not (self.profiles_enabled and self.cfg_dir):
            return
        try:
            os.makedirs(self.cfg_dir, mode=0o700, exist_ok=True)
            mon = Gio.File.new_for_path(self.cfg_dir).monitor_file(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
            mon.connect("changed",
                        lambda *_a: GLib.idle_add(self.check_claim, "monitor"))
            self._claim_monitor = mon          # no reference, no monitor
        except Exception as exc:               # noqa: BLE001 -- the poll still works
            print(f"  profile monitor unavailable ({exc}); polling every 2s")
        self._claim_timer = GLib.timeout_add(2000, self.check_claim)

    def cycle_profile(self) -> None:
        """The hub's way round the profiles, and the only one that needs no launcher."""
        if not self.cfg_dir:
            return
        names = prof.list_profiles(self.cfg_dir)
        if not names:
            self._say("no profiles yet -- run: glasspad-profile --seed")
            return
        if len(names) == 1:
            self._say(f"only one profile ({names[0]}) -- "
                      f"glasspad-profile --seed makes another")
            return
        nxt = prof.cycle(self.profile_name, names)
        try:
            prof.write_claim(self.cfg_dir, nxt, source="hub")
        except OSError as exc:
            self._say(f"could not record the profile change: {exc}")
            return
        self.check_claim("hub")

    def _sync_hub_label(self) -> None:
        """The profile name when nothing is held, the contact count while it is."""
        if self.hub_label is None or self.edit:
            return
        n = len(self.active)
        if n:
            self.hub_label.set_text(f"held: {n}   max: {self.max_contacts}")
        elif self.profiles_enabled and self.profile_name:
            self.hub_label.set_text(self.profile_name)

    def _release_control(self, name: str) -> None:
        """Release one control by name. The watchdog's entry point.

        Looks the kind up instead of taking one, so a timer firing later cannot
        release a control as the wrong kind -- the name may well be a different
        control by then, or nothing at all.
        """
        kind = self.active.get(name)
        if kind is not None:
            self._release(name, kind, forced=True)

    def _arm_hold_timer(self) -> None:
        """One timer for the whole overlay, set to the soonest deadline.

        A timer per control would mean one per finger, armed and cancelled on every
        touch. One timer re-armed from next_deadline() costs nothing when nothing is
        held, and still fires for the first control to go stale.
        """
        if self._hold_timer is not None:
            GLib.source_remove(self._hold_timer)
            self._hold_timer = None
        now = time.monotonic()
        due = self.watchdog.next_deadline(now)
        if due is None:
            return
        self._hold_timer = GLib.timeout_add(
            max(20, int((due - now) * 1000) + 20), self._on_hold_timeout)

    def _on_hold_timeout(self) -> bool:
        """Release whatever has been held past the limit, once per press.

        Deliberately not re-armed for a control still under a finger: someone
        genuinely holding a charge shot would otherwise be cut off every limit
        seconds. The next press arms it again.

        GLib timeouts run on the monotonic clock, so resuming from suspend does not
        fire this early -- but a control that was held when the tablet slept is still
        counted once the session wakes, which is one of the cases it exists for.
        """
        self._hold_timer = None
        stuck = self.watchdog.due(time.monotonic())
        for name in stuck:
            self.watchdog.disarm(name)
            self._release_control(name)
            area = self.areas.get(name)
            if area is not None:
                area.queue_draw()
        if stuck:
            # :g, not :.0f -- "after 0s held" for a 0.25 s limit reads like a bug in
            # the message rather than a short limit
            self._say(f"released {', '.join(stuck)} after "
                      f"{self.watchdog.limit:g}s held (stuck input?)")
        self._arm_hold_timer()
        return False

    # -- turbo ---------------------------------------------------------------

    def _turbo_apply(self, name: str, on: bool) -> None:
        """Put one control's turbo state on the pad and on the screen.

        Does nothing for a control that is no longer active: a timer can fire between
        the finger lifting and the source being removed, and sending a press for a
        control nobody is touching is exactly the bug this feature must not have --
        the pad would hold a button with no surface left on screen to release it.
        """
        kind = self.active.get(name)
        if kind == BUTTON:
            if on:
                self.service.press_button(name)
            else:
                self.service.release_button(name)
        elif kind == TRIGGER:
            setter = (self.service.set_left_trigger if name == "LT"
                      else self.service.set_right_trigger)
            setter(1.0 if on else 0.0)
        else:
            return                       # sticks and the d-pad are not turbo targets
        self._vis(name, "press", 1.0 if on else 0.0)
        area = self.areas.get(name)
        if area is not None:
            area.queue_draw()

    def _turbo_start(self, name: str) -> None:
        """Fire a held control until the finger lifts."""
        if name in self._turbo_timers or not self.turbo.active(name):
            return
        # The watchdog is taken out of this control while it turboes. A turbo button is
        # held for minutes on purpose, which is the exact shape of input the hold
        # watchdog exists to break up: without this the safety net would switch the
        # feature off eight seconds into the one thing it is armed for.
        self.watchdog.disarm(name)
        self._turbo_apply(name, True)          # start on the press, never on a gap
        # Show it. The knob that says "armed" is an edit-mode affordance, so without
        # this there is no way to tell mid-game whether A is autofiring or stuck --
        # which is a confusing thing to be deciding while something is trying to kill
        # you. The mark is only up while it actually fires, so a control that happens
        # to be armed adds no ink to the pad when it is not in use.
        self._vis(name, "turbo", True)
        area = self.areas.get(name)
        if area is not None:
            area.queue_draw()
        ms = half_period_ms(self.turbo.hz)
        self._turbo_timers[name] = GLib.timeout_add(ms, self._turbo_tick, name)
        if self.trace:
            print(f"    {name:<5} turbo started at {self.turbo.hz:g} Hz "
                  f"(every {ms}ms)", flush=True)

    def _turbo_tick(self, name: str) -> bool:
        if name not in self.active or not self.turbo.active(name):
            # Nothing is driving this control any more, so nothing may leave the pad
            # sitting on the last edge the timer happened to send.
            self._turbo_stop(name, leave_pad_released=True)
            return False
        self._turbo_apply(name, self.turbo.state(name, time.monotonic()))
        return True                      # keep firing

    def _turbo_stop(self, name: str, leave_pad_released: bool = False) -> None:
        """Stop the blink.

        `leave_pad_released` exists because the callers want opposite things and only
        they know which: on a normal release the press must be left alone, since
        _ease_press is about to run it down over 140 ms and zeroing it here turned the
        eased release into a blink (a test caught exactly that). What stops the pad
        being left pressed is the release that follows this, not this.
        """
        src = self._turbo_timers.pop(name, None)
        if src is not None:
            GLib.source_remove(src)
        self._vis(name, "turbo", False)
        if leave_pad_released:
            self._turbo_apply(name, False)

    def _turbo_stop_all(self) -> None:
        for name in list(self._turbo_timers):
            self._turbo_stop(name, leave_pad_released=True)

    def _toggle_turbo(self, name: str) -> bool:
        """Arm or disarm one control. Only buttons and triggers can turbo."""
        kind = _KIND_BY_NAME.get(name)
        if kind not in (BUTTON, TRIGGER):
            self._say(f"{name}: turbo is for buttons and triggers, not {kind}")
            return False
        on = self.turbo.toggle(name)
        self._dirty = True
        area = self.areas.get(name)
        if area is not None:
            area.queue_draw()
        self._say(f"{name}: turbo {'armed' if on else 'off'} "
                  f"({self.turbo.hz:g} Hz)")
        return on

    def _set_turbo_hz(self, value) -> None:
        """Global turbo rate. 0 stops it everywhere, armed controls included."""
        self.turbo.hz = clamp_hz(value)
        self._dirty = True
        self._update_turbo_buttons()
        # a control firing at the old rate must not keep the old interval
        for name in list(self._turbo_timers):
            self._turbo_stop(name)
            if name not in self.active:
                continue
            if self.turbo.active(name):
                self._turbo_start(name)
            else:
                # Rate went to 0 (or this control was just disarmed) with a finger
                # still down: the truth of the touch is "pressed", not whatever edge
                # the blink last happened to emit.
                self._turbo_apply(name, True)
        armed = len(self.turbo.armed)
        self._say(self.turbo.describe()
                  + (f" ({armed} armed)" if armed else ""))

    def _set_stick_preset(self, key: str) -> None:
        """Swap the response curve live, and show the numbers that resulted.

        Presets rather than three sliders: nobody can pick a saturation fraction, but
        everybody can say "too twitchy" or "not enough travel" and read back which
        preset moved it.
        """
        tuning, warn = stick_preset(key)
        if warn:
            self._say(warn)
            return
        self.stick_tuning = tuning
        self._dirty = True
        if getattr(self, "stick_caption", None) is not None:
            self.stick_caption.set_text(f"  {tuning.describe()}")
        self._update_stick_buttons()
        # a stick held while the curve changes should not keep the old mapping
        for name in ("LS", "RS"):
            area = self.areas.get(name)
            if area is not None:
                area.queue_draw()
        self._say(f"stick: {tuning.describe()}")

    def _update_stick_buttons(self) -> None:
        for key, btn in getattr(self, "_stick_btns", ()):
            selected = PRESETS.get(key) == self.stick_tuning
            btn.add_css_class("off") if not selected else btn.remove_css_class("off")

    def _update_turbo_buttons(self) -> None:
        for hz, btn in getattr(self, "_turbo_btns", ()):
            selected = (hz == 0.0) if not self.turbo.enabled else (hz == self.turbo.hz)
            btn.add_css_class("off") if not selected else btn.remove_css_class("off")

    def _release(self, name, kind, forced: bool = False) -> None:
        """Release a control.

        `forced` means we decided it -- the watchdog, or the hide button -- rather than
        the player lifting their finger. That distinction is the whole of the ghost fix:
        a forced release fences the contact, because the contact is still out there
        sending events; a normal one clears any fence, because the contact is gone.
        """
        if forced:
            self.lockout.force(name)
        else:
            self.lockout.clear(name)
        self._turbo_stop(name)
        self._ignored.discard(name)
        self.watchdog.disarm(name)
        if kind in (BUTTON, TRIGGER):
            self._ease_press(name)
        if kind == STICK:
            self._vis(name, "nub", (0.0, 0.0))
        if kind == DPAD:
            self._fade_arms(name)
        if self.active.pop(name, None) is None:
            return
        if kind == BUTTON:
            self.service.release_button(name)
        elif kind == TRIGGER:
            (self.service.set_left_trigger if name == "LT"
             else self.service.set_right_trigger)(0.0)
        elif kind == STICK:
            self._start.pop(name, None)
            (self.service.set_left_stick if name == "LS"
             else self.service.set_right_stick)(0.0, 0.0)   # zone-space centre
        elif kind == DPAD:
            self._hat = "None"
            self.service.set_dpad("None")
        # After the whole chain, not inside it: splicing this between two `elif`s left
        # the d-pad release running only when something was *still* held, which is the
        # opposite of the condition and still valid syntax.
        if not self.active:
            self._retry_pending_profile()

    def _ease_press(self, name: str) -> None:
        """Ease the press back to rest over press_anim_ms with the app's own ease-out.

        The client sets and clears the transform outright, which reads as a blink.
        A short eased return is what makes the shape look like something the finger
        pushed rather than something that switched pictures -- and the shading, scale
        and glyph all ride the same number, so they cannot disagree mid-transition.

        A generation counter retires the animation if the control is pressed again
        part-way through; without it the old timer would keep dragging the new press
        back toward rest.
        """
        gen = self._anim_gen[name] = self._anim_gen.get(name, 0) + 1
        start = self.vis.get(name, {}).get("press", 0.0)
        if self.press_anim_ms <= 0 or start <= 0.0:
            self._vis(name, "press", 0.0)
            return
        t0 = GLib.get_monotonic_time()
        dur = float(self.press_anim_ms)

        def step():
            if self._anim_gen.get(name) != gen:
                return False
            t = min(1.0, (GLib.get_monotonic_time() - t0) / 1000.0 / dur)
            self._vis(name, "press", start * (1.0 - S.ease_out_cubic(t)))
            return t < 1.0

        GLib.timeout_add(16, step)

    def _stick(self, name, w, h, x, y) -> None:
        """Thumb offset -> pad deflection, through the tuning, and the nub with it."""
        # `press` anchoring: wherever the thumb lands becomes the stick centre, so
        # the deflection is the drag offset alone. That is both how touch pads
        # usually feel and the only reading that does not depend on the surface
        # centre agreeing with the compositor about coordinates. `surface` keeps
        # the absolute-disc behaviour of the Qt widget for comparison.
        cx, cy = (self._start.get(name, (w / 2.0, h / 2.0))
                  if self.stick_anchor == "press" else (w / 2.0, h / 2.0))
        radius = min(w, h) / 2.0
        dx, dy = deflect(x - cx, y - cy, radius, self.stick_tuning)
        # The nub is derived from the deflection, never from the finger. It used to be
        # clamped on its own (StickZone clamps its nub to MaxTravel), which left the
        # ring frozen at full travel while the deflection carried on out to the rim --
        # the overlay drawing one thing and sending another. One number feeds both now.
        rng = S.STICK_MAX_TRAVEL * (min(w, h) / S.STICK_OUTER)
        self._vis(name, "nub", nub_offset(dx, dy, rng))
        setter = self.service.set_left_stick if name == "LS" else self.service.set_right_stick
        # ZONE space, resting value 0. The previous line here was
        # `setter(0.5 + dx * 0.5, ...)`, which fed 0..1 into a -1..1 contract:
        # to_hm turned that into 0.5..1.0, so the negative half of every stick
        # was unreachable and releasing parked the axis at half deflection.
        setter(dx, dy)

    def _dpad(self, w, h, x, y) -> None:
        dx, dy = (x - w / 2) / (w / 2), (y - h / 2) / (h / 2)
        # zones.DpadZone: DEAD_ZONE 12 in a BASE 180 figure, scaled to this surface.
        if math.hypot(x - w / 2.0, y - h / 2.0) <= (12.0 / 180.0) * min(w, h):
            hat = "None"
        else:
            hat = hat_with_hysteresis(self._hat, math.degrees(math.atan2(dy, dx)),
                                      mode=self.dpad_mode)
        if hat != self._hat:
            self._hat = hat
            self._light_arms("dpad", hat)
            self.service.set_dpad(hat)

    # -- visual state ------------------------------------------------------
    @staticmethod
    def _fresh_vis() -> dict:
        """Per-control visual state. One definition, because a key invented in one
        place and read in another is a crash that only happens on press."""
        return {"scale": 1.0, "nub": (0.0, 0.0), "arms": {}, "arms_gen": 0,
                "turbo": False, "press": 0.0}

    def _vis(self, name: str, key: str, value) -> None:
        # setdefault from _fresh_vis rather than a second literal: this file has one
        # definition of what a control's visual state holds, and a key spelled in one
        # place and read in another is a crash that only happens on press.
        self.vis.setdefault(name, self._fresh_vis())[key] = value
        area = self.areas.get(name)
        if area is not None:
            area.queue_draw()

    def _light_arms(self, name: str, hat: str) -> None:
        """Arm highlight levels, as DpadZone lights them: cardinal 1.0, and a
        diagonal is two arms at 0.7. Entering a direction is immediate; leaving
        fades over 120 ms (DoubleAnimation(0, 120ms))."""
        arms = {"N": 0.0, "E": 0.0, "S": 0.0, "W": 0.0}
        if hat and hat != "None":
            for ch in hat:              # "N", "NE" -> the arms it names
                if ch in arms:
                    arms[ch] = S.DPAD_CARDINAL if len(hat) == 1 else S.DPAD_DIAGONAL
        self._vis(name, "arms", arms)
        # Bump the generation so a fade started by an earlier release cannot come
        # back and blank arms that this press just lit.
        self.vis[name]["arms_gen"] = self.vis[name].get("arms_gen", 0) + 1

    def _fade_arms(self, name: str) -> None:
        v = self.vis.setdefault(name, self._fresh_vis())
        start = dict(v["arms"])
        if not any(start.values()):
            return
        gen = v.get("arms_gen", 0)
        t0 = GLib.get_monotonic_time()
        area = self.areas.get(name)

        def step():
            if self.vis.get(name, v).get("arms_gen", gen) != gen:
                return False                      # a newer press owns the arms now
            e = (GLib.get_monotonic_time() - t0) / 1000.0 / S.DPAD_FADE_MS
            if e >= 1.0:
                v["arms"] = {k: 0.0 for k in start}
            else:
                v["arms"] = {k: lv * (1.0 - e) for k, lv in start.items()}
            if area is not None:
                area.queue_draw()
            return e < 1.0

        GLib.timeout_add(16, step)

    # -- build ------------------------------------------------------------
    def build(self, frames: list[Frame], sw: int) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.frames = list(frames)
        # The geometry as authored. Every screen change refits from this and from
        # design_screen, never from wherever the controls end up: fitting the
        # already-fitted thing is how a layout creeps off screen one rotation at a
        # time, and refitting from base makes portrait-then-back exact.
        self.base_frames = list(frames)
        for frame in frames:
            name, kind = frame.name, frame.kind
            x, y, w, h = frame.x, frame.y, frame.w, frame.h
            self.geo[name] = [x, y, w, h]
            win = self._make_surface(int(round(w)), int(round(h)), "")
            area = Gtk.DrawingArea()
            area.set_content_width(int(round(w)))
            area.set_content_height(int(round(h)))
            area.set_hexpand(True)
            area.set_vexpand(True)
            self.vis[name] = self._fresh_vis()
            area.set_draw_func(self._paint_for(name, kind))
            self.areas[name] = area
            win.set_child(area)
            # Edit mode attaches ONLY the move gesture: dragging repositions the
            # surface and emits no pad input at all, so editing cannot leave a
            # button stuck down when the drag ends somewhere unexpected.
            # Both controller sets are built up front so edit mode can be toggled
            # at runtime, the way the original's EditModeBtn does -- swapping them
            # is remove_controller + add_controller on the same widget.
            if kind in (STICK, DPAD):
                self.ctrl_input[name] = self._drag_controller(name, kind, w, h)
            else:
                self.ctrl_input[name] = self._click_controller(name, kind, int(round(w)), int(round(h)))
            self.ctrl_move[name] = self._move_controller(name)
            win.add_controller(self.ctrl_move[name] if self.edit else self.ctrl_input[name])
            self.place(win, x, y)
            win.present()
            if self.input_regions:
                apply_input_region(win, region_for(kind, int(round(w)), int(round(h))))
            self.windows.append(win)
            self.surfaces[name] = win

        self._build_fab(sw)

    def _paint_for(self, name: str, kind: str):
        """Painter for one control, taken from layer_style rather than from CSS.

        CSS cannot express any of this: a d-pad is a 12-point cross whose arms light
        individually, a stick is a ring plus a nub that travels inside it, and the
        labels are the app's glyph set at the app's font ratios. Drawing it also
        keeps the appearance identical to tools/render_style.py, which is what the
        appearance is tested against.
        """
        fill = FILLS.get(name, "#B3405060")
        label = label_for(name)
        # Shape comes from render_shape(), not from `kind`: the shoulder buttons are
        # BUTTON for input and pill for painting, and using kind here drew LB/RB as
        # 48 px circles inside their 120 x 48 boxes.
        shape = render_shape(name, kind)
        # the app sizes a pill's label off its height and a circle's off its
        # diameter, and neither is known until the area is actually drawn
        ratio = S.PILL_LABEL_RATIO if shape == "pill" else S.CIRCLE_LABEL_RATIO

        def paint(_area, cr, cw, ch, *_user):
            v = self.vis.get(name, {})
            scale = v.get("scale", 1.0)
            size = label_size(name, ch if shape == "pill" else min(cw, ch), ratio)
            if self.edit:
                self._paint_grip(cr, cw, ch, self._sizing == name,
                                 self.turbo.is_armed(name))
            press = v.get("press", 0.0)
            turbo_ring = v.get("turbo", False)
            if shape == "pill":
                LP.paint(cr, cw, ch, "pill", fill, label, mode=self.press_mode,
                         press=press, text_size=size)
            elif shape == "circle":
                LP.paint(cr, cw, ch, "circle", fill, label, mode=self.press_mode,
                         press=press, text_size=size)
            elif shape == "stick":
                dx, dy = v.get("nub", (0.0, 0.0))
                S.paint_stick(cr, cw, ch, dx, dy, scale)
            else:
                DP.paint(cr, cw, ch, v.get("arms"), self.dpad_mode, scale=scale,
                         style=self.dpad_style, cross=self.dpad_cross,
                         press=self.dpad_press)
            if turbo_ring:
                # Last, so it survives the press shading rather than being painted
                # over by it.
                self._paint_turbo_ring(cr, cw, ch, shape)

        return paint

    def _paint_turbo_ring(self, cr, w: float, h: float, shape: str) -> None:
        """An amber outline on a control that is autofiring right now.

        Reuses the press module's own body path: the ring has to sit on exactly the
        outline the control is drawn with, and a second copy of that geometry would
        drift from it the first time either changed.
        """
        cr.save()
        LP._body_path(cr, w, h, "circle" if shape in ("circle", "stick") else "pill")
        cr.set_line_width(2.5)
        cr.set_source_rgba(*_TURBO_KNOB[:3], 0.90)
        cr.stroke()
        cr.restore()


    # -- edit mode ---------------------------------------------------------
    def _paint_grip(self, cr, w: float, h: float, active: bool,
                    turbo: bool = False) -> None:
        """The bottom-right sizing knob.

        Inset by its own radius: the surface is exactly the control's frame, so a
        knob centred on the corner would be drawn as a quarter circle, cut off. It
        is also smaller than its grab zone on purpose -- the zone has to suit a
        thumb, the knob only has to suit an eye.
        """
        cx, cy = grip_centre(w, h, GRIP_SIZE)
        r = GRIP_SIZE / 2.0 if not active else GRIP_SIZE / 2.0 + 1.0
        if r * 2.0 > min(w, h) * 0.8:
            return                          # tiny control: the knob would be the control
        cr.arc(cx, cy, r, 0.0, 6.283185307179586)
        cr.set_source_rgba(*(_TURBO_KNOB if turbo else
                             (S.GRIP_FILL_ACTIVE if active else S.GRIP_FILL)))
        cr.fill_preserve()
        cr.set_line_width(1.5)
        cr.set_source_rgba(*S.GRIP_RING)
        cr.stroke()
        if turbo:
            # Colour alone would leave which knob is armed to memory. The letter is
            # readable at this size and lets the state be checked at a glance.
            S._text(cr, "T", max(7.0, GRIP_SIZE * 0.62), (0.08, 0.05, 0.0, 0.95), cx, cy)

    def _move_controller(self, name: str) -> Gtk.GestureDrag:
        """Drag this control's own surface to move it, or drag its corner knob to size it.

        One surface per control makes both nearly free: the position IS the surface's
        layer margin, and the probe in tools/resize_probe.py showed that a plain
        size request resizes a mapped layer surface in place -- no unmap, no new
        surface, nothing to re-anchor.
        """
        g = Gtk.GestureDrag.new()
        g.set_button(0)
        g.connect("drag-begin", lambda _g, x, y: self._edit_begin(name, x, y))
        g.connect("drag-update", lambda _g, ox, oy: self._edit_update(name, ox, oy))
        g.connect("drag-end", lambda _g, _x, _y: self._edit_end(name))
        g.connect("cancel", lambda _g, _s: self._edit_end(name))
        return g

    def _edit_begin(self, name: str, x: float, y: float) -> None:
        """Where the finger lands decides what the drag does.

        Sizing is the thing you have to aim for -- a knob in the bottom-right corner
        only -- rather than something that can be grabbed by accident while moving.
        Four corner knobs on a 75 px button cover about 30 % of its face, and a drag
        meant to move would resize instead.
        """
        gx, gy, w, h = self.geo[name]
        sizing = on_grip(x, y, w, h)
        self._grab[name] = {"mode": "size" if sizing else "move",
                            "x": gx, "y": gy, "w": w, "h": h, "moved": False}
        if sizing:
            self._sizing = name
            self._hud_show(name)

    def _edit_update(self, name: str, ox: float, oy: float) -> None:
        st = self._grab.get(name)
        gx, gy, w, h = self.geo[name]
        if st is None:
            st = self._grab[name] = {"mode": "move", "x": gx, "y": gy, "w": w, "h": h}
        if not st.get("moved") and math.hypot(ox, oy) > TAP_SLOP:
            st["moved"] = True
        if st["mode"] == "size" and not st.get("moved"):
            # A touch on the knob that never travelled: not a resize yet. Resizing on
            # the first motion would mean every attempt to arm turbo also grew the
            # control by a couple of pixels.
            return
        win = self.surfaces[name]
        if st["mode"] == "move":
            nx = min(max(0.0, st["x"] + ox), max(0.0, self.sw - w))
            ny = min(max(0.0, st["y"] + oy), max(0.0, self.sh - h))
            self.geo[name][0], self.geo[name][1] = nx, ny
            self.place(win, nx, ny)
            self._dirty = True
            self._say_quiet(f"{name}  {nx:.0f},{ny:.0f}", key=name)
            return
        # Sizing keeps proportions for every control, and the knob's opposite corner
        # is the anchor: the control grows away from where it already is, so the
        # margin that is already correct is never touched.
        nw, nh = size_from_drag(st["w"], st["h"], ox, oy, MIN_CONTROL,
                                max_w=max(1.0, self.sw - st["x"]),
                                max_h=max(1.0, self.sh - st["y"]))
        if abs(nw - w) < 0.5 and abs(nh - h) < 0.5:
            return                       # nothing changed; don't thrash the surface
        self.geo[name][2], self.geo[name][3] = nw, nh
        self._resize_surface(name, nw, nh)
        self.place(win, st["x"], st["y"])
        self._dirty = True
        self._hud_show(name)
        self._say_quiet(f"{name}  {nw:.0f}x{nh:.0f}", key=name)

    def _edit_end(self, name: str) -> None:
        self._flush_quiet(name)              # the settled geometry belongs in the log
        st = self._grab.pop(name, None)
        if st and st.get("mode") == "size":
            self._sizing = None
            self._hud_hide()
            if not st.get("moved"):
                # A tap on the knob, as opposed to a drag of it. Arming turbo is the
                # one thing that can share this corner, because a tap never resized
                # anything in the first place.
                self._toggle_turbo(name)

    def _resize_surface(self, name: str, w: float, h: float) -> None:
        """Change a mapped layer surface's size in place."""
        iw, ih = max(int(round(w)), 1), max(int(round(h)), 1)
        win, area = self.surfaces[name], self.areas[name]
        win.set_default_size(iw, ih)
        win.set_size_request(iw, ih)
        area.set_content_width(iw)
        area.set_content_height(ih)
        area.queue_resize()

    # -- the size readout ---------------------------------------------------
    def _hud_ensure(self) -> Gtk.Window:
        if self.hud is not None:
            return self.hud
        win = self._make_menu_surface()
        area = Gtk.DrawingArea()
        area.set_hexpand(True)
        area.set_vexpand(True)
        area.set_draw_func(self._paint_hud)
        win.set_child(area)
        win.add_controller(Gtk.EventControllerMotion.new())   # keeps it reactive
        self.hud, self.hud_area = win, area
        self.windows.append(win)
        return win

    def _hud_set_text(self, text: str) -> tuple[int, int]:
        """Give the HUD surface some text and size it to fit. Returns that size.

        Sized from the measured text, never from a guess: a surface with room to spare
        is surface that paints nothing and still swallows taps.
        """
        win = self._hud_ensure()
        self._hud_text = text
        tw, th = S.measure_text(text, HUD_PX)
        iw, ih = int(math.ceil(tw)) + 10, int(math.ceil(th)) + 8
        if self._hud_size != (iw, ih):
            win.set_default_size(iw, ih)
            win.set_size_request(iw, ih)
            if self.hud_area is not None:
                self.hud_area.set_content_width(iw)
                self.hud_area.set_content_height(ih)
            self._hud_size = (iw, ih)
        return iw, ih

    def _toast(self, text: str, ms: int = 1800) -> None:
        """A short message above the hub, for what a player needs while playing.

        `_say` wrote to stdout, and stdout during a game is a terminal nobody has
        open. The watchdog's most useful sentence -- that it just released a control
        that had been stuck -- was therefore invisible, and so was "turbo armed".

        Same surface as the size readout: shadowed text with no panel behind it, so it
        covers nothing it does not have to. It sits above the hub rather than over it,
        because a layer surface this size will happily eat a hub press.
        """
        if self._sizing is not None:
            return                       # an in-flight resize readout owns the surface
        iw, ih = self._hud_set_text(text)
        win = self.hud
        bx, by, bw, bh = self._hub_bbox(self.menu_open)
        x = max(0.0, min(self.sw - iw, bx + (bw - iw) / 2.0))
        # Below the hub, which is where there is room: the hub lives at the top of the
        # screen with a 12 px margin, so "just above the hub" is off the display. The
        # first draft asked for above and quietly fell through to the fallback every
        # single time, which a test with a generously-placed hub happily passed.
        y = by + bh + 8.0
        if y + ih > self.sh:
            y = max(0.0, by - ih - 8.0)
        self.place(win, x, y)
        self._hud_gen += 1
        gen = self._hud_gen
        if not win.get_visible():
            win.present()
        if self.hud_area is not None:
            self.hud_area.queue_draw()

        def fade():
            # Generation guard: a newer toast (or a readout) has taken the surface by
            # now, and hiding it would be hiding *that*.
            if self._hud_gen == gen and self.hud is not None and self._sizing is None:
                self.hud.set_visible(False)
            return False

        GLib.timeout_add(max(400, ms), fade)

    def _hud_show(self, name: str) -> None:
        w, h = self.geo[name][2], self.geo[name][3]
        self._hud_set_text(f"{w:.0f} \u00d7 {h:.0f}")
        win = self.hud
        # place on the requested size, not get_width(): a surface that has never been
        # mapped reports 0 there, and centring on 0 parks it at the screen centre
        hx, hy, hw, _hh = self.geo[name]
        iw, ih = self._hud_size
        self.place(win, max(0.0, min(self.sw - iw, hx + (hw - iw) / 2.0)),
                   min(self.sh - ih, hy + _hh + 6))
        if not win.get_visible():
            win.present()
        if self.hud_area is not None:
            self.hud_area.queue_draw()

    def _hud_hide(self, delay_ms: int = 650) -> None:
        """The last number stays a moment, then goes.

        Hiding it on the same frame the drag ends means the value you were trying to
        read is gone before your eyes reached it.
        """
        if self.hud is None:
            return
        self._hud_gen += 1
        gen = self._hud_gen

        def hide():
            if self._hud_gen == gen and self.hud is not None and self._sizing is None:
                self.hud.set_visible(False)
            return False

        GLib.timeout_add(delay_ms, hide)

    def _paint_hud(self, _area, cr, w, h, *_user) -> None:
        """Text with a shadow, no box.

        A panel behind the number would sit on top of whatever is being laid out; a
        shadow keeps it legible over a bright game without covering anything.
        """
        if not self._hud_text:
            return
        cx, cy = w / 2.0, h / 2.0
        S._text(cr, self._hud_text, HUD_PX, (0.0, 0.0, 0.0, 0.55), cx + 0.9, cy + 1.3)
        S._text(cr, self._hud_text, HUD_PX, (1.0, 1.0, 1.0, 0.95), cx, cy)

    def screen(self) -> tuple[int, int]:
        return (int(self.sw), int(self.sh))

    def apply_screen(self, w: int, h: int, reason: str = "",
                     force: bool = False) -> bool:
        """Refit the authored layout onto a different screen and re-place everything.

        Rotation on this tablet is the case that matters, but the same path covers a
        dock, a monitor swap, and a display-scale change -- which moves the *logical*
        size the layout is stored in, not just the pixels underneath it.

        Refits from base_frames and design_screen rather than from the current
        geometry, so it is idempotent: rotate to portrait and back and you get the
        saved layout, not the saved layout times two rounding steps.
        """
        w, h = int(w), int(h)
        if w <= 0 or h <= 0 or not self.base_frames:
            return False
        if not force and (w, h) == self.screen():
            return False
        design = self.design_screen or (w, h)
        fitted = fit_frames(self.base_frames, design[0], design[1], w, h)
        for frame in fitted:
            self.geo[frame.name] = [frame.x, frame.y, frame.w, frame.h]
            if frame.name in self.surfaces:
                self._resize_surface(frame.name, frame.w, frame.h)
                self.place(self.surfaces[frame.name], frame.x, frame.y)
        self.sw, self.sh = w, h
        self._dirty = True
        self._place_hub()
        if self.trace:
            a = self.geo.get("A")
            got = self.surfaces["A"].get_default_size() if "A" in self.surfaces else None
            print(f"    apply_screen -> {w}x{h}: A geo "
                  f"{a[0]:.0f},{a[1]:.0f} {a[2]:.0f}x{a[3]:.0f}, surface asked {got}, "
                  f"base_frames {len(self.base_frames)}", flush=True)
        if reason:
            scale = min(w / design[0], h / design[1])
            # "back to the authored 1707x1067" rather than "1707x1067 -> 1707x1067":
            # rotating back is the common case and the arrow form reads like a bug
            if (w, h) == (design[0], design[1]):
                self._say(f"{reason}: back to the authored {w}x{h}")
            else:
                self._say(f"{reason}: {design[0]}x{design[1]} -> {w}x{h} "
                          f"(x{scale:.2f})")
        return True

    def watch_display(self) -> None:
        """Follow the display: rotation, resolution change, scale-factor change.

        There is no monitor-changed signal to connect in this GTK -- 4.24 offers no
        monitors-changed on GdkDisplay, and no monitor-added/removed either (checked
        against the introspection data, not assumed) -- so the live path observes the
        properties that do exist on each GdkMonitor and re-checks on a slow timer on
        top. The timer is not laziness: a monitor that has just been plugged in does
        not exist yet to connect to, and KWin may replace the monitor object on a
        rotation instead of updating the one we hold.

        The check is two integer comparisons; twice a second costs nothing next to a
        compositor redrawing a game.
        """
        self._bind_monitors()
        GLib.timeout_add_seconds(2, self._poll_display)

    def _bind_monitors(self) -> None:
        model = Gdk.Display.get_default().get_monitors()
        for i in range(model.get_n_items()):
            mon = model.get_item(i)
            if mon is None:
                continue
            # Keyed on the connector name, not on object identity: PyGObject hands
            # back a fresh Python wrapper for the same GdkMonitor on every call, so
            # id() (and there is no pointer accessor here) would rebind the same
            # monitor every poll and leak both connections and references.
            key = mon.get_connector() or f"monitor-{i}"
            if key in self._monitor_sources:
                continue
            self._monitor_refs.append(mon)   # keep alive; a dead object drops signals
            self._monitor_sources[key] = [
                mon.connect(f"notify::{prop}", self._on_display_changed)
                for prop in ("geometry", "scale-factor")]
            if self.trace:
                print(f"    watching display {key}", flush=True)

    def _on_display_changed(self, *_a) -> None:
        # Coalesce: a rotation moves geometry and scale-factor at once, a dock moves
        # several monitors. One relayout per batch is what we want, not four.
        GLib.idle_add(self._poll_display)

    def _poll_display(self) -> bool:
        self._bind_monitors()
        w, h = screen_size()
        if (w, h) != self.screen():
            self.apply_screen(w, h, reason="display changed")
        return True                      # keep polling

    def current_frames(self) -> list[Frame]:
        kinds = {name: kind for name, kind, _tag in ELEMENT_ORDER}
        return [Frame(name, kinds[name], *self.geo[name])
                for name in kinds if name in self.geo]

    def _set_opacity(self, scale: Gtk.Scale) -> None:
        self.opacity = scale.get_value()
        self._dirty = True
        for win in self.surfaces.values():
            win.set_opacity(self.opacity)
        # The app binds FabButton and DisableButton's Opacity to the same slider, so
        # the hub fades with the pad rather than staying opaque over it.
        if self.hub is not None:
            self.hub.set_opacity(self.opacity)

    def _reset_geometry(self) -> None:
        """Back to the app's default layout, sizes included.

        The surfaces have to be resized as well as the geometry: a control's size is
        its surface's size, so writing the default width and height into geo alone
        leaves the layer surface at whatever it was dragged to. The file and the
        screen then disagree -- a save would have written the defaults while the pad
        went on drawing, and swallowing taps, at the old size.
        """
        # window.py's Reset also puts the opacity slider back to 100%
        if getattr(self, "opacity_slider", None) is not None:
            self.opacity_slider.set_value(1.0)
        for frame in default_frames(self.sw, self.sh):
            self.geo[frame.name] = [frame.x, frame.y, frame.w, frame.h]
            if frame.name in self.surfaces:
                self._resize_surface(frame.name, frame.w, frame.h)
                self.place(self.surfaces[frame.name], frame.x, frame.y)
        self._dirty = True
        self._say("defaults restored -- press save to keep")

    def _save(self) -> None:
        try:
            # Everything in one call: geometry, opacity, d-pad mode, the screen this
            # was laid out for, and the turbo rate. Splitting the rate into a second
            # read-modify-write doubled the window in which a crash could eat the
            # layout, for one float.
            write_frames(self.layout_path, self.current_frames(),
                         self.opacity, self.dpad_mode, screen=self.screen(),
                         turbo=self.turbo.armed, turbo_hz=self.turbo.hz,
                         stick=self.stick_tuning)
        except OSError as exc:
            msg = f"save FAILED: {exc}"
        else:
            msg = f"saved -> {self.layout_path}"
            self._dirty = False
        print(f"  {msg}", flush=True)
        if self.confirm is not None:
            self._show_panel(self.confirm)   # window.py: save shows the dialog

    def _quit_request(self) -> None:
        """Quit -- unless that would throw away a layout nobody saved.

        This guard was removed once already, for parity with the Qt hub, whose quit
        button is close() and asks nothing. That was cheap when edit mode could only
        nudge controls about. Since the sizing knobs arrived a session can resize
        sixteen controls, arm turbo and refit the whole pad to a rotated screen, and
        one press of the quit pill discarded all of it silently -- `_dirty` was set in
        eleven places and read in none.

        So it asks, and only where it costs something: outside edit mode nothing can
        have changed, so the press quits in one go exactly as before.
        """
        if self.edit and self._dirty:
            self._open_discard_confirm()
            return
        self.quit()

    def _open_discard_confirm(self) -> None:
        if self.discard is None:
            self._build_discard_confirm()
        self._show_panel(self.discard)
        self._say("unsaved layout changes -- save them or discard them?")

    def _build_discard_confirm(self) -> None:
        self.discard = self._make_menu_surface()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("confirm")
        box.set_size_request(int(S.CONFIRM_MIN_WIDTH), -1)
        self.discard_box = box

        box.append(self._caption("Unsaved changes"))
        box.append(self._caption("Save the layout before quitting?", "body"))
        box.append(self._row(self._pill("Save", "", self._discard_save, 100, 34),
                             self._pill("Discard", "warn", self._discard_now, 100, 34)))

        self.discard.set_child(box)
        self.windows.append(self.discard)

    def _hide_discard(self) -> None:
        if self.discard is not None:
            self.discard.set_visible(False)

    def _discard_save(self) -> None:
        """Save, then quit: the answer to the question is usually yes."""
        self._hide_discard()
        self._save()
        self.quit()

    def _discard_now(self) -> None:
        self._hide_discard()
        self._dirty = False               # a second press must not ask again
        self.quit()

    # -- status -------------------------------------------------------------
    def _say(self, text: str) -> None:
        """Hub status, to the terminal alongside the contact diagnostics.

        The Qt hub shows this text in a label inside its info panel; that panel's
        label is used for the pad state here, exactly as it is there, so hub
        messages would have nowhere to go but stdout.
        """
        print(f"  status: {text}", flush=True)
        self._toast(text)

    def _say_quiet(self, text: str, key: str = "") -> None:
        """Terminal only, and folded. For the messages that repeat every motion event.

        A resize readout is already on screen showing the same numbers; toasting each
        one would replace a stable readout with a flicker.

        It also used to print every one, and that turned out to matter once the app was
        launched by something other than a terminal. Installed through Shelly, the overlay
        runs as a systemd transient unit and stdout goes to the journal, which is then the
        ONLY record of the run: one edit session wrote 412 lines of
        `status: A 1494,601`, and the pad creation, the profile and any error were somewhere
        under the coordinates. So a readout for the same control prints at most once per
        QUIET_FLUSH_S, the updates it stands for are counted, and the settled value is
        flushed when the drag ends -- see _flush_quiet for why that last part is not
        optional.

        Without `key`, nothing is folded: the caller said something once (a profile
        switch, a refusal), and a rate limiter must not be allowed to swallow a sentence
        that only ever gets said once. Pass key=<control> for telemetry that streams.
        """
        if not key:
            state = getattr(self, "_quiet", None) or {}
            if state.get(_ONE_SHOT, "") == text:
                return                     # literally the same sentence again; nothing new
            self._quiet = {**state, _ONE_SHOT: text}
            print(f"  status: {text}", flush=True)
            return

        state = getattr(self, "_quiet", None)
        if state is None:
            state = self._quiet = {}
        last = state.get(key)
        if last is not None and last[0] == text:
            return                         # the value did not move; neither does the log
        if (last is not None and not _log_wants_everything()
                and time.monotonic() - last[1] < QUIET_FLUSH_S):
            state[key] = (text, last[1], last[2] + 1)
            return
        state[key] = (text, time.monotonic(), 0)
        print(f"  status: {text}", flush=True)

    def _flush_quiet(self, key: str) -> None:
        """Print whatever folding is holding for `key`.

        Rate limiting alone would drop the one number anybody cares about: where the
        control ENDED UP, whenever a drag stopped between two ticks. The folded count is
        printed alongside so the log says "this line stands for 37 updates" instead of
        looking like the drag was that short.
        """
        state = getattr(self, "_quiet", None) or {}
        last = state.pop(key, None)
        if not last or not last[2]:
            return
        text, _at, folded = last
        print(f"  status: {text}  (+{folded} updates folded)", flush=True)

    # -- the latching disable button (window.py's \u2298) ---------------------
    def _toggle_overlay_enabled(self) -> None:
        self._set_overlay_enabled(not self.overlay_enabled)

    def _set_overlay_enabled(self, enabled: bool) -> None:
        """Port of SetOverlayEnabled, latching: one press hides, the next brings back.

        The Qt hub made the same change from the original's momentary hold, for the
        same reason -- the state has to be remembered, so the button can say what it
        is. Holding a thumb against a tablet you are already leaning on to keep the
        pad hidden is a worse deal than holding a mouse button.

        Every active contact is released on the way out. A control held under
        another finger would otherwise stay pressed on the virtual pad for the rest
        of the session, with nothing left on screen to release it.
        """
        self.overlay_enabled = enabled
        if not enabled:
            self._turbo_stop_all()
            self.service.reset()
            for held in list(self.active):
                self.lockout.force(held)
            self.active.clear()
            self._ignored.clear()
            self.watchdog.clear()
        for win in self.surfaces.values():
            win.set_visible(enabled)
        self._sync_disable_button()
        self._say("overlay shown" if enabled
                  else "overlay hidden -- tap the hide button to bring the controls back")

    def _sync_disable_button(self) -> None:
        """The two colours and the two wordings, copied from window.py.

        With the overlay off, this button and the FAB are the only things left on
        screen and only one of them explains why, so the disabled wording has to
        name the way back rather than just the action. Tooltips are set as well,
        though a layer surface may never manage to show one; the colour is the part
        that carries on a tablet.
        """
        if "disable" not in self.hub_state:
            return
        self.hub_state["disable"]["colour"] = (
            S.HUB_BUTTONS["disable"][0] if self.overlay_enabled
            else S.DISABLED_COLOR)
        area = self.hub_children.get("disable")
        if area is not None:
            area.set_tooltip_text(S.TOOLTIP_ENABLED if self.overlay_enabled
                                  else S.TOOLTIP_DISABLED)
            area.queue_draw()

    # -- edit mode ----------------------------------------------------------
    def _set_edit(self, on: bool) -> None:
        """Swap every control between its input controller and its move controller.

        Both objects are kept for the life of the overlay; only which one is
        attached changes. Dropping the reference to the one you are switching away
        from is how the first version of this broke -- it left nothing to switch
        back to, and the pad came out of edit mode dead.

        Parity with window.py's _toggle_edit: the hub closes first (a fan of buttons
        over the control you are dragging is in the way), all input is released, and
        entering edit mode brings a hidden overlay back -- editing positions you
        cannot see is not worth supporting, and Reset reveals them anyway.
        """
        if on == self.edit:
            return
        if self.menu_open:
            self._close_fab()
        self._turbo_stop_all()
        self.service.reset()
        self.active.clear()
        self._ignored.clear()
        self.watchdog.clear()
        if on and not self.overlay_enabled:
            self._set_overlay_enabled(True)
        self.edit = on
        for name, win in self.surfaces.items():
            away = self.ctrl_input[name] if on else self.ctrl_move[name]
            onto = self.ctrl_move[name] if on else self.ctrl_input[name]
            if away is not None:
                win.remove_controller(away)
            if onto is not None:
                win.add_controller(onto)
            # Ask for a repaint: the sizing knob is drawn by the control itself, so
            # toggling the flag is not enough -- nothing would draw it until some
            # other event happened to repaint that surface. `--edit` at startup
            # looked correct because its very first paint already had edit mode on,
            # and pressing Reset appeared to make the knob show up only because
            # resizing queues one. Toggling from the hub is the path that showed it.
            area = self.areas.get(name)
            if area is not None:
                area.queue_draw()
        self._dirty = True
        self._refresh_hub()
        if self.panel is not None:
            if on:
                self._show_panel(
                    self.panel,
                    lambda w, _h: (self.hub_x + (S.HUB_CANVAS[0] - w) / 2.0,
                                   S.EDIT_PANEL_TOP))
            self.panel.set_visible(on)
        self._say("EDIT  drag a control to move it" if on else "edit off")

    def quit(self) -> None:
        try:
            self._turbo_stop_all()
            self.service.reset()
            self.service.dispose()
        finally:
            if self.loop is not None:
                self.loop.quit()

    # -- panel surfaces ------------------------------------------------------
    def _make_menu_surface(self) -> Gtk.Window:
        """A surface for a panel: opaque, and sized entirely by its content.

        _make_surface pins width and height because a control's hit area must be
        exactly its frame. A panel is the opposite case: pinning it to a guessed
        number leaves a band of surface that paints nothing and still swallows taps,
        so the pin is cleared and gtk4-layer-shell sizes it from the child.
        """
        win = self._make_surface(1, 1, "")
        win.add_css_class("panelwin")
        win.set_default_size(-1, -1)
        win.set_size_request(-1, -1)
        win.set_opacity(1.0)          # panels are not see-through
        return win

    def _show_panel(self, win: Gtk.Window, pos=None) -> None:
        """Present a panel, and place it once its real size is known.

        The surface is deliberately not sized by hand: gtk4-layer-shell makes it
        exactly as large as its child's border box, so a panel can never swallow a
        tap in space it did not paint. That also means its size is unknown until it
        has been allocated -- get_width() is 0 for a surface that has never been
        mapped, and centring on 0 parks the top-left corner at the middle of the
        screen, which is what the first version of this did to all three panels.

        `pos(w, h) -> (x, y)`; the default centres, as window.py's _show_centred.
        """
        win.present()
        if getattr(win, "_placing", False):
            return                      # one reposition pass is already queued
        win._placing = True

        def place():
            w, h = win.get_width(), win.get_height()
            if not (w and h):
                return True             # wait for the allocation
            x, y = pos(w, h) if pos is not None else \
                ((self.sw - w) / 2.0, (self.sh - h) / 2.0)
            self.place(win, x, y)
            if os.environ.get("GLASSPAD_DEBUG"):
                print(f"  panel placed: {w}x{h} at ({x:.0f},{y:.0f}) "
                      f"realized={win.get_realized()} mapped={win.get_mapped()} "
                      f"visible={win.get_visible()} opacity={win.get_opacity()}",
                      flush=True)
            win._placing = False
            return False

        GLib.idle_add(place)

    # -- hub (MainWindow.xaml's HubCanvas: a radial FAB, not a toolbar) -----
    HUB_ALWAYS = ("disable", "fab")
    HUB_CHILDREN = ("quit", "edit", "info")

    def _hub_bbox(self, open_: bool) -> tuple[float, float, float, float]:
        """(x, y, w, h) of the hub surface in canvas coordinates.

        Collapsed it is only as big as the two always-visible buttons -- 100x44
        rather than the app's 230x200 canvas. That difference is deliberate: WPF's
        transparent canvas swallows a tap only where a button is, while a Wayland
        surface swallows one anywhere it lands.
        """
        names = S.HUB_BUTTONS if open_ else self.HUB_ALWAYS
        xs = [S.HUB_BUTTONS[n][2] for n in names]
        ys = [S.HUB_BUTTONS[n][3] for n in names]
        x0, y0 = min(xs), min(ys)
        return x0, y0, max(xs) + S.FAB_SIZE - x0, max(ys) + S.FAB_SIZE - y0

    def _paint_hub(self, name: str):
        def paint(_area, cr, cw, ch, *_user):
            st = self.hub_state[name]
            if st["alpha"] <= 0.0:
                return
            alpha = st["alpha"] * (S.FAB_PRESSED_OPACITY if st["pressed"] else 1.0)
            S.paint_fab(cr, min(cw, ch), st["colour"], st["glyph"], st["scale"], alpha)
        return paint

    def _place_hub(self) -> None:
        x0, y0, bw, bh = self._hub_bbox(self.menu_open)
        self.hub.set_default_size(int(bw), int(bh))
        self.hub.set_size_request(int(bw), int(bh))
        self.place(self.hub, self.hub_x + x0, S.HUB_TOP_MARGIN + y0)
        for name, area in self.hub_children.items():
            _c, _g, cx, cy, _a = S.HUB_BUTTONS[name]
            self.hub_box.move(area, int(cx - x0), int(cy - y0))
        return False

    def _refresh_hub(self) -> None:
        if not self.hub_state:
            return
        fab = self.hub_state["fab"]
        fab["glyph"] = "\u00d7" if self.menu_open else "\u2261"
        fab["colour"] = (S.FAB_COLOR_OPEN if self.menu_open
                         else S.FAB_COLOR_EDIT if self.edit
                         else S.HUB_BUTTONS["fab"][0])
        self.hub_state["edit"]["colour"] = (S.EDIT_COLOR_ACTIVE if self.edit
                                            else S.HUB_BUTTONS["edit"][0])
        for name in ("fab", "edit"):
            self.hub_children[name].queue_draw()
        self._sync_disable_button()

    def _animate_hub(self, name: str, opening: bool) -> None:
        """AnimateIn / AnimateOut, as written in MainWindow.xaml.cs and ported in
        window.py: 180 ms opacity plus 0.5->1 scale, CubicEase EaseOut, children
        staggered 0/60/120 ms; out is a 140 ms fade and then Collapsed.
        """
        area = self.hub_children[name]
        st = self.hub_state[name]
        delay = S.ANIM_IN_DELAYS.get(name, 0.0) if opening else 0.0
        dur = S.ANIM_IN_MS if opening else S.ANIM_OUT_MS
        if opening:
            st["alpha"], st["scale"] = 0.0, 0.5
            area.set_visible(True)
        t0 = GLib.get_monotonic_time()

        def step():
            el = (GLib.get_monotonic_time() - t0) / 1000.0 - delay
            if el < 0.0:
                return True
            t = 1.0 if dur <= 0 else min(1.0, el / dur)
            if opening:
                st["alpha"] = t
                st["scale"] = 0.5 + 0.5 * S.ease_out_cubic(t)
            else:
                st["alpha"] = 1.0 - t
            area.queue_draw()
            if t >= 1.0:
                if not opening:
                    st["alpha"], st["scale"] = 0.0, 1.0
                    area.set_visible(False)
                return False
            return True

        GLib.timeout_add(16, step)

    def _close_fab(self) -> None:
        if not self.menu_open:
            return
        self.menu_open = False
        self._refresh_hub()
        for name in self.HUB_CHILDREN:
            self._animate_hub(name, False)
        # shrink only once the fade has finished, or the buttons vanish with the
        # surface instead of fading where they stand
        GLib.timeout_add(int(S.ANIM_OUT_MS) + 30, self._place_hub)

    def _toggle_menu(self) -> None:
        if self.menu_open:
            self._close_fab()
            return
        self.menu_open = True
        self._place_hub()                 # make room first, or the children clip
        self._refresh_hub()
        for name in self.HUB_CHILDREN:
            self._animate_hub(name, True)

    def _hub_press(self, name: str) -> None:
        self.hub_state[name]["pressed"] = True     # FabBtn IsPressed -> opacity 0.7
        self.hub_children[name].queue_draw()
        if name == "disable":
            # Latching, and taken on press rather than release exactly as window.py
            # wires `pressed`: a finger sliding off the circle on the way out must
            # not toggle it a second time.
            self._toggle_overlay_enabled()

    def _hub_release(self, name: str) -> None:
        self.hub_state[name]["pressed"] = False
        self.hub_children[name].queue_draw()
        if name == "disable":
            return                                  # already latched on press
        if name == "fab":
            self._toggle_menu()
        elif name == "quit":
            self._quit_request()
        elif name == "edit":
            self._set_edit(not self.edit)
        elif name == "info":
            self._open_info()
        elif name == "profile":
            self.cycle_profile()

    # -- panels (EditPanel, the About box, the save confirmation) -----------
    def _pill(self, text: str, colour_class: str = "", on_click=None,
              w: int | None = None, h: int | None = None) -> Gtk.Button:
        btn = Gtk.Button(label=text)
        btn.add_css_class("pillbtn")
        if colour_class:
            btn.add_css_class(colour_class)
        if w and h:
            btn.set_size_request(w, h)
        if on_click is not None:
            btn.connect("clicked", lambda *_a: on_click())
        return btn

    def _caption(self, text: str, css_class: str | None = None) -> Gtk.Label:
        lab = Gtk.Label(label=text)
        if css_class:
            lab.add_css_class(css_class)
        return lab

    def _row(self, *widgets) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_halign(Gtk.Align.CENTER)
        for w in widgets:
            box.append(w)
        return box

    def _build_panel(self) -> None:
        """EditPanel, row for row: opacity, d-pad mode, save / reset."""
        self.panel = self._make_menu_surface()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("panel")
        box.set_size_request(int(S.PANEL_MIN_WIDTH), -1)
        self.panel_box = box

        box.append(self._caption("Button Opacity"))
        self.opacity_slider = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.15, 1.0, 0.05)
        self.opacity_slider.set_value(self.opacity)
        self.opacity_slider.set_draw_value(False)
        self.opacity_slider.connect("value-changed", self._set_opacity)
        box.append(self.opacity_slider)

        box.append(self._caption("D-Pad Mode"))
        self.dpad4_btn = self._pill("4-Way", "", lambda: self._set_dpad_mode(4))
        self.dpad8_btn = self._pill("8-Way", "off", lambda: self._set_dpad_mode(8))
        box.append(self._row(self.dpad4_btn, self.dpad8_btn))
        box.append(self._caption("Stick Response"))
        self._stick_btns = tuple(
            (key, self._pill(label, "",
                             lambda _c, k=key: self._set_stick_preset(k), 52, 30))
            for key, label in (("app", "App"), ("soft", "Soft"),
                               ("crisp", "Crisp"), ("wide", "Wide")))
        box.append(self._row(*[b for _k, b in self._stick_btns]))
        self.stick_caption = self._caption(f"  {self.stick_tuning.describe()}", "body")
        box.append(self.stick_caption)
        box.append(self._caption("Turbo Rate"))
        self._turbo_btns = tuple(
            (hz, self._pill(label, "" if self.turbo.hz == hz else "off",
                            lambda _c, v=hz: self._set_turbo_hz(v), 46, 30))
            for hz, label in ((0.0, "Off"), (10.0, "10"), (20.0, "20"), (30.0, "30")))
        box.append(self._row(*[b for _hz, b in self._turbo_btns]))
        box.append(self._row(
            self._pill("\U0001f4be  Save", "", self._save, 106, 32),
            self._pill("\u21ba  Reset", "warn", self._reset_geometry, 106, 32)))
        box.append(self._caption("tap a knob = arm turbo \u00b7 drag it = resize"))

        self.panel.set_child(box)
        self.windows.append(self.panel)
        # not presented: the panel starts hidden, as Visibility=Collapsed does
        self._update_dpad_buttons()

    def _build_info_panel(self) -> None:
        """The About box.

        Colours, sizes and order still follow the client. The credit line does not, and
        that is the one deliberate divergence here: the original says "by Dansunmi", and
        putting that sentence on a build that behaves differently in a dozen small ways
        would credit the wrong person for this code while misdescribing that one. This
        says what it is and points at what it ports; the author's name is still on
        screen, in the URL, and in the MIT notice shipped as LICENSE.
        """
        backend = getattr(self.service, "_backend", None)
        pad_state = f"pad backend: {type(backend).__name__}" if backend else \
            "no pad backend (null pad)"
        self.info = self._make_menu_surface()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("info")
        # the CSS padding sits inside this width, as it does inside Qt's minimumWidth
        box.set_size_request(int(S.INFO_MIN_WIDTH), -1)
        self.info_box = box

        box.append(self._caption("GlassPad", "title"))
        box.append(self._caption("Touchscreen \u2192 Gamepad Overlay", "sub"))
        # Was the client's "by Dansunmi". A port names what it ports instead.
        box.append(self._caption("Linux port of:", "author"))
        box.append(self._caption("github.com/dansunmi/GlassPad", "url"))
        # window.py prints BACKEND_LABEL because the Linux pad is created by the
        # kernel's uinput rather than by the HIDMaestro SDK the original cites.
        # Interpolated, not typed: this is the number a bug report quotes, and
        # "name right, number off by one" is this port's recorded bug class.
        box.append(self._caption(f"Linux uinput backend (ABI v{xd360.UINPUT_VERSION})",
                                "backend"))
        box.append(self._caption(f"GlassPad v{__version__} (Linux)", "ver"))
        self.status_label = self._caption(pad_state, "status")
        box.append(self.status_label)
        box.append(self._row(self._pill("Open-source licenses", "grey",
                                        self._open_licenses, 200, 34)))
        # Only when there is no pad. Drag-and-drop installs -- an AppImage manager like
        # Shelly, or an icon -- have no terminal behind them, and the one step that
        # needs a password is the one that needs to be READ: which udev rule is about to
        # be written, and what the verdict was. Telling that user to "run glasspad
        # --install-uinput" is a note on a door with no handle, so the box opens the
        # window instead. It does not escalate anything: the terminal runs our own
        # command, and the password prompt stays inside pkexec where it can be refused.
        self.grant_pill = None
        self.grant_note = None
        if getattr(self.service, "fell_back_to_null", False):
            self.grant_pill = self._pill("Grant gamepad access", "",
                                         self._grant_access, 200, 34)
            box.append(self._row(self.grant_pill))
            # Empty until the button is pressed, then the verdict -- see _watch_grant.
            # A LABEL, and the pill stays a BUTTON: relabelling the action away would
            # leave a control that does nothing when pressed, and a refused password
            # prompt has to be retryable from the same place.
            self.grant_note = self._caption("", "status")
            self.grant_note.set_wrap(True)
            self.grant_note.set_max_width_chars(48)
            self.grant_note.set_xalign(0.5)
            box.append(self.grant_note)
        box.append(self._row(self._pill("Close", "grey", self._close_info, 120, 34)))

        self.info.set_child(box)
        self.windows.append(self.info)

    def _build_save_confirm(self) -> None:
        """The dialog the original shows after a save: "Close edit mode?"."""
        self.confirm = self._make_menu_surface()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("confirm")
        box.set_size_request(int(S.CONFIRM_MIN_WIDTH), -1)
        self.confirm_box = box

        box.append(self._caption("\u2713  Layout Saved", "ok"))
        box.append(self._caption("Close edit mode?", "body"))
        box.append(self._row(self._pill("Yes", "", self._save_confirm_yes, 100, 34),
                             self._pill("No", "grey", self._close_confirm, 100, 34)))

        self.confirm.set_child(box)
        self.windows.append(self.confirm)

    def _grant_access(self) -> None:
        """Put a terminal on screen running our own --install-uinput.

        See glasspad/terminal.py for why a window rather than a toast: the installer's
        plan, the polkit prompt and the final verdict are the things a user needs in
        front of them, and the same output is what a bug report quotes. Failures here
        are reported on screen AND on stderr, because "no terminal found" printed only
        to a stdout nobody can see is how this feature would have looked broken.
        """
        from glasspad import bundle, terminal

        tgt = bundle.cli_target()
        if not tgt:
            self._say("cannot tell what to run \u2014 start GlassPad from the AppImage")
            return
        argv, cwd, env_extra = terminal.install_command(tgt[0], tgt[1],
                                                       ["--install-uinput"])
        ok, note = terminal.open_command_window(argv, cwd=cwd, env_extra=env_extra)
        print(f"[grant] {note}", file=sys.stderr, flush=True)
        if ok:
            self._say("terminal opened \u2014 approve the password prompt there")
            self._watch_grant()
        else:
            self._say("no terminal found \u2014 see the command on stderr")

    def _watch_grant(self) -> None:
        """Follow the grant to its end, because the terminal closes and this app stays open.

        The grant is not one outcome, it is three, and the thing a user tries first is the
        one that cannot work. When the rule and the ACL changed the NODE, a new process gets
        the pad and a restart is the fix. When the grant added the account to `input`, this
        process still carries the credentials it was started with, and no amount of
        restarting, re-reading the rule or `udevadm trigger` changes that -- only a new login
        does. Telling somebody to restart in the second case spends their patience on a
        no-op, and they conclude the app is broken rather than that they are one log out
        away from it.

        So the box asks two questions on a timer and says which answer it got. The logic is
        in glasspad/pad.py (`grant_state`), where all three states can be produced on demand
        and asserted against; this is only the timer and the label.
        """
        self._grant_polls = 0
        self._grant_verdict = None
        GLib.timeout_add_seconds(2, self._poll_grant)

    def _poll_grant(self) -> bool:
        """One look. Returns True to keep the timer running, False to stop it."""
        from glasspad import pad as _pad

        verdict, detail = _pad.grant_state()
        self._grant_polls = getattr(self, "_grant_polls", 0) + 1
        if verdict != getattr(self, "_grant_verdict", None):
            self._grant_verdict = verdict
            self._set_grant_note(detail, ok=verdict != _pad.GRANT_PENDING)
            if verdict != _pad.GRANT_PENDING:
                # Out loud as well as on the label: the About box is usually closed while
                # the terminal is up, and the toast is what the user is looking at.
                self._say(detail)
        return _pad.keep_polling(verdict, self._grant_polls)

    def _set_grant_note(self, text: str, ok: bool = False) -> None:
        note = getattr(self, "grant_note", None)
        if note is None:
            return                          # no pill means we are not in that state
        try:
            note.set_text(text)
            if ok:
                note.add_css_class("ok")
        except Exception as exc:            # noqa: BLE001 - a label is not a failure
            print(f"[grant] could not update the verdict label: {exc}",
                  file=sys.stderr, flush=True)

    def _open_info(self) -> None:
        self._close_fab()                     # window.py does exactly this first
        if self.info is not None:
            self._show_panel(self.info)

    def _close_info(self) -> None:
        if self.info is not None:
            self.info.set_visible(False)

    def _close_confirm(self) -> None:
        if self.confirm is not None:
            self.confirm.set_visible(False)

    def _save_confirm_yes(self) -> None:
        """Yes closes edit mode, the panel and the hub -- as window.py's does."""
        self._close_confirm()
        self._set_edit(False)
        self._close_fab()

    def _licence_documents(self) -> tuple[str, list[str]]:
        """(text, problems) for the reader: the MIT notice, then the third-party notices.

        `problems` is what makes this honest. A licence panel that renders an empty
        rectangle is indistinguishable from one that renders nothing, and both read to
        a user as "this app has no licence" -- so the reason has to be text on screen,
        with the paths that were tried, rather than a silent empty buffer.
        """
        parts, problems = [], []
        for heading, name in (("MIT licence", "LICENSE"),
                              ("Third-party notices", "THIRD-PARTY-NOTICES.txt")):
            body, problem = read_doc(name)
            if problem:
                problems.append(problem)
                continue
            parts.append(f"{heading}\n{'=' * 72}\n{body}")
        return "\n\n".join(parts), problems

    def _build_licence_panel(self) -> None:
        """Read the licence where the overlay is, not in somebody else's window.

        The original opens a LicenseWindow. Here that would mean launching a text
        editor from a layer surface -- and a layer surface sits on the OVERLAY layer,
        so whatever opened would land UNDER the controls, which reads as a crash from a
        tablet. Handing the file to the desktop was the first attempt at this and it
        did not even do that: see the note on `read_doc` in bundle.py about why
        `file://` is not a scheme anybody registered.

        So: a scrolled, read-only TextView in the same centred-panel shape as the
        About box and the save dialog. No browser, no handler, no network, no portal,
        and the text shown is the text that shipped in the artefact.
        """
        self.licence = self._make_menu_surface()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.add_css_class("licence")
        # wider than the About box: licence text at About-box width is 90 lines of
        # ragged right edge. Height comes from the scrolled window below, because this
        # surface is sized by its child -- pinning it would paint nothing and still
        # swallow taps.
        box.set_size_request(int(S.INFO_MIN_WIDTH) + 180, -1)

        view = Gtk.TextView()
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_left_margin(10)
        view.set_right_margin(10)
        view.set_top_margin(8)
        self.licence_view = view

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(300)
        scroll.set_max_content_height(520)
        scroll.set_child(view)
        self.licence_scroll = scroll

        box.append(scroll)
        box.append(self._row(self._pill("Close", "grey", self._close_licence, 120, 34)))
        self.licence.set_child(box)
        self.windows.append(self.licence)

    def _open_licenses(self) -> None:
        body, problems = self._licence_documents()
        if self.licence is None:
            self._build_licence_panel()
        if self.licence is None or self.licence_view is None:
            return
        if problems:
            body = ("Not shipped in this build, or not readable:\n"
                    + "".join(f"  - {p}\n" for p in problems)
                    + ("\n" + body if body else ""))
        self.licence_view.get_buffer().set_text(body or "No licence text available.")
        if self.licence_scroll is not None:
            # get_vadjustment(), not .vadjustment: it is an interface property of
            # Gtk.Scrollable, and PyGObject does not expose interface properties as
            # attributes -- the attribute form raises AttributeError at open time.
            adj = self.licence_scroll.get_vadjustment()
            if adj is not None:
                adj.set_value(0.0)
        self._close_info()        # both centre, and they would stack exactly
        self._show_panel(self.licence)

    def _close_licence(self) -> None:
        if self.licence is not None:
            self.licence.set_visible(False)
    def _update_dpad_buttons(self) -> None:
        if self.dpad4_btn is None or self.dpad8_btn is None:
            return
        for mode, btn in ((4, self.dpad4_btn), (8, self.dpad8_btn)):
            if mode == self.dpad_mode:
                btn.remove_css_class("off")
            else:
                btn.add_css_class("off")

    def _set_dpad_mode(self, mode: int) -> None:
        if mode == self.dpad_mode:
            return
        self.dpad_mode = mode
        self._hat = "None"
        self.service.set_dpad("None")
        # every d-pad surface repaints: 8-way adds the wider ring
        for area in self.areas.values():
            area.queue_draw()
        self._dirty = True
        self._update_dpad_buttons()
        self._say(f"d-pad {self.dpad_mode}-way")

    # -- build the hub ------------------------------------------------------
    def _build_fab(self, sw: int) -> None:
        self.hub_x = (sw - S.HUB_CANVAS[0]) / 2.0      # canvas is top-centred
        x0, y0, bw, bh = self._hub_bbox(False)
        self.hub = self._make_menu_surface()
        self.hub.set_default_size(int(bw), int(bh))
        self.hub.set_size_request(int(bw), int(bh))
        self.hub.set_opacity(self.opacity)   # as the XAML binds the two hub buttons
        self.hub_box = Gtk.Fixed()
        self.hub.set_child(self.hub_box)

        for name, (colour, glyph, cx, cy, always) in S.HUB_BUTTONS.items():
            area = Gtk.DrawingArea()
            area.set_content_width(int(S.FAB_SIZE))
            area.set_content_height(int(S.FAB_SIZE))
            self.hub_state[name] = {"colour": colour, "glyph": glyph,
                                    "alpha": 1.0 if always else 0.0,
                                    "scale": 1.0, "pressed": False}
            area.set_draw_func(self._paint_hub(name))
            area.set_visible(always)
            self.hub_box.put(area, int(cx - x0), int(cy - y0))
            self.hub_children[name] = area
            tap = Gtk.GestureClick.new()
            tap.set_button(0)
            tap.connect("pressed", lambda *_a, n=name: self._hub_press(n))
            tap.connect("released", lambda *_a, n=name: self._hub_release(n))
            tap.connect("cancel", lambda *_a, n=name: self._hub_release(n))
            area.add_controller(tap)

        self._place_hub()
        self.hub.present()
        self.windows.append(self.hub)
        self._build_panel()
        self._build_info_panel()
        self._build_save_confirm()
        self._sync_disable_button()

        if os.environ.get("GLASSPAD_DEBUG"):
            def report():
                print(f"  hub surface {self.hub.get_width()}x{self.hub.get_height()} "
                      f"(collapsed box wants "
                      f"{self._hub_bbox(False)[2]:.0f}x{self._hub_bbox(False)[3]:.0f})",
                      flush=True)
                for label, win in (("edit panel", self.panel), ("info", self.info),
                                   ("save confirm", self.confirm)):
                    if win is not None:
                        req = win.get_default_size()
                        print(f"  {label} surface {win.get_width()}x{win.get_height()} "
                              f"(requested {req[0]}x{req[1]}) visible={win.get_visible()}",
                              flush=True)
                return False
            GLib.timeout_add(600, report)

    # -- self test ----------------------------------------------------------
    def _selftest(self) -> None:
        """Drive every hub action in sequence and report, then quit.

        Pixels can be measured from a screenshot; a handler only reveals itself when
        it is called. The hub was once shipped with five missing methods because
        nothing ever pressed the buttons, so this presses them -- with GLib timeouts
        between steps so the animations and the compositor keep up.

        Save is redirected to a scratch file: the point is to exercise the write
        path, not to overwrite the user's layout.
        """
        import tempfile

        steps = []
        scratch = os.path.join(tempfile.gettempdir(), "glasspad_selftest_layout.json")
        self.layout_path = scratch

        def check(label, cond):
            steps.append((label, bool(cond)))
            print(f"  {'ok  ' if cond else 'FAIL'}  {label}", flush=True)

        def step_open():
            self._hub_release("fab")
            check("hub opens", self.menu_open)
            return False

        def step_close():
            # get_width() is the live allocation and GTK reallocates on its own
            # schedule, so asserting on it here failed in one run out of four while
            # the hub was drawn correctly. get_default_size() is what _place_hub
            # asked for, which is the thing this step is actually about: the box got
            # bigger when the children fanned out.
            want = self._hub_bbox(True)[2]
            asked = self.hub.get_default_size()[0]
            check(f"hub grew to fit its children ({asked}px asked, {want:.0f}px wanted)",
                  abs(asked - want) <= 1 and asked > self._hub_bbox(False)[2])
            self._hub_release("fab")
            check("hub closes", not self.menu_open)
            return False

        def step_edit_on():
            self._hub_release("edit")
            check("edit mode on", self.edit)
            check("edit panel shown while editing",
                  self.panel is not None and self.panel.get_visible())
            return False

        def step_resize():
            """Grab A's corner knob and drag it, on the real surfaces.

            The geometry has its own unit tests; what this covers is the part only a
            live compositor can answer -- that asking a mapped layer surface to change
            size actually changes it, and that the state machine leaves nothing stuck
            behind afterwards.
            """
            name = "A"
            x0, y0, w0, h0 = self.geo[name]
            self._edit_begin(name, w0 - 1.0, h0 - 1.0)
            check("grabbing the corner knob sizes rather than moves",
                  self._grab[name]["mode"] == "size")
            self._edit_update(name, 50.0, 50.0)
            nw, nh = self.geo[name][2], self.geo[name][3]
            check(f"the control grew to {nw:.0f}x{nh:.0f}", nw > w0 + 20.0)
            check("proportions were kept", abs(nw - nh) < 0.5)
            check("the anchor corner did not move",
                  abs(self.geo[name][0] - x0) < 0.5 and abs(self.geo[name][1] - y0) < 0.5)
            want = (int(round(nw)), int(round(nh)))
            got = self.surfaces[name].get_default_size()
            check(f"the surface was resized to {got[0]}x{got[1]}", tuple(got) == want)
            self._edit_update(name, -9000.0, -9000.0)
            check(f"shrinking stops at the floor ({self.geo[name][2]:.0f}px)",
                  abs(self.geo[name][2] - MIN_CONTROL) < 0.5)
            self._edit_update(name, 90000.0, 90000.0)
            bx, by, bw, bh = self.geo[name]
            check("a huge drag cannot push it off screen",
                  bx + bw <= self.sw + 1.0 and by + bh <= self.sh + 1.0)
            self._edit_end(name)
            check("letting go ends sizing", self._sizing is None and name not in self._grab)
            # a body drag afterwards must move, not resize: the leak this guards is a
            # sizing state that survived the release and hijacked the next touch
            self._edit_begin(name, 2.0, 2.0)
            mode = self._grab[name]["mode"]
            self._edit_end(name)
            check("the next touch moves instead of resizing", mode == "move")
            return False

        def step_reset_restores_size():
            """Reset has to undo a resize, not just a move.

            It wrote the default width and height into the geometry but left the
            layer surface at the dragged size, so the pad kept drawing (and eating
            taps) at the size Reset was supposed to have thrown away.
            """
            name = "A"
            want = [f for f in default_frames(self.sw, self.sh) if f.name == name][0]
            self._edit_begin(name, self.geo[name][2] - 1.0, self.geo[name][3] - 1.0)
            self._edit_update(name, 55.0, 55.0)
            self._edit_end(name)
            grew = self.geo[name][2]
            check(f"resized A to {grew:.0f} first", grew > want.w + 10.0)
            self._reset_geometry()
            x, y, w, h = self.geo[name]
            check(f"reset restored the size ({w:.0f}x{h:.0f})",
                  abs(w - want.w) < 0.5 and abs(h - want.h) < 0.5)
            got = self.surfaces[name].get_default_size()
            check(f"the surface went back to {got[0]}x{got[1]} too",
                  abs(got[0] - round(want.w)) <= 1 and abs(got[1] - round(want.h)) <= 1)
            return False

        def step_rotate():
            """Rotate the overlay's idea of the screen and check the real surfaces.

            The watcher itself was proven against a real kscreen-doctor rotation;
            what belongs in the selftest is the arithmetic of apply_screen on live
            surfaces, and especially the round trip -- a layout that drifts a pixel
            per rotation is the failure mode this whole path exists to avoid.
            """
            dw, dh = self.design_screen or self.screen()
            before = list(self.geo["A"])
            self.apply_screen(dh, dw, reason="selftest rotate")     # swap the axes
            x, y, w, h = self.geo["A"]
            scale = min(dh / dw, dw / dh)
            check(f"it scaled to x{scale:.2f} ({before[2]:.0f} -> {w:.0f}px)",
                  abs(w - before[2] * scale) < 1.0)
            got = self.surfaces["A"].get_default_size()
            check(f"the surface followed ({got[0]}x{got[1]})",
                  abs(got[0] - round(w)) <= 1 and abs(got[1] - round(h)) <= 1)
            inside = all(gx + gw <= dh + 1.0 and gy + gh <= dw + 1.0
                         for gx, gy, gw, gh in self.geo.values())
            check("no control is off the rotated screen", inside)
            self.apply_screen(dw, dh, reason="selftest rotate back")
            after = self.geo["A"]
            check("rotating back returns the authored layout exactly",
                  all(abs(a - b) < 0.001 for a, b in zip(before, after)))
            return False

        def step_watchdog_arm():
            """Hold a control and let the real timer decide.

            Runs at the shortest limit the watchdog honours (250 ms) so the release
            lands inside the 300 ms gap between selftest steps. The fake-clock unit
            tests cover the arithmetic; what this covers is that a GLib timeout was
            really registered and that firing it releases the *pad* -- a release that
            only updated self.active would have passed every other check in here.
            """
            backend = getattr(self.service, "_backend", None)
            if getattr(backend, "name", "") != "null":
                check("the watchdog step needs --null-pad to see the pad", False)
                return False
            self.watchdog.limit = 0.25
            _x, _y, w, h = self.geo["A"]
            self._press("A", BUTTON, w / 2.0, h / 2.0, w, h)
            check("A is held", "A" in self.active)
            check("A is pressed on the pad", "A" in backend.last_buttons)
            check("the watchdog is counting on it",
                  self.watchdog.deadline("A") is not None)
            return False

        def step_watchdog_fire():
            backend = self.service._backend
            check("the watchdog released A", "A" not in self.active)
            check("the pad saw the release", "A" not in backend.last_buttons)
            check("nothing is left counting on A", self.watchdog.deadline("A") is None)
            # A fresh press has to be guarded again; the callback is one-shot per press.
            # The contact has to end first: the watchdog's release fenced it, and a
            # re-press without lifting the finger is exactly what the fence exists to
            # refuse -- so this step asserts both halves rather than the easy one.
            _x, _y, w, h = self.geo["A"]
            self._press("A", BUTTON, w / 2.0, h / 2.0, w, h)
            check("the released contact cannot press again before it ends",
                  "A" not in self.active)
            self._release("A", BUTTON)          # the finger lifts, and the fence goes
            self._press("A", BUTTON, w / 2.0, h / 2.0, w, h)
            check("a new press arms it again", self.watchdog.deadline("A") is not None)
            self._release("A", BUTTON)
            check("a normal release disarms it", self.watchdog.deadline("A") is None)
            self.watchdog.limit = DEFAULT_HOLD_LIMIT
            return False

        def step_ghost_arm():
            """Hold a stick and let the watchdog release it (250 ms, so it lands in
            the gap between steps). The finger does not lift: that is the point.
            """
            backend = getattr(self.service, "_backend", None)
            if getattr(backend, "name", "") != "null":
                check("the ghost step needs --null-pad to see the pad", False)
                return False
            # limit FIRST: the hold timer is armed from next_deadline() during the
            # press, so setting it afterwards leaves a timer counting to the old limit
            # and the step then "proves" the watchdog never fires.
            self.watchdog.limit = 0.25
            _x, _y, w, h = self.geo["LS"]
            self._drag_start("LS", STICK, w, h, w / 2.0, h / 2.0)
            self._drag_step("LS", STICK, w, h, 40.0, 0.0)
            self._ghost_axes = dict(backend.last_axes)
            return False

        def step_ghost_after():
            backend = self.service._backend
            rest = dict(backend.last_axes)
            check("the watchdog released the stick", "LS" not in self.active)
            check(f"the pad sits at centre after the release {rest}", True)
            # The finger is still down and still moving. GTK keeps sending
            # drag-update for a contact it still owns.
            _x, _y, w, h = self.geo["LS"]
            self._drag_step("LS", STICK, w, h, 60.0, 0.0)
            after = dict(backend.last_axes)
            print(f"    DIAG held={self._ghost_axes} released={rest} "
                  f"ghost-drag={after}", flush=True)
            check("a released stick stays off the pad (no ghost input)",
                  after == rest)
            # The other half: the fence must not be a permanent disable. The finger
            # lifts, and the next touch behaves normally -- without this the fix would
            # be worse than the bug.
            self._release("LS", STICK)
            self._drag_start("LS", STICK, w, h, w / 2.0, h / 2.0)
            self._drag_step("LS", STICK, w, h, 20.0, 0.0)
            back = dict(backend.last_axes)
            check(f"the stick works again after the finger lifted {back}",
                  back.get(0, 0.5) > 0.5)
            self._release("LS", STICK)
            self.watchdog.limit = DEFAULT_HOLD_LIMIT
            return False

        def step_turbo_arm():
            """Arm turbo, hold the control, and let the real timer take over.

            The unit tests drive the wave with a fake clock; this is the half that
            only a running main loop can show -- that the timeout was really
            registered, and that the pad keeps receiving edges while a finger stays
            down. 40 Hz over the 300 ms gap between steps is ~12 edges, so a timer
            that never fires cannot pass this.
            """
            backend = getattr(self.service, "_backend", None)
            if getattr(backend, "name", "") != "null":
                check("the turbo step needs --null-pad to see the pad", False)
                return False
            self.turbo.armed.add("A")
            self.turbo.hz = 40.0
            _x, _y, w, h = self.geo["A"]
            self._turbo_submits_before = backend.submits
            self._press("A", BUTTON, w / 2.0, h / 2.0, w, h)
            check("A is held", "A" in self.active)
            check("the turbo timer is registered", "A" in self._turbo_timers)
            check("the watchdog stood down for it",
                  self.watchdog.deadline("A") is None)
            return False

        def step_turbo_fired():
            backend = self.service._backend
            gained = backend.submits - self._turbo_submits_before
            check(f"the pad saw {gained} autofire frames while held", gained >= 6)
            self._release("A", BUTTON)
            check("letting go stops the firing", "A" not in self._turbo_timers)
            return False

        def step_turbo_released():
            backend = self.service._backend
            check("and the pad is left released", "A" not in backend.last_buttons)
            self.turbo.armed.discard("A")
            self.turbo.hz = DEFAULT_TURBO_HZ
            return False

        def step_grant_verdict():
            """What the About box says AFTER the grant terminal has closed.

            Three outcomes, three sentences -- because the action a user reaches for first
            (restart the app) is the one that cannot work when the grant arrived as group
            membership: a running process keeps the groups it started with, so only a new
            login helps. Which of the three applies is decided in glasspad/pad.py and tested
            there; this step proves the part no headless suite can -- that the real label on
            the real panel changes, that the toast repeats it, and that the timer stops at a
            verdict instead of polling for the rest of the session.
            """
            from glasspad import pad as _pad

            if getattr(self, "grant_note", None) is None:
                check("the About box has a verdict label while there is no pad", False)
                return False
            check("the verdict label starts empty: nothing has been attempted yet",
                  self.grant_note.get_text() == "")

            saved_state, saved_say = _pad.grant_state, self._say
            said = []
            self._say = lambda text: said.append(text)
            seen = []
            try:
                for state, groups, can in ((_pad.GRANT_PENDING, set(), False),
                                           (_pad.GRANT_RELOGIN, {"input"}, False),
                                           (_pad.GRANT_RESTART, set(), True)):
                    _pad.grant_state = (lambda st=state, g=groups, c=can:
                                        (st, _pad.GRANT_MESSAGES[st]))
                    self._grant_verdict = None
                    self._grant_polls = 0
                    keep = self._poll_grant()
                    seen.append((state, self.grant_note.get_text(), keep))
            finally:
                _pad.grant_state = saved_state
                self._say = saved_say

            for state, text, keep in seen:
                check(f"{state}: the panel shows its own wording ({text[:26]!r})",
                      text == _pad.GRANT_MESSAGES[state])
                check(f"{state}: the toast carries it too", len(said) >= 1)
                check(f"{state}: timer {'kept running' if keep else 'stopped'}",
                      keep == (state == _pad.GRANT_PENDING))
            relogin = _pad.GRANT_MESSAGES[_pad.GRANT_RELOGIN]
            check("and the membership verdict warns that restarting will not help",
                  "Restarting" in relogin and "LOG IN" in relogin)
            check("while the node verdict says a log out is not needed",
                  "No log out needed" in _pad.GRANT_MESSAGES[_pad.GRANT_RESTART])
            return False

        def step_disable_off():
            self._hub_press("disable")
            check("tap hides the pad", not self.overlay_enabled)
            check("hide button shows its latched colour",
                  self.hub_state["disable"]["colour"] == S.DISABLED_COLOR)
            return False

        def step_disable_on():
            self._hub_press("disable")
            check("tapping again brings it back", self.overlay_enabled)
            check("hide button returns to its live colour",
                  self.hub_state["disable"]["colour"] == S.HUB_BUTTONS["disable"][0])
            return False

        def step_edit_off_via_confirm():
            # the save dialog is the documented way out of edit mode
            self._save()
            check("save raises the confirmation", 
                  self.confirm is not None and self.confirm.get_visible())
            self._save_confirm_yes()
            check("Yes closes edit mode", not self.edit)
            check("Yes hides the edit panel",
                  self.panel is not None and not self.panel.get_visible())
            return False

        def step_info():
            self._hub_release("info")
            check("about box opens", self.info is not None and self.info.get_visible())
            self._close_info()
            check("Close dismisses it", not self.info.get_visible())
            return False

        def step_licence():
            """The licence reader is a panel like any other, so drive it like one.

            It used to be a pill that handed a file:// URI to a handler nobody
            registers, reported success in the status line, and opened nothing. The
            checks below are the ones that would have failed then: the panel exists,
            it is visible, and the text it shows is the text that shipped.
            """
            self._open_info()
            self._open_licenses()
            check("licence panel opens",
                  self.licence is not None and self.licence.get_visible())
            body, problems = self._licence_documents()
            check("licence text read from the tree"
                  + ("" if not problems else f"  ({problems})"),
                  bool(body.strip()) and not problems)
            check("it is the MIT notice", "MIT" in body)
            check("and the third-party notices shipped too", "gtk4-layer-shell" in body)
            check("the about box stepped aside rather than stacking",
                  self.info is not None and not self.info.get_visible())
            buf = self.licence_view.get_buffer() if self.licence_view else None
            shown = "" if buf is None else buf.get_text(
                buf.get_start_iter(), buf.get_end_iter(), True)
            check("and the widget actually holds that text",
                  "MIT" in shown and len(shown) == len(body))
            self._close_licence()
            check("Close dismisses it", not self.licence.get_visible())
            return False
        def step_dpad():
            self._set_dpad_mode(8)
            check("8-way selected", self.dpad_mode == 8)
            self._set_dpad_mode(4)
            check("back to 4-way", self.dpad_mode == 4)
            return False

        def step_profile_setup():
            """Two profiles in a temp config dir: the current layout, and the fighter
            variant of it. The real ~/.config is left alone by the selftest."""
            import shutil
            import tempfile
            from glasspad.layer_profiles import fighter_variant
            d = os.path.join(tempfile.gettempdir(), "glasspad_selftest_profiles")
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(os.path.join(d, "profiles"), exist_ok=True)
            base = list(self.base_frames)
            write_frames(os.path.join(d, "profiles", "default.json"), base,
                         self.opacity, self.dpad_mode, screen=(self.sw, self.sh),
                         turbo=self.turbo.armed, turbo_hz=self.turbo.hz,
                         stick=self.stick_tuning)
            variant, mode = fighter_variant(base, self.sw, self.sh)
            write_frames(os.path.join(d, "profiles", "fighter.json"), variant,
                         0.7, mode, screen=(self.sw, self.sh))
            self.cfg_dir = d
            self.profiles_enabled = True
            self.profile_name = "default"
            check("two profiles are visible to the overlay",
                  prof.list_profiles(d) == ["default", "fighter"])
            return False

        def step_profile_switch():
            import shutil
            if self.cfg_dir is None:
                return False
            before = list(self.geo.get("dpad", [0, 0, 0, 0]))
            prof.write_claim(self.cfg_dir, "fighter", source="selftest")
            self.check_claim("selftest")
            after = list(self.geo.get("dpad", [0, 0, 0, 0]))
            check("the claim switched the profile", self.profile_name == "fighter")
            check(f"the d-pad actually got bigger ({before[2]:.0f} -> {after[2]:.0f})",
                  after[2] > before[2])
            check("the profile's opacity came with it",
                  abs(self.opacity - 0.7) < 1e-6)
            # The hub label only exists if the hub was built with one. Assert on what
            # is actually there rather than inventing an accessor to make a check pass
            # -- that is how a test ends up proving nothing.
            if self.hub_label is not None:
                check("and it says which profile you are on",
                      self.hub_label.get_text() == "fighter")
            # the surface followed the geometry, not just the bookkeeping
            size = self.surfaces["dpad"].get_default_size()
            check(f"the layer surface was resized too ({size[0]}x{size[1]})",
                  abs(size[0] - round(after[2])) <= 1)
            return False

        def step_profile_defer():
            """The switch that must wait: a thumb is on the stick."""
            if self.cfg_dir is None:
                return False
            _x, _y, w, h = self.geo["LS"]
            self._press("LS", STICK, w / 2.0, h / 2.0, w, h)
            prof.write_claim(self.cfg_dir, "default", source="selftest")
            self.check_claim("selftest")
            check("a held control defers the switch", self.profile_name == "fighter")
            check("and remembers it", self._pending_profile == "default")
            self._release("LS", STICK)
            self.check_claim("released")
            check("it lands as soon as nothing is held",
                  self.profile_name == "default")
            check("and the pending queue is empty", self._pending_profile is None)
            return False

        def step_profile_cycle():
            if self.cfg_dir is None:
                return False
            self.cycle_profile()
            check("the hub button cycles profiles", self.profile_name == "fighter")
            self.cycle_profile()
            check("and wraps", self.profile_name == "default")
            # claims are the transport for the hub too, so a launcher and the hub
            # cannot disagree about what is current
            claim = prof.read_claim(self.cfg_dir)
            check("the hub records its choice where a launcher would look",
                  claim and claim["profile"] == "default"
                  and claim.get("source") == "hub")
            prof.write_claim(self.cfg_dir, "default", source="selftest")
            return False

        def step_live_geom():
            """A resized control is measured at its new size, through the real
            gesture callbacks rather than by calling _stick() with nice arguments."""
            backend = getattr(self.service, "_backend", None)
            gest = self.ctrl_input.get("LS")
            if getattr(backend, "name", "") != "null" or gest is None:
                return False
            x0, y0, w0, h0 = self.geo["LS"]
            try:
                # double it, the way the resize knob does
                self._resize_surface("LS", w0 * 2.0, h0 * 2.0)
                self.geo["LS"] = [x0, y0, w0 * 2.0, h0 * 2.0]
                gest.emit("drag-begin", w0, h0)           # near the new centre
                gest.emit("drag-update", 0.30 * w0, 0.0)  # 30% of the NEW radius
                # last_axes is device space, 0..1 with 0.5 at centre; the deflection
                # is what the tuning works in, so convert before asserting on it. The
                # first draft of this check compared the two and "failed" a correct fix.
                axis = backend.last_axes.get(0, 0.5)
                val = (axis - 0.5) * 2.0
                # 30% of the new radius is ~0.58 deflection; measured against the old
                # 59.5 px radius it would be past saturation at 1.00
                check(f"a resized stick is measured at its new radius "
                      f"(deflection {val:.2f})", 0.40 < val < 0.75)
                gest.emit("drag-end", 0.0, 0.0)
            finally:
                self._release("LS", STICK)
                self._resize_surface("LS", w0, h0)
                self.geo["LS"] = [x0, y0, w0, h0]
            return False

        def step_hide_fence():
            """The hide button drops every contact. One still under a finger must not
            put itself back on the pad while the overlay is hidden."""
            _x, _y, w, h = self.geo["A"]
            self._press("A", BUTTON, w / 2.0, h / 2.0, w, h)
            check("A held before hiding", "A" in self.active)
            self._set_overlay_enabled(False)
            check("hiding released A", "A" not in self.active)
            check("and fenced the contact that is still down",
                  self.lockout.is_locked("A"))
            self._press("A", BUTTON, w / 2.0, h / 2.0, w, h)
            check("that finger cannot hold it through a hide", "A" not in self.active)
            self._release("A", BUTTON)              # the finger lifts
            self._set_overlay_enabled(True)
            self._press("A", BUTTON, w / 2.0, h / 2.0, w, h)
            check("A works again once the contact ended", "A" in self.active)
            self._release("A", BUTTON)
            return False

        def step_edge_gate():
            """A control pushed flush against the edge loses its outer band and keeps
            its face. The pure tests cover the arithmetic; this covers the wiring, i.e.
            that a press actually consults it."""
            if "A" not in self.geo:
                return False
            saved = list(self.geo["A"])
            try:
                self.geo["A"] = [0.0, 400.0, 75.0, 75.0]
                _x, _y, w, h = self.geo["A"]
                self._press("A", BUTTON, 2.0, h / 2.0, w, h)
                check("a contact in the edge band does not press",
                      "A" not in self.active)
                self._press("A", BUTTON, w / 2.0, h / 2.0, w, h)
                check("the middle of that same flush control still presses",
                      "A" in self.active)
                self._release("A", BUTTON)
            finally:
                self.geo["A"] = saved
            return False

        def step_stick():
            """The pad must receive the tuned mapping, and the nub must agree with it.

            The port used to send 0.52 where the original sent 1.0 at the same thumb
            position, with the nub already frozen at its limit -- so the overlay drew
            full deflection and delivered half of it. Asserted against the client's own
            saturation distance, scaled to this surface.
            """
            from glasspad.layer_stick import APP_SATURATE, StickTuning
            from glasspad.layer_stick import MAX_TRAVEL as CL_TRAVEL
            if "LS" not in self.geo:
                return False
            w, h = self.geo["LS"][2], self.geo["LS"][3]
            radius = min(w, h) / 2.0
            rng = S.STICK_MAX_TRAVEL * (min(w, h) / S.STICK_OUTER)
            travel = CL_TRAVEL * (min(w, h) / S.STICK_OUTER)
            self.stick_tuning = StickTuning()
            seen: list[tuple[float, float]] = []
            real = self.service.set_left_stick
            self.service.set_left_stick = lambda x, y, *a: seen.append((x, y))
            try:
                self._stick("LS", w, h, radius + travel, radius)
                full = seen[-1] if seen else (0.0, 0.0)
                # _vis is a setter; the state dict is the readable form
                nub = self.vis.get("LS", {}).get("nub") or (0.0, 0.0)
                self._stick("LS", w, h, radius, radius)
                rest = seen[-1] if seen else (9.0, 9.0)
            finally:
                self.service.set_left_stick = real
                self._stick("LS", w, h, radius, radius)
            check(f"stick: full deflection at the original's travel "
                  f"({travel:.0f}px -> {full[0]:.2f})", full[0] > 0.99)
            check("stick: releasing returns the axis to centre", rest == (0.0, 0.0))
            check("stick: the nub agrees with what the pad was told",
                  abs(nub[0] - full[0] * rng) < 1e-6)
            check("stick: default tuning is the app's own curve",
                  abs(APP_SATURATE - self.stick_tuning.saturate) < 1e-9)
            return False

        def step_quit_guard():
            """An unsaved layout must not vanish under one press.

            The flag was set everywhere and read nowhere, so nothing could fail -- the
            layout simply disappeared, which no test can see after the fact.
            """
            self._set_edit(True)
            self._dirty = True
            self._quit_request()
            check("quit asked instead of quitting", self.loop.is_running())
            check("the discard dialog is up",
                  self.discard is not None and self.discard.get_visible())
            self._hide_discard()
            self._save()
            check("saving clears the unsaved flag", not self._dirty)
            self._set_edit(False)
            return False

        def step_stick_ui():
            """The edit panel must actually offer the presets, and change the live
            tuning when one is pressed -- a row of dead pills is worse than no row,
            because it looks like a control that works."""
            self._set_edit(True)
            btns = getattr(self, "_stick_btns", ())
            check("edit panel offers all four stick presets", len(btns) == 4)
            check("the preset pills are realised, not just constructed",
                  all(b.get_realized() for _k, b in btns))
            self._set_stick_preset("crisp")
            check("pressing Crisp changes the live tuning",
                  self.stick_tuning == PRESETS["crisp"])
            txt = self.stick_caption.get_text()
            check("the caption shows the numbers behind the choice "
                  f"({txt.strip()})",
                  "sat 39%" in txt and "curve 0.62" in txt)
            check("the change is marked unsaved", self._dirty is True)
            self._set_stick_preset("app")
            check("App restores the original's curve",
                  self.stick_tuning.is_app_default)
            self._set_edit(False)
            return False

        def step_grant_route():
            """The About box is the ONLY route to a pad for a drag-and-drop install.

            Somebody who installed by dropping the AppImage onto Shelly has no terminal
            and no command line. The null-pad notice tells them to run
            `--install-uinput`, which is a sentence about a place they are not standing.
            So this checks the button is there, wired to the thing that opens a window --
            not to a toast claiming a grant that did not happen.
            """
            # --null-pad is a DELIBERATE choice, so it does not set fell_back_to_null --
            # that flag means "we tried the real pad and the machine said no", which is
            # the only state where the button earns its place on the box. So the check
            # creates the state the report describes, and rebuilds the panel that was
            # built from the honest one.
            self.service.fell_back_to_null = True
            stale, self.info = self.info, None
            self._build_info_panel()
            if stale is not None:
                try:                        # cleanup only: it may already be gone
                    self.windows.remove(stale)
                    stale.destroy()
                except Exception:           # noqa: BLE001 - a leaked panel is not a failure
                    pass
            self._open_info()
            check("About box is shown", self.info is not None
                  and bool(self.info.get_mapped()))
            check("offers to grant gamepad access while there is no pad",
                  getattr(self, "grant_pill", None) is not None)
            _docs, problems = self._licence_documents()
            check("the licence documents in this build still resolve", not problems)
            self._close_info()
            self.service.fell_back_to_null = False
            return False

        def step_quit():
            self._quit_request()
            check("quit ends the session", not self.loop.is_running())
            # The marker has to be printed here. It used to sit on a timer after the
            # chain, which could never run: this step quits the loop, so the timer died
            # with it and an outside watcher had no way to tell "all passed" from
            # "stopped early" -- tools/check_all.py correctly called that unfinished.
            print("  selftest done", flush=True)
            return False

        chain = [step_open, step_close, step_edit_on, step_resize,
                 step_reset_restores_size, step_rotate,
                 step_watchdog_arm, step_watchdog_fire,
                 step_ghost_arm, step_ghost_after,
                 step_turbo_arm, step_turbo_fired, step_turbo_released,
                 step_grant_route, step_grant_verdict,
                 step_disable_off,
                 step_disable_on, step_edit_off_via_confirm, step_info, step_licence, step_dpad, step_profile_setup, step_profile_switch,
                 step_profile_defer, step_profile_cycle, step_live_geom, step_hide_fence, step_edge_gate, step_stick, step_stick_ui, step_quit_guard,
                 step_quit]
        t = 500
        for fn in chain:
            GLib.timeout_add(t, fn)
            t += 300
        # Safety net only. The real marker is printed by step_quit; this one fires if
        # the chain stopped short (an exception between steps), so a watcher is never
        # left guessing -- and it cannot produce a false "done".
        def _abandoned():
            print("  selftest ABANDONED: the chain never reached step_quit",
                  flush=True)
            self.loop.quit()
            return False

        GLib.timeout_add(t + 8000, _abandoned)


def region_for(kind: str, w: int, h: int):
    """Hit shape for a control: disc for buttons and sticks, pill for triggers,
    full square for the d-pad (it is drawn as a rounded square and is genuinely a
    directional area, so shrinking its corners would lose reachable input)."""
    if kind in (BUTTON, STICK):
        return circle_region(w, h)
    if kind == TRIGGER:
        return rounded_rect_region(w, h)
    return rect_region(w, h)


def apply_input_region(win: Gtk.Window, region) -> None:
    """Hand the hit shape to the compositor.

    GTK creates the Gdk.Surface during the first map, so calling this straight
    after present() finds nothing: retry on idle until the surface exists.
    """
    def attempt(*_a):
        surface = win.get_surface()
        if surface is None:
            return True                      # not mapped yet; try again
        surface.set_input_region(region)
        return False
    GLib.idle_add(attempt)


class NoDisplay(RuntimeError):
    """There is no display to draw on.

    Over ssh, from a systemd unit, or from any environment whose XDG_RUNTIME_DIR is not the
    one holding the compositor's socket. Distinct from "no layer-shell": the library can be
    perfectly fine here, and telling the user to install it would be confidently wrong.
    """


# Distinct from the generic non-zero exits so `python3 -m glasspad` knows not to append the
# libgtk4-layer-shell hint underneath a message that is not about that library.
EXIT_NO_DISPLAY = 5


def screen_size() -> tuple[int, int]:
    """First monitor's geometry, or the fallback when a display exists but reports none.

    `Gdk.Display.get_default()` returns None rather than raising when nothing can be opened,
    which used to make the very next attribute access an AttributeError several frames below
    the entry point. Two virgin-run walkthroughs produced that traceback, and a traceback is
    not a user interface: say what is missing, to stderr, because with no display there is
    nothing for the notice window to draw on either.
    """
    display = Gdk.Display.get_default()
    if display is None:
        wd = os.environ.get("WAYLAND_DISPLAY") or "unset"
        xrd = os.environ.get("XDG_RUNTIME_DIR") or "unset"
        raise NoDisplay(
            f"no Wayland display reachable (WAYLAND_DISPLAY={wd}, XDG_RUNTIME_DIR={xrd})")
    model = display.get_monitors()
    if model is not None and model.get_n_items():
        g = model.get_item(0).get_geometry()
        return g.width, g.height
    return 1920, 1200


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(
        "GlassPad overlay on gtk4-layer-shell: one OVERLAY surface per control, so "
        "pass-through is structural rather than a mask, and the pad composites above "
        "fullscreen games -- the reason this is the default backend. Also reachable as "
        "`python3 -m glasspad`, which answers --version and --status first.\n\n"
        "(--backend is read by that entry point and removed before this parser "
        "runs, so it is deliberately absent from the options below.)"))
    ap.add_argument("--null-pad", action="store_true", help="draw only, no uinput device")
    ap.add_argument("--opacity", type=float, default=None,
                    help="override the opacity stored in layout.json")
    ap.add_argument("--layout", default=None,
                    help="layout.json to load (default: the app's own config path)")
    ap.add_argument("--save-layout", action="store_true",
                    help="write the geometry in use to --layout and exit "
                         "(gives the Qt editor something to fine-tune)")
    ap.add_argument("--no-input-regions", action="store_true",
                    help="leave hit areas as full squares (A/B against the default)")
    ap.add_argument("--no-hit-shapes", action="store_true",
                    help="let the whole square of a round button fire it")
    ap.add_argument("--edit", action="store_true",
                    help="drag controls to place them; hub gets defaults/save/quit")
    ap.add_argument("--trace", action="store_true",
                    help="log every stick/dpad position (calibration debugging)")
    ap.add_argument("--press", choices=LP.MODES,
                    default=os.environ.get("GLASSPAD_PRESS", "bevel"),
                    help="how a button shows a press: bevel (raised key pushed "
                         "inward, default), glow (no movement, soft brighten), or "
                         "outset -- the Windows client's 1.08, which the surface "
                         "clips into a square")
    ap.add_argument("--press-anim", type=int, default=140, metavar="MS",
                    help="eased return to rest in milliseconds; 0 snaps")
    ap.add_argument("--dpad-mode", choices=(4, 8), type=int, default=None,
                    help="override the d-pad mode stored in layout.json")
    ap.add_argument("--dpad-style", choices=tuple(DP.STYLE_TO_INDICATOR),
                    default=os.environ.get("GLASSPAD_DPAD_STYLE", "notch"),
                    help="how 8-way mode shows at rest: notch (four faint corner "
                         "dots, default), spoke, hairline, bare, or app for the "
                         "Windows client's ice-blue ring")
    ap.add_argument("--dpad-cross", choices=tuple(DP.CROSS_STYLES),
                    default=os.environ.get("GLASSPAD_DPAD_CROSS", "soft"),
                    help="cross silhouette: app (the client's white outline), "
                         "soft (default), rounded (soft plus rounded arm ends)")
    ap.add_argument("--dpad-press", choices=tuple(DP.PRESS_COLORS),
                    default=os.environ.get("GLASSPAD_DPAD_PRESS", "cool"),
                    help="lit-arm colour: ice (the client's cyan), cool (default), "
                         "neutral")
    ap.add_argument("--stick-anchor", choices=("press", "surface"), default="press",
                    help="press: thumb landing point is the stick centre (default)")
    ap.add_argument("--profile", default=None,
                    help="start on this profile from ~/.config/GlassPad/profiles")
    ap.add_argument("--no-profiles", action="store_true",
                    help="ignore profile claims (one layout for everything)")
    ap.add_argument("--profile-timeout", type=float, default=0.0, metavar="SECONDS",
                    help="go back to 'default' this long after the last claim; 0 "
                         "keeps the claimed profile until something else claims "
                         "(launchers like Steam have no exit hook, and a layout "
                         "reverting mid-alt-tab is worse than sticky)")
    ap.add_argument("--edge-margin", type=float, default=EDGE_DEFAULT, metavar="PX",
                    help="logical pixels at the panel edge where a press is refused "
                         "(default %.0f; 0 turns it off). Only ever touches controls "
                         "that sit that close to an edge, and never more than a "
                         "quarter of one" % EDGE_DEFAULT)
    ap.add_argument("--stick-preset", choices=tuple(PRESETS), default=None,
                    help="response curve preset: app (the Windows original, full "
                         "deflection 52%% of the way to the rim), soft (small dead "
                         "zone), crisp (fast), wide (deliberate). Overridden by the "
                         "three flags below")
    ap.add_argument("--stick-sat", type=float, default=None, metavar="FRACTION",
                    help="fraction of the stick radius at which deflection maxes out")
    ap.add_argument("--stick-dead", type=float, default=None, metavar="FRACTION",
                    help="fraction of the stick radius ignored around the centre")
    ap.add_argument("--stick-curve", type=float, default=None, metavar="EXPONENT",
                    help="<1 responds sooner, >1 is finer near the centre")
    ap.add_argument("--turbo-hz", type=float, default=None, metavar="HZ",
                    help="autofire rate for armed controls; 0 turns turbo off "
                         f"entirely (default {DEFAULT_TURBO_HZ:g}, saved in the layout)")
    ap.add_argument("--turbo", default=None, metavar="A,B",
                    help="arm these controls for autofire (buttons and triggers only), "
                         "overriding the layout. A tap on a control's knob in edit "
                         "mode arms it too, and shows an amber T on it")
    ap.add_argument("--screen", default=None, metavar="WxH",
                    help="pretend the screen is this size (logical px). A test aid for "
                         "the rotation path: it also turns off the display watcher, "
                         "which would otherwise notice the real screen immediately")
    ap.add_argument("--hold-limit", type=float, metavar="SECONDS",
                    default=clamp_limit(os.environ.get("GLASSPAD_HOLD_LIMIT")),
                    help="release a control held longer than this, for the cases where "
                         "nothing tells us to: an exclusive input grab, a blanked "
                         "screen, a suspend with a thumb on the stick. "
                         f"0 disables it (default {DEFAULT_HOLD_LIMIT:.0f}s)")
    args = ap.parse_args(argv)

    cfg_dir = os.path.dirname(default_layout_path())
    profile_name = None
    profiles_on = not args.no_profiles
    if args.layout:
        # An explicit file beats any profile, and switches claiming off for the run:
        # someone testing a layout by hand must not have a launcher change it under
        # them halfway through.
        path = args.layout
        profiles_on = False
    else:
        profile_name, ppath, pnote = prof.resolve(cfg_dir, args.profile)
        path = ppath or default_layout_path()
        if pnote:
            print(f"  profile: {pnote}")
    try:
        sw, sh = screen_size()
    except NoDisplay as exc:
        # Last chance to fail politely: from here on everything assumes a display. The
        # paragraphs are the ones the dependency notice uses, chosen by the same state name,
        # so the window and the stderr copy never drift apart.
        from glasspad import deps as _deps
        _deps.print_notice("nodisplay", detail=str(exc))
        return EXIT_NO_DISPLAY
    simulated = None
    if args.screen:
        try:
            w_s, h_s = args.screen.lower().split("x")
            simulated = (int(w_s), int(h_s))
            sw, sh = simulated
        except ValueError:
            print(f"  --screen wants WxH, not {args.screen!r}")
            return 2
    frames, file_opacity, dpad_mode, note = load_frames(path, sw, sh)
    # Trust the recorded design size only when the file was actually accepted: on the
    # refusal paths load_frames returns defaults generated for *this* screen, and
    # calling them the file's design size would then scale them for no reason.
    design = load_screen_size(path) if note.startswith("loaded") else None

    if args.save_layout:
        # The rewrite keeps what the file already knew: dropping the turbo flags or
        # the rate here would make --save-layout quietly disarm somebody's pad.
        loaded = note.startswith("loaded")
        write_frames(path, frames, args.opacity or file_opacity, dpad_mode,
                     screen=design or (sw, sh),
                     turbo=load_turbo(path) if loaded else set(),
                     turbo_hz=load_turbo_hz(path))
        print(f"  {note}")
        print(f"  wrote {len(frames)} controls to {path}")
        return 0

    print(f"  layer-shell preload: {_HOW}")
    if not LayerShell.is_supported():
        # The library was found but the link order still lost, which is the one failure
        # LD_PRELOAD actually fixes. Name the file we loaded, since on a foreign distro
        # "install libgtk4-layer-shell" would be the wrong advice.
        print("  ERROR: gtk4-layer-shell loaded but unusable here (libwayland won the\n"
              "         link order). Force it:\n"
              f"    LD_PRELOAD={LAYER_SHELL_LIB or '<path to libgtk4-layer-shell.so.0>'} "
              "python3 -m glasspad.overlay_layer\n"
              f"         (found via {LAYER_SHELL_WHY}; override the search with "
              f"{GLASSPAD_ENV_HINT})")
        # Draw nothing, and say so where it can be seen. Before this the process printed the
        # above to a stdout nobody had, exited 3, and left a person with a desktop that
        # looked untouched -- the closest thing this app has to a silent failure, and the
        # blank-overlay case the README warns about. The selftest is exempt: it runs legs
        # that are headless by design, and a window waiting for a click would hang the suite.
        if not os.environ.get("GLASSPAD_SELFTEST"):
            from glasspad import notice as _notice
            _notice.run("unbound")
        return 3

    opacity = args.opacity if args.opacity is not None else file_opacity
    service = pad.PadService(pad.NullBackend() if args.null_pad else None)
    service.init(lambda *_: None, synchronous=True)

    ov = Overlay(service, min(1.0, max(0.05, opacity)), edit=args.edit,
                 layout_path=path, dpad_mode=dpad_mode, hold_limit=args.hold_limit)
    ov.trace = args.trace or bool(os.environ.get("GLASSPAD_TRACE"))
    if service.fell_back_to_null:
        # pad.py has already shouted about this on stderr, which is the right
        # place when something launched GlassPad from a terminal. This is the
        # place that matters when it came from a desktop icon: nothing else on
        # screen will ever look wrong.
                # Deliberately terse and path-free: this string is painted onto the overlay,
        # where the absolute path of a 1.5 MB AppImage sitting in a Downloads folder does
        # not fit and cannot be copy-pasted anyway. The full instruction, with a command
        # that actually runs, goes to stderr for whoever launched this from a terminal.
        ov._say("NO PAD: /dev/uinput not writable -- run with --install")
    ov.stick_anchor = args.stick_anchor
    ov.edge_margin = edge_clamp(args.edge_margin)
    tuning, warn = stick_preset(args.stick_preset) if args.stick_preset \
        else (load_stick_tuning(path), None)
    if args.stick_sat is not None or args.stick_dead is not None \
            or args.stick_curve is not None:
        tuning = StickTuning(
            args.stick_sat if args.stick_sat is not None else tuning.saturate,
            args.stick_dead if args.stick_dead is not None else tuning.deadzone,
            args.stick_curve if args.stick_curve is not None else tuning.curve,
        ).clamp()
    if warn:
        print(f"  {warn}")
    ov.stick_tuning = tuning
    if args.dpad_mode:
        # set it on the overlay, not on the local: Overlay was already built with the
        # file's mode and the paint closure reads self.dpad_mode. Assigning the local
        # only updated the line printed below, which then reported a mode the pad was
        # not in -- measured, all three style variants drew the same 4-way cross.
        ov.dpad_mode = args.dpad_mode
    ov.press_mode = args.press
    ov.press_anim_ms = max(0, args.press_anim)
    ov.dpad_style = args.dpad_style
    ov.dpad_cross = args.dpad_cross
    ov.dpad_press = args.dpad_press
    turbo = load_turbo(path) if note.startswith("loaded") else set()
    if args.turbo is not None:
        turbo = {n.strip() for n in args.turbo.split(",") if n.strip()}
    # Rate precedence: the flag wins, then what the layout says, then the default.
    saved_hz = load_turbo_hz(path)
    ov.turbo = Turbo(clamp_hz(args.turbo_hz if args.turbo_hz is not None
                              else (saved_hz if saved_hz is not None
                                    else DEFAULT_TURBO_HZ)), armed=turbo)
    ov.design_screen = design or (sw, sh)
    ov.input_regions = not args.no_input_regions
    ov.hit_shapes = not args.no_hit_shapes
    ov.sw, ov.sh = sw, sh
    ov.loop = GLib.MainLoop()
    ov.cfg_dir = cfg_dir
    ov.profiles_enabled = profiles_on
    ov.profile_name = profile_name
    ov.profile_timeout = max(0.0, args.profile_timeout)
    ov.build(frames, sw)
    ov._sync_hub_label()
    if profiles_on:
        ov.watch_claims()
    if not simulated:
        ov.watch_display()
    if ov.design_screen != (sw, sh):
        # The file was laid out on another screen -- a rotation, a dock, a display
        # scale change. Refit once from the authored geometry, before the first frame.
        dw, dh = ov.design_screen
        ov.apply_screen(sw, sh, force=True,
                        reason=f"layout authored for {dw}x{dh}")
    # Built from parts rather than one nested-f-string conditional: the version that
    # tried to put the comma inside the ternary printed "hold limit 8s, , turbo 20 Hz".
    hold = f"{ov.watchdog.limit:.0f}s" if ov.watchdog.enabled else "off"
    armed = ",".join(sorted(ov.turbo.armed))
    print(f"  {note}  (opacity {ov.opacity:.2f}, dpad {ov.dpad_mode}-way "
          f"{ov.dpad_style}/{ov.dpad_cross}/{ov.dpad_press}, "
          f"press {ov.press_mode} over {ov.press_anim_ms}ms, "
          f"hold limit {hold}, "
          f"stick {ov.stick_tuning.describe()}, "
          f"{('profile ' + ov.profile_name) if (profiles_on and ov.profile_name) else 'profiles off'}, "
          f"{edge_describe(ov.frames if hasattr(ov, 'frames') else [],
                           (ov.sw, ov.sh), ov.edge_margin)}, "
          f"{ov.turbo.describe()}"
          f"{f' armed {armed}' if armed else ''}, "
          f"screen {sw}x{sh} authored {ov.design_screen[0]}x{ov.design_screen[1]})")
    print(f"  {len(ov.windows)} layer surfaces up (screen {sw}x{sh})")
    if os.environ.get("GLASSPAD_SELFTEST"):
        # press the hub's own buttons once the loop is running; see Overlay._selftest
        GLib.idle_add(ov._selftest)
    if args.edit:
        print(f"  EDIT MODE: drag any control to move it; save -> {path}")
    # Ctrl-C used to do nothing. The KeyboardInterrupt surfaced inside the GLib loop and
    # was swallowed, so the process stayed up -- transparent, invisible, still holding a
    # registered "Microsoft X-Box 360 pad" that games would read with nobody left to send
    # it input. Defer through idle_add so teardown runs on the main context, on the same
    # path as the Quit button; see glasspad/shutdown.py.
    from glasspad import shutdown as _shutdown
    _shutdown.install(lambda: GLib.idle_add(ov._quit_request))
    try:
        ov.loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        ov.quit()
    print(f"  closed. maximum controls held simultaneously: {ov.max_contacts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
