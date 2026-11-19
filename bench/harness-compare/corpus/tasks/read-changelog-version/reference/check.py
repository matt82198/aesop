"""Hidden check for read-changelog-version. Usage: check.py <workdir>. Exit 0 only if the answer is correct."""

import os
import sys

WORKDIR = sys.argv[1]
WANT = "2.4.0"

path = os.path.join(WORKDIR, "answer.txt")
if not os.path.isfile(path):
    print("answer.txt is missing")
    sys.exit(1)

with open(path, encoding="utf-8") as fh:
    lines = fh.read().splitlines()
got = lines[0].strip() if lines else ""

if got != WANT:
    print("answer.txt first line: got %r want %r" % (got, WANT))
    sys.exit(1)
print("ok")
sys.exit(0)
