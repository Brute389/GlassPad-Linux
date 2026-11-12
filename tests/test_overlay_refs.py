"""Every `self.x` must resolve to something.

A method can vanish without any syntax error, without any import error, and
without a single test failing -- and then the first tap on the button that calls it
raises AttributeError in front of the user. That is exactly what happened when the
hub was spliced in: the replacement region swallowed _set_edit, _hold_disable,
_set_overlay_visible, _say and quit, and the only thing that showed it was pressing
the buttons. This parses the source instead of pressing the buttons, so it needs no
display, no GTK and no uinput device.

Run: python3 tests/test_overlay_refs.py
"""
from __future__ import annotations

import ast
import os
import sys

GLASSPAD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "glasspad")

# Names that exist on the object for reasons static analysis cannot see: slots
# filled by GTK, mixins, or setattr with a computed key. Empty by intent -- adding
# to this list is admitting the checker is wrong, so it should be visible in review.
KNOWN_DYNAMIC: set[str] = set()


def unresolved(path: str) -> list[str]:
    """Return "Class.attr (line N)" for every self-reference with nothing behind it."""
    with open(path) as fh:
        tree = ast.parse(fh.read(), filename=path)

    out = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        if cls.bases:
            # A subclass of QWidget/Gtk.Widget reads members that exist only in C
            # (setGeometry, rect, update...). They could be listed, but the list
            # would be a second source of truth about someone else's API and would
            # quietly go stale. This package's own classes -- Overlay among them --
            # have no bases, which is what makes the check exact where it matters.
            continue
        own = {n.name for n in cls.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        attrs = set()
        # class-level attributes, e.g. Overlay.HUB_ALWAYS
        for n in cls.body:
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        attrs.add(t.id)
        for n in ast.walk(cls):
            if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in targets:
                    # `self.sw, self.sh = 0, 0` unpacks through a Tuple target
                    for leaf in (t,) if not isinstance(t, (ast.Tuple, ast.List)) \
                            else t.elts:
                        if (isinstance(leaf, ast.Attribute)
                                and isinstance(leaf.value, ast.Name)
                                and leaf.value.id == "self"):
                            attrs.add(leaf.attr)
        for n in ast.walk(cls):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
                    and n.value.id == "self":
                name = n.attr
                if name in own or name in attrs or name in KNOWN_DYNAMIC:
                    continue
                out.append(f"{cls.name}.{name} (line {n.lineno}) UNRESOLVED")
    return out


def test_no_unresolved_self_references():
    offenders = []
    for fn in sorted(os.listdir(GLASSPAD)):
        if fn.endswith(".py"):
            offenders += unresolved(os.path.join(GLASSPAD, fn))
    assert not offenders, ("dangling self-references:\n    "
                           + "\n    ".join(dict.fromkeys(offenders)))


def test_hub_handlers_all_exist():
    """The hub wires five buttons by name; every action it dispatches must be real."""
    path = os.path.join(GLASSPAD, "overlay_layer.py")
    with open(path) as fh:
        tree = ast.parse(fh.read(), filename=path)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "Overlay")
    own = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    # the handlers a tap can reach, from _hub_release / _hub_press / _toggle_menu
    # every action a tap on the hub can reach, plus the state it drives. The
    # latching disable button replaced the hold gesture, so _hold_disable is
    # deliberately NOT in this list -- and if something starts calling it again,
    # that is a regression worth failing on.
    needed = {"_hub_press", "_hub_release", "_toggle_menu", "_close_fab",
              "_open_info", "_close_info", "_quit_request", "_set_edit",
              "_toggle_overlay_enabled", "_set_overlay_enabled",
              "_sync_disable_button", "_refresh_hub", "_animate_hub",
              "_place_hub", "_set_dpad_mode", "_save", "_save_confirm_yes",
              "_close_confirm", "_open_licenses", "_reset_geometry", "_say",
              "quit"}
    missing = sorted(needed - own)
    assert not missing, f"hub dispatches to methods that do not exist: {missing}"


# ------------------------------------------------- the stub must track the real class

def test_the_test_stub_defines_every_attribute_the_real_overlay_starts_with():
    """The reason there is one fixture instead of two.

    Both hand-built fixtures broke, twice each, every time Overlay grew a piece of
    state -- most recently `lockout` and `edge_margin`, fifteen failures from a correct
    change. Reading __init__ and comparing means the next attribute is reported by name
    instead of by an AttributeError inside an unrelated test.
    """
    import ast
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from glasspad import overlay_layer as OL

    tree = ast.parse(open(OL.__file__, encoding="utf-8").read())
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Overlay")
    init = next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    owned = set()
    for node in ast.walk(init):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if (isinstance(t, ast.Attribute)
                        and isinstance(t.value, ast.Name) and t.value.id == "self"):
                    owned.add(t.attr)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _overlay_stub import make_overlay
    ov = make_overlay()
    missing = sorted(n for n in owned if not hasattr(ov, n))
    # Things only a live session has: surfaces, gestures, the loop, the hub widgets.
    allow = {n for n in missing
             if n.startswith(("loop", "hub", "areas", "surfaces", "gestures"))
             or n in {"panels", "hub_label"}}
    unexpected = [n for n in missing if n not in allow]
    assert not unexpected, (
        f"Overlay gained state the stub does not know about: {unexpected}. Add a "
        f"default to tests/_overlay_stub.py -- that file is the one place.")


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok    {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL  {name}:\n{exc}")
    print(f"\n  {'all clean' if not failed else str(failed) + ' test(s) failed'}")
    sys.exit(1 if failed else 0)
