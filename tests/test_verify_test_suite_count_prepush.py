#!/usr/bin/env python3
"""
Tests for test suite count verification in pre-push-policy.sh.

These tests verify that the pre-push hook correctly detects and blocks
pushes when test suite counts drift from the documented counts in the
generated tests/SUITE-COUNTS.json artifact.

Root cause being tested: PR #605 windows-shard escape where test count (206)
drifted from documented (205) but was not caught by the local pre-push hook
because verify_test_suite_count.py was not wired into hooks/pre-push-policy.sh.

Structural fix #776 moved the documented counts out of hand-maintained
tests/CLAUDE.md lines (every test-adding PR touching the same line serialized
the PR board) into generated tests/SUITE-COUNTS.json, produced by
tools/gen_suite_counts.py. tools/verify_test_suite_count.py is now a thin
backward-compatible wrapper around that tool, and hooks/pre-push-policy.sh is
unchanged: it still just runs `verify_test_suite_count.py --check` in the repo
being pushed. These fixtures therefore write tests/SUITE-COUNTS.json instead of
tests/CLAUDE.md count lines, but the escape being guarded against is the same.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional, Tuple


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

    def create_test_files_and_suite_counts(
        self, node_count: int, shell_count: int, python_count: int
    ) -> Tuple[int, int, int]:
        """Create test files and tests/SUITE-COUNTS.json with specified counts.

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

        # Create the generated artifact with documented counts (may differ from
        # actual -- that mismatch is exactly what each drift test exercises).
        # Matches the byte format tools/gen_suite_counts.py itself writes.
        suite_counts_content = (
            "<!-- GENERATED-BY: tools/gen_suite_counts.py -->\n"
            "{\n"
            f'  "Node": {node_count},\n'
            f'  "Shell": {shell_count},\n'
            f'  "Python": {python_count}\n'
            "}\n"
            "<!-- END-GENERATED -->\n"
        )
        (tests_dir / "SUITE-COUNTS.json").write_text(suite_counts_content)

        # Git add the files
        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/"],
            check=True,
            capture_output=True,
        )

        return node_count, shell_count, python_count

    def get_aesop_root(self) -> Path:
        """The real aesop checkout this test file lives in (has tools/gen_suite_counts.py)."""
        return Path(__file__).parent.parent

    def get_prepush_hook_path(self) -> Path:
        """Get the path to pre-push-policy.sh in the repo.

        For testing, we use the one from the worktree being tested.
        """
        return self.get_aesop_root() / "hooks" / "pre-push-policy.sh"

    def run_prepush_check_test_suite_count(
        self, repo_root: Path, aesop_root: Optional[Path] = None, stdin: str = ""
    ) -> int:
        """Run the check_test_suite_count function from pre-push-policy.sh in test mode.

        We'll source the script and call the function directly, with cwd set to
        the fixture repo being pushed -- exactly how a real pre-push hook runs.

        Args:
            repo_root: Root of the test repository (the tree being "pushed")
            aesop_root: The aesop checkout providing tools/verify_test_suite_count.py.
                Defaults to this test file's own checkout. Exported as AESOP_ROOT so
                resolve_aesop_root() does not fall back to the fixture repo's own git
                toplevel (which has no tools/ directory at all).
            stdin: Input to provide to the hook (for other checks)

        Returns:
            Exit code from the function (0=success, 1=failure)
        """
        hook_path = self.get_prepush_hook_path()
        if aesop_root is None:
            aesop_root = self.get_aesop_root()

        # Posix-ify every path interpolated into the script: on Windows a raw
        # backslash path (C:\Users\...) is eaten by bash as escape sequences
        # (producing "C:Usersmatt8..."), but Git Bash accepts "C:/Users/..." as
        # a valid absolute path directly.
        aesop_root_posix = Path(aesop_root).as_posix()
        hook_path_posix = Path(hook_path).as_posix()
        repo_root_posix = Path(repo_root).as_posix()

        # Create a simple test script that sources the hook and calls check_test_suite_count
        test_script = f"""
#!/bin/bash
set -uo pipefail
export AESOP_ROOT="{aesop_root_posix}"
source "{hook_path_posix}"
cd "{repo_root_posix}"
check_test_suite_count
"""
        script_path = self.repo_root / "test_script.sh"
        script_path.write_text(test_script)
        script_path.chmod(0o755)

        # Resolve bash's full path explicitly: on Windows, a bare "bash" can
        # resolve to the WSL launcher stub (no distributions installed) rather
        # than Git Bash, depending on PATH search order for subprocess.run.
        bash_path = shutil.which("bash") or "bash"

        # Run the test script
        result = subprocess.run(
            [bash_path, str(script_path)],
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
        self.create_test_files_and_suite_counts(5, 3, 205)

        # Run the verify_test_suite_count tool to ensure counts match
        aesop_root = self.get_aesop_root()
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

    def test_hook_allows_push_on_clean_state(self):
        """The REAL check_test_suite_count hook function must allow a clean push.

        Unlike test_clean_state_no_false_positive (which calls the tool directly),
        this exercises the actual pre-push-policy.sh wiring end to end.
        """
        self.create_test_files_and_suite_counts(5, 3, 205)

        exit_code = self.run_prepush_check_test_suite_count(self.repo_root)

        self.assertEqual(
            exit_code,
            0,
            "pre-push hook's check_test_suite_count must allow a push when "
            "tests/SUITE-COUNTS.json matches the actual test files",
        )

    def test_hook_blocks_push_on_undeclared_test_file(self):
        """RED-FIRST: the REAL pre-push hook must block an undeclared test file.

        Reproduces the PR #605 escape end to end through hooks/pre-push-policy.sh
        itself (not just the bare tool): 205 Python suites are documented, a 206th
        is added without regenerating tests/SUITE-COUNTS.json, and the hook's
        check_test_suite_count function -- the thing that actually runs at push
        time -- must refuse the push (nonzero exit), proving the gate is wired,
        not just that the underlying tool can detect drift in isolation.
        """
        self.create_test_files_and_suite_counts(5, 3, 205)

        tests_dir = self.repo_root / "tests"
        (tests_dir / "test_py_206.py").write_text("# Python test 206\n")
        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/test_py_206.py"],
            check=True,
            capture_output=True,
        )

        exit_code = self.run_prepush_check_test_suite_count(self.repo_root)

        self.assertNotEqual(
            exit_code,
            0,
            "pre-push hook's check_test_suite_count must block a push that adds "
            "a test file without regenerating tests/SUITE-COUNTS.json",
        )

    def test_escape_original_drift_206_vs_205(self):
        """Reproduce the original escape: documented 205, actual 206 Python suites.

        This is the exact scenario from PR #605 that was not caught by the
        pre-push hook. The drift was only discovered in CI.

        We create 205 Python files but document 205 in the generated artifact.
        Then we add one more file (206 total) without regenerating the artifact.
        This drift should be caught by the gate.
        """
        # First, create 205 Python files and document them
        self.create_test_files_and_suite_counts(5, 3, 205)

        tests_dir = self.repo_root / "tests"

        # Add one more Python file WITHOUT regenerating the documentation
        (tests_dir / "test_py_206.py").write_text("# Python test 206\n")
        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/test_py_206.py"],
            check=True,
            capture_output=True,
        )

        # Verify the drift exists (205 documented, 206 actual)
        aesop_root = self.get_aesop_root()
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
            (tests_dir / "SUITE-COUNTS.json").read_text(encoding="utf-8"),
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
        # Shell and Python are genuinely non-empty (and correctly documented)
        # so only the targeted Node family drifts -- an actual family of ZERO
        # fails closed (exit 2) regardless of what is documented, which would
        # mask the drift this test means to exercise.
        (tests_dir / "test_shell_0.sh").write_text("#!/bin/bash\n")
        (tests_dir / "test_py_0.py").write_text("# Python test\n")

        # Document 3 (intentional drift)
        suite_counts_content = (
            "<!-- GENERATED-BY: tools/gen_suite_counts.py -->\n"
            "{\n"
            '  "Node": 3,\n'
            '  "Shell": 1,\n'
            '  "Python": 1\n'
            "}\n"
            "<!-- END-GENERATED -->\n"
        )
        (tests_dir / "SUITE-COUNTS.json").write_text(suite_counts_content)

        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/"],
            check=True,
            capture_output=True,
        )

        # Verify drift is detected
        aesop_root = self.get_aesop_root()
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
        # Node and Python are genuinely non-empty (and correctly documented) so
        # only the targeted Shell family drifts; see test_drift_node_count_mismatch
        # for why an actual family of zero would fail closed (exit 2) instead.
        (tests_dir / "node_0.test.mjs").write_text("// Node test\n")
        (tests_dir / "test_py_0.py").write_text("# Python test\n")

        # Document 2 (intentional drift)
        suite_counts_content = (
            "<!-- GENERATED-BY: tools/gen_suite_counts.py -->\n"
            "{\n"
            '  "Node": 1,\n'
            '  "Shell": 2,\n'
            '  "Python": 1\n'
            "}\n"
            "<!-- END-GENERATED -->\n"
        )
        (tests_dir / "SUITE-COUNTS.json").write_text(suite_counts_content)

        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/"],
            check=True,
            capture_output=True,
        )

        # Verify drift is detected
        aesop_root = self.get_aesop_root()
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
        # Node and Shell are genuinely non-empty (and correctly documented) so
        # only the targeted Python family drifts; see test_drift_node_count_mismatch
        # for why an actual family of zero would fail closed (exit 2) instead.
        (tests_dir / "node_0.test.mjs").write_text("// Node test\n")
        (tests_dir / "test_shell_0.sh").write_text("#!/bin/bash\n")

        # Document 205 (intentional drift - the original escape)
        suite_counts_content = (
            "<!-- GENERATED-BY: tools/gen_suite_counts.py -->\n"
            "{\n"
            '  "Node": 1,\n'
            '  "Shell": 1,\n'
            '  "Python": 205\n'
            "}\n"
            "<!-- END-GENERATED -->\n"
        )
        (tests_dir / "SUITE-COUNTS.json").write_text(suite_counts_content)

        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/"],
            check=True,
            capture_output=True,
        )

        # Verify drift is detected
        aesop_root = self.get_aesop_root()
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
        suite_counts_content = (
            "<!-- GENERATED-BY: tools/gen_suite_counts.py -->\n"
            "{\n"
            '  "Node": 4,\n'
            '  "Shell": 3,\n'
            '  "Python": 205\n'
            "}\n"
            "<!-- END-GENERATED -->\n"
        )
        (tests_dir / "SUITE-COUNTS.json").write_text(suite_counts_content)

        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/"],
            check=True,
            capture_output=True,
        )

        # Verify all drifts are detected
        aesop_root = self.get_aesop_root()
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
            '"Node": 4',
            (tests_dir / "SUITE-COUNTS.json").read_text(encoding="utf-8"),
            "--check must not rewrite documented counts",
        )


if __name__ == "__main__":
    unittest.main()
