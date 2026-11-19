"""Hidden check for refactor-dedupe-validators. Usage: check.py <workdir>. Exit 0 only if every case passes."""

import os
import sys

WORKDIR = sys.argv[1]
sys.path.insert(0, WORKDIR)

failures = []

src_path = os.path.join(WORKDIR, "validators.py")
with open(src_path, encoding="utf-8") as fh:
    source = fh.read()

digit_tests = source.count("isdigit()")
if digit_tests != 1:
    failures.append("digit test appears %d times, want exactly 1" % digit_tests)

from validators import is_pin, is_year, is_zip  # noqa: E402

cases = [
    ("is_zip ok", is_zip, "12345", True),
    ("is_zip short", is_zip, "1234", False),
    ("is_zip letter", is_zip, "1234a", False),
    ("is_zip empty", is_zip, "", False),
    ("is_pin ok", is_pin, "0000", True),
    ("is_pin letter", is_pin, "12a4", False),
    ("is_pin empty", is_pin, "", False),
    ("is_year ok", is_year, "2026", True),
    ("is_year dash", is_year, "20-6", False),
    ("is_year long", is_year, "20266", False),
]

for label, fn, value, want in cases:
    got = fn(value)
    if got is not want:
        failures.append("%s: %r -> %r want %r" % (label, value, got, want))

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ok")
sys.exit(0)
