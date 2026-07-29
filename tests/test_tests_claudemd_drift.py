#!/usr/bin/env python3
"""
Test suite: Validate tests/CLAUDE.md suite counts match git ls-files reality.

This drift test ensures the documented counts of test suites in CLAUDE.md
stay synchronized with the actual files in the repo. If this test fails,
it means CLAUDE.md is stale and needs updating.

Gap-centric: Catches drift that would otherwise rot silently.

The actual counting and fix logic lives in tools/verify_test_suite_count.py,
which supports both --check and --fix modes. This test delegates to it.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path


class TestClaudeMdDrift(unittest.TestCase):
    """Validate tests/CLAUDE.md counts vs actual test files."""

    def test_claudemd_suite_counts_match(self):
        """All suite counts in CLAUDE.md must match actual test files.

        Delegates to tools/verify_test_suite_count.py --check, which
        verifies Node, Shell, and Python counts in a single invocation.

        If counts drift, the helper message suggests running --fix.
        """
        # Locate repo root and tool
        tests_dir = Path(__file__).parent
        repo_root = tests_dir.parent

        # Run the verification tool in --check mode
        result = subprocess.run(
            [sys.executable, str(repo_root / "tools" / "verify_test_suite_count.py"), "--check"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=30,
        )

        self.assertEqual(
            result.returncode,
            0,
            f"Test suite counts in tests/CLAUDE.md are out of sync.\n"
            f"Stdout: {result.stdout}\n"
            f"Stderr: {result.stderr}\n"
            f"To fix automatically, run:\n"
            f"  python tools/verify_test_suite_count.py --fix\n"
            f"Then commit the updated tests/CLAUDE.md.",
        )


if __name__ == "__main__":
    unittest.main()
