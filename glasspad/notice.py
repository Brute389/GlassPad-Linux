"""A window that tells the user what is missing, on a machine that cannot draw the overlay.

Why a window at all: `libgtk4-layer-shell` is what puts a surface *above a fullscreen
game*. Its absence costs stacking, not GTK. A plain decorated window still works, so there
is no excuse for the current behaviour -- dying at import with

    ValueError: Namespace Gtk4LayerShell not available

and leaving the explanation in a stderr that a Shelly- or Big-Picture-launched process does
not have. This module is what that traceback should have been.

Split deliberately:

  * `window_plan()` is pure. Headline, paragraphs, which buttons exist, what the clipboard
    gets. It is what the tests assert, because a suite with no display cannot click
    anything and should not pretend to.
  * `run()` turns a plan into widgets and a main loop. It is allowed to fail -- with no
    display it prints the same paragraphs and returns a distinct code, because a traceback
    from a diagnostic is worse than the bug it was called to explain.
"""
from __future__ import annotations

import sys

from glasspad import deps

TITLE = "GlassPad cannot draw its controls"

HEADLINES = {
    "missing": "A library is missing: libgtk4-layer-shell",
    "unbound": "The library loaded, but layer-shell is not usable here",
    "nocompositor": "This compositor cannot host an overlay",
    "nodisplay": "Nothing to draw on: no Wayland session from here",
    "ok": "Everything GlassPad needs is present",
}


def window_plan(state: str, profile: dict | None = None,
                looked_up: bool = True) -> dict:
    """Everything the window shows, computed without touching a widget.

    `looked_up` is off in the tests and in --status: `repo_lookup` asks a package manager,
    and an assertion must not depend on whether this particular machine has synced its
    databases.
    """
    prof = profile or deps.system_profile()
    cmd = deps.install_command(prof)
    paras = deps.notice_lines(state, profile=prof, looked_up=looked_up)
    buttons = []
    if cmd:
        buttons += ["copy", "terminal"]
    else:
        buttons += ["copy"]
    buttons += ["again", "quit"]
    # What the clipboard receives: just the command where there is one, because that is
    # what gets pasted into a terminal; the whole explanation where there is not, because
    # that is what gets pasted into a bug report.
    clipboard = " ".join(cmd) if cmd else "\n\n".join(paras)
    return {
        "title": TITLE,
        "headline": HEADLINES.get(state, HEADLINES["missing"]),
        "paragraphs": paras,
        "buttons": buttons,
        "clipboard": clipboard,
        "state": state,
        "measured": prof.get("measured", ""),
    }


def check_again() -> tuple[str, str]:
    """Re-probe without restarting. Three answers, because 'no' has two meanings.

    The library can now exist on disk while the GObject-Introspection typelib stays
    invisible to a process that already looked for it -- girepository caches its search
    path. Saying "found it!" and then failing to import is how a tool teaches people not to
    trust it, so that case is named exactly: relaunch.
    """
    lib, why = deps.bundle.layer_shell_path()
    if not lib:
        return "still-missing", f"still not found ({why})"
    try:
        import gi
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - the whole point is to report this politely
        return ("found-needs-relaunch",
                f"found {lib}, but this process cannot load its typelib now "
                f"({type(exc).__name__}). Close this window and start GlassPad again.")
    return "ready", f"found {lib} and it loads; start GlassPad again and it should draw"


# --- widgets -------------------------------------------------------------------

def _css() -> str:
    """The notice is a dialog, not the overlay: readable, opaque, ordinary chrome."""
    return """
    window.glasspad-notice { background-color: #23262b; color: #eaeaea; }
    .gp-head  { font-weight: 700; font-size: 15pt; color: #ffffff; }
    .gp-body  { font-size: 10.5pt; color: #dcdcdc; }
    .gp-cmd   { font-family: monospace; font-size: 10.5pt; color: #b8e0ff;
                background-color: #191c20; padding: 6px 8px; border-radius: 4px; }
    .gp-meta  { font-size: 9pt; color: #9aa0a6; }
    .gp-ok    { color: #9be29b; }
    """


def run(state: str, selfclose_ms: int = 0) -> int:
    """Show the notice. Returns 0 when the dependency turned up, 4 when it did not.

    `selfclose_ms` exists for the live test and nothing else: it closes the window after N
    milliseconds, after printing whether the window was actually mapped and how big it got.
    A suite with no compositor can assert everything about the PLAN and nothing about the
    window, and "we show a notice" was already once a sentence said about code that showed
    nothing -- so on a machine with a screen the suite asks the toolkit directly instead of
    guessing from a screenshot. No production path passes it.


    Never raises: no display, no GTK, or a compositor that refuses a normal window all end
    with the same paragraphs printed instead, because the alternative is a traceback in
    front of someone who is trying to play a game.
    """
    plan = window_plan(state)
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        # Gdk too: without it PyGObject emits "Gdk was imported without specifying a
        # version first" on every run, and a notice that arrives with warnings attached is
        # a notice the user has reason not to trust.
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gio, GLib, Gtk
    except Exception:  # noqa: BLE001 - no display, no typelib, anything
        deps.print_notice(state, looked_up=False)
        return 4

    app = Gtk.Application(application_id="org.glasspad.notice",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)

    def build() -> Gtk.ApplicationWindow:
        win = Gtk.ApplicationWindow(application=app)
        win.set_title(plan["title"])
        win.set_default_size(760, 540)
        win.set_decorated(True)
        # Not modal: the whole point of this window is that the user goes and types in the
        # terminal it opens, and a modal grab is exactly what stops that. `set_keep_above`
        # is GTK 3 and does not exist in GTK 4; the first run of this
        # window died with AttributeError inside the `activate` handler, printed a traceback
        # into a stdout nobody was reading, and left a live process with nothing on screen --
        # the exact failure this module exists to replace. Modal is what GTK 4 offers, and
        # Wayland compositors decide stacking regardless; KDE users who want it pinned have
        # the window rules documented in the README.
        win.add_css_class("glasspad-notice")

        provider = Gtk.CssProvider()
        provider.load_from_data(_css().encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_top(14)
        outer.set_margin_bottom(14)
        outer.set_margin_start(16)
        outer.set_margin_end(16)
        win.set_child(outer)

        head = Gtk.Label(label=plan["headline"], xalign=0.0)
        head.add_css_class("gp-head")
        head.set_wrap(True)
        outer.append(head)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroller.set_child(body)
        outer.append(scroller)

        for para in plan["paragraphs"]:
            label = Gtk.Label(label=para, xalign=0.0)
            label.set_wrap(True)
            label.set_selectable(True)
            if para.startswith("    "):
                label.add_css_class("gp-cmd")
            else:
                label.add_css_class("gp-body")
            body.append(label)

        meta = Gtk.Label(label=plan["measured"], xalign=0.0)
        meta.add_css_class("gp-meta")
        meta.set_wrap(True)
        meta.set_selectable(True)
        outer.append(meta)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.END)
        outer.append(row)

        status = Gtk.Label(label="", xalign=0.0)
        status.add_css_class("gp-meta")
        status.set_wrap(True)
        outer.append(status)

        def on_copy(*_a):
            clip = Gdk.Display.get_default().get_clipboard()
            clip.set_text(plan["clipboard"])
            status.set_text("copied to the clipboard")

        def on_terminal(*_a):
            from glasspad import terminal
            cmd = deps.install_command(deps.system_profile())
            if not cmd:
                status.set_text("there is no command that would work on this system")
                return
            ok, note = terminal.open_command_window(deps.staged_run(cmd))
            status.set_text((note or ("terminal opened; the command runs only after you "
                                      "press Enter" if ok else "could not open a terminal")))

        def on_again(*_a):
            outcome, detail = check_again()
            status.set_text(detail)
            if outcome in ("ready", "found-needs-relaunch"):
                status.add_css_class("gp-ok")
                if outcome == "ready":
                    # Nothing to show any more, and staying open would be the lie. The
                    # lambda returns False so the timeout does not fire a second time --
                    # `win.close` returns True, which means "keep repeating".
                    def _close_once():
                        win.close()
                        return False
                    GLib.timeout_add_seconds(2, _close_once)

        labels = {"copy": ("Copy", on_copy),
                  "terminal": ("Open a terminal and let me run it", on_terminal),
                  "again": ("Check again", on_again),
                  "quit": ("Quit", lambda *a: win.close())}
        for key in plan["buttons"]:
            text, handler = labels[key]
            btn = Gtk.Button(label=text)
            if key == "quit":
                btn.add_css_class("suggested-action")
            btn.connect("clicked", handler)
            row.append(btn)
        return win

    def activate(_app):
        # Belt and reason: an exception in this handler does NOT reach our outer try, and
        # GTK carries on running an application with no window. That is how "we show a
        # notice" turned into a live process displaying nothing at all. Anything that goes
        # wrong building widgets falls back to the same paragraphs on stderr and quits, so
        # the user gets words and an exit code either way.
        try:
            win = build()
            win.present()
            if selfclose_ms:
                def _report_and_close():
                    print(f"notice-window mapped={bool(win.get_mapped())} "
                          f"size={win.get_width()}x{win.get_height()}", flush=True)
                    win.close()
                    return False
                GLib.timeout_add(max(200, int(selfclose_ms)), _report_and_close)
        except Exception as exc:  # noqa: BLE001 - anything here must degrade, never hang
            print(f"(could not build the notice window: {type(exc).__name__}: {exc})",
                  file=sys.stderr, flush=True)
            deps.print_notice(state, looked_up=False)
            _app.quit()

    app.connect("activate", activate)

    try:
        code = app.run([])
    except SystemExit as exc:  # pragma: no cover - GTK can exit directly
        code = exc.code or 0
    except Exception as exc:  # noqa: BLE001 - never trade a notice for a traceback
        print(f"(could not show a window: {type(exc).__name__}: {exc})", file=sys.stderr)
        deps.print_notice(state, looked_up=False)
        return 4
    outcome, _detail = check_again()
    # What we PROVED, not what we were displaying. `state` is the text we were asked to put
    # up; this is the answer to "will the overlay work if you start it again", which is the
    # only question the exit code is for. A notice about a missing library that exits 0 on a
    # machine where it loaded fine is telling the truth about the machine, not contradicting
    # its own headline.
    return 0 if outcome in ("ready", "found-needs-relaunch") else 4
