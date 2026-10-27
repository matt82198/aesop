"""Teardown-race proof for TestPrePushGate.tearDown (tests/test_generated_paths.py).

TestPrePushGate builds fixture repos under a TemporaryDirectory. A git process
or any lingering child that still holds a handle inside `<repo>/.git` can make
directory removal fail (Windows: sharing violation / WinError 32 or Errno 39;
Linux: Errno 39 Directory not empty). The fixture's tearDown retries the
cleanup on a 0.2 s doubling backoff and re-raises from the final attempt.

Two tests pin both sides of that contract. The holder is a real child process
(tests/_teardown_holder.py) so the handle belongs to another process, the way
a git child does in CI:

  (a) test_teardown_completes_after_a_child_releases_its_handle
      The child releases its handle about 0.5 s after tearDown starts. A
      retry must succeed once the handle is free, tearDown must return
      without raising, and the temp tree must be gone.

  (b) test_teardown_raises_when_the_handle_is_never_released
      The child keeps the handle open until after tearDown returns. The final
      attempt must raise OSError (fail closed, never swallowed). Windows only:
      POSIX unlinks an open file, so there is no removal failure to pin there.

Run: python -m unittest tests.test_tmp_git_teardown -v
"""

import shutil
import subprocess  # subprocess-ok
import sys
import time
import unittest
from pathlib import Path

import tests.test_generated_paths as tgp  # module ref: avoids re-collecting the class

HOLDER = Path(__file__).resolve().parent / "_teardown_holder.py"
RELEASE_AFTER_S = "0.5"
TEARDOWN_BUDGET_S = 10.0


def _reap(proc):
    """Close the holder's stdin, wait for it to exit, and release its pipes."""
    if proc.stdin and not proc.stdin.closed:
        try:
            proc.stdin.close()
        except OSError:
            pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    for stream in (proc.stdout, proc.stderr):
        if stream:
            stream.close()


def _force_remove(path):
    """Last-resort removal so a failing test never leaks its temp tree."""
    shutil.rmtree(str(path), ignore_errors=True)


class TestTmpGitTeardown(unittest.TestCase):

    def _make_fixture(self):
        fixture = tgp.TestPrePushGate("test_empty_stdin_passes")
        fixture.setUp()
        self.addCleanup(_force_remove, fixture.tmp)
        return fixture

    def _start_holder(self, held_path, *mode_args):
        """Start the holder child and block until it reports the handle is open."""
        proc = subprocess.Popen(  # subprocess-ok
            [sys.executable, str(HOLDER), str(held_path)] + list(mode_args),
            cwd=str(held_path.parent), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8")
        self.addCleanup(_reap, proc)
        ready = proc.stdout.readline().strip()
        self.assertEqual(ready, "held", "holder never took the handle: %r" % ready)
        return proc

    def test_teardown_completes_after_a_child_releases_its_handle(self):
        fixture = self._make_fixture()
        repo, _base, _head = fixture.make_repo("tools/INDEX.md", "teardown_release")
        held_path = repo / ".git" / "lingering-handle.tmp"
        proc = self._start_holder(held_path, "timed", RELEASE_AFTER_S)
        tmp_root = fixture.tmp

        started = time.monotonic()
        try:
            fixture.tearDown()
        except OSError as exc:
            self.fail(
                "TestPrePushGate.tearDown raised although the child releases its "
                "handle after %s s (retry schedule should outlast it): %r"
                % (RELEASE_AFTER_S, exc))
        elapsed = time.monotonic() - started

        self.assertFalse(tmp_root.exists(),
                         "teardown returned but left the temp tree behind: %s" % tmp_root)
        self.assertLess(elapsed, TEARDOWN_BUDGET_S,
                        "teardown took %.2f s, over the retry budget" % elapsed)
        self.assertEqual(proc.wait(timeout=30), 0, "holder did not exit cleanly")

    @unittest.skipUnless(
        sys.platform == "win32",
        "POSIX unlinks an open file, so a held handle cannot block removal there")
    def test_teardown_raises_when_the_handle_is_never_released(self):
        fixture = self._make_fixture()
        repo, _base, _head = fixture.make_repo("tools/INDEX.md", "teardown_pinned")
        held_path = repo / ".git" / "pinned-handle.tmp"
        proc = self._start_holder(held_path, "until-eof")

        with self.assertRaises(OSError):
            fixture.tearDown()

        self.assertTrue(held_path.exists(),
                        "the held handle must still be on disk when teardown fails")
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=30), 0, "holder did not exit cleanly")


if __name__ == "__main__":
    unittest.main()
