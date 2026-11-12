"""The Windows client's visual spec, transcribed from GlassPad-1.0.2.

Every number here has a source. The layer-shell overlay draws with cairo, so it
needs the same numbers the WPF client used, and "looks roughly like it" is not a
specification. Cited locations:

  Overlay/GamepadLayout.cs  Pill/Circle/Lbl builders        (fills, stroke, label sizes)
  Input/StickZone.cs        Outer/Inner/MaxTravel, fills
  Input/DpadZone.cs         Base/BaseArm, cross, arm highlight, 120 ms fade
  MainWindow.xaml           FabBtn/PillBtn styles, HubCanvas geometry, panel styling
  MainWindow.xaml.cs        AnimateIn/AnimateOut, the 1.08 press scale, FAB colour states

Painters take a cairo context and draw in logical pixels; GTK hands an already
scaled context to a drawing-area draw func, so no device-pixel maths is needed.
"""
from __future__ import annotations

# -- colour ---------------------------------------------------------------
def argb(spec: str) -> tuple[float, float, float, float]:
    """WPF hex to a cairo rgba tuple.

    WPF writes #AARRGGBB, which is the opposite of CSS's assumption for an
    8-digit value: '#B8202030' is alpha 0xB8 on rgb(0x20,0x20,0x30), not
    rgb(0xB8,0x20,0x20) with alpha 0x30.
    """
    s = spec.lstrip("#")
    if len(s) == 8:
        a, r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4, 6))
    elif len(s) == 6:
        a = 255
        r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
    else:
        raise ValueError(f"not a WPF colour: {spec!r}")
    return r / 255.0, g / 255.0, b / 255.0, a / 255.0


# -- shared control decoration (GamepadLayout.cs) -------------------------
STROKE_COLOR = (1.0, 1.0, 1.0, 200 / 255)      # Color.FromArgb(200,255,255,255)
STROKE_WIDTH = 1.5
LABEL_COLOR = (1.0, 1.0, 1.0, 220 / 255)       # Color.FromArgb(220,255,255,255)
LABEL_FAMILY = "Segoe UI"                      # fontconfig falls back on Linux
LABEL_WEIGHT = "bold"
PILL_LABEL_RATIO = 0.38                        # Lbl(tag, bh * 0.38)
CIRCLE_LABEL_RATIO = 0.32                      # Lbl(tag, d * 0.32)

#: Fill colours from GamepadLayout.Build(), kept as the original #AARRGGBB.
FILLS = {
    "LT": "#B3505060", "RT": "#B3505060",
    "LB": "#B3607080", "RB": "#B3607080",
    "Y": "#B3D4AC00", "X": "#B31A5FB0", "B": "#B3C02020", "A": "#B3108040",
    "L3": "#B3304060", "R3": "#B3304060",
    "BACK": "#B3404055", "GUIDE": "#B3908020", "START": "#B3404055",
}

# -- stick (StickZone.cs) --------------------------------------------------
STICK_OUTER = 119.0
STICK_INNER = 57.0
STICK_MAX_TRAVEL = (STICK_OUTER - STICK_INNER) / 2.0      # 31 px
# NOTE: StickZone.cs writes the same expression with a comment saying "28 px".
# The arithmetic says 31 and the compiled value is 31; the comment is stale. Quoting
# it instead of evaluating it is how the parity target got guessed wrong once already.
STICK_RING_FILL = "#B8202030"
STICK_NUB_FILL = "#B8909098"

# -- d-pad (DpadZone.cs) ---------------------------------------------------
DPAD_BASE = 180.0
DPAD_ARM = 60.0
DPAD_FILL = "#C8404055"
DPAD_STROKE = (1.0, 1.0, 1.0, 180 / 255)        # Color.FromArgb(180,255,255,255)
DPAD_ARM_FILL = (0.0, 220 / 255, 1.0, 100 / 255)  # Color.FromArgb(100,0,220,255)
DPAD_ARM_RADIUS = 4.0
DPAD_FADE_MS = 120                                # DoubleAnimation(0, 120ms)
DPAD_CARDINAL = 1.0
DPAD_DIAGONAL = 0.7                               # diagonals light two arms at .7
DPAD_RING_SIZE = 200.0                            # 8-way indicator ring
DPAD_RING_FILL = (0.0, 180 / 255, 1.0, 60 / 255)
DPAD_RING_STROKE = (0.0, 220 / 255, 1.0, 200 / 255)
DPAD_RING_STROKE_W = 2.5
DPAD_LABEL_COLOR = (1.0, 1.0, 1.0, 210 / 255)


def cross_polygon(outer: float = DPAD_BASE, arm: float = DPAD_ARM) -> list[tuple[float, float]]:
    """The 12-point closed cross, copied from DpadZone.CrossGeom."""
    c = (outer - arm) / 2.0
    return [
        (c, 0), (c + arm, 0), (c + arm, c), (outer, c), (outer, c + arm),
        (c + arm, c + arm), (c + arm, outer), (c, outer), (c, c + arm),
        (0, c + arm), (0, c), (c, c),
    ]


def arm_rects(outer: float = DPAD_BASE, arm: float = DPAD_ARM) -> dict[str, tuple[float, float, float, float]]:
    """Highlight rectangles, from DpadZone.AddArmHighlight."""
    c = (outer - arm) / 2.0
    return {
        "N": (c, 0, arm, c),
        "S": (c, c + arm, arm, c),
        "W": (0, c, c, arm),
        "E": (c + arm, c, c, arm),
    }


# -- edit mode grips -------------------------------------------------------
# Not from the client: its editor drags whole elements with no resize affordance,
# so these are ours. Green because it is the app's own "committed" colour (the Save
# pill), and it is the one hue on the pad that no control uses at rest.
GRIP_FILL = (0.13, 0.55, 0.42, 0.92)
GRIP_FILL_ACTIVE = (0.20, 0.78, 0.58, 1.0)
GRIP_RING = (1.0, 1.0, 1.0, 0.92)

# -- hub (MainWindow.xaml + MainWindow.xaml.cs) ---------------------------
FAB_SIZE = 44.0
FAB_RADIUS = 22.0                               # CornerRadius="22"
FAB_FONT = 18.0                                 # FontSize="18"
FAB_PRESSED_OPACITY = 0.7                       # IsPressed trigger
HUB_CANVAS = (230.0, 200.0)
HUB_TOP_MARGIN = 8.0

#: name -> (colour, glyph, canvas x, canvas y, always_visible)
HUB_BUTTONS = {
    "disable": ("#CCE07030", "\u2298",  65.0,  88.0, True),
    "fab":     ("#CC5050A0", "\u2261", 121.0,  88.0, True),
    "quit":    ("#CCFF3333", "\u2715", 121.0,  28.0, False),
    "edit":    ("#CC4080FF", "\u270e", 181.0,  88.0, False),
    "info":    ("#CC208060", "\u2139", 121.0, 148.0, False),
    # No counterpart in the Windows client: profiles are a Linux-only affordance, so
    # there is no XAML to copy. It lives in the opened menu rather than being a third
    # always-visible button because a collapsed hub is 100x44 of swallowed taps and a
    # Wayland surface eats a press anywhere on it, not only where a button is drawn.
    "profile": ("#CC704090", "\u25c8", 181.0, 148.0, False),
}
FAB_COLOR_OPEN = "#CCA02020"                    # FabButton_Click when opening
FAB_COLOR_EDIT = "#CC2070A8"                    # CloseFab while edit mode active
EDIT_COLOR_ACTIVE = "#CC00CCFF"                 # EditModeBtn while edit active

#: AnimateIn stagger, from AnimateIn(CloseAppBtn,0)/(EditModeBtn,60)/(InfoBtn,120)
ANIM_IN_DELAYS = {"quit": 0.0, "edit": 60.0, "info": 120.0, "profile": 180.0}
ANIM_IN_MS = 180.0
ANIM_OUT_MS = 140.0
PRESS_SCALE = 1.08                              # ScaleTransform(1.08, 1.08) on TouchDown


def ease_out_cubic(t: float) -> float:
    """WPF CubicEase { EasingMode = EaseOut }."""
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


# -- settings panel (MainWindow.xaml EditPanel) ---------------------------
PANEL_BG = "#DD1a1a2e"
PANEL_RADIUS = 12.0
PANEL_PADDING = (16.0, 12.0)
PANEL_MIN_WIDTH = 260.0
PANEL_LABEL_COLOR = "#AAC0D0FF"
PANEL_LABEL_SIZE = 11.0
PILLBTN_RADIUS = 8.0
PILLBTN_FONT = 12.0
BTN_W, BTN_H = 106.0, 30.0
BTN_H_TALL = 32.0
BTN_ACTIVE = "#CC208060"
BTN_INACTIVE = "#80406090"
BTN_RESET = "#CC804020"
CONFIRM_BG = "#F01a1a2e"
CONFIRM_RADIUS = 14.0
CONFIRM_OK_COLOR = "#88EEBB"

# -- font fallback note ---------------------------------------------------
# The client asks for Segoe UI, which is not present on Linux; fontconfig maps it
# (fc-match 'Segoe UI' here resolves to Adwaita Sans). The request is kept as-is
# so the port follows whatever the user's fontconfig decides, exactly as it does
# on the Qt side, rather than hardcoding a substitution that would be wrong on a
# machine that does have Segoe UI.


def _text(cr, text: str, size: float, color: tuple[float, float, float, float],
          cx: float, cy: float, bold: bool = True, family: str = LABEL_FAMILY) -> None:
    """Draw `text` centred on (cx, cy) with Pango, at `size` logical pixels."""
    import cairo                                          # noqa: F401
    from gi.repository import Pango, PangoCairo

    layout = PangoCairo.create_layout(cr)
    layout.set_text(text, -1)
    desc = Pango.FontDescription()
    desc.set_family(family)
    desc.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    desc.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(desc)
    _, extents = layout.get_pixel_extents()
    cr.set_source_rgba(*color)
    cr.move_to(cx - extents.width / 2.0, cy - extents.height / 2.0 - extents.y)
    PangoCairo.show_layout(cr, layout)


def measure_text(text: str, size: float, bold: bool = True,
                 family: str = LABEL_FAMILY) -> tuple[float, float]:
    """Pixel extents of `text` at `size`, through the same Pango path _text uses.

    A surface that hosts a readout has to be sized to that readout: too small and
    the text is clipped, too big and it swallows touches in space it never drew in.
    Measuring with a different engine than the one that draws is how you get the
    first of those by a couple of pixels.
    """
    import cairo

    from gi.repository import Pango, PangoCairo

    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)
    layout = PangoCairo.create_layout(cairo.Context(surf))
    layout.set_text(text, -1)
    desc = Pango.FontDescription()
    desc.set_family(family)
    desc.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    desc.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(desc)
    _ink, logical = layout.get_pixel_extents()
    return float(logical.width), float(logical.height)


def _deco(cr, scale: float, alpha: float, w: float, h: float) -> None:
    """Common press scale + element opacity, about the control's centre."""
    cr.save()
    cr.translate(w / 2.0, h / 2.0)
    if scale != 1.0:
        cr.scale(scale, scale)
    cr.translate(-w / 2.0, -h / 2.0)
    cr.push_group()


def _finish(cr, alpha: float) -> None:
    cr.pop_group_to_source()
    cr.paint_with_alpha(alpha)
    cr.restore()


# -- painters --------------------------------------------------------------
def paint_pill(cr, w: float, h: float, fill: str, label: str,
               scale: float = 1.0, alpha: float = 1.0,
               text_size: float | None = None) -> None:
    """Border{CornerRadius=bh/2, Stroke=white/200, 1.5} + bold label at bh*0.38.

    text_size overrides the app's ratio for labels that need it (see
    layer_geom.label_size); None keeps the app's sizing exactly.
    """
    _deco(cr, scale, alpha, w, h)
    r = h / 2.0
    cr.new_sub_path()
    cr.arc(w - r, r, r, -1.5708, 0.0)
    cr.arc(w - r, h - r, r, 0.0, 1.5708)
    cr.arc(r, h - r, r, 1.5708, 3.14159)
    cr.arc(r, r, r, 3.14159, 4.71239)
    cr.close_path()
    cr.set_source_rgba(*argb(fill))
    cr.fill_preserve()
    cr.set_line_width(STROKE_WIDTH)
    cr.set_source_rgba(*STROKE_COLOR)
    cr.stroke()
    _text(cr, label, text_size if text_size is not None else h * PILL_LABEL_RATIO,
          LABEL_COLOR, w / 2.0, h / 2.0)
    _finish(cr, alpha)


def paint_circle(cr, w: float, h: float, fill: str, label: str,
                 scale: float = 1.0, alpha: float = 1.0,
                 text_size: float | None = None) -> None:
    """Ellipse{Fill, Stroke=white/200, 1.5} + bold label at d*0.32 (or text_size)."""
    _deco(cr, scale, alpha, w, h)
    cx, cy, r = w / 2.0, h / 2.0, min(w, h) / 2.0 - STROKE_WIDTH / 2.0
    cr.arc(cx, cy, r, 0.0, 6.28319)
    cr.set_source_rgba(*argb(fill))
    cr.fill_preserve()
    cr.set_line_width(STROKE_WIDTH)
    cr.set_source_rgba(*STROKE_COLOR)
    cr.stroke()
    _text(cr, label,
          text_size if text_size is not None else min(w, h) * CIRCLE_LABEL_RATIO,
          LABEL_COLOR, cx, cy)
    _finish(cr, alpha)


def paint_stick(cr, w: float, h: float, nub_dx: float = 0.0, nub_dy: float = 0.0,
                scale: float = 1.0, alpha: float = 1.0) -> None:
    """Outer ring + nub. StickZone: Outer 119 #B8202030, Inner 57 #B8909098.

    The nub is drawn at its own fixed size regardless of surface size, then
    offset by the (already clamped) travel, matching the TranslateTransform.
    """
    _deco(cr, scale, alpha, w, h)
    cx, cy = w / 2.0, h / 2.0
    outer_r = min(w, h) / 2.0 - STROKE_WIDTH / 2.0
    cr.arc(cx, cy, outer_r, 0.0, 6.28319)
    cr.set_source_rgba(*argb(STICK_RING_FILL))
    cr.fill_preserve()
    cr.set_line_width(STROKE_WIDTH)
    cr.set_source_rgba(*STROKE_COLOR)
    cr.stroke()
    # nub: INNER as a fraction of OUTER, so a resized control keeps proportions
    nub_d = STICK_INNER * (min(w, h) / STICK_OUTER)
    nub_r = nub_d / 2.0
    cr.arc(cx + nub_dx, cy + nub_dy, nub_r, 0.0, 6.28319)
    cr.set_source_rgba(*argb(STICK_NUB_FILL))
    cr.fill()
    _text(cr, "", 1, LABEL_COLOR, cx, cy)
    _finish(cr, alpha)


def paint_dpad(cr, w: float, h: float, arms: dict[str, float] | None = None,
               mode: int = 4, scale: float = 1.0, alpha: float = 1.0) -> None:
    """Cross + arm highlights, from DpadZone.Build()."""
    _deco(cr, scale, alpha, w, h)
    s = min(w, h) / DPAD_BASE                  # the figure scales with the control
    cx, cy = w / 2.0, h / 2.0

    if mode == 8:
        ring = DPAD_RING_SIZE * s
        cr.arc(cx, cy, ring / 2.0, 0.0, 6.28319)
        cr.set_source_rgba(*DPAD_RING_FILL)
        cr.fill_preserve()
        cr.set_line_width(DPAD_RING_STROKE_W * s)
        cr.set_source_rgba(*DPAD_RING_STROKE)
        cr.stroke()

    pts = cross_polygon()
    ox, oy = cx - DPAD_BASE * s / 2.0, cy - DPAD_BASE * s / 2.0
    cr.move_to(ox + pts[0][0] * s, oy + pts[0][1] * s)
    for x, y in pts[1:]:
        cr.line_to(ox + x * s, oy + y * s)
    cr.close_path()
    cr.set_source_rgba(*argb(DPAD_FILL))
    cr.fill_preserve()
    cr.set_line_width(STROKE_WIDTH)
    cr.set_source_rgba(*DPAD_STROKE)
    cr.stroke()

    for key, (rx, ry, rw, rh) in arm_rects().items():
        level = (arms or {}).get(key, 0.0)
        if level <= 0.0:
            continue
        x, y, ww, hh = ox + rx * s, oy + ry * s, rw * s, rh * s
        r = DPAD_ARM_RADIUS * s
        cr.new_sub_path()
        cr.arc(x + ww - r, y + r, r, -1.5708, 0.0)
        cr.arc(x + ww - r, y + hh - r, r, 0.0, 1.5708)
        cr.arc(x + r, y + hh - r, r, 1.5708, 3.14159)
        cr.arc(x + r, y + r, r, 3.14159, 4.71239)
        cr.close_path()
        # DpadZone lights an arm by setting the element's Opacity (1.0 cardinal,
        # 0.7 diagonal) over a fill that is itself 100/255, so the two multiply.
        ar, ag, ab, aa = DPAD_ARM_FILL
        cr.set_source_rgba(ar, ag, ab, aa * level)
        cr.fill()

    _finish(cr, alpha)


def paint_fab(cr, size: float, fill: str, glyph: str,
              scale: float = 1.0, alpha: float = 1.0) -> None:
    """FabBtn: 44x44 circle, CornerRadius 22, white glyph at 18px."""
    _deco(cr, scale, alpha, size, size)
    cr.arc(size / 2.0, size / 2.0, size / 2.0, 0.0, 6.28319)
    cr.set_source_rgba(*argb(fill))
    cr.fill()
    _text(cr, glyph, FAB_FONT, (1, 1, 1, 1), size / 2.0, size / 2.0)
    _finish(cr, alpha)


def paint_panel_pill(cr, w: float, h: float, fill: str, label: str,
                     scale: float = 1.0, alpha: float = 1.0) -> None:
    """PillBtn: CornerRadius 8, white 12px label."""
    _deco(cr, scale, alpha, w, h)
    r = min(PILLBTN_RADIUS, h / 2.0)
    cr.new_sub_path()
    cr.arc(w - r, r, r, -1.5708, 0.0)
    cr.arc(w - r, h - r, r, 0.0, 1.5708)
    cr.arc(r, h - r, r, 1.5708, 3.14159)
    cr.arc(r, r, r, 3.14159, 4.71239)
    cr.close_path()
    cr.set_source_rgba(*argb(fill))
    cr.fill()
    _text(cr, label, PILLBTN_FONT, (1, 1, 1, 1), w / 2.0, h / 2.0)
    _finish(cr, alpha)


def paint_panel_bg(cr, w: float, h: float, fill: str = PANEL_BG,
                   radius: float = PANEL_RADIUS, alpha: float = 1.0) -> None:
    r = radius
    cr.new_sub_path()
    cr.arc(w - r, r, r, -1.5708, 0.0)
    cr.arc(w - r, h - r, r, 0.0, 1.5708)
    cr.arc(r, h - r, r, 1.5708, 3.14159)
    cr.arc(r, r, r, 3.14159, 4.71239)
    cr.close_path()
    r0, g0, b0, a0 = argb(fill)
    # fill(), NOT paint_with_alpha(): paint() ignores the current path and floods
    # the whole surface, which silently overpaints every control drawn after it.
    cr.set_source_rgba(r0, g0, b0, a0 * alpha)
    cr.fill()


# -- hub states and panels -------------------------------------------------
# Ported from glasspad/window.py, which is itself a port of MainWindow.xaml.
# These live here rather than in overlay_layer so the two backends can be checked
# against one another for agreement.

#: The disable button's latched-off colour. window.py spells both states as
#: #AARRGGBB strings; the enabled one is HUB_BUTTONS["disable"][0] (#CCE07030).
DISABLED_COLOR = "#CC55606E"

#: The two wordings, kept because a hidden overlay reads as a crash unless the one
#: button still on screen says why it is alone.
TOOLTIP_ENABLED = "Hide the overlay (click again to bring it back)"
TOOLTIP_DISABLED = "Overlay hidden - click to bring the controls back"

#: EditPanel top: 8 hub margin + 200 hub canvas + 4 gap (WPF Margin="0,212,0,0").
EDIT_PANEL_TOP = 212.0

INFO_BG = "#F01a1a2e"
INFO_RADIUS = 16.0
INFO_PADDING = (36.0, 28.0)
INFO_MIN_WIDTH = 320.0
CONFIRM_PADDING = (28.0, 22.0)
CONFIRM_MIN_WIDTH = 280.0
CONFIRM_BODY_COLOR = "#AAC0D0FF"

PANEL_GREY = "#CC404055"
PANEL_GREY_DIM = "#80406090"
