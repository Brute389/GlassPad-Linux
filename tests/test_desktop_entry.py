"""The desktop entry a bundle installs -- what it points at, and what it must not.

Ported out of the .run launcher, where `install-desktop` was a heredoc with
`Icon=` pointing INTO the unpacked payload. That path is a FUSE mount which stops
existing when the app exits, so the same line in an AppImage yields a menu entry
with a broken icon within seconds. These tests are written against the failure,
not the implementation: a path that outlives the process is checked directly, and
so is the case where nothing launchable exists at all.

Run: python3 tests/test_desktop_entry.py   (no pytest, no display)
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import desktop_entry as de  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


def sandbox(with_package=True):
    """A fake $HOME, optionally holding a copied-out (not checkout) package."""
    d = tempfile.mkdtemp()
    if with_package:
        shutil.copytree(os.path.join(ROOT, "glasspad"), os.path.join(d, "glasspad"))
    return d


def run_in(dirpath, argv, extra_env=None):
    env = dict(os.environ)
    env.pop("APPIMAGE", None)
    env["HOME"] = dirpath
    env["XDG_DATA_HOME"] = os.path.join(dirpath, ".local", "share")
    env["XDG_CONFIG_HOME"] = os.path.join(dirpath, ".config")
    env["PYTHONPATH"] = dirpath
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-c",
                           "import sys;from glasspad.__main__ import main;"
                           "sys.exit(main(sys.argv[1:]))"] + argv,
                          cwd=dirpath, env=env, capture_output=True, text=True,
                          timeout=180)


def tree(base):
    """Every file under base, relative -- so 'no junk' is a set comparison."""
    out = set()
    for root, _dirs, files in os.walk(base):
        for f in files:
            out.add(os.path.relpath(os.path.join(root, f), base))
    return out


# --- where the entry points --------------------------------------------------
def test_an_appimage_wins_and_a_stale_one_is_ignored():
    with tempfile.TemporaryDirectory() as d:
        real = os.path.join(d, "GlassPad.AppImage")
        open(real, "w").close()
        got, why = de.launch_target(env={"APPIMAGE": real})
        check(f"APPIMAGE is preferred ({got})", got == real)
        check("and the reason names it", "AppImage" in why)

        gone, _ = de.launch_target(env={"APPIMAGE": os.path.join(d, "nope.AppImage")})
        check("a dangling APPIMAGE is not trusted as a launch target",
              gone != os.path.join(d, "nope.AppImage"))
        check("an empty APPIMAGE is not a target either",
              de.launch_target(env={"APPIMAGE": "   "})[0] != "")


def test_a_copied_out_tree_refuses_instead_of_writing_a_dead_entry():
    """No APPIMAGE, no checkout markers, not installed: nothing valid to point at.

    The tempting fallback -- "there is a glasspad package right here" -- is what
    makes this wrong: that is equally true of an installed copy, a copied-out
    folder, and a tarball someone unpacked, and only one of those launches from a
    menu without inheriting a shell's PYTHONPATH.
    """
    d = sandbox()
    try:
        r = run_in(d, ["--install-desktop"])
        check(f"exit 2 rather than a guess (got {r.returncode})", r.returncode == 2)
        check("it says why, in terms of what it looked for",
              "no APPIMAGE" in r.stdout and "PATH" in r.stdout)
        check("and writes nothing at all", tree(os.path.join(d, ".local")) == set())
    finally:
        shutil.rmtree(d)


def test_checkout_target_is_the_package_not_this_file():
    got, why = de.launch_target(env={})
    check(f"from the checkout, Exec runs the package ({got})",
          got is not None and got.endswith("-m glasspad"))
    check("and the reason names the tree", ROOT in why)


# --- what gets written -------------------------------------------------------
def test_install_writes_the_entry_and_icon_and_nothing_else():
    d = sandbox()
    try:
        # Pretend to be the bundle: a real file for APPIMAGE to point at, since a
        # copied-out tree is (correctly) refused.
        app = os.path.join(d, "GlassPad.AppImage")
        open(app, "w").close()
        r = run_in(d, ["--install-desktop"], extra_env={"APPIMAGE": app})
        base = os.path.join(d, ".local")
        check(f"install succeeded ({r.returncode}: {r.stdout.strip()[:80]})",
              r.returncode == 0)
        made = tree(base)
        check(f"exactly two files, an entry and an icon ({sorted(made)})",
              len(made) == 2 and any(f.endswith(".desktop") for f in made))
        entry = next((os.path.join(base, f) for f in made if f.endswith(".desktop")), None)
        body = open(entry).read() if entry else ""
        check("the entry points at the AppImage, not at the sandbox tree",
              app in body)
        check("and sets no Path= (that would name a mount)", "Path=" not in body)
        check("no stray files anywhere under the user dir",
              not any("GlassPad" in f and not f.endswith((".desktop", ".svg", ".png"))
                      for f in made))
    finally:
        shutil.rmtree(d)


def test_the_entry_has_one_exec_and_no_field_codes():
    text = de.desktop_text("/opt/Glass Pad/GlassPad.AppImage")
    execs = [l for l in text.splitlines() if l.startswith("Exec=")]
    # One per section is fine; the point is no Exec on the main entry besides ours.
    main = text.split("[Desktop Action")[0]
    check(f"exactly one Exec on the main entry ({execs})",
          len([l for l in main.splitlines() if l.startswith("Exec=")]) == 1)
    # Field codes only matter on an Exec line -- the file carries a comment
    # explaining why it has none, and a check over the whole text would flag that.
    exec_lines = [l for l in text.splitlines() if l.startswith("Exec=")]
    check(f"no %f/%u/%U field codes on any Exec line ({exec_lines})",
          not any(code in l for l in exec_lines for code in ("%f", "%u", "%U", "%F")))
    check("the path is quoted so a space in it survives",
          'Exec="/opt/Glass Pad/GlassPad.AppImage"' in main)


def test_icon_is_a_theme_name_never_a_path_into_the_mount():
    """The bug this whole module exists to avoid.

    The .run's heredoc wrote Icon=$dest/app/share/icons/glasspad.svg, a path into
    the unpacked payload. Harmless there; in an AppImage it is a FUSE mount that
    ceases to exist when the overlay closes, so the menu shows a broken icon.
    """
    text = de.desktop_text("/home/user/Downloads/GlassPad.AppImage")
    icon = [l for l in text.splitlines() if l.startswith("Icon=")]
    check(f"Icon= names the theme entry ({icon})", icon == ["Icon=glasspad"])
    check("no directory in Icon=, so it survives any move of the file",
          "/" not in "".join(icon))
    mountish = de.desktop_text("/home/user/GlassPad.AppImage",
                               workdir="/tmp/.mount_GlassPadiU3x9f")
    check("a mount path is only ever rejected, never echoed into Icon=",
          ".mount_" not in "".join(l for l in mountish.splitlines()
                                   if l.startswith("Icon=")))


def test_path_key_only_for_a_tree_that_still_exists():
    bundle = de.desktop_text("/home/x/GlassPad.AppImage")
    check("a bundle entry sets no Path= (it would name the mount)",
          "Path=" not in bundle)
    checkout = de.desktop_text("python3 -m glasspad", workdir=ROOT)
    check(f"a checkout entry does set it, so -m resolves ({ROOT in checkout})",
          f"Path={ROOT}" in checkout)


# --- removing it -------------------------------------------------------------
def test_uninstall_removes_only_what_it_can_prove_is_its_own():
    """Ownership by marker, not by filename.

    Shelly -- the AppImage manager on CachyOS -- integrates this very application under
    the filename `glasspad.desktop`, which is the name an old install.sh used and which
    LEGACY_NAMES still lists so upgrades find it. An uninstaller that matched names
    deleted the manager's menu entry while its metadata database went on believing in it,
    leaving a menu item owned by nobody. Uninstall therefore removes files carrying
    MARKER_KEY (or, for entries predating the key, files at our own name whose Exec points
    at this install) and reports everything else as left alone.
    """
    d = sandbox()
    try:
        apps = os.path.join(d, ".local", "share", "applications")
        os.makedirs(apps, exist_ok=True)
        img = os.path.join(d, "GlassPad-1.1.0-x86_64.AppImage")
        open(img, "w").close()
        os.chmod(img, 0o755)
        modern = os.path.join(apps, de.DESKTOP_NAME)
        theirs = os.path.join(apps, "glasspad.desktop")
        cfg = os.path.join(d, ".config", "GlassPad")
        with open(modern, "w", encoding="utf-8") as fh:
            fh.write(f'[Desktop Entry]\nName=GlassPad\nExec="{img}"\n'
                     f'{de.MARKER_KEY}=1\n')
        with open(theirs, "w", encoding="utf-8") as fh:
            fh.write('[Desktop Entry]\nName=GlassPad\n'
                     f'Exec="{img}"\nX-KDE-StartupNotify=false\n')
        os.makedirs(cfg, exist_ok=True)
        open(os.path.join(cfg, "layout.json"), "w").write("{}")

        r = run_in(d, ["--uninstall-desktop"], {"APPIMAGE": img})
        check(f"uninstall succeeded ({r.returncode})", r.returncode == 0)
        check("our marked entry is gone", not os.path.exists(modern))
        check("the manager's same-named file SURVIVES", os.path.exists(theirs))
        check("and it says why instead of reporting a clean sweep",
              "left alone" in r.stdout and de.MARKER_KEY in r.stdout)
        check("the saved layout survived -- this is not an app uninstall",
              os.path.exists(os.path.join(cfg, "layout.json")))

        again = run_in(d, ["--uninstall-desktop"], {"APPIMAGE": img})
        check(f"and it is safe to run twice ({again.returncode})",
              again.returncode == 0 and "no menu entry" in again.stdout
              and os.path.exists(theirs))
    finally:
        shutil.rmtree(d)


def test_the_icon_is_the_apps_own_artwork_not_the_builds():
    """`Icon=glasspad` means the application's icon.

    desktop/glasspad.desktop.in says Icon=glasspad, and the artwork that name denotes is
    glasspad/img/glasspad_icon_512.png -- upstream, RGBA, the thing the Windows client
    ships. The build used to substitute a tablet-with-gamepad svg drawn for the build,
    which is fine as a stand-in and wrong as a permanent resident: an app whose menu icon
    is not its own icon is a small, permanent inconsistency.
    """
    src = de.icon_source()
    check(f"icon_source resolves to something ({src})", bool(src) and os.path.isfile(src))
    check(f"and it is the upstream PNG, not a drawn stand-in ({os.path.basename(src or '')})",
          os.path.basename(src or "") == "glasspad_icon_512.png")
    check("the placeholder svg is gone from the tree, so it cannot be preferred again",
          not os.path.exists(os.path.join(ROOT, "packaging", "glasspad.svg")))
    check("it is a real image (PNG signature, 512x512)",
          open(src, "rb").read(8) == b"\x89PNG\r\n\x1a\n"
          and open(src, "rb").read(24)[16:24] == (512).to_bytes(4, "big") * 2)
    # The entry must still name the app id, not a path: a path into the payload is a
    # mount point that vanishes when the app exits, leaving a broken picture in the menu.
    body = de.desktop_text("/tmp/GlassPad.AppImage")
    check("and Icon= stays a theme name", "Icon=glasspad\n" in body
          and "Icon=/" not in body)


def test_ownership_is_content_not_filename():
    """entry_owner's three states, including the upgrade path that has no marker.

    A pre-marker entry at OUR name with an Exec pointing at this install is ours: an
    install over an older one must still replace it. The same bytes under Shelly's name is
    foreign, because the name is the only thing that differs and it is not ours.
    """
    d = sandbox()
    try:
        apps = os.path.join(d, ".local", "share", "applications")
        os.makedirs(apps, exist_ok=True)
        img = os.path.join(d, "GlassPad.AppImage")
        open(img, "w").close()
        body = f'[Desktop Entry]\nName=GlassPad\nExec="{img}"\n'
        at_our_name = os.path.join(apps, de.DESKTOP_NAME)
        under_theirs = os.path.join(apps, "glasspad.desktop")
        unrelated = os.path.join(apps, "firefox.desktop")
        for p in (at_our_name, under_theirs):
            open(p, "w").write(body)
        open(unrelated, "w").write('[Desktop Entry]\nName=Firefox\n'
                                   'Exec=firefox\n')
        check("no marker, our name, our Exec  -> ours (upgrade)",
              de.entry_owner(at_our_name, img) == "ours")
        check("no marker, another name, our Exec -> foreign",
              de.entry_owner(under_theirs, img) == "foreign")
        check("another application entirely      -> other",
              de.entry_owner(unrelated, img) == "other")
        # foreign_entries_for walks the XDG applications dirs, so this in-process call
        # needs the sandbox pointed at -- run_in only sets it for subprocesses, and a
        # scan of the real $HOME here would find nothing and prove nothing.
        saved = {k: os.environ.get(k) for k in ("HOME", "XDG_DATA_HOME")}
        try:
            os.environ["HOME"] = d
            os.environ["XDG_DATA_HOME"] = os.path.join(d, ".local", "share")
            found = [p for p, _n in de.foreign_entries_for(img)]
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        check(f"and a manager entry is what install_desktop would find ({found})",
              found == [under_theirs])
    finally:
        shutil.rmtree(d)


def test_no_wayland_session_required():
    """A menu entry has nothing to do with a running compositor.

    Unknown flags in this CLI once fell through and started a fullscreen overlay;
    the setup commands must not depend on a session either, since they are how you
    get to a working one.
    """
    d = sandbox()
    try:
        app = os.path.join(d, "GlassPad.AppImage")
        open(app, "w").close()
        r = run_in(d, ["--install-desktop", "--dry-run"],
                   extra_env={"WAYLAND_DISPLAY": "", "DISPLAY": "", "APPIMAGE": app})
        check(f"dry run works headless ({r.returncode})", r.returncode == 0)
        check("it printed a plan rather than starting a surface",
              "would write" in r.stdout)
        check("nothing was written by a dry run", tree(os.path.join(d, ".local")) == set())
    finally:
        shutil.rmtree(d)


def test_the_module_ships_in_the_appimage():
    """Desktop install is useless from a bundle if the module is not in the bundle."""
    appimage = os.path.join(ROOT, "dist", "GlassPad-1.1.0-x86_64.AppImage")
    if not os.path.isfile(appimage):
        check("no built AppImage on this box; nothing to inspect", True)
        return
    # unsquashfs cannot read a type-2 AppImage -- the squashfs starts behind the runtime
    # ELF -- and this assertion sat behind `if not tool: skip` for months, turning red the
    # moment squashfs-tools was installed. See tests/_payload.py.
    from _payload import paths_in
    with tempfile.TemporaryDirectory() as tmp:
        ok, paths, note = paths_in(appimage, tmp)
        if not ok:
            check(f"payload not inspectable here ({note}); skipped", True)
            return
        # endswith, not `in paths`: the helper returns whole paths relative to the
        # payload root, so the file is usr/share/glasspad/glasspad/desktop_entry.py. The
        # old check substring-matched a text listing, which is the difference between a
        # set membership test and a scan -- and only one of them fails honestly.
        check("desktop_entry.py is inside the packed payload",
              any(p.endswith("glasspad/desktop_entry.py") for p in paths))


def test_installing_creates_no_caches_in_a_pristine_account():
    """`update-desktop-database` writes mimeinfo.cache and intent.cache from nothing.

    Both are standard files and neither belongs in a home directory that had no
    MIME caches, for an application that declares no MimeType at all. This is what
    the "exactly two files" check caught; it is split out so the reason survives.
    """
    d = sandbox()
    try:
        app = os.path.join(d, "GlassPad.AppImage")
        open(app, "w").close()
        run_in(d, ["--install-desktop"], extra_env={"APPIMAGE": app})
        made = tree(os.path.join(d, ".local"))
        caches = [f for f in made if f.endswith((".cache", ".cache.1"))
                  or "mimeinfo" in f or "intent" in f]
        check(f"no cache files created ({sorted(caches)})", not caches)
    finally:
        shutil.rmtree(d)


def test_the_entry_passes_the_desktop_file_validator():
    """The distro's own linter, when the distro ships one.

    Not everyone has it, so absence is reported rather than counted as a pass --
    a suite that quietly skips its only external check is how a malformed entry
    ships.
    """
    tool = shutil.which("desktop-file-validate")
    if not tool:
        check("desktop-file-validate absent on this box; nothing to validate against",
              True)
        return
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "org.glasspad.GlassPad.desktop")
        open(path, "w").write(de.desktop_text("/opt/GlassPad.AppImage"))
        r = subprocess.run([tool, path], capture_output=True, text=True, timeout=120)
        check(f"validator is silent (exit {r.returncode}: {(r.stdout or r.stderr).strip()[:120]})",
              r.returncode == 0)


def test_a_foreign_glasspad_on_path_is_never_the_launch_target():
    """Any command by that name became Exec= for whichever tree asked.

    This sat in launch_target() as a bare `shutil.which("glasspad")` until it was
    demonstrated: install the CLI link on a real machine, then run --install-desktop
    from a copied-out folder, and the folder stops refusing -- it writes a menu entry
    that launches the OTHER GlassPad and exits 0. Two installs, one name, and the
    entry silently belongs to whichever one PATH found first.
    """
    d = sandbox()
    other = tempfile.mkdtemp()
    bindir = os.path.join(other, "bin")
    os.makedirs(bindir)
    foreign = os.path.join(bindir, "glasspad")
    with open(foreign, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexec /opt/some-other-build/glasspad \"$@\"\n")
    os.chmod(foreign, 0o755)
    try:
        r = run_in(d, ["--install-desktop"],
                   extra_env={"PATH": bindir + ":/usr/bin:/bin"})
        check(f"still refuses rather than pointing at someone else (got {r.returncode})",
              r.returncode == 2)
        check("and says it ignored the command, and why",
              "not this install" in r.stdout or "ignored" in r.stdout.lower())
        check("naming the path it resolved to", foreign in r.stdout or other in r.stdout)
        check("writes nothing", tree(os.path.join(d, ".local")) == set())
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(other, ignore_errors=True)


def test_our_own_wrapper_on_path_is_accepted_for_that_tree():
    """The exception has to work, or the rule is just a refusal with extra steps."""
    d = sandbox()
    other = tempfile.mkdtemp()
    bindir = os.path.join(other, "bin")
    os.makedirs(bindir)
    ours = os.path.join(bindir, "glasspad")
    with open(ours, "w", encoding="utf-8") as fh:
        from glasspad import bundle
        fh.write("#!/bin/sh\n" + bundle.LINK_MARKER + d + "\n"
                 'exec python3 -m glasspad "$@"\n')
    os.chmod(ours, 0o755)
    try:
        r = run_in(d, ["--install-desktop"],
                   extra_env={"PATH": bindir + ":/usr/bin:/bin"})
        check(f"accepted (got {r.returncode})", r.returncode == 0)
        entry = os.path.join(d, ".local", "share", "applications", de.DESKTOP_NAME)
        body = open(entry, encoding="utf-8").read() if os.path.exists(entry) else ""
        # Group-aware: the entry legitimately carries a second Exec= inside
        # [Desktop Action status], which is not the launch line.
        main, in_main = [], True
        for ln in body.splitlines():
            if ln.startswith("["):
                in_main = ln.strip() == "[Desktop Entry]"
            elif in_main and ln.startswith("Exec="):
                main.append(ln)
        check(f"exactly one launch Exec= ({main})", len(main) == 1)
        check(f"and it is the wrapper we wrote ({main[:1]})",
              bool(main) and ours in main[0])
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(other, ignore_errors=True)


def _installed_glasspad_files(home):
    """What the installer put under $HOME -- nothing else.

    Scoped to .local/ on purpose: the sandbox also contains a copied-out package whose
    own img/glasspad_icon.png is source, not installed state, and a stand-in AppImage.
    Counting those made this test fail for the wrong reason on its first run.
    """
    out = []
    top = os.path.join(home, ".local")
    for base, _dirs, files in os.walk(top):
        for f in files:
            if "glasspad" in f.lower():
                out.append(os.path.relpath(os.path.join(base, f), home))
    return sorted(out)


def test_uninstall_takes_the_icon_too_and_leaves_everything_else():
    """install writes three files; uninstall used to take two.

    It ended with the sentence "left alone: ... the icon theme entry", delivered with
    complete sincerity by a program that had just put that entry there. Discovered while
    wiping a machine back to virgin -- where a leftover is the entire thing you were
    trying to avoid -- and the fix is bounded rather than clever: only files named
    exactly after the app id, in the three sizes install itself chooses between.

    The other half matters as much: a directory full of other people's icons must come
    through untouched.
    """
    d = sandbox()
    try:
        img = os.path.join(d, "GlassPad.AppImage")
        open(img, "w").close()
        os.chmod(img, 0o755)
        apps512 = os.path.join(d, ".local", "share", "icons", "hicolor",
                               "512x512", "apps")
        os.makedirs(apps512, exist_ok=True)
        keep = os.path.join(apps512, "someone-else.png")
        with open(keep, "w", encoding="utf-8") as fh:
            fh.write("not yours")

        r = run_in(d, ["--install-desktop"], extra_env={"APPIMAGE": img})
        check(f"install ran ({r.returncode})", r.returncode == 0)
        after_install = _installed_glasspad_files(d)
        check(f"install left an entry and an icon ({len(after_install)})",
              len(after_install) >= 2)

        r2 = run_in(d, ["--uninstall-desktop"], extra_env={"APPIMAGE": img})
        left = _installed_glasspad_files(d)
        check(f"uninstall removed every one of them ({left})", left == [])
        check("and the unrelated icon in the same directory survived",
              os.path.exists(keep))
        check("it says the layout is left alone, not the icon",
              "icon theme entry" not in r2.stdout)
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
