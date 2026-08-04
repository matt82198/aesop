#!/usr/bin/env python3
"""
Test suite: Validate tests/CLAUDE.md suite counts match git ls-files reality.

This drift test ensures the documented counts of test suites in CLAUDE.md
stay synchronized with the actual files in the repo. If this test fails,
it means CLAUDE.md is stale and needs regenerating.

Gap-centric: Catches drift that would otherwise rot silently.

The counting logic lives in tools/verify_test_suite_count.py. This test
delegates to its READ-ONLY --check mode and additionally asserts that the
mode really is read-only: before #A1 the gate auto-corrected drift by
WRITING tests/CLAUDE.md and exiting 0, which meant (a) the test suite
mutated a tracked file on every run and (b) drift could never fail CI.
"""

import subprocess
import sys
import unittest
from pathlib import Path


class TestClaudeMdDrift(unittest.TestCase):
    """Validate tests/CLAUDE.md counts vs actual test files."""

    def test_claudemd_suite_counts_match(self):
        """All suite counts in CLAUDE.md must match actual test files.

        Delegates to tools/verify_test_suite_count.py --check, which verifies
        Node, Shell, and Python counts in a single invocation.
        """
        tests_dir = Path(__file__).parent
        repo_root = tests_dir.parent
        claudemd_path = tests_dir / "CLAUDE.md"

        before = claudemd_path.read_bytes()

        result = subprocess.run(
            [sys.executable, str(repo_root / "tools" / "verify_test_suite_count.py"), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(repo_root),
            timeout=30,
        )

        after = claudemd_path.read_bytes()

        # The gate must never mutate the tree it is checking.
        self.assertEqual(
            before,
            after,
            "verify_test_suite_count.py --check mutated tests/CLAUDE.md; "
            "--check is read-only and only --regenerate may write.",
        )

        self.assertEqual(
            result.returncode,
            0,
            f"Test suite counts in tests/CLAUDE.md are out of sync.\n"
            f"Stdout: {result.stdout}\n"
            f"Stderr: {result.stderr}\n"
            f"To resolve, run:\n"
            f"  python tools/verify_test_suite_count.py --regenerate\n"
            f"Then commit the updated tests/CLAUDE.md.",
        )


if __name__ == "__main__":
    unittest.main()
