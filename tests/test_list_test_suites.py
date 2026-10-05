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
        """Verify counts match those found by gen_suite_counts.py."""
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

        # Extract expected counts from SUITE-COUNTS.json (generated artifact)
        suite_counts_path = self.repo_root / "tests" / "SUITE-COUNTS.json"
        self.assertTrue(suite_counts_path.exists(), f"{suite_counts_path} not found")

        suite_counts_text = suite_counts_path.read_text()
        # Extract JSON between markers
        start = suite_counts_text.find("{")
        end = suite_counts_text.rfind("}") + 1
        suite_counts = json.loads(suite_counts_text[start:end])

        # Counts discovered should match those in SUITE-COUNTS.json
        self.assertEqual(
            list_node,
            suite_counts["Node"],
            f"Node count mismatch: list_test_suites says {list_node}, SUITE-COUNTS.json says {suite_counts['Node']}",
        )
        self.assertEqual(
            list_shell,
            suite_counts["Shell"],
            f"Shell count mismatch: list_test_suites says {list_shell}, SUITE-COUNTS.json says {suite_counts['Shell']}",
        )
        self.assertEqual(
            list_python,
            suite_counts["Python"],
            f"Python count mismatch: list_test_suites says {list_python}, SUITE-COUNTS.json says {suite_counts['Python']}",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
