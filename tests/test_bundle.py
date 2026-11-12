"""Finding libgtk4-layer-shell on a machine we did not build.

The app used to hardcode /usr/lib/libgtk4-layer-shell.so.0 -- correct on Arch, wrong on
Debian (multiarch), Fedora (lib64) and in any bundle that ships its own copy. Preload
happens before gi is imported, so these tests need no display and no GObject.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glasspad import bundle  # noqa: E402

FAILS = []


def check(label, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILS.append(label)


def touch_lib(d, name="libgtk4-layer-shell.so.0"):
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, "w") as fh:
        fh.write("not a real library, just a file to find\n")
    return p


def test_finds_the_system_library():
    p, why = bundle.layer_shell_path()
    if p is None:
        check("a machine without the library reports that, in words",
              "searched" in why or "does not exist" in why)
        return
    check(f"the layer-shell library was found ({p} via {why})",
          os.path.isfile(p) and "gtk4-layer-shell" in os.path.basename(p))
    check("and the report says which rule matched", why in ("bundle", "distro path", "ld.so"))


def test_explicit_override_wins(tmp="/tmp/gp_bundle_override"):
    lib = touch_lib(tmp)
    keep = os.environ.get(bundle.ENV_OVERRIDE)
    try:
        os.environ[bundle.ENV_OVERRIDE] = lib
        p, why = bundle.layer_shell_path()
        check("the override is honoured", p == lib)
        check("and is named as the reason", why.startswith("$" + bundle.ENV_OVERRIDE))
    finally:
        if keep is None:
            os.environ.pop(bundle.ENV_OVERRIDE, None)
        else:
            os.environ[bundle.ENV_OVERRIDE] = keep


def test_a_bad_override_is_not_silently_ignored(tmp="/tmp/gp_bundle_override"):
    """Someone set the variable; falling back quietly would hide a typo forever."""
    keep = os.environ.get(bundle.ENV_OVERRIDE)
    try:
        os.environ[bundle.ENV_OVERRIDE] = "/nonexistent/libgtk4-layer-shell.so.0"
        p, why = bundle.layer_shell_path()
        check("a wrong override returns nothing rather than another file", p is None)
        check("and says the variable itself is wrong",
              bundle.ENV_OVERRIDE in why and "does not exist" in why)
    finally:
        if keep is None:
            os.environ.pop(bundle.ENV_OVERRIDE, None)
        else:
            os.environ[bundle.ENV_OVERRIDE] = keep


def test_whitespace_env_counts_as_unset():
    """`GLASSPAD_LAYER_SHELL=` from a launcher means "unset", not "not found"."""
    keep = os.environ.get(bundle.ENV_OVERRIDE)
    try:
        os.environ.pop(bundle.ENV_OVERRIDE, None)
        base, _ = bundle.layer_shell_path()
        for blank in ("", "   ", "\t", " \n "):
            os.environ[bundle.ENV_OVERRIDE] = blank
            p, _ = bundle.layer_shell_path()
            check(f"blank override {blank!r} behaves exactly as no override", p == base)
    finally:
        if keep is None:
            os.environ.pop(bundle.ENV_OVERRIDE, None)
        else:
            os.environ[bundle.ENV_OVERRIDE] = keep


def test_bundle_copies_beat_the_system(tmp="/tmp/gp_bundle_tree"):
    lib = touch_lib(os.path.join(tmp, "lib"))
    keep = {k: os.environ.get(k) for k in ("GLASSPAD_BUNDLE", "GLASSPAD_BUNDLE_LIB",
                                           bundle.ENV_OVERRIDE)}
    try:
        os.environ.pop(bundle.ENV_OVERRIDE, None)
        os.environ["GLASSPAD_BUNDLE"] = tmp
        p, why = bundle.layer_shell_path()
        check("a bundle's own library is preferred", p == lib)
        check("and is identified as the bundle's", why == "bundle")
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_versioned_soname_first(tmp="/tmp/gp_bundle_both"):
    d = os.path.join(tmp, "lib")
    bare = touch_lib(d, "libgtk4-layer-shell.so")
    ver = touch_lib(d, "libgtk4-layer-shell.so.0")
    keep = {k: os.environ.get(k) for k in ("GLASSPAD_BUNDLE", bundle.ENV_OVERRIDE)}
    try:
        os.environ.pop(bundle.ENV_OVERRIDE, None)
        os.environ["GLASSPAD_BUNDLE"] = tmp
        p, _ = bundle.layer_shell_path()
        check(f"the real soname beats a -dev symlink ({os.path.basename(p)})", p == ver)
        # basename, not path: LAYER_SHELL_SONAMES holds sonames, and comparing a path
        # against a soname would be false even when the lookup is correct
        check("the bare name is a candidate too",
              os.path.basename(bare) in bundle.LAYER_SHELL_SONAMES)
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_search_covers_the_distros_that_exist():
    joined = " ".join(bundle.LAYER_SHELL_DIRS)
    check("Arch (/usr/lib)", "/usr/lib " in joined + " ")
    check("Fedora (/usr/lib64)", "/usr/lib64" in bundle.LAYER_SHELL_DIRS)
    check("Debian multiarch", "/usr/lib/x86_64-linux-gnu" in bundle.LAYER_SHELL_DIRS)
    check("no path appears twice",
          len(bundle.LAYER_SHELL_DIRS) == len(set(bundle.LAYER_SHELL_DIRS)))


def test_uinput_report():
    ok, msg = bundle.uinput_ready()
    check("uinput check returns a sentence either way", isinstance(ok, bool) and len(msg) > 4)
    missing, msg2 = bundle.uinput_ready("/nonexistent/uinput")
    check("a missing uinput is reported as missing", missing is False and "does not exist" in msg2)


def test_describe_mentions_both_halves():
    line = bundle.describe()
    check(f"one line for --status: {line}",
          line.startswith("layer-shell") and "(" in line and ")" in line)


def test_removed_backend_stays_removed():
    """The list in bundle.py is a fence, so check that the fence is standing.

    REMOVED_QT_MODULES names eight modules deleted in the orphan-code pass. Left
    unread, it would decay into exactly the kind of constant that lies: a file called
    window.py could reappear out of git history, the list would still be there looking
    like it did something, and the payload would carry an untested backend again.

    The second assertion is the important one. A fence test that only checks
    "nothing in the list is present" passes trivially if the list is emptied, so the
    list itself has to be non-empty for this to mean anything.
    """
    pkg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "glasspad")
    present = [n for n in bundle.REMOVED_QT_MODULES
               if os.path.exists(os.path.join(pkg, n))]
    check(f"none of the deleted Qt modules is back (found {present})", not present)
    check(f"the fence is not empty ({len(bundle.REMOVED_QT_MODULES)} names)",
          len(bundle.REMOVED_QT_MODULES) >= 8)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"{fn.__name__}:")
        fn()
    print(f"\n  {len(tests)} tests, {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
