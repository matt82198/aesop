#!/usr/bin/env python3
"""
Test suite: Validate tests/SUITE-COUNTS.json suite counts match git ls-files reality.

This drift test ensures the documented counts of test suites in the generated
artifact stay synchronized with the actual files in the repo. If this test
fails, it means tests/SUITE-COUNTS.json is stale and needs regenerating.

Gap-centric: Catches drift that would otherwise rot silently.

The counting logic lives in tools/gen_suite_counts.py; tools/verify_test_suite_count.py
is a thin backward-compatible wrapper around it, kept for existing pre-push/CI
callers. This test delegates to the wrapper's READ-ONLY --check mode and
additionally asserts that the mode really is read-only: before #A1 the gate
auto-corrected drift by WRITING the documented counts and exiting 0, which meant
(a) the test suite mutated a tracked file on every run and (b) drift could never
fail CI. Counts moved from hand-maintained tests/CLAUDE.md lines into generated
tests/SUITE-COUNTS.json (structural fix #776) so that adding/removing a test
suite no longer conflicts with every other in-flight PR editing the same line.
"""

import subprocess
import sys
import unittest
from pathlib import Path


class TestSuiteCountsDrift(unittest.TestCase):
    """Validate tests/SUITE-COUNTS.json counts vs actual test files."""

    def test_suite_counts_match(self):
        """All suite counts in tests/SUITE-COUNTS.json must match actual test files.

        Delegates to tools/verify_test_suite_count.py --check (which delegates to
        tools/gen_suite_counts.py --check), verifying Node, Shell, and Python
        counts in a single invocation.
        """
        tests_dir = Path(__file__).parent
        repo_root = tests_dir.parent
        counts_path = tests_dir / "SUITE-COUNTS.json"

        before = counts_path.read_bytes()

        result = subprocess.run(
            [sys.executable, str(repo_root / "tools" / "verify_test_suite_count.py"), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(repo_root),
            timeout=30,
        )

        after = counts_path.read_bytes()

        # The gate must never mutate the tree it is checking.
        self.assertEqual(
            before,
            after,
            "verify_test_suite_count.py --check mutated tests/SUITE-COUNTS.json; "
            "--check is read-only and only --regenerate may write.",
        )

        self.assertEqual(
            result.returncode,
            0,
            f"Test suite counts in tests/SUITE-COUNTS.json are out of sync.\n"
            f"Stdout: {result.stdout}\n"
            f"Stderr: {result.stderr}\n"
            f"To resolve, run:\n"
            f"  python tools/verify_test_suite_count.py --regenerate\n"
            f"Then commit the updated tests/SUITE-COUNTS.json.",
        )


if __name__ == "__main__":
    unittest.main()
