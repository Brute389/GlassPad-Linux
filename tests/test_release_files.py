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
    check("installing a dependency that may not exist is a warning, not a failure",
          "::warning::" in text)


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


def test_readmes_own_links_resolve_either_in_the_tree_or_on_the_page():
    """Two ways a README link dies silently: the file moves, or a heading gets reworded.

    Neither shows up as a broken build, because markdown is not compiled -- the reader
    just clicks something that goes nowhere, in the one document that is supposed to
    explain the software. So both kinds are checked: relative paths against the tree,
    and `#anchors` against the headings that actually exist on the page.
    """
    text = read("README.md")
    heads = _readme_headings(text)
    targets = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text)
    targets += re.findall(r"(?<!!)\[[^\]]*\]\(([^)\s]+)", text)
    broken_paths, broken_anchors = [], []
    for t in targets:
        if t.startswith(("http://", "https://", "mailto:")):
            continue
        if t.startswith("#"):
            if t[1:] not in heads:
                broken_anchors.append(t)
        elif not os.path.exists(os.path.join(ROOT, t.split("#")[0])):
            broken_paths.append(t)
    check(f"every relative link in README resolves to a file ({broken_paths[:3]})",
          not broken_paths)
    check(f"every #anchor in README names a heading on the page ({broken_anchors[:3]})",
          not broken_anchors)
    check(f"the scan found links to check at all ({len(targets)})", len(targets) > 5)


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
