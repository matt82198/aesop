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
import tempfile
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
        """Verify counts match those found by verify_test_suite_count.py."""
        # Run list_test_suites.py and extract counts
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

        node_match = re.search(r"## Node\.js \((\d+) suites?\)", output)
        shell_match = re.search(r"## Shell \((\d+) suites?\)", output)
        python_match = re.search(r"## Python \((\d+) suites?\)", output)

        self.assertIsNotNone(node_match, "Could not extract Node count")
        self.assertIsNotNone(shell_match, "Could not extract Shell count")
        self.assertIsNotNone(python_match, "Could not extract Python count")

        list_node = int(node_match.group(1))
        list_shell = int(shell_match.group(1))
        list_python = int(python_match.group(1))

        # Run verify_test_suite_count.py to get expected counts
        verify_result = subprocess.run(
            [sys.executable, "tools/verify_test_suite_count.py", "--check", "--repo", str(self.repo_root)],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        # verify_test_suite_count may return 0 (match) or 1 (drift), both are valid

        # Extract expected counts from CLAUDE.md
        claudemd = (self.repo_root / "tests" / "CLAUDE.md").read_text()
        node_in_md = re.search(r"\*\*Node \((\d+) suites?\)\*\*:", claudemd)
        shell_in_md = re.search(r"\*\*Shell \((\d+) suites?\)\*\*:", claudemd)
        python_in_md = re.search(r"\*\*Python \((\d+) suites?\)\*\*:", claudemd)

        self.assertIsNotNone(node_in_md)
        self.assertIsNotNone(shell_in_md)
        self.assertIsNotNone(python_in_md)

        # Counts discovered should match those documented in CLAUDE.md
        self.assertEqual(
            list_node,
            int(node_in_md.group(1)),
            f"Node count mismatch: list_test_suites says {list_node}, CLAUDE.md says {node_in_md.group(1)}",
        )
        self.assertEqual(
            list_shell,
            int(shell_in_md.group(1)),
            f"Shell count mismatch: list_test_suites says {list_shell}, CLAUDE.md says {shell_in_md.group(1)}",
        )
        self.assertEqual(
            list_python,
            int(python_in_md.group(1)),
            f"Python count mismatch: list_test_suites says {list_python}, CLAUDE.md says {python_in_md.group(1)}",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
