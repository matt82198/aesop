#!/usr/bin/env python3
"""Test suite for tools/list_test_suites.py.

Tests:
- Scanning discovers all test files (Node, Shell, Python)
- First-line doc extraction (Python docstrings, comments, block comments)
- Non-ASCII character sanitization
- Count totals match disk reality
- Output is deterministic and ASCII-safe
"""

import subprocess
import sys
from pathlib import Path
from unittest import TestCase


class TestListTestSuites(TestCase):
    """Tests for list_test_suites.py discovery and inventory."""

    def setUp(self):
        """Set up test fixtures."""
        self.repo_root = Path(__file__).parent.parent.resolve()

    def test_scan_discovers_python_tests(self):
        """Verify scan discovers Python test files."""
        result = subprocess.run(
            [sys.executable, "tools/list_test_suites.py", "--repo", str(self.repo_root)],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        output = result.stdout

        # Check that Python section exists with count
        self.assertIn("## Python (", output)
        self.assertIn("test_", output)  # At least one test file should be mentioned

    def test_scan_discovers_node_tests(self):
        """Verify scan discovers Node.js test files."""
        result = subprocess.run(
            [sys.executable, "tools/list_test_suites.py", "--repo", str(self.repo_root)],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        output = result.stdout

        # Check that Node section exists with count
        self.assertIn("## Node.js (", output)
        self.assertIn(".test.mjs", output)  # At least one test file should be mentioned

    def test_scan_discovers_shell_tests(self):
        """Verify scan discovers shell test files."""
        result = subprocess.run(
            [sys.executable, "tools/list_test_suites.py", "--repo", str(self.repo_root)],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        output = result.stdout

        # Check that Shell section exists with count
        self.assertIn("## Shell (", output)
        # Should mention at least one .test.sh or .sh file
        self.assertTrue(
            ".test.sh" in output or "pre-push-policy.sh" in output,
            "No shell tests found in output",
        )

    def test_output_is_ascii_safe(self):
        """Verify output is ASCII-safe (no encoding errors)."""
        result = subprocess.run(
            [sys.executable, "tools/list_test_suites.py", "--repo", str(self.repo_root)],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        # Should encode to ASCII without errors
        try:
            result.stdout.encode("ascii")
        except UnicodeEncodeError as e:
            self.fail(f"Output contains non-ASCII characters: {e}")

    def test_output_contains_totals(self):
        """Verify output includes total counts."""
        result = subprocess.run(
            [sys.executable, "tools/list_test_suites.py", "--repo", str(self.repo_root)],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        output = result.stdout

        # Should have a total line
        self.assertIn("Total:", output, "No total count in output")
        self.assertIn("Node +", output)
        self.assertIn("Shell +", output)
        self.assertIn("Python", output)

    def test_output_is_deterministic(self):
        """Verify output is deterministic across runs."""
        results = []
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, "tools/list_test_suites.py", "--repo", str(self.repo_root)],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            results.append(result.stdout)

        # Both runs should produce identical output
        self.assertEqual(results[0], results[1], "Output is not deterministic")

    def test_counts_match_verify_gate(self):
        """list_test_suites.py's filesystem scan must agree with the live,
        git-index-derived counts from tools/gen_suite_counts.py.

        Protects against: the "Live suite inventory" command
        (`tools/list_test_suites.py`, documented in tests/CLAUDE.md as the way
        to see current counts) silently diverging from what CI actually runs.
        The two tools discover suites through genuinely independent
        mechanisms -- list_test_suites.py walks the filesystem with
        `Path.glob()`, gen_suite_counts.py asks git's index via
        `git ls-files` -- so this is not tautological: a file present on disk
        but not yet `git add`ed (or the reverse, a file staged for deletion but
        still on disk) makes them disagree, and that disagreement is exactly
        the live-vs-tracked split this test exists to catch. There is no
        stored artifact any more (removed by PR #830); the reference side is
        gen_suite_counts.py's live `--json` output, computed fresh here.
        """
        result = subprocess.run(
            [sys.executable, "tools/list_test_suites.py", "--repo", str(self.repo_root)],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        output = result.stdout

        import re
        import json

        node_match = re.search(r"## Node\.js \((\d+) suites?\)", output)
        shell_match = re.search(r"## Shell \((\d+) suites?\)", output)
        python_match = re.search(r"## Python \((\d+) suites?\)", output)

        self.assertIsNotNone(node_match, "Could not extract Node count")
        self.assertIsNotNone(shell_match, "Could not extract Shell count")
        self.assertIsNotNone(python_match, "Could not extract Python count")

        list_node = int(node_match.group(1))
        list_shell = int(shell_match.group(1))
        list_python = int(python_match.group(1))

        # Reference: live counts derived straight from git ls-files, computed
        # fresh (no stored artifact to read).
        gen_result = subprocess.run(
            [sys.executable, "tools/gen_suite_counts.py", "--json",
             "--repo", str(self.repo_root)],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(gen_result.returncode, 0, f"stderr: {gen_result.stderr}")
        live_counts = json.loads(gen_result.stdout)

        self.assertEqual(
            list_node,
            live_counts["Node"],
            f"Node count mismatch: list_test_suites (filesystem) says {list_node}, "
            f"gen_suite_counts.py (git index) says {live_counts['Node']}",
        )
        self.assertEqual(
            list_shell,
            live_counts["Shell"],
            f"Shell count mismatch: list_test_suites (filesystem) says {list_shell}, "
            f"gen_suite_counts.py (git index) says {live_counts['Shell']}",
        )
        self.assertEqual(
            list_python,
            live_counts["Python"],
            f"Python count mismatch: list_test_suites (filesystem) says {list_python}, "
            f"gen_suite_counts.py (git index) says {live_counts['Python']}",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
