"""Hidden check for bug-slugify. Usage: check.py <workdir>. Exit 0 only if every case passes."""

import sys


from slug import slugify  # noqa: E402

failures = []


def expect(label, thunk, want):
    try:
        got = thunk()
    except Exception as exc:
        failures.append("%s: raised %r" % (label, exc))
        return
    if got != want:
        failures.append("%s: got %r want %r" % (label, got, want))


expect("digits kept", lambda: slugify("Version 2 Release!"), "version-2-release")
expect("trim and punctuation", lambda: slugify("  Hello, World  "), "hello-world")
expect("mixed", lambda: slugify("C3PO & R2-D2"), "c3po-r2-d2")
expect("plain", lambda: slugify("already-clean"), "already-clean")
expect("empty", lambda: slugify("!!!"), "")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ok")
sys.exit(0)
