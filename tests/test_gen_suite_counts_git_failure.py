#!/usr/bin/env python3
"""RED tests: tools/gen_suite_counts.py behaviour when `git ls-files` fails.

Context (receipt matrix row f-928fe970): a transient git failure inside the
pre-push receipt matrix (two matrices running concurrently) made
`git ls-files tests/test-*.sh` raise CalledProcessError, and the gate reported
a bare "CalledProcessError" with no git stderr. The desired behaviour:

1. One retry (short backoff) on a git failure: a single transient failure that
   succeeds on the second attempt yields the real counts and exit 0.
2. When git fails twice, the error names the cwd AND carries the git stderr text.
3. The retry must never mask a genuine result: with git succeeding, a family
   that derives to zero still exits non-zero, and git is not re-run.

Tests 1 and 2 are expected to FAIL on origin/main (RED). Test 3 pins existing
fail-closed behaviour and is expected to pass.

Subprocess is faked so no real git runs; time.sleep is patched so backoff is
instant.
"""

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

_TOOLS_DIR = str(Path(__file__).resolve().parent.parent / "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import gen_suite_counts as gsc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# Per-pattern `git ls-files` output in a healthy tree.
HEALTHY_FILES = {
    "tests/*.test.mjs": ["tests/n.test.mjs"],
    "tests/*.test.sh": ["tests/x.test.sh"],
    "tests/test_*.sh": [],
    "tests/test-*.sh": ["tests/test-y.sh"],
    "tests/test_*.py": ["tests/test_a.py", "tests/test_b.py"],
}
EXPECTED_COUNTS = {"Node": 1, "Shell": 2, "Python": 2}

GIT_STDERR = "fatal: Unable to create '.git/index.lock': File exists."


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class FakeGit:
    """Stand-in for subprocess.run that serves the git calls gen_suite_counts makes.

    fail_plan maps a `git ls-files <pattern>` pattern to a list of stderr strings;
    each entry is consumed by one call that raises CalledProcessError.
    """

    def __init__(self, files=None, fail_plan=None):
        self.files = HEALTHY_FILES if files is None else files
        self.fail_plan = {k: list(v) for k, v in (fail_plan or {}).items()}
        self.calls = []

    def __call__(self, cmd, *args, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[:2] == ["git", "rev-parse"]:
            if "--is-inside-work-tree" in cmd:
                return _completed("true\n")
            # MERGE_HEAD probe: no merge in progress.
            return _completed("", returncode=1)
        if cmd[:2] == ["git", "ls-files"]:
            pattern = cmd[-1]
            pending = self.fail_plan.get(pattern)
            if pending:
                stderr = pending.pop(0)
                raise subprocess.CalledProcessError(128, cmd, output="", stderr=stderr)
            return _completed("".join(p + "\n" for p in self.files[pattern]))
        raise AssertionError("unexpected subprocess call: %r" % (cmd,))

    def ls_files_calls_for(self, pattern):
        return [c for c in self.calls if c[:2] == ["git", "ls-files"] and c[-1] == pattern]


class TestGenSuiteCountsGitFailure(unittest.TestCase):
    """git ls-files failure handling in tools/gen_suite_counts.py."""

    def _run_json_mode(self, fake):
        """Run the --json path with git faked; return (rc, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(gsc.subprocess, "run", side_effect=fake), \
                mock.patch("time.sleep") as sleep_mock, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = gsc.json_mode(REPO_ROOT)
        self._sleep_mock = sleep_mock
        return rc, out.getvalue(), err.getvalue()

    def test_single_transient_git_failure_is_retried_and_counts_returned(self):
        """A one-off `git ls-files` CalledProcessError is retried once; counts come back, exit 0."""
        fake = FakeGit(fail_plan={"tests/*.test.mjs": [GIT_STDERR]})

        rc, out, err = self._run_json_mode(fake)

        self.assertEqual(
            rc, 0,
            "a single transient git failure must be retried once and succeed; "
            "stderr was: %r" % err,
        )
        self.assertEqual(json.loads(out), EXPECTED_COUNTS)
        self.assertEqual(
            len(fake.ls_files_calls_for("tests/*.test.mjs")), 2,
            "the failing pattern must be retried exactly once (2 attempts total)",
        )
        self.assertEqual(
            self._sleep_mock.call_count, 1,
            "the retry must wait a short backoff exactly once before re-running git",
        )
        delay = self._sleep_mock.call_args[0][0]
        self.assertTrue(0 < delay <= 5, "backoff must be short (0 < delay <= 5s), got %r" % (delay,))

    def test_double_git_failure_reports_cwd_and_git_stderr(self):
        """When git fails on both attempts, the error names the cwd and includes git's stderr."""
        fake = FakeGit(fail_plan={"tests/test_*.py": [GIT_STDERR, GIT_STDERR, GIT_STDERR]})

        rc, out, err = self._run_json_mode(fake)

        self.assertEqual(rc, 2, "a persistent git failure must still fail closed with exit 2")
        self.assertEqual(out, "", "no counts may be printed when derivation failed")
        self.assertIn(
            "Cannot derive test suite counts", err,
            "error must keep the 'Cannot derive test suite counts' prefix",
        )
        self.assertIn(
            str(REPO_ROOT), err,
            "error must name the cwd (repo root) git was run in",
        )
        self.assertIn(
            "index.lock", err,
            "error must include the git stderr text, not just the exception class name; "
            "stderr was: %r" % err,
        )

    def test_retry_does_not_mask_genuine_zero_family_when_git_succeeds(self):
        """With git succeeding, a family that derives to zero still exits non-zero, with no retry."""
        files = dict(HEALTHY_FILES)
        files["tests/*.test.sh"] = []
        files["tests/test-*.sh"] = []
        fake = FakeGit(files=files)

        rc, out, err = self._run_json_mode(fake)

        self.assertNotEqual(rc, 0, "a genuine zero Shell family must not exit 0")
        self.assertEqual(rc, 2)
        self.assertIn("ZERO Shell", err, "fail-closed vacuous-zero message must still be printed")
        self.assertEqual(out, "", "no counts may be printed for a vacuous zero")
        self.assertEqual(
            len(fake.ls_files_calls_for("tests/test-*.sh")), 1,
            "a successful git call must not be re-run; the retry only covers failures",
        )
        self.assertEqual(self._sleep_mock.call_count, 0, "no backoff when git succeeded")


if __name__ == "__main__":
    unittest.main()
