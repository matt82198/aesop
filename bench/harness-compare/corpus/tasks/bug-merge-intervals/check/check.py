"""Hidden check for bug-merge-intervals. Usage: check.py <workdir>. Exit 0 only if every case passes."""

import sys

sys.path.insert(0, sys.argv[1])

from intervals import merge_intervals  # noqa: E402

failures = []


def expect(label, thunk, want):
    try:
        got = thunk()
    except Exception as exc:
        failures.append("%s: raised %r" % (label, exc))
        return
    if got != want:
        failures.append("%s: got %r want %r" % (label, got, want))


expect("unsorted", lambda: merge_intervals([(8, 10), (1, 3), (2, 6)]), [(1, 6), (8, 10)])
expect("touching", lambda: merge_intervals([(1, 4), (4, 5)]), [(1, 5)])
expect("empty", lambda: merge_intervals([]), [])
expect("disjoint sorted", lambda: merge_intervals([(1, 2), (5, 6)]), [(1, 2), (5, 6)])
expect("nested", lambda: merge_intervals([(1, 10), (2, 3)]), [(1, 10)])

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ok")
sys.exit(0)
