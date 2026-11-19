"""Hidden check for feat-csv-quoted. Usage: check.py <workdir>. Exit 0 only if every case passes."""

import os
import sys

WORKDIR = sys.argv[1]

failures = []

if not os.path.isfile(os.path.join(WORKDIR, "tests", "test_csv_util.py")):
    failures.append("tests/test_csv_util.py is missing")

from csv_util import csv_row  # noqa: E402


def expect(label, line, want):
    try:
        got = csv_row(line)
    except Exception as exc:
        failures.append("%s: raised %r" % (label, exc))
        return
    if got != want:
        failures.append("%s: got %r want %r" % (label, got, want))


expect("plain", "a,b,c", ["a", "b", "c"])
expect("quoted comma", 'a,"b,c",d', ["a", "b,c", "d"])
expect("doubled quote", '"x""y",z', ['x"y', "z"])
expect("empty field", "a,,c", ["a", "", "c"])
expect("quoted only", '"only"', ["only"])

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ok")
sys.exit(0)
