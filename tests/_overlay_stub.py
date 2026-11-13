"""One hand-built Overlay for the suites that must not start GTK.

Two suites used to carry their own copy of this fixture, and every new attribute on
Overlay broke both of them in turn -- most recently `lockout` and `edge_margin`, fifteen
failures from a change that was correct. The copies had also drifted: one set `edit`,
the other did not, and neither set `geo`, so the same `_press` was being tested against
two different objects.

There is one fixture here now, and `__getattr__` below turns the next omission into a
pointer at this file rather than an AttributeError in someone else's test.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import overlay_layer as OL  # noqa: E402
from glasspad.layer_edge import DEFAULT_MARGIN  # noqa: E402
from glasspad.layer_lock import Lockout  # noqa: E402
from glasspad.layer_stick import StickTuning  # noqa: E402
from glasspad.layer_turbo import Turbo  # noqa: E402
from glasspad.watchdog import Watchdog  # noqa: E402


class StubOverlay(OL.Overlay):
    """An Overlay with no GTK. A missing attribute says where to put it."""

    def __getattr__(self, name):
        raise AttributeError(
            f"the test stub has no attribute {name!r}. That is almost certainly a real "
            f"Overlay attribute that _press/_release now reads: give it a default in "
            f"tests/_overlay_stub.py so every suite gets it, rather than patching one "
            f"test file.")


class RecordingService:
    """Pad calls as a list of tuples, so a test can assert on what the pad was told."""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    # Event spellings follow what the suites already assert on -- ("press", "A") and
    # ("release", "A") -- because a fixture that renames the events quietly invalidates
    # every assertion that reads them.
    def press_button(self, name):
        self.events.append(("press", name))

    def release_button(self, name):
        self.events.append(("release", name))

    def set_left_trigger(self, v):
        self.events.append(("lt", v))

    def set_right_trigger(self, v):
        self.events.append(("rt", v))

    def set_left_stick(self, x, y):
        self.events.append(("ls", x, y))

    def set_right_stick(self, x, y):
        self.events.append(("rs", x, y))

    def set_dpad(self, hat):
        self.events.append(("hat", hat))

    def reset(self):
        self.events.append(("reset",))


def make_overlay(*, service=None, edit: bool = False, geo=None, hold_limit: float = 8.0,
                 turbo_hz: float = 0.0, armed=(), sw: int = 1707, sh: int = 1067,
                 edge_margin: float = 0.0, opacity: float = 1.0, dpad_mode: int = 4,
                 **overrides):
    """A usable Overlay: real `_press`/`_release`, no windows, no main loop.

    Edge rejection defaults to off here even though the app default is 8 px: a test that
    exercises presses should not have them silently refused unless it asked for that.
    Anything the caller passes in `overrides` is set last, so a suite can replace a
    collaborator without subclassing.
    """
    # __new__ and not StubOverlay(...): Overlay.__init__ builds GTK widgets and needs a
    # live display, which is the whole thing being avoided here.
    ov = StubOverlay.__new__(StubOverlay)
    ov.service = service if service is not None else RecordingService()
    ov.active: dict[str, str] = {}
    ov.lockout = Lockout()
    ov.edge_margin = edge_margin
    ov.geo = geo if geo is not None else {
        "A": [100.0, 100.0, 75.0, 75.0],
        "LS": [200.0, 770.0, 119.0, 119.0],
        "dpad": [0.0, 300.0, 180.0, 180.0],
    }
    ov.sw, ov.sh = sw, sh
    ov.trace = False
    ov.hit_shapes = False
    ov.edit = edit
    ov._dirty = False
    ov._ignored: set[str] = set()
    ov.vis: dict[str, dict] = {}
    ov._anim_gen: dict[str, int] = {}
    ov._start: dict[str, tuple[float, float]] = {}
    ov._grab: dict[str, dict] = {}
    ov._sizing = None
    ov.areas: dict[str, object] = {}
    ov.surfaces: dict[str, object] = {}
    ov.place = lambda win, x, y: None
    ov._resize_surface = lambda name, w, h: None
    ov.sayings: list[str] = []
    ov.say_keys: list[str] = []
    ov._say = ov.sayings.append

    def _quiet(text: str, key: str = "") -> None:
        """Mirror Overlay._say_quiet's signature, keyword included.

        This used to be `ov.sayings.append`, which took one positional argument -- so when
        _say_quiet grew `key=` for rate limiting, every keyed call blew up with
        "list.append() takes no keyword arguments" inside the turbo suite, where nothing
        about turbo had changed. A stub has to track the signature it stands in for; that
        is the whole reason it exists.
        """
        ov.sayings.append(text)
        ov.say_keys.append(key)

    ov._say_quiet = _quiet
    # Visual and animation side effects, without the GLib timers behind them.
    ov._count = lambda: None
    ov._vis = lambda name, key, value: None
    ov._ease_press = lambda name: None
    ov._light_arms = lambda name, hat: None
    ov._fade_arms = lambda name: None
    ov._hud_show = lambda name: None
    ov._hud_hide = lambda *_a: None
    ov.watchdog = Watchdog(hold_limit)
    ov._hold_timer = None
    ov._arm_hold_timer = lambda: None          # no main loop in here
    ov.turbo = Turbo(turbo_hz, armed=set(armed))
    ov._turbo_timers: dict[str, int] = {}
    ov.stick_tuning = StickTuning()
    # Everything else __init__ initialises, with the real class's own values. The guard
    # in test_overlay_refs.py compares this fixture against Overlay.__init__ by AST, so
    # a value written here by guesswork would be caught; a new attribute not written
    # here is caught too, by name, which is the failure that used to arrive as fifteen
    # AttributeErrors across two files.
    ov.opacity = opacity
    ov.dpad_mode = dpad_mode
    ov.layout_path = os.path.join(tempfile.gettempdir(),
                                  f"glasspad_stub_layout_{os.getpid()}.json")
    ov.hud = None
    ov.hud_area = None
    ov._hud_text = ""
    ov._hud_size = (1, 1)
    ov._hud_gen = 0
    ov._quit_armed = False
    ov.input_regions = True
    ov.ctrl_input = {}
    ov.ctrl_move = {}
    ov.menu_open = False
    ov.overlay_enabled = True
    ov.hub = None
    ov.hub_box = None
    ov.hub_children = {}
    ov.hub_state = {}
    ov.hub_x = 0.0
    ov.hub_label = None
    ov.panel = None
    ov.dpad4_btn = None
    ov.dpad8_btn = None
    ov.info = None
    ov.confirm = None
    # the licence reader: added when the licence panel moved in-overlay, and
    # required here by test_overlay_refs -- the stub is the one place.
    ov.licence = None
    ov.licence_view = None
    ov.licence_scroll = None
    ov.discard = None
    ov.status_label = None
    ov.windows = []
    ov.base_frames = []
    ov.design_screen = None
    ov._monitor_refs = []
    ov._monitor_sources = {}
    ov.max_contacts = 0
    ov._hat = "None"
    ov.stick_anchor = "press"
    ov.dpad_style = "notch"
    ov.dpad_cross = "soft"
    ov.dpad_press = "cool"
    ov.press_mode = "bevel"
    ov.press_anim_ms = 140
    ov.loop = None
    ov.profile_name = None
    ov.cfg_dir = None
    ov.profiles_enabled = False       # a test must never read the real config dir
    ov.profile_timeout = 0.0
    ov._pending_profile = None
    ov._claim_monitor = None
    ov._claim_timer = None
    ov._claim_seen = None
    ov.__dict__.update(overrides)
    return ov
