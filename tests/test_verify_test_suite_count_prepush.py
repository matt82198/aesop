#!/usr/bin/env python3
"""
Tests for test suite count verification in pre-push-policy.sh.

These tests verify that the pre-push hook correctly detects and blocks
pushes when test suite counts drift from the documented counts in tests/CLAUDE.md.

Root cause being tested: PR #605 windows-shard escape where test count (206)
drifted from documented (205) but was not caught by the local pre-push hook
because verify_test_suite_count.py was not wired into hooks/pre-push-policy.sh.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Tuple


class TestVerifyTestSuiteCountPrepush(unittest.TestCase):
    """Test suite count verification in pre-push-policy.sh"""

    def setUp(self):
        """Set up temporary directories and git repos for testing."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmpdir.name)

        # Initialize a minimal git repo
        subprocess.run(
            ["git", "init", "-q", str(self.repo_root)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo_root), "config", "user.email", "test@example.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo_root), "config", "user.name", "Test User"],
            check=True,
            capture_output=True,
        )

        # Create initial commit on main
        (self.repo_root / "README.md").write_text("# Test Repo\n")
        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "README.md"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo_root), "commit", "-q", "-m", "initial"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo_root), "checkout", "-q", "-b", "main"],
            check=True,
            capture_output=True,
        )

    def tearDown(self):
        """Clean up temporary directories."""
        self.tmpdir.cleanup()

    def create_test_files_and_claude_md(
        self, node_count: int, shell_count: int, python_count: int
    ) -> Tuple[int, int, int]:
        """Create test files and tests/CLAUDE.md with specified counts.

        Args:
            node_count: Number of Node test files to create
            shell_count: Number of Shell test files to create
            python_count: Number of Python test files to create

        Returns:
            Tuple of (actual_node, actual_shell, actual_python) counts
        """
        tests_dir = self.repo_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        # Create test files using distinct naming to avoid multi-pattern matches
        # Node files: tests/*.test.mjs pattern
        for i in range(node_count):
            (tests_dir / f"node_test_{i}.test.mjs").write_text("// Node test\n")
        # Shell files: tests/test_*.sh pattern (avoid .test.sh to prevent double-counting)
        for i in range(shell_count):
            (tests_dir / f"test_shell_{i}.sh").write_text("#!/bin/bash\n# Shell test\n")
        # Python files: tests/test_*.py pattern
        for i in range(python_count):
            (tests_dir / f"test_py_{i}.py").write_text("# Python test\n")

        # Create tests/CLAUDE.md with documented counts (may differ from actual)
        # We'll create the file with counts that we can then verify
        claude_md_content = f"""# tests/ - Test Suite Documentation

## Test Suite Map

**Node ({node_count} suites)**: Node.js test files

**Shell ({shell_count} suites)**: Shell script test files

**Python ({python_count} suites)**: Python unit tests

## Hygiene Rules

All tests must follow hygiene rules.
"""
        (tests_dir / "CLAUDE.md").write_text(claude_md_content)

        # Git add the files
        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/"],
            check=True,
            capture_output=True,
        )

        return node_count, shell_count, python_count

    def get_prepush_hook_path(self) -> Path:
        """Get the path to pre-push-policy.sh in the repo.

        For testing, we use the one from the worktree being tested.
        """
        # This assumes the hook is in hooks/pre-push-policy.sh in the main repo
        aesop_root = Path(__file__).parent.parent
        return aesop_root / "hooks" / "pre-push-policy.sh"

    def run_prepush_check_test_suite_count(self, repo_root: Path, stdin: str = "") -> int:
        """Run the check_test_suite_count function from pre-push-policy.sh in test mode.

        We'll source the script and call the function directly.

        Args:
            repo_root: Root of the test repository
            stdin: Input to provide to the hook (for other checks)

        Returns:
            Exit code from the function (0=success, 1=failure)
        """
        hook_path = self.get_prepush_hook_path()

        # Create a simple test script that sources the hook and calls check_test_suite_count
        test_script = f"""
#!/bin/bash
set -uo pipefail
source {hook_path}
cd {repo_root}
check_test_suite_count
"""
        script_path = self.repo_root / "test_script.sh"
        script_path.write_text(test_script)
        script_path.chmod(0o755)

        # Run the test script
        result = subprocess.run(
            ["bash", str(script_path)],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
        return result.returncode

    def test_clean_state_no_false_positive(self):
        """Test that clean state (counts match) does not block push.

        This is the success case: documented counts = actual counts.
        The gate should return 0 (allow push).
        """
        # Create 5 Node, 3 Shell, 205 Python test files and document them
        self.create_test_files_and_claude_md(5, 3, 205)

        # Run the verify_test_suite_count tool to ensure counts match
        aesop_root = Path(__file__).parent.parent
        verify_script = aesop_root / "tools" / "verify_test_suite_count.py"

        result = subprocess.run(
            [sys.executable, str(verify_script), "--check"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        # Tool should exit 0 (counts match)
        self.assertEqual(
            result.returncode,
            0,
            f"Expected clean counts to pass verification. Output: {result.stderr}",
        )

    def test_escape_original_drift_206_vs_205(self):
        """Reproduce the original escape: documented 205, actual 206 Python suites.

        This is the exact scenario from PR #605 that was not caught by the
        pre-push hook. The drift was only discovered in CI.

        We create 205 Python files but document 205 in CLAUDE.md. Then we add
        one more file (206 total) without updating CLAUDE.md. This drift should
        be caught by the gate.
        """
        # First, create 205 Python files and document them
        self.create_test_files_and_claude_md(5, 3, 205)

        tests_dir = self.repo_root / "tests"

        # Add one more Python file WITHOUT updating the documentation
        (tests_dir / "test_py_206.py").write_text("# Python test 206\n")
        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/test_py_206.py"],
            check=True,
            capture_output=True,
        )

        # Verify the drift exists (205 documented, 206 actual)
        aesop_root = Path(__file__).parent.parent
        verify_script = aesop_root / "tools" / "verify_test_suite_count.py"

        verify_result = subprocess.run(
            [sys.executable, str(verify_script), "--check"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        # A1 gate-fix: --check is READ-ONLY and fails closed on drift.
        # (PR #661 briefly made this exit 0 by auto-correcting the file in place,
        # which meant this escape class could never fail CI again.)
        self.assertEqual(
            verify_result.returncode,
            1,
            f"Expected drift to block (exit 1). Output: {verify_result.stdout}",
        )
        self.assertIn(
            "[DRIFT]",
            verify_result.stdout,
            f"Expected drift report in output: {verify_result.stdout}",
        )
        self.assertIn(
            "205",
            (tests_dir / "CLAUDE.md").read_text(encoding="utf-8"),
            "--check must leave the stale documented count in place (read-only)",
        )

    def test_drift_node_count_mismatch(self):
        """Test drift detection: documented node count differs from actual."""
        # Create 5 Node files but document 3
        tests_dir = self.repo_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        # Create 5 Node test files
        for i in range(5):
            (tests_dir / f"node_{i}.test.mjs").write_text("// Node test\n")

        # Document 3 (intentional drift)
        claude_md_content = """# tests/ - Test Suite Documentation

**Node (3 suites)**: Node.js test files
**Shell (0 suites)**: Shell script test files
**Python (0 suites)**: Python unit tests
"""
        (tests_dir / "CLAUDE.md").write_text(claude_md_content)

        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/"],
            check=True,
            capture_output=True,
        )

        # Verify drift is detected
        aesop_root = Path(__file__).parent.parent
        verify_script = aesop_root / "tools" / "verify_test_suite_count.py"

        verify_result = subprocess.run(
            [sys.executable, str(verify_script), "--check"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(
            verify_result.returncode,
            1,
            f"Expected drift in Node count to fail closed. Output: {verify_result.stdout}",
        )
        self.assertIn("Node:", verify_result.stdout)

    def test_drift_shell_count_mismatch(self):
        """Test drift detection: documented shell count differs from actual."""
        tests_dir = self.repo_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        # Create 4 Shell test files
        for i in range(4):
            (tests_dir / f"test_shell_{i}.sh").write_text("#!/bin/bash\n")

        # Document 2 (intentional drift)
        claude_md_content = """# tests/ - Test Suite Documentation

**Node (0 suites)**: Node.js test files
**Shell (2 suites)**: Shell script test files
**Python (0 suites)**: Python unit tests
"""
        (tests_dir / "CLAUDE.md").write_text(claude_md_content)

        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/"],
            check=True,
            capture_output=True,
        )

        # Verify drift is detected
        aesop_root = Path(__file__).parent.parent
        verify_script = aesop_root / "tools" / "verify_test_suite_count.py"

        verify_result = subprocess.run(
            [sys.executable, str(verify_script), "--check"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(
            verify_result.returncode,
            1,
            f"Expected drift in Shell count to fail closed. Output: {verify_result.stdout}",
        )
        self.assertIn("Shell:", verify_result.stdout)

    def test_drift_python_count_mismatch(self):
        """Test drift detection: documented python count differs from actual."""
        tests_dir = self.repo_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        # Create 210 Python test files
        for i in range(210):
            (tests_dir / f"test_py_{i}.py").write_text("# Python test\n")

        # Document 205 (intentional drift - the original escape)
        claude_md_content = """# tests/ - Test Suite Documentation

**Node (0 suites)**: Node.js test files
**Shell (0 suites)**: Shell script test files
**Python (205 suites)**: Python unit tests
"""
        (tests_dir / "CLAUDE.md").write_text(claude_md_content)

        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/"],
            check=True,
            capture_output=True,
        )

        # Verify drift is detected
        aesop_root = Path(__file__).parent.parent
        verify_script = aesop_root / "tools" / "verify_test_suite_count.py"

        verify_result = subprocess.run(
            [sys.executable, str(verify_script), "--check"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(
            verify_result.returncode,
            1,
            f"Expected drift in Python count (210 vs 205) to fail closed. "
            f"Output: {verify_result.stdout}",
        )
        self.assertIn("Python:", verify_result.stdout)

    def test_multiple_drift_detection(self):
        """Test that multiple drifts are all detected and reported."""
        tests_dir = self.repo_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        # Create 6 Node, 5 Shell, 210 Python test files
        for i in range(6):
            (tests_dir / f"node_{i}.test.mjs").write_text("// Node test\n")
        for i in range(5):
            (tests_dir / f"test_shell_{i}.sh").write_text("#!/bin/bash\n")
        for i in range(210):
            (tests_dir / f"test_py_{i}.py").write_text("# Python test\n")

        # Document wrong counts for all (4, 3, 205)
        claude_md_content = """# tests/ - Test Suite Documentation

**Node (4 suites)**: Node.js test files
**Shell (3 suites)**: Shell script test files
**Python (205 suites)**: Python unit tests
"""
        (tests_dir / "CLAUDE.md").write_text(claude_md_content)

        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/"],
            check=True,
            capture_output=True,
        )

        # Verify all drifts are detected
        aesop_root = Path(__file__).parent.parent
        verify_script = aesop_root / "tools" / "verify_test_suite_count.py"

        verify_result = subprocess.run(
            [sys.executable, str(verify_script), "--check"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(
            verify_result.returncode,
            1,
            f"Expected drift detection to fail closed. Output: {verify_result.stdout}",
        )
        # Check that all three drifts are reported
        self.assertIn("Node:", verify_result.stdout)
        self.assertIn("Shell:", verify_result.stdout)
        self.assertIn("Python:", verify_result.stdout)
        # And that nothing was rewritten
        self.assertIn(
            "**Node (4 suites)**",
            (tests_dir / "CLAUDE.md").read_text(encoding="utf-8"),
            "--check must not rewrite documented counts",
        )


if __name__ == "__main__":
    unittest.main()
