"""Hidden check for bug-paginate. Usage: check.py <workdir>. Exit 0 only if every case passes."""

import sys

sys.path.insert(0, sys.argv[1])

from paginate import paginate  # noqa: E402

failures = []


def expect(label, thunk, want):
    try:
        got = thunk()
    except Exception as exc:  # report, do not crash
        failures.append("%s: raised %r" % (label, exc))
        return
    if got != want:
        failures.append("%s: got %r want %r" % (label, got, want))


expect("page1", lambda: paginate(list(range(10)), 1, 4), [0, 1, 2, 3])
expect("page2", lambda: paginate(list(range(10)), 2, 4), [4, 5, 6, 7])
expect("page3 partial", lambda: paginate(list(range(10)), 3, 4), [8, 9])
expect("page4 past end", lambda: paginate(list(range(10)), 4, 4), [])
expect("page0", lambda: paginate(list(range(10)), 0, 4), [])
expect("exact fit", lambda: paginate([1, 2, 3, 4], 1, 4), [1, 2, 3, 4])

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ok")
sys.exit(0)
