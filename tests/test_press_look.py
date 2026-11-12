#!/usr/bin/env python3
"""Offscreen checks for the button press treatment in glasspad/layer_press.py.

The bug these encode: a control is its own layer surface sized exactly to its frame,
so a press that grows the shape past the frame does not overflow - it is clipped,
and a circle pressed to 1.08 comes out with four flat sides. Measured on the real
compositor: coverage 0.884 of the frame with ink 1.09 of the way to the corner,
where an unpressed circle is pi/4 = 0.785.

Run directly: python3 tests/test_press_look.py
"""
from __future__ import annotations

import math
import os
import sys

import cairo
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import layer_press as LP  # noqa: E402

DIA = 75            # the A/B/X/Y buttons in the default layout, logical px
S = 2               # measurement scale


def render(shape, mode, press, *, fill="#963070A0", label="A", size=None):
    """Paint one control and return (straight-alpha RGBA mask, composited RGB)."""
    w = h = DIA
    if shape == "pill":
        w, h = 110, 48
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(w * S), int(h * S))
    cr = cairo.Context(surf)
    cr.scale(S, S)
    LP.paint(cr, w, h, shape, fill, label, mode=mode, press=press, text_size=size)
    surf.flush()
    buf = surf.get_data()
    W = int(w * S)
    rgba = bytearray(len(buf))
    for i in range(0, len(buf), 4):
        b, g, r, a = buf[i], buf[i + 1], buf[i + 2], buf[i + 3]
        if a:
            rgba[i] = min(255, int(r * 255 / a))
            rgba[i + 1] = min(255, int(g * 255 / a))
            rgba[i + 2] = min(255, int(b * 255 / a))
        rgba[i + 3] = a
    img = Image.frombytes("RGBA", (W, int(h * S)), bytes(rgba))
    back = Image.new("RGB", img.size, (110, 118, 128))     # a mid-tone game pixel
    return img, Image.alpha_composite(back.convert("RGBA"), img).convert("RGB")


def ink(img):
    a = img.getchannel("A").load()
    return sum(1 for y in range(img.height) for x in range(img.width) if a[x, y] > 8)


def alpha_at(img, lx, ly):
    return img.getchannel("A").getpixel((int(lx * S), int(ly * S)))


def coverage(img):
    return ink(img) / (img.width * img.height)


def max_radius_fraction(img):
    """How far the ink reaches toward the frame corner, 1.0 == the corner itself."""
    cx, cy = img.width / 2.0, img.height / 2.0
    lim = math.hypot(cx, cy)
    a = img.getchannel("A").load()
    best = 0.0
    for y in range(0, img.height, 2):
        for x in range(0, img.width, 2):
            if a[x, y] > 8:
                best = max(best, math.hypot(x - cx, y - cy) / lim)
    return best


def band_luma(rgb, yfrac):
    """Mean luminance across the shape's middle column at a given height fraction."""
    px = rgb.load()
    x = rgb.width // 2
    ys = [int(rgb.height * (yfrac + d)) for d in (-2, -1, 0, 1, 2)]
    vals = [sum(px[x, y]) / 3.0 for y in ys if 0 <= y < rgb.height]
    return sum(vals) / max(len(vals), 1)


def test_nothing_is_painted_outside_the_frame():
    for mode in LP.MODES:
        for shape in ("circle", "pill"):
            for press in (0.0, 0.5, 1.0):
                img, _ = render(shape, mode, press)
                edge = max(alpha_at(img, 0, y / S) for y in range(0, img.height, 4))
                assert edge >= 0                     # sanity: the mask is readable
                a = img.getchannel("A").load()
                outside = sum(1 for y in range(img.height) for x in range(img.width)
                              if a[x, y] > 8 and not (0 <= x < img.width))
                assert outside == 0, f"{mode}/{shape} painted outside its pixels"


def test_a_pressed_circle_stays_a_circle():
    """The regression test. A circle is pi/4 of its square; a clipped one is more."""
    rest = coverage(render("circle", "bevel", 0.0)[0])
    pressed = coverage(render("circle", "bevel", 1.0)[0])
    assert 0.72 < rest < 0.85, f"rest circle is not round-looking ({rest:.3f})"
    assert pressed < rest + 0.01, (
        f"bevel press must not add area; it went to {pressed:.3f}")
    assert pressed > 0.60, f"bevel press shrank the button too far ({pressed:.3f})"
    assert max_radius_fraction(render("circle", "bevel", 1.0)[0]) <= \
        max_radius_fraction(render("circle", "bevel", 0.0)[0]) + 0.005, \
        "bevel press reached further into the frame than rest"


def test_outset_is_the_block_and_is_documented_as_such():
    """If someone flips the default back to outset, this test should make them think."""
    outset = coverage(render("circle", "outset", 1.0)[0])
    circle = coverage(render("circle", "outset", 0.0)[0])
    assert outset > circle + 0.05, (
        f"outset no longer grows the shape; is the clipping still happening? "
        f"({circle:.3f} -> {outset:.3f})")
    assert outset > math.pi / 4 + 0.05, (
        f"a clipped circle reads as a block ({outset:.3f} vs pi/4 = "
        f"{math.pi / 4:.3f}) - keep this as the counter-example")


def test_bevel_reads_raised_then_pushed_in():
    """Light along the top at rest, shade along the top when held: the flip is the cue."""
    _img, rest = render("circle", "bevel", 0.0)
    _img, held = render("circle", "bevel", 1.0)
    assert band_luma(rest, 0.24) > band_luma(rest, 0.76), "rest should be top-lit"
    assert band_luma(held, 0.24) < band_luma(held, 0.76), "held should be top-shaded"


def test_glow_moves_nothing_and_brightens_something():
    rest_img, rest_rgb = render("circle", "glow", 0.0)
    held_img, held_rgb = render("circle", "glow", 1.0)
    assert rest_img.size == held_img.size
    assert abs(coverage(rest_img) - coverage(held_img)) < 0.012, (
        "glow changed the shape's footprint")
    rp, hp = rest_rgb.load(), held_rgb.load()
    x, y = rest_rgb.width // 2, rest_rgb.height // 2
    assert sum(hp[x, y]) > sum(rp[x, y]), "glow did not brighten the centre"


def test_press_is_a_ramp_not_a_switch():
    """Half-held must sit between rest and held, or the animation cannot exist."""
    areas = [coverage(render("circle", "bevel", p)[0]) for p in (0.0, 0.5, 1.0)]
    assert areas[0] > areas[1] > areas[2], f"bevel press is not monotonic: {areas}"
    rims = [band_luma(render("circle", "bevel", p)[1], 0.24) for p in (0.0, 0.5, 1.0)]
    assert rims[0] > rims[1] > rims[2], f"shading does not interpolate: {rims}"


def test_every_mode_paints_every_shape_with_and_without_a_label():
    for mode in LP.MODES:
        for shape in ("circle", "pill"):
            for label in ("A", "", "⊟"):
                for press in (0.0, 1.0):
                    img, _ = render(shape, mode, press, label=label)
                    assert ink(img) > 500, f"{mode}/{shape}/{label!r} painted nothing"


def test_press_scale_and_fit_are_consistent():
    assert LP.press_scale("bevel", 0.0) == 1.0
    assert LP.press_scale("bevel", 1.0) < 1.0, "bevel must go inward"
    assert LP.press_scale("glow", 1.0) == 1.0
    assert LP.press_scale("nonsense", 1.0) == LP.press_scale("bevel", 1.0)
    # a growing press fits the frame exactly when it starts at 1/peak
    assert abs(LP.press_scale("outset", 1.0) * LP.fit(1.0, 1.08) - 1.0) < 1e-9


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok    {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
    print(f"\n  {len(tests) - failed} passed, {failed} failed of {len(tests)}")
    sys.exit(1 if failed else 0)
