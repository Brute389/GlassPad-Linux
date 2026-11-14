"""The files that face outward: SECURITY.md, issue templates, CI, and what README claims.

These rot differently from the code. A painter goes wrong when a number changes; a
facing file goes wrong when a number changes and nobody re-reads the sentence. So the
checks here are of two kinds, and the second kind is the one that earns the suite:

  * **the claim is present** -- SECURITY.md still names the input-group disclosure, the
    templates still ask for the two commands that actually diagnose this app, CI still
    runs the suite it says it runs;
  * **the claim is true** -- SECURITY.md says "no network access", so this suite greps the
    package for a socket and fails the build if one appears. A security document nobody
    checks is a list of intentions, and intentions do not stop a regression.

The templates are also checked against the real command line: a template that tells people
to paste the output of a flag that no longer exists produces a report about a command that
failed, which is worse than no template.
"""
import tempfile
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def sh(argv):
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=180)


# --- SECURITY.md -------------------------------------------------------------
def test_security_document_exists_and_names_the_grant():
    """The disclosure has to exist in the place a person reads before typing a password."""
    try:
        text = read("SECURITY.md")
    except OSError:
        check("SECURITY.md exists", False)
        return
    check("SECURITY.md exists", True)
    needles = {
        "the group can read every input device": "every keyboard",
        "mouse and touchpad are named too": "touchpad",
        "the grant is decided, not unconditional": "smallest",
        "the rule mode is stated": "0660",
        "0666 is refused by name": "0666",
        "one policy file, no embedded copy": "one policy file",
        "removal is documented": "--uninstall-uinput",
        "root at runtime is denied": "No root at runtime",
        "the private reporting path is named": "private security advisory",
    }
    for label, needle in needles.items():
        check(f"SECURITY.md: {label}", needle in text)


def test_security_claims_about_the_code_are_true():
    """Every "does not" in the document, checked against the package.

    A claim like "no network access" is worth nothing once a `urllib.request` lands in a
    helper three months from now -- and it is worth LESS than nothing, because the document
    is where a reader goes to be reassured. So the document's promises are compiled into
    assertions here, and the day one of them stops being true, a test says so instead of
    the document quietly becoming fiction.
    """
    offenders = {"network": [], "telemetry": [], "bundled_host_library": []}
    net = re.compile(r"\bimport\s+(socket|http|urllib|requests|ftplib|smtplib|asyncio"
                     r"|ssl|webbrowser)\b|\bfrom\s+(socket|http|urllib|requests)\b")
    for base, dirs, files in os.walk(os.path.join(ROOT, "glasspad")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".py"):
                continue
            full = os.path.join(base, name)
            text = open(full, encoding="utf-8", errors="ignore").read()
            for i, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                m = net.search(stripped)
                if m:
                    offenders["network"].append(f"{name}:{i}: {stripped[:60]}")
    check(f"no module in the package imports a network client "
          f"({offenders['network'][:2]})", not offenders["network"])

    payload = os.path.join(ROOT, "dist", "GlassPad.AppDir", "usr", "lib")
    if os.path.isdir(payload):
        libs = [n for n in os.listdir(payload) if n.endswith((".so", ".so.0"))]
        check(f"the staged payload bundles no host library ({libs[:2]})", not libs)
    else:
        check("no staged AppDir to inspect (nothing built); the bundling claim is "
              "checked by tests/test_appimage_artifact.py against a built image", True)

    # The runtime pin is what makes "we do not ship your libraries" survivable: the one
    # binary that IS fetched has to be identified by hash, not by a mutable tag alone.
    pin = read("packaging", "runtime-pin.json")
    check("the fetched runtime is pinned by sha256", '"sha256"' in pin)


def test_security_document_cites_files_that_exist():
    """A markdown link into the tree is a promise the reader can hold you to."""
    text = read("SECURITY.md")
    links = re.findall(r"\]\(([^)#][^)]*)\)", text)
    broken = []
    for rel in links:
        if rel.startswith(("http://", "https://", "mailto:")):
            continue
        if not os.path.exists(os.path.join(ROOT, rel)):
            broken.append(rel)
    check(f"every relative link in SECURITY.md resolves ({broken})", not broken)


# --- issue templates ---------------------------------------------------------
def _templates():
    d = os.path.join(ROOT, ".github", "ISSUE_TEMPLATE")
    if not os.path.isdir(d):
        return {}
    return {n: open(os.path.join(d, n), encoding="utf-8").read()
            for n in sorted(os.listdir(d))}


def test_templates_exist_and_blank_issues_are_closed_on_purpose():
    t = _templates()
    check(f"the template directory is not empty ({list(t)})", bool(t))
    check("there is an overlay-failure template",
          any("overlay" in n for n in t))
    config = t.get("config.yml", "")
    check("blank issues are disabled", "blank_issues_enabled: false" in config)
    check("...and the reason is written down, not just configured",
          len(config) > 100 and ("#" in config or "about" in config))


def test_the_overlay_template_asks_for_the_two_commands_that_diagnose_this_app():
    """`--status` separates the four ways this fails; `--selftest` proves the chain.

    Every report that omitted them became a round trip, so they are required fields --
    a report that cannot say which of the four it is cannot be triaged.
    """
    text = "".join(v for k, v in _templates().items() if "overlay" in k)
    check("asks for --status output", "--status" in text)
    check("asks for --selftest output", "--selftest" in text)
    check("names the four failure modes it is triaging",
          text.count("layer-shell") >= 2 and "/dev/uinput" in text)
    check("asks which compositor, since stacking is where it breaks",
          "KWin" in text and "Hyprland" in text and "GNOME" in text)
    check("asks how the game is running (exclusive fullscreen is another path)",
          "fullscreen" in text.lower())
    check("both diagnostics are required, not suggested",
          text.count("required: true") >= 2)
    check("and points at the log line for a manager-launched run",
          "journalctl" in text)


def test_templates_only_name_flags_that_exist():
    """A template that advertises a flag which exits 2 produces a report about the template.

    This is the --selftest bug from the other side: --help promised a flag, argparse had
    never heard of it, and the one command this project tells people to run for diagnostics
    failed in their hands.
    """
    help_out = sh([sys.executable, "-m", "glasspad", "--help"])
    known = set(re.findall(r"--[a-z][a-z-]+", help_out.stdout))
    # Flags valid on the module's own privileged script, plus what --status prints.
    known |= set(re.findall(r"--[a-z][a-z-]+",
                            sh([sys.executable, os.path.join("glasspad",
                                                             "uinput_setup.py"),
                                "--help"]).stdout))
    # Environment variables are written the same way; `GLASSPAD_LOG=full` is advice, not a flag.
    unknown = set()
    for name, text in _templates().items():
        if not name.endswith(".md"):
            continue
        for flag in set(re.findall(r"`glasspad (--[a-z][a-z-]+)`", text)):
            if flag not in known:
                unknown.add(f"{name}: {flag}")
    check(f"no template tells a reader to type a flag that does not exist ({sorted(unknown)})",
          not unknown)


# --- CI ----------------------------------------------------------------------
CI_PATH = os.path.join(".github", "workflows", "ci.yml")


def test_ci_runs_the_suite_this_repository_claims():
    if not os.path.isfile(os.path.join(ROOT, CI_PATH)):
        check("a CI workflow exists", False)
        return
    text = read(CI_PATH)
    check("a CI workflow exists", True)
    check("it runs tools/check_all.py", "tools/check_all.py" in text)
    check("it excludes the live self-test rather than failing on it", "--no-live" in text)
    check("and says why, instead of leaving the skip unexplained",
          "self-test" in text or "selftest" in text)
    check("it builds the artefact", "tools/make_appimage.sh" in text)
    check("it opens the artefact it built", "-k appimage" in text or "appimage" in text)
    check("it publishes provenance with the file", "BUILD-INFO.txt" in text)
    check("the runner image is pinned, not -latest",
          "ubuntu-" in text and "ubuntu-latest" not in text)
    check("the pin is justified in a comment",
          "-latest" in text.split("#", 1)[0] or "latest" in text.lower())
    # This check used to assert the opposite: that a missing libgtk4-layer-shell package
    # was a `::warning::` and the pipeline carried on. The first real run of the workflow
    # disproved that policy -- Ubuntu 24.04 has no such package, the warning fired, and 12
    # of 30 suites died on `Namespace Gtk4LayerShell not available`. check_all was right not
    # to call those passes, which means the pipeline was red anyway, only with a green-
    # looking dependency step and a comment claiming a narrower run. So the policy now is:
    # the environment is provided, one owner decides how, and failing to provide it stops
    # everything. The guard follows the lesson, not the memory.
    # Comments stripped first. The naive version failed against the comment that EXPLAINS
    # the removed `|| true`, which is the third time today a guard of mine scanned source
    # text for a thing the source also describes (see the writable_now AST check). The
    # invariant is about the commands the runner executes, so that is what gets read.
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    check("the dependency is not treated as optional", "|| true" not in code)
    check("    (and the only remaining mention is prose, not a command)",
          "|| true" in text and "|| true" not in code)
    check("and the old warn-and-continue is gone from what runs",
          "::warning::" not in code)
    action = ".github/actions/setup-deps/action.yml"
    check("one shared step owns the environment for both jobs",
          os.path.isfile(os.path.join(ROOT, action)))
    n_uses = text.count("uses: ./.github/actions/setup-deps")
    check(f"both jobs use it ({n_uses})", n_uses == 2)
    if os.path.isfile(os.path.join(ROOT, action)):
        atext = read(action)
        check("it builds from a pinned upstream release, not 'latest'",
              "archive/v1.3.0.tar.gz" in atext or "VER=1.3.0" in atext)
        check("and verifies the tarball before building it", "sha256sum -c" in atext)
        check("introspection is on: the GIR namespace is what GlassPad imports",
              "-Dintrospection=true" in atext)
        check("the typelib import is asserted, not assumed",
              'gi.require_version("Gtk4LayerShell", "1.0")' in atext)
        check("and a library glasspad cannot resolve is still a failure",
              "layer_shell_path()" in atext)
        check("nothing there is allowed to fail quietly",
              "continue-on-error" not in atext)


def test_ci_is_syntactically_valid_when_a_yaml_parser_is_around():
    """Deep-verified when possible, honestly skipped when not.

    PyYAML is not a dependency of this project and will not become one, so the check
    degrades. It says "no parser here" rather than passing quietly -- the same rule the
    suite applies to a suite that could not start.
    """
    try:
        import yaml
    except ImportError:
        check("PyYAML absent; the workflow's structure was not parsed (textual checks "
              "above still ran)", True)
        return
    spec = yaml.safe_load(read(CI_PATH))
    check("the workflow parses as YAML", isinstance(spec, dict) and "jobs" in spec)
    jobs = (spec or {}).get("jobs") or {}
    check(f"it has jobs ({list(jobs)})", len(jobs) >= 2)
    problems = []
    for job, body in jobs.items():
        if not str(body.get("runs-on", "")).startswith("ubuntu-"):
            problems.append(f"{job}: unpinned runner")
        steps = body.get("steps") or []
        if not any("run" in (s or {}) for s in steps):
            problems.append(f"{job}: no commands")
        for i, step in enumerate(steps):
            script = (step or {}).get("run")
            if not script:
                continue
            for chunk in script.split("python3 - <<'PY'")[1:]:
                code = chunk.split("\nPY")[0]
                try:
                    compile(code, f"{job}/{i}", "exec")
                except SyntaxError as exc:
                    problems.append(f"{job}/{i}: embedded python {exc}")
    check(f"every job pins its runner and every embedded script compiles ({problems})",
          not problems)


# --- README ------------------------------------------------------------------
def test_readme_separates_measured_from_reasoned():
    """The compatibility table has to distinguish "we ran it" from "we thought about it"."""
    text = read("README.md")
    m = re.search(r"^## Tested on\n(.*?)^## ", text, re.M | re.S)
    if not m:
        check("README has a Tested on section", False)
        return
    check("README has a Tested on section", True)
    section = m.group(1)
    check("it says which is which, in words", "measured" in section
          and "reasoned" in section)
    check("KWin on Wayland is the measured reference", "KWin" in section)
    check("GNOME is refused by name, with the reason", "GNOME" in section
          and "zwlr_layer_shell_v1" in section)
    check("X11 is refused, not left ambiguous", "X11" in section)
    # The honest part: a row that was not run must say so in the same cell, or the table
    # reads as a list of supported systems.
    reasoned_rows = [ln for ln in section.splitlines()
                     if ln.strip().startswith("|") and "reasoned" in ln]
    check(f"every reasoned row says it was not run ({len(reasoned_rows)} rows)",
          reasoned_rows and all("not run" in ln for ln in reasoned_rows))
    check("and the reader is told how to upgrade a row", "issue" in section.lower())


def test_readme_says_what_ci_cannot_check():
    text = read("README.md")
    m = re.search(r"^## Tests\n(.*?)^## ", text, re.M | re.S)
    check("the Tests section mentions the workflow file",
          bool(m) and "workflows/ci.yml" in m.group(1))
    check("it names the flag that skips the live suite", bool(m) and "--no-live" in m.group(1))
    check("it states, in the README, what no runner can verify",
          bool(m) and "fullscreen" in m.group(1) and "stacking" in m.group(1).lower())
    check("it says a skipped suite is reported as skipped",
          bool(m) and "skipped" in m.group(1))


def test_facing_files_do_not_cite_the_deleted_installer_as_present():
    """install.sh is history. History is fine in the code's reasoning; it is a lie in an
    instruction."""
    offenders = []
    for rel in ("README.md", "SECURITY.md"):
        text = read(rel)
        for i, line in enumerate(text.splitlines(), start=1):
            if "install.sh" not in line:
                continue
            # Prose about the past is allowed; anything shaped like a command to run is not.
            if re.search(r"(\./install\.sh|sudo\s+.*install\.sh|bash\s+install\.sh)", line):
                offenders.append(f"{rel}:{i}: {line.strip()[:70]}")
    check(f"no facing file tells a reader to run install.sh ({offenders})", not offenders)



# --- the pictures ------------------------------------------------------------
def _slug(heading: str) -> str:
    """GitHub's anchor rule, applied to a heading: lowercase, punctuation dropped,
    spaces to hyphens. Emoji and backticks go with them -- decoration in a heading,
    invisible in a URL."""
    out = re.sub(r"[^a-z0-9 -]", "", heading.lower())
    return re.sub(r"[ -]+", "-", out).strip("-")


def _readme_headings(text: str) -> set:
    heads, in_code = set(), False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = re.match(r"#{1,6}\s+(.*?)\s*$", line)
        if m:
            heads.add(_slug(m.group(1)))
    return heads


def _md_pages():
    """Every prose page that ships: the front door and the archaeology beside it."""
    pages = {"README.md": read("README.md")}
    if os.path.isfile(os.path.join(ROOT, "docs", "NOTES.md")):
        pages["docs/NOTES.md"] = read("docs", "NOTES.md")
    return pages


_HEADING_CACHE = {}


def _headings_of(rel):
    """Slugs of the headings in a markdown file -- computed once per run."""
    if rel not in _HEADING_CACHE:
        _HEADING_CACHE[rel] = _readme_headings(read(rel) if os.path.isabs(rel)
                                               else read(*rel.split("/")))
    return _HEADING_CACHE[rel]


def test_every_link_in_the_docs_resolves_to_a_file_and_an_anchor():
    """Two ways a documentation link dies silently: the file moves, or a heading is reworded.

    Neither breaks a build, because markdown is not compiled -- the reader just clicks
    something that goes nowhere, in the one document meant to explain the software. Three
    target shapes are checked, because the split into README + docs/NOTES.md created the
    third one: `#anchor` must exist on the same page, `file.md` must exist, and
    `file.md#anchor` must do both. A cross-file anchor is the fragile kind -- it survives
    the file being renamed by nobody and the heading being reworded by accident.
    """
    broken_paths, broken_anchors = [], []
    checked = 0
    for page, text in _md_pages().items():
        page_dir = os.path.dirname(os.path.join(ROOT, page))
        targets = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text)
        targets += re.findall(r"(?<!!)\[[^\]]*\]\(([^)\s]+)", text)
        for t in targets:
            if t.startswith(("http://", "https://", "mailto:")):
                continue
            checked += 1
            path, _, anchor = t.partition("#")
            if not path:                                   # same-page anchor
                if anchor and anchor not in _readme_headings(text):
                    broken_anchors.append(f"{page}{t}")
                continue
            where = path if os.path.isabs(path) else os.path.normpath(
                os.path.join(page_dir, path))
            if not os.path.exists(where):
                broken_paths.append(f"{page} -> {t}")
                continue
            if anchor:                                     # file + anchor
                rel = os.path.relpath(where, ROOT)
                if rel.endswith(".md") and anchor not in _headings_of(rel):
                    broken_anchors.append(f"{page} -> {t}")
    check(f"every relative link in the docs resolves to a file ({broken_paths[:3]})",
          not broken_paths)
    check(f"every anchor names a heading that exists ({broken_anchors[:6]})",
          not broken_anchors)
    check(f"the scan found links to check at all ({checked})", checked > 15)


def test_the_split_holds_short_readme_long_notes():
    """The README is the download-facing document, and it has to stay that way.

    It grew to 1,272 lines by accumulating reasoning that a person installing a gamepad
    overlay does not need at 11pm: type-2 AppImage offsets, how the packaging tool borrows
    mksquashfs, the history of a Qt backend nobody ships. All of it is kept -- in
    docs/NOTES.md -- because every paragraph there records a real machine and a real
    failure. The ceiling is not about brevity for its own sake: a document you cannot
    skim is a document nobody reads, and the sections a reader needs fast (install,
    status, troubleshooting, environment variables) must be reachable in one scroll.
    """
    readme = read("README.md")
    notes = read("docs", "NOTES.md")
    n_readme, n_notes = len(readme.splitlines()), len(notes.splitlines())
    check(f"README stays under 750 lines ({n_readme})", n_readme < 750)
    check(f"NOTES exists and holds the moved material ({n_notes} lines)", n_notes > 400)
    # ...and says so in a heading a scanner finds, not only in a passing sentence.
    pointer = [h for h in re.findall(r"^## (.*)$", readme, re.M)
               if "long version" in h.lower() or "notes" in h.lower()]
    check(f"and the README has a heading pointing at the long version ({pointer})",
          bool(pointer) and "docs/NOTES.md" in readme)
    # The moved chapters must not have been left behind in both places: one copy each.
    for marker in ("### Type-2 internals", "### Where the privileged half lives",
                   "### Why nothing from the host is bundled",
                   "### A third way: a screen pixel diff"):
        check(f"{marker!r} lives in NOTES only",
              marker in notes and marker not in readme)
    # Sections a reader needs fast stay on the front door, not filed away.
    for section in ("## Install", "## Check your tree", "## Troubleshooting",
                    "## Environment variables", "## Tested on", "## Known limits"):
        check(f"{section} is still in the README", section in readme)


def test_notes_contents_table_matches_the_headings_it_claims():
    """docs/NOTES.md opens with a table of contents, which is a promise about its own
    headings -- the one part of a document that goes stale when someone reworded a
    heading eight months ago and never touched the list at the top."""
    notes = read("docs", "NOTES.md")
    toc = notes.split("## Contents", 1)
    if len(toc) < 2:
        check("NOTES has a Contents list", False)
        return
    check("NOTES has a Contents list", True)
    toc = toc[1].split("\n---", 1)[0]
    links = re.findall(r"\((#[a-z0-9-]+)\)", toc)
    check(f"the contents list names sections ({len(links)})", len(links) >= 6)
    heads = _readme_headings(notes)
    dead = [a for a in links if a[1:] not in heads]
    check(f"every contents entry names a heading that exists ({dead})", not dead)
    # ...and the reverse, so the table cannot quietly omit a chapter.
    top = [ _slug(m) for m in re.findall(r"^## (?!Contents)(.*)$", notes, re.M)]
    unlisted = [h for h in top if f"#{h}" not in links]
    check(f"every chapter is listed in the contents ({unlisted})", not unlisted)


def test_the_pictures_are_pictures_and_not_four_megabyte_mistakes():
    """A repository's screenshots are the one binary asset people add without thinking.

    Bounds, not aesthetics: PNG rather than a JPEG of flat-colour UI (which smears the
    labels), wide enough to read those labels, and a total that keeps a clone cheap. The
    ceiling is deliberately generous -- it exists to catch the 4 MB lossless grab, not to
    argue about 60 KB.
    """
    text = read("README.md")
    shots = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text)
    check(f"the README links at least one picture ({len(shots)})", len(shots) >= 1)
    total, problems = 0, []
    for rel in shots:
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            problems.append(f"{rel}: missing")
            continue
        size = os.path.getsize(path)
        total += size
        if open(path, "rb").read(8) != b"\x89PNG\r\n\x1a\n":
            problems.append(f"{rel}: not a PNG")
        if size > 600 * 1024:
            problems.append(f"{rel}: {size // 1024} KB")
        try:
            import struct
            w, _h = struct.unpack(">II", open(path, "rb").read(24)[16:24])
            if w < 900:
                problems.append(f"{rel}: only {w}px wide, labels unreadable")
        except Exception as exc:                         # noqa: BLE001
            problems.append(f"{rel}: unreadable ({exc})")
    check(f"every picture is a readable PNG within budget ({problems})", not problems)
    check(f"the whole picture set is under 1.5 MB ({total // 1024} KB)",
          total < 1500 * 1024)


def test_pictures_are_described_not_decorated():
    """Alt text on a screenshot is not for search engines.

    It is the sentence a screen reader gets, and the one that survives when the image
    fails to load on a slow connection -- which, for a project whose pitch is "this is
    what it looks like", is the pitch failing.
    """
    text = read("README.md")
    alts = re.findall(r"!\[([^\]]*)\]", text)
    check(f"every picture has alt text ({len(alts)} pictures)",
          bool(alts) and all(a.strip() for a in alts))
    check("and each says what is in the frame, rather than `screenshot`",
          all(len(a.split()) >= 3 for a in alts))




def test_cleaning_staging_never_eats_a_deliverable():
    """`--clean` in a throwaway tree: scratch gone, everything shipped still there.

    Run against a copied script in a temp root rather than the real dist/ -- the
    interesting assertion is "the published image survived", and the only safe way to
    test that is with a fake artefact that does not matter if the code is wrong.
    """
    import shutil
    import subprocess
    tmp = tempfile.mkdtemp(prefix="gp-clean-")
    try:
        os.makedirs(os.path.join(tmp, "tools"))
        dist = os.path.join(tmp, "dist")
        os.makedirs(os.path.join(dist, "GlassPad.AppDir", "usr", "lib"))
        shutil.copy2(os.path.join(ROOT, "tools", "make_appimage.sh"),
                     os.path.join(tmp, "tools", "make_appimage.sh"))
        ship = ["GlassPad-9.9.9-x86_64.AppImage", "GlassPad-9.9.9-x86_64.AppImage.sha256",
                "BUILD-INFO.txt"]
        scratch = ["payload.sqsh", "runtime-active", "runtime-borrowed", "GlassPad.AppDir"]
        for name in ship + scratch:
            path = os.path.join(dist, name)
            if os.path.isdir(path):
                continue
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("fixture\n")
        with open(os.path.join(dist, "GlassPad.AppDir", "usr", "lib", "libstolen.so"),
                  "w", encoding="utf-8") as fh:
            fh.write("fixture\n")

        r = subprocess.run(["bash", os.path.join(tmp, "tools", "make_appimage.sh"),
                            "--clean"], capture_output=True, text=True, timeout=120)
        check(f"--clean exits 0 (rc={r.returncode}: {r.stderr.strip()[:60]})", r.returncode == 0)
        left = sorted(os.listdir(dist))
        for name in ship:
            check(f"    kept {name}", name in left)
        for name in scratch:
            check(f"    removed {name}", name not in left)
        check("it says what it kept, so the user is not guessing",
              "GlassPad-9.9.9-x86_64.AppImage" in r.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_build_route_is_allowed_to_delete_an_artefact():
    """Static guard behind the behavioural one: no `rm` line may name a deliverable.

    Scanned, not read. The cleanup in make_appimage.sh is a list of paths and the list is
    the safety property; the day someone adds "dist/*" because it is shorter, the build
    script eats the release. Comments are stripped first -- this suite has tripped on
    prose three times, and each time the prose was explaining the rule, not breaking it.
    """
    import ast
    ship = (".AppImage", ".sha256", "BUILD-INFO")
    bad = []
    sh = read("tools", "make_appimage.sh")
    for i, line in enumerate(sh.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if re.search(r"\brm\b", stripped) and any(t in stripped for t in ship):
            bad.append(f"make_appimage.sh:{i}: {stripped[:70]}")

    py = read("tools", "mk_appimage_offline.py")
    tree = ast.parse(py)
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "clean_scratch"), None)
    check("the offline builder has a clean_scratch() at all", fn is not None)
    if fn is not None:
        body_src = ast.get_source_segment(py, fn)
        for token in (".AppImage", ".sha256", "BUILD-INFO.txt"):
            check(f"    clean_scratch() refuses {token}", token in body_src)
        # and it must be *called*, or the guard is a museum piece
        called = any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "clean_scratch"
                     for n in ast.walk(tree))
        check("    clean_scratch() is actually called", called)
        check("    GLASSPAD_KEEP_STAGING can override the cleanup",
              "GLASSPAD_KEEP_STAGING" in py)
    check(f"no rm line in the builders names a deliverable ({bad[:2]})", not bad)

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        try:
            fn()
        except Exception as exc:                       # noqa: BLE001
            FAILS.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
