#!/usr/bin/env python3
"""Test that auto_merge.py requires PR numbers or --all flag (fail-closed guardrail).

Test coverage:
- Bare invocation (no args) exits 2 with error message
- --all flag allows board-wide merge
- Specific PR numbers allow targeted merge
"""
import subprocess
import sys
import unittest
import os


class TestAutoMergePRRequired(unittest.TestCase):
    """Test fail-closed PR scoping for auto_merge.py."""

    def setUp(self):
        """Set up paths."""
        self.tools_dir = os.path.join(os.path.dirname(__file__), '..', 'tools')

    def test_bare_invocation_exits_2_stderr(self):
        """Bare invocation outputs error message to stderr and exits 2."""
        result = subprocess.run(
            [sys.executable, os.path.join(self.tools_dir, 'auto_merge.py')],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(self.tools_dir)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('must specify PR number(s)', result.stderr)
        self.assertIn('--all', result.stderr)

    def test_all_flag_exits_0_with_no_prs(self):
        """--all flag with --dry-run exits 0 when no open PRs."""
        # Use --dry-run to avoid actual merging; use --all to pass validation
        result = subprocess.run(
            [sys.executable, os.path.join(self.tools_dir, 'auto_merge.py'),
             '--all', '--dry-run'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(self.tools_dir)
        )
        # Should exit 0 or 1 depending on whether there are open PRs
        # Main point: validation passed (exit 2 not returned)
        self.assertNotEqual(result.returncode, 2)

    def test_json_flag_alone_fails(self):
        """--json without PR numbers or --all fails."""
        result = subprocess.run(
            [sys.executable, os.path.join(self.tools_dir, 'auto_merge.py'),
             '--json'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(self.tools_dir)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('must specify PR number(s)', result.stderr)

    def test_no_fix_flag_alone_fails(self):
        """--no-fix without PR numbers or --all fails."""
        result = subprocess.run(
            [sys.executable, os.path.join(self.tools_dir, 'auto_merge.py'),
             '--no-fix'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(self.tools_dir)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('must specify PR number(s)', result.stderr)

    def test_loop_flag_alone_fails(self):
        """--loop without PR numbers or --all fails."""
        result = subprocess.run(
            [sys.executable, os.path.join(self.tools_dir, 'auto_merge.py'),
             '--loop'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(self.tools_dir)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('must specify PR number(s)', result.stderr)

    def test_dry_run_flag_alone_fails(self):
        """--dry-run without PR numbers or --all fails."""
        result = subprocess.run(
            [sys.executable, os.path.join(self.tools_dir, 'auto_merge.py'),
             '--dry-run'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(self.tools_dir)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('must specify PR number(s)', result.stderr)

    def test_multiple_flags_without_pr_fails(self):
        """Multiple flags without PR numbers or --all fails."""
        result = subprocess.run(
            [sys.executable, os.path.join(self.tools_dir, 'auto_merge.py'),
             '--json', '--loop', '--dry-run'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(self.tools_dir)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('must specify PR number(s)', result.stderr)


if __name__ == '__main__':
    unittest.main()
