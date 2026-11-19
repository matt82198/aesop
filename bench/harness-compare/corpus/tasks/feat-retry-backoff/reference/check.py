"""Hidden check for feat-retry-backoff. Usage: check.py <workdir>. Exit 0 only if every case passes."""

import os
import sys

WORKDIR = sys.argv[1]

failures = []

if not os.path.isfile(os.path.join(WORKDIR, "tests", "test_retry.py")):
    failures.append("tests/test_retry.py is missing")

from retry import retry  # noqa: E402


class Flaky:
    def __init__(self, fail_times, value="ok"):
        self.fail_times = fail_times
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ValueError("boom %d" % self.calls)
        return self.value


def expect(label, thunk, want):
    try:
        got = thunk()
    except Exception as exc:
        failures.append("%s: raised %r" % (label, exc))
        return
    if got != want:
        failures.append("%s: got %r want %r" % (label, got, want))


sleeps = []
fn = Flaky(fail_times=2)
expect("succeeds on third attempt", lambda: retry(fn, attempts=3, base_delay=0.1, sleep=sleeps.append), "ok")
if sleeps != [0.1, 0.2]:
    failures.append("backoff delays: got %r want [0.1, 0.2]" % (sleeps,))

sleeps = []
fn = Flaky(fail_times=0)
expect("first try needs no sleep", lambda: retry(fn, attempts=3, base_delay=0.5, sleep=sleeps.append), "ok")
if sleeps != []:
    failures.append("no-failure sleeps: got %r want []" % (sleeps,))

sleeps = []
fn = Flaky(fail_times=5)
try:
    retry(fn, attempts=3, base_delay=0.1, sleep=sleeps.append)
    failures.append("exhausted retries did not raise")
except ValueError as exc:
    if str(exc) != "boom 3":
        failures.append("expected last error 'boom 3', got %r" % str(exc))
if fn.calls != 3:
    failures.append("exhausted call count: got %d want 3" % fn.calls)

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ok")
sys.exit(0)
