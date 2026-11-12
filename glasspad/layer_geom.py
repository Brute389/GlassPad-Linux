"""Pure geometry for the layer-shell overlay: no GTK, no Qt, no display.

Split out of overlay_layer.py for the reason xd360.py is split out: these rules
are the part worth asserting, and importing either toolkit into a test run
brings its event loop and its display assumptions along with it.
"""
from __future__ import annotations

import json
import math
import shutil

from glasspad.layer_stick import (  # noqa: E402
    from_dict as stick_from_dict,
    to_dict as stick_to_dict,
)
import os
from dataclasses import dataclass

from glasspad import layer_style as S

# Sizes the app uses, reached through layer_style so "the d-pad is 180 px" is
# stated exactly once. layer_style imports nothing from here, so there is no cycle.
DPAD_BASE = S.DPAD_BASE
STICK_OUTER = S.STICK_OUTER

# Control kinds: `button` is a named EV_KEY, `trigger` an axis, `stick` a
# normalised vector, `dpad` a hat direction.
BUTTON, TRIGGER, STICK, DPAD = "button", "trigger", "stick", "dpad"


@dataclass(frozen=True)
class Frame:
    """One control's rectangle, in logical surface coordinates."""

    name: str
    kind: str
    x: float
    y: float
    w: float
    h: float


#: layout.json stores positions as a bare array with no names attached, so the
#: index IS the identity. This tuple mirrors layout.GamepadLayout.all_editable
#: -- buttons in build() order, then stick elements in build() order -- and
#: test_element_order_matches_the_qt_layout() is what keeps it true. Note that
#: dpad belongs to the stick list, not the button list: dpad_at() appends to
#: _stick_elements, which is easy to get wrong and silent when you do.
ELEMENT_ORDER: tuple[tuple[str, str, str], ...] = (
    ("LT", TRIGGER, "LT"),
    ("RT", TRIGGER, "RT"),
    ("LB", BUTTON, "LB"),
    ("RB", BUTTON, "RB"),
    ("Y", BUTTON, "Y"),
    ("X", BUTTON, "X"),
    ("B", BUTTON, "B"),
    ("A", BUTTON, "A"),
    ("L3", BUTTON, "L3"),
    ("R3", BUTTON, "R3"),
    ("BACK", BUTTON, "\u229f"),
    ("GUIDE", BUTTON, "\u2299"),
    ("START", BUTTON, "\u229e"),
    ("dpad", DPAD, "dpad"),
    ("LS", STICK, "LS"),
    ("RS", STICK, "RS"),
)


_LABELS = {name: tag for name, _kind, tag in ELEMENT_ORDER}

#: Controls the app draws as pills. LT/RT are pills and so are LB/RB -- but LB/RB
#: are ordinary EV_KEY buttons, so their input `kind` is BUTTON. Shape and kind are
#: separate taxonomies; conflating them drew both shoulder buttons as circles.
PILL_NAMES = frozenset({"LT", "RT", "LB", "RB"})


def render_shape(name: str, kind: str) -> str:
    """"pill" | "circle" | "stick" | "dpad": the figure GamepadLayout builds.

    GamepadLayout.Build() calls Pill() for LT/RT/LB/RB and Circle() for the rest,
    so the shoulder buttons are rounded bars even though they are plain buttons as
    far as the gamepad device is concerned.
    """
    if kind == STICK:
        return "stick"
    if kind == DPAD:
        return "dpad"
    return "pill" if name in PILL_NAMES else "circle"


#: Labels to paint, where the app's own glyph does not survive the disc.
#
# \u229f and \u229e are SQUARED MINUS and SQUARED PLUS -- a bar/plus inside a box. On Windows
# they sit on a round button too, but at 52 px on this panel the inner box turns to
# mud and reads as "two circles, one of them busy" rather than minus and plus. The
# identity stays the app's glyph (layout.json and the Qt backend both use it); this
# only changes what the painter strokes.
PAINT_LABELS = {"BACK": "\u2212", "START": "+"}

#: Label size overrides, as a fraction of the control's diameter.
#:
#: The app draws circle labels at 0.32, which suits a letter. A lone minus at that
#: size measures 11 x 2 px and vanishes, which would defeat the point of dropping the
#: boxed glyph. Measured in a 52 px disc: 0.44 -> 11x2 (still a hairline), 0.55 ->
#: 14x3, 0.62 -> 15x4 with the plus at 16x16 and the glyph reaching only 9.5 px from
#: centre against a ~24 px inner radius. So 0.62, and GUIDE keeps 0.32 because a
#: letter-sized \u2299 is already legible.
LABEL_SIZE_OVERRIDES = {"BACK": 0.62, "START": 0.62}


def label_size(name: str, dia: float, default_ratio: float) -> float:
    """Font size in logical px for a control's label."""
    return dia * LABEL_SIZE_OVERRIDES.get(name, default_ratio)


def label_for(name: str) -> str:
    """What to print on a control.

    Falls back to the app's glyph from ELEMENT_ORDER, so nothing has to know the
    list: A/B/X/Y and the shoulder labels are already the app's own text, and GUIDE
    keeps \u2299 because a dot-in-a-ring is still legible inside a disc.
    """
    return PAINT_LABELS.get(name, _LABELS.get(name, name))


# NB: label_for() and label_size() are the display side only. layout.json keeps
# addressing controls by index in ELEMENT_ORDER, and ELEMENT_ORDER keeps the app's
# own glyphs, so this stays a painting decision rather than a data-model one.


def default_frames(sw: int, sh: int) -> list[Frame]:
    """Geometry mirroring layout.GamepadLayout.build(sw, sh): left cluster
    anchored on the left edge, right cluster mirrored from the right."""
    rx = lambda inset: float(sw - inset)  # noqa: E731
    # Listed in ELEMENT_ORDER, not in visual order: layout.json addresses
    # controls by index, and write_frames() asserts the two agree. Getting this
    # wrong is exactly the silent-reassignment failure load_frames() refuses to
    # tolerate in someone else's file -- so it is refused in ours too.
    # Transcribed from GamepadLayout.Build(w, h) in the Windows client, which
    # glasspad/layout.py already mirrors: give the centre the app used, then subtract
    # the radius. Sizes come from layer_style rather than being invented here.
    #
    # An earlier revision of this function had 12 of these 16 wrong (LT at (30,22)
    # instead of (40,35), the face cluster 100 px too far right, L3/R3 and the centre
    # cluster misplaced, and 96 px d-pad/sticks against the app's 180/119). Nothing
    # caught it; test_default_geometry_matches_the_qt_layout does now.
    d = DPAD_BASE
    s = STICK_OUTER
    fd = 83.0                                  # face cluster centre-to-centre
    fcx, fcy = sw - 175.0, sh * 0.52           # face cluster centre

    def circle(cx, cy, dia):
        return (cx - dia / 2.0, cy - dia / 2.0, dia, dia)

    raw = [
        ("LT", TRIGGER, 40, 35, 110, 55),
        ("RT", TRIGGER, rx(150), 35, 110, 55),
        ("LB", BUTTON, 40, 100, 120, 48),
        ("RB", BUTTON, rx(160), 100, 120, 48),
        ("Y", BUTTON, *circle(fcx, fcy - fd, 75)),
        ("X", BUTTON, *circle(fcx - fd, fcy, 75)),
        ("B", BUTTON, *circle(fcx + fd, fcy, 75)),
        ("A", BUTTON, *circle(fcx, fcy + fd, 75)),
        ("L3", BUTTON, *circle(190, 124, 44)),
        ("R3", BUTTON, *circle(rx(190), 124, 44)),
        ("BACK", BUTTON, *circle(sw / 2.0 - 115, sh * 0.90, 52)),
        ("GUIDE", BUTTON, *circle(sw / 2.0, sh * 0.88, 62)),
        ("START", BUTTON, *circle(sw / 2.0 + 115, sh * 0.90, 52)),
        ("dpad", DPAD, *circle(175, sh * 0.52, d)),
        ("LS", STICK, *circle(260, sh * 0.78, s)),
        ("RS", STICK, *circle(rx(300), sh * 0.78, s)),
    ]
    return [Frame(name, kind, x, y, w, h) for name, kind, x, y, w, h in raw]


def default_layout_path() -> str:
    """window.layout_path(), duplicated rather than imported: importing window
    drags PyQt6 into a process that is trying not to have two toolkits."""
    base = os.environ.get(
        "XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")
    )
    return os.path.join(base, "GlassPad", "layout.json")


def load_frames(path: str, sw: int, sh: int) -> tuple[list[Frame], float, int, str]:
    """Return (frames, opacity, dpad_mode, note); never raises.

    Refusing is the important path. Because the JSON has no names, a file with
    the wrong element count would otherwise be accepted and every control would
    quietly receive some other control's rectangle -- a layout that looks loaded
    and plays wrong. Same for NaN or zero-size entries.
    """
    fallback, opacity, dpad_mode = default_frames(sw, sh), 0.85, 4
    if not os.path.exists(path):
        return fallback, opacity, dpad_mode, f"no {path}; built-in defaults"
    try:
        with open(path, encoding="utf-8") as fh:
            root = json.load(fh)
    except (OSError, ValueError) as exc:
        return fallback, opacity, dpad_mode, f"{path} unreadable ({exc}); defaults"
    elements = root.get("elements")
    if not isinstance(elements, list):
        return fallback, opacity, dpad_mode, f"{path} has no elements array; defaults"
    if len(elements) != len(ELEMENT_ORDER):
        return fallback, opacity, dpad_mode, (
            f"{path} has {len(elements)} elements, expected {len(ELEMENT_ORDER)}; "
            "positions are stored by index, so loading it would reassign every "
            "control -- refusing, using defaults"
        )
    frames: list[Frame] = []
    for (name, kind, _tag), el in zip(ELEMENT_ORDER, elements):
        try:
            x, y = float(el["left"]), float(el["top"])
            w, h = float(el["width"]), float(el["height"])
        except (KeyError, TypeError, ValueError):
            return fallback, opacity, dpad_mode, f"{path}: element '{name}' malformed; defaults"
        if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
            return fallback, opacity, dpad_mode, (
                f"{path}: element '{name}' has non-finite or zero geometry; defaults"
            )
        frames.append(Frame(name, kind, x, y, w, h))
    try:
        opacity = min(1.0, max(0.05, float(root.get("opacity", 0.85))))
    except (TypeError, ValueError):
        opacity = 0.85
    dpad_mode = 8 if root.get("dpadMode") == 8 else 4
    return frames, opacity, dpad_mode, f"loaded {len(frames)} controls from {path}"


#: Previous layouts kept, newest first as layout.json.bak1 .. .bak3.
BACKUPS_KEPT = 3


def rotate_backups(path: str, keep: int = BACKUPS_KEPT) -> None:
    """Shift layout.json.bak1 -> .bak2 -> ... and copy the current file into .bak1.

    Copying rather than renaming the live file into slot 1 is deliberate: renaming
    leaves a window in which the layout does not exist at all, which trades a
    half-written file for a missing one.
    """
    if keep <= 0 or not os.path.exists(path):
        return
    for i in range(keep - 1, 0, -1):
        src, dst = f"{path}.bak{i}", f"{path}.bak{i + 1}"
        if os.path.exists(src):
            try:
                os.replace(src, dst)
            except OSError:
                break                        # read-only dir, or .bak3 is someone's file
    try:
        shutil.copyfile(path, f"{path}.bak1")
    except OSError:
        pass                                 # backups are a courtesy; the save is not


def atomic_write_json(path: str, root: dict, keep: int = BACKUPS_KEPT) -> None:
    """Write a sibling, fsync it, then rename over the target.

    ``open(path, "w")`` truncates *before* a single byte of the new content exists: a
    crash, a full disk, or a pulled power cable mid-write leaves an empty or half file
    where an hour of hand-placing a layout used to be, and the next launch loads
    defaults and looks like it forgot. With a rename the target is either the old
    layout or the new one, never neither -- os.replace is atomic on btrfs and ext4.

    The backup rotation is for the failure that is not a crash but the regret. Without
    it, one accidental save over a good layout is unrecoverable.

    `keep=0` opts a file out. The profile claim is written every time a game starts and
    every time the hub cycles; rotating it would push the user's layout backups out of
    the rotation in order to protect a value that is cheap to retype.
    """
    folder = os.path.dirname(path) or "."
    tmp = os.path.join(folder, f".{os.path.basename(path)}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(root, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    rotate_backups(path, keep)
    try:
        os.replace(tmp, path)
    except OSError:
        try:                                 # never leave a stray sibling behind
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_frames(path: str, frames: list[Frame], opacity: float = 0.85,
                 dpad_mode: int = 4,
                 screen: tuple[float, float] | None = None,
                 turbo=None, turbo_hz: float | None = None,
                 stick=None) -> None:
    """Write the Windows-compatible schema, same element order as ELEMENT_ORDER.

    ``screen`` records the screen these rectangles were laid out on, which is the
    only way a layout can survive a rotation or a monitor swap: the numbers are
    absolute pixels, and without knowing what they were absolute *pixels of* there is
    nothing honest to do with them on a different display. The Windows client stores
    no such field, so this is an addition rather than a translation, and readers that
    do not know it simply ignore it.
    """
    names = [f.name for f in frames]
    want = [name for name, _k, _t in ELEMENT_ORDER]
    if names != want:
        raise ValueError(f"frames must be in ELEMENT_ORDER; got {names}")
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    root = {
        "opacity": float(opacity),
        "dpadMode": int(dpad_mode),
        "elements": [_element_entry(f, turbo) for f in frames],
    }
    if screen is not None:
        root["screenWidth"] = int(round(screen[0]))
        root["screenHeight"] = int(round(screen[1]))
    if turbo_hz is not None:
        root["turboHz"] = float(turbo_hz)
    if stick is not None:
        root["stickTuning"] = stick_to_dict(stick)
    # One write per save. An earlier revision wrote the layout and then reopened it to
    # add the turbo rate, doubling the window in which a crash could eat someone's
    # layout for the sake of one float.
    atomic_write_json(path, root)


#: How close to an edge a control has to sit in the authored layout to be treated as
#: anchored to that edge when the layout is fitted to a different screen. Half a
#: thumb: a control within this of the border is there deliberately, and should stay
#: against that border whatever the screen does.
EDGE_ANCHOR = 48.0


def fit_frames(frames: list[Frame], from_w: float, from_h: float,
               to_w: float, to_h: float,
               edge: float = EDGE_ANCHOR) -> list[Frame]:
    """Refit a layout authored for one screen onto another, keeping it usable.

    Two rules, both learned from what breaks:

    * **Sizes scale uniformly** (``min`` of the two axis ratios). Non-uniform scaling
      turns buttons into ellipses and stretches the d-pad cross; the round hit region
      and the drawn figure would disagree with the pad it drives. Uniform scaling
      keeps the aspect of everything, which is what edit mode does too.
    * **Positions keep their intent.** A control that sat against an edge stays
      against that edge, because it was put there to be reachable from the frame of
      the device. Anything else keeps the fraction of the span its centre sat at.

    The design size is always the *authored* one, never the last fit: fitting from
    base each time makes the operation idempotent, so rotating to portrait and back
    returns the layout exactly as it was saved rather than drifting by a rounding
    step per rotation.

    Nothing is ever left off screen: a control wider than the destination (a 180 px
    d-pad on a 400 px wide screen is fine, one on a 100 px screen is not) is clamped
    back inside, which can overlap its neighbours but cannot hide them.
    """
    if from_w <= 0 or from_h <= 0 or to_w <= 0 or to_h <= 0:
        return list(frames)
    if abs(from_w - to_w) < 0.5 and abs(from_h - to_h) < 0.5:
        return list(frames)                    # identity, exactly

    scale = min(to_w / from_w, to_h / from_h)

    out: list[Frame] = []
    for f in frames:
        w, h = f.w * scale, f.h * scale
        if f.x <= edge:
            x = f.x * scale
        elif from_w - (f.x + f.w) <= edge:
            x = to_w - w - (from_w - (f.x + f.w)) * scale
        else:
            x = ((f.x + f.w / 2.0) / from_w) * to_w - w / 2.0
        if f.y <= edge:
            y = f.y * scale
        elif from_h - (f.y + f.h) <= edge:
            y = to_h - h - (from_h - (f.y + f.h)) * scale
        else:
            y = ((f.y + f.h / 2.0) / from_h) * to_h - h / 2.0
        x = min(max(0.0, x), max(0.0, to_w - w))
        y = min(max(0.0, y), max(0.0, to_h - h))
        out.append(Frame(f.name, f.kind, x, y, w, h))
    return out


def _element_entry(f: Frame, turbo) -> dict:
    """One element as the file stores it.

    Turbo is written only when armed, so a layout without any turbo stays exactly the
    file an earlier build would have written -- including for the Windows client,
    which ignores keys it does not know but preserves them on its own saves.
    """
    entry = {"left": f.x, "top": f.y, "width": f.w, "height": f.h}
    if turbo and f.name in turbo:
        entry["turbo"] = True
    return entry


def load_stick_tuning(path: str):
    """The saved stick response, or the original's own curve when the file has none."""
    try:
        with open(path, encoding="utf-8") as fh:
            root = json.load(fh)
    except (OSError, ValueError):
        return stick_from_dict(None)
    return stick_from_dict(root.get("stickTuning") if isinstance(root, dict) else None)


def load_turbo_hz(path: str) -> float | None:
    """The saved autofire rate, or None when the file has no opinion."""
    try:
        with open(path, encoding="utf-8") as fh:
            root = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(root, dict):
        return None
    try:
        return float(root["turboHz"])
    except (KeyError, TypeError, ValueError):
        return None


def load_turbo(path: str) -> set[str]:
    """Which controls are armed for autofire.

    An absent flag means off, which is what makes files written before turbo existed
    load as "no turbo" rather than as an error.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            root = json.load(fh)
    except (OSError, ValueError):
        return set()
    elements = root.get("elements") if isinstance(root, dict) else None
    if not isinstance(elements, list):
        return set()
    out: set[str] = set()
    for (name, _kind, _tag), el in zip(ELEMENT_ORDER, elements):
        if isinstance(el, dict) and el.get("turbo"):
            out.add(name)
    return out


def load_screen_size(path: str) -> tuple[int, int] | None:
    """The screen a layout file was authored for, if it recorded one.

    ``None`` means the file predates this field (or the app that wrote it never
    knew): the geometry is then taken at face value for whatever screen is up now,
    which is exactly how these files behaved before, with no surprise rescaling of
    somebody's hand-placed pad.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            root = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(root, dict):
        return None
    try:
        w, h = int(root["screenWidth"]), int(root["screenHeight"])
    except (KeyError, TypeError, ValueError):
        return None
    return (w, h) if w > 0 and h > 0 else None


def clamp_travel(dx: float, dy: float, limit: float) -> tuple[float, float, float]:
    """Clamp a stick offset to `limit`, returning (dx, dy, norm) with norm in 0..1.

    This is StickZone's rule -- translate, then scale back onto the circle if the
    distance exceeds MaxTravel -- expressed as pure geometry so the clamp itself is
    testable. `norm` is the unclamped magnitude divided by the limit, which is what
    the d-pad/stick code wants for deflection, and it is kept unclamped so a caller
    can still tell how far past the stop the finger went.
    """
    dist = math.hypot(dx, dy)
    if dist <= 0.0:
        return 0.0, 0.0, 0.0
    norm = dist / limit
    if dist > limit:
        k = limit / dist
        return dx * k, dy * k, norm
    return dx, dy, norm


def circle_region(w: int, h: int, inset: float = 2.0):
    """A pycairo Region covering a disc, for Gdk.Surface.set_input_region.

    Why this exists: the controls are circles painted on square surfaces. A tap
    is routed by the compositor from the surface input region, not from what was
    painted, and the default region is the whole square. Measured with
    tools/hit_test.py: a tap 97 px from the centre of a 78 px circle was taken by
    the circle's surface, while a tap just outside the square fell through to the
    surface underneath.

    Rows of 1px spans, unioned. pycairo's Region.union mutates in place and
    returns None, so it must not be re-bound -- `region = region.union(...)`
    silently leaves `region` as None.
    """
    import cairo

    region = cairo.Region()
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    radius = min(w, h) / 2.0 - inset
    if radius <= 0:
        return region
    for y in range(h):
        dy = y - cy
        if abs(dy) > radius:
            continue
        half = math.sqrt(radius * radius - dy * dy)
        x0 = max(0, int(math.floor(cx - half)))
        x1 = min(w - 1, int(math.ceil(cx + half)))
        if x1 >= x0:
            region.union(cairo.Region(cairo.RectangleInt(x0, y, x1 - x0 + 1, 1)))
    return region


def rounded_rect_region(w: int, h: int, radius: float | None = None):
    """A disc-capped pill: the shape layout.py's Pill() draws. Used for LT/RT."""
    import cairo

    region = cairo.Region()
    r = radius if radius is not None else min(w, h) / 2.0
    r = max(1.0, min(r, min(w, h) / 2.0))
    for y in range(h):
        dy = y - (h - 1) / 2.0
        if abs(dy) <= max(0.0, h / 2.0 - r):
            x0, x1 = 0, w - 1                      # middle band: full width
        else:
            inside = r * r - (abs(dy) - (h / 2.0 - r)) ** 2
            if inside <= 0:
                continue
            half = math.sqrt(inside)
            cx = (w - 1) / 2.0
            x0 = max(0, int(math.floor(cx - half)))
            x1 = min(w - 1, int(math.ceil(cx + half)))
        if x1 >= x0:
            region.union(cairo.Region(cairo.RectangleInt(x0, y, x1 - x0 + 1, 1)))
    return region


def rect_region(w: int, h: int):
    """Whole square -- what the d-pad uses, since it is genuinely a square."""
    import cairo

    return cairo.Region(cairo.RectangleInt(0, 0, w, h))


def clamp_zone(value: float) -> float:
    return max(-1.0, min(1.0, value))


def stick_deflection(x: float, y: float, cx: float, cy: float,
                     w: float, h: float) -> tuple[float, float]:
    """Deflection in ZONE space: -1..1, resting value 0.

    This is the trap that made the first in-game test unusable. The compositor
    hands positions in 0..1-of-surface terms, and PadService.set_left_stick takes
    *zone* space -- its docstring's `to_hm` maps -1..1 onto the device's 0..1.
    Feeding zone setters the 0..1 value quietly yields to_hm(0..1) = 0.5..1.0, so
    the negative half of the axis becomes unreachable and a "released" stick is
    parked at to_hm(0.5) = half deflection instead of at centre.
    """
    return clamp_zone((x - cx) / (w / 2.0)), clamp_zone((y - cy) / (h / 2.0))


def inside_shape(kind: str, x: float, y: float, w: float, h: float,
                 inset: float = 2.0) -> bool:
    """Is this press inside the shape the control is drawn as?

    The compositor-level answer (set_input_region) was measured to be inert on
    layer surfaces: two runs, one of them blinded, a tap 102 px from a 78 px disc
    still taken by the circle's surface. This is the part that is still ours to
    decide. It cannot give the tap back to the game -- the surface is still on
    top -- but it stops a thumb landing on a round button's corner from firing
    the button, which is the part that reads as broken rather than as blocked.
    """
    if not (0.0 <= x < w and 0.0 <= y < h):
        return False
    if kind == DPAD:
        return True                      # a d-pad is genuinely a square
    if kind == TRIGGER:
        r = min(w, h) / 2.0
        dx = max(0.0, abs(x - (w - 1) / 2.0) - (w / 2.0 - r))
        dy = max(0.0, abs(y - (h - 1) / 2.0) - (h / 2.0 - r))
        return dx * dx + dy * dy <= r * r
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    radius = max(0.0, min(w, h) / 2.0 - inset)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


def to_hat8(angle: float) -> str:
    """8-way sectors, copied from zones.to_hat8 (45-degree, centred)."""
    norm = ((angle % 360) + 360) % 360
    sector = int((norm + 22.5) / 45) % 8
    return ("E", "SE", "S", "SW", "W", "NW", "N", "NE",)[sector]


def to_hat4(angle: float) -> str:
    """4-way sectors, boundaries copied from zones.to_hat4 (which mirrors C#)."""
    if -135 < angle <= -45:
        return "N"
    if -45 < angle <= 45:
        return "E"
    if 45 < angle <= 135:
        return "S"
    return "W"


_HAT_CENTRE = {"N": -90.0, "E": 0.0, "S": 90.0, "W": 180.0}
_HAT8_CENTRE = {**_HAT_CENTRE,
                "NE": -45.0, "SE": 45.0, "SW": 135.0, "NW": -135.0}


def hat_with_hysteresis(previous: str, angle: float, margin: float | None = None,
                        mode: int = 4) -> str:
    """Sector pick that refuses to switch until the new sector is entered by
    `margin` degrees.

    zones.DpadZone compares against a bare boundary, which is harmless under a
    mouse but chatters under a thumb resting on the cross: an evdev tap on the
    first build measured 87 on/off transitions on a single hat key across a
    handful of presses -- the pad screaming N/NE/N/NE at the game dozens of times
    a second.
    """
    candidate = to_hat8(angle) if mode == 8 else to_hat4(angle)
    if candidate == previous or previous == "None":
        return candidate
    half = 22.5 if mode == 8 else 45.0
    # An 8-way pad has a quarter of the slack to work with, so the dead margin
    # scales with the sector instead of staying a fixed 8 degrees.
    gap = half * 0.18 if margin is None else margin
    centre = (_HAT8_CENTRE if mode == 8 else _HAT_CENTRE)[candidate]
    off = abs(((angle - centre + 180.0) % 360.0) - 180.0)   # distance from centre
    return candidate if off <= half - gap else previous


# -- edit mode: resizing ---------------------------------------------------
#: Smallest a control may be shrunk to, in logical px. 32 sits just above the
#: app's own smallest controls (the 30 px BACK/GUIDE/START pills) so a shrink can
#: never produce a key that is harder to hit than the defaults.
MIN_CONTROL = 32.0

#: The grab knob painted at the bottom-right corner in edit mode, and how much of
#: the corner counts as "the user wants to size this" rather than "move this".
#: A thumb is not precise: the zone is deliberately far bigger than the knob.
GRIP_SIZE = 13.0
GRAB_ZONE = 30.0


def grip_zone(w: float, h: float) -> float:
    """How much of the corner counts as "size this".

    Fixed at 30 px for normal controls, and shrunk with the control below that.
    Without the shrink the minimum size is unusable: a 32 px control with a 30 px
    zone leaves a 2 px corner for moving it, and 12 % of the face is all that is
    left. Half the short edge keeps three quarters of the face for moving while
    still giving a thumb a real target to hit.
    """
    return max(8.0, min(GRAB_ZONE, 0.5 * min(w, h)))


def on_grip(x: float, y: float, w: float, h: float,
            zone: float | None = None) -> bool:
    """Is this touch inside the bottom-right sizing zone?

    One knob, in one corner, on purpose: with grips at every corner the zone would
    overlap much of a small control's face, and a drag meant to move would resize
    instead. That collision is the failure most likely to annoy someone laying out
    sixteen controls, so sizing is the thing you have to aim for.
    """
    if not (0.0 <= x <= w and 0.0 <= y <= h):
        return False
    z = grip_zone(w, h) if zone is None else zone
    return x >= w - z and y >= h - z


def size_from_drag(w: float, h: float, dx: float, dy: float,
                   min_size: float = MIN_CONTROL,
                   max_w: float | None = None,
                   max_h: float | None = None) -> tuple[float, float]:
    """New size after dragging the corner by (dx, dy), proportions kept.

    Aspect is preserved for every control, which is what was asked for and also what
    the painters assume: the stick nub is sized from the ring's diameter and its
    travel limit is derived from the same number, so a free-shaped stick would end
    up with a nub that does not fit its own well.

    The dominant axis wins (max, not mean) so a mostly-horizontal drag still grows
    the control even if the finger wobbles vertically -- a mean makes the control
    feel like it is fighting the finger.
    """
    if w <= 0.0 or h <= 0.0:
        return w, h
    scale = max((w + dx) / w, (h + dy) / h)
    # The minimum is a floor on what you may drag *to*, never a reason to change the
    # control you grabbed. The client's own BACK/GUIDE/START pills are 30 px, below
    # the floor, and clamping them up meant touching the knob resized them before
    # the finger moved -- a layout quietly rewriting itself.
    lo = min(1.0, max(min_size / w, min_size / h))
    hi = float("inf")
    if max_w is not None and max_w > 0.0:
        hi = min(hi, max_w / w)
    if max_h is not None and max_h > 0.0:
        hi = min(hi, max_h / h)
    # the control always fits the range at rest: it is on screen and above the
    # minimum already, so scale == 1.0 is inside [lo, hi] before any drag
    if hi < lo:
        hi = lo
    scale = min(max(scale, lo), hi)
    return w * scale, h * scale


def grip_centre(w: float, h: float, size: float = GRIP_SIZE) -> tuple[float, float]:
    """Where to paint the knob. Inset by its own radius, because the surface is
    exactly the control's frame and a knob centred on the corner would be drawn
    half outside it -- clipped into a quarter circle."""
    r = size / 2.0
    return max(w - r - 1.0, r), max(h - r - 1.0, r)
