#!/usr/bin/env python3
"""
Test suite: Validate gen_suite_counts.py's live counts against an
INDEPENDENTLY-derived ground truth, and prove the derivation never mutates
or regenerates anything.

Historical context: before structural fix #776 this test validated
hand-maintained count lines in tests/CLAUDE.md against disk reality. #776
moved those counts into a generated tests/SUITE-COUNTS.json artifact, and
this test shifted to comparing the committed artifact against
tools/gen_suite_counts.py's own derivation. That artifact then drifted on two
clean merges anyway (PR #828 postmortem) -- unsurprising, since nothing but
the generator/gate/registry triangle ever consumed its committed VALUE -- so
PR #830 removed it outright: counts are now computed fresh on every call,
so there is nothing stored left to drift.

With no artifact to compare against, simply calling
`gen_suite_counts.py --json` twice and asserting the two calls agree would be
tautological (both sides exercise the identical code path). This test
instead derives the Node/Shell/Python counts a SECOND, INDEPENDENT way --
raw `git ls-files` + a hand-rolled set-dedup, written directly in this test
file rather than importing gen_suite_counts.list_git_files() -- and asserts
that independent derivation agrees with the tool's live `--json` output. That
catches a regression in the tool's own glob patterns or dedup logic, which
merely re-invoking the tool twice never could.

Gap-centric: catches drift that would otherwise rot silently.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path


def _independent_live_counts(repo_root: Path) -> dict:
    """Second, independently-written derivation of the same ground truth.

    Deliberately does NOT import tools/gen_suite_counts.py: the point is a
    derivation that does not share code with the tool under test, so a bug in
    the tool's own glob set or dedup logic cannot cancel out against itself.
    """
    def tracked(*patterns):
        paths = set()
        for pattern in patterns:
            result = subprocess.run(
                ["git", "ls-files", pattern],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                check=True,
            )
            for line in result.stdout.splitlines():
                if line:
                    paths.add(line)
        return paths

    return {
        "Node": len(tracked("tests/*.test.mjs")),
        "Shell": len(tracked("tests/*.test.sh", "tests/test_*.sh", "tests/test-*.sh")),
        "Python": len(tracked("tests/test_*.py")),
    }


class TestSuiteCountsDrift(unittest.TestCase):
    """Validate gen_suite_counts.py's live derivation against independent ground truth."""

    def setUp(self):
        self.repo_root = Path(__file__).parent.parent
        self.tool = self.repo_root / "tools" / "gen_suite_counts.py"

    def _run_tool(self):
        return subprocess.run(
            [sys.executable, str(self.tool), "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(self.repo_root),
            timeout=30,
        )

    def test_suite_counts_match(self):
        """gen_suite_counts.py's live counts must match an independently-coded
        git ls-files derivation of the same ground truth.

        If this fails, either the tool's glob patterns or dedup logic have a
        real bug, or this test's independent derivation needs updating to
        match an intentional, reviewed change to what counts as a suite.
        """
        result = self._run_tool()

        self.assertEqual(
            result.returncode,
            0,
            f"gen_suite_counts.py --json failed to evaluate.\n"
            f"Stdout: {result.stdout}\nStderr: {result.stderr}",
        )
        live = json.loads(result.stdout)
        independent = _independent_live_counts(self.repo_root)

        self.assertEqual(
            live,
            independent,
            "gen_suite_counts.py's derivation disagrees with an independently "
            f"coded git ls-files count: tool says {live}, independent "
            f"derivation says {independent}.",
        )

    def test_derivation_never_writes_anything(self):
        """There is no artifact any more: running the live counter must never
        create, modify, or delete any tracked or untracked file in the repo.
        """
        tracked_before = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(self.repo_root), capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        ).stdout

        self._run_tool()
        self._run_tool()

        tracked_after = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(self.repo_root), capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        ).stdout

        self.assertEqual(
            tracked_before,
            tracked_after,
            "gen_suite_counts.py --json must never mutate the working tree; "
            "there is no stored artifact left for it to regenerate.",
        )

    def test_derivation_is_deterministic_across_calls(self):
        """Calling --json twice in a row must agree byte-for-byte (no stored
        state, no ordering nondeterminism from set iteration)."""
        first = self._run_tool()
        second = self._run_tool()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)


if __name__ == "__main__":
    unittest.main()
