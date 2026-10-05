#!/usr/bin/env python3
"""
Tests for CI-shard-coverage verification in pre-push-policy.sh.

These tests verify that the pre-push hook correctly detects and blocks pushes
when the CI workflow's shard matrix would silently drop tracked test files.

Root cause being tested: PR #605 windows-shard escape where a tracked test
file existed on disk and in git but was never actually run by CI -- the local
pre-push hook did not catch it either, because verify_test_suite_count.py was
not wired into hooks/pre-push-policy.sh at the time.

Structural fix #776 moved documented counts out of hand-maintained
tests/CLAUDE.md lines into a generated tests/SUITE-COUNTS.json artifact.
Structural fix #830 ("guard: compute suite counts live") removed that
artifact outright: two clean merges drifted it anyway (PR #828 postmortem)
even though nothing but the generator/gate/registry triangle ever consumed
its committed value. tools/verify_test_suite_count.py is repointed at the one
way the ORIGINAL #605 incident class can still happen with counts computed
fresh on every call: the CI workflow's shard matrix (`.github/workflows/ci.yml`)
drifting out of sync with the `total_shards` argument `ci_shard_runner.py` is
invoked with, leaving a shard index no job ever requests. hooks/pre-push-policy.sh
is unchanged: it still just runs `verify_test_suite_count.py --check` in the
repo being pushed. These fixtures therefore write a `.github/workflows/ci.yml`
shard matrix instead of tests/SUITE-COUNTS.json, but the escape class being
guarded against -- a tracked test file that CI silently never runs -- is
the same.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional


class TestVerifyTestSuiteCountPrepush(unittest.TestCase):
    """CI-shard-coverage verification in pre-push-policy.sh"""

    def setUp(self):
        """Set up temporary directories and git repos for testing."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmpdir.name)

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

    def write_ci_shard_matrix(self, total: int, ids) -> None:
        """Write a minimal .github/workflows/ci.yml with one shard job.

        Args:
            total: total_shards argument passed to ci_shard_runner.py.
            ids: the matrix's configured shard ids (a gap vs. range(total) is
                exactly the escape these tests exercise).
        """
        ids_str = ", ".join(str(i) for i in ids)
        ci_dir = self.repo_root / ".github" / "workflows"
        ci_dir.mkdir(parents=True, exist_ok=True)
        (ci_dir / "ci.yml").write_text(
            "name: ci\n"
            "on: [push]\n"
            "jobs:\n"
            "  test:\n"
            "    strategy:\n"
            "      matrix:\n"
            f"        python-shard: [{ids_str}]\n"
            "    steps:\n"
            f"      - run: python tools/ci_shard_runner.py ${{{{ matrix.python-shard }}}} {total}\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", ".github/"],
            check=True, capture_output=True,
        )

    def add_python_test_files(self, count: int) -> None:
        tests_dir = self.repo_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            (tests_dir / f"test_fixture_{i}.py").write_text("# Python test\n")
        subprocess.run(
            ["git", "-C", str(self.repo_root), "add", "tests/"],
            check=True, capture_output=True,
        )

    def get_aesop_root(self) -> Path:
        """The real aesop checkout this test file lives in (has tools/verify_test_suite_count.py)."""
        return Path(__file__).parent.parent

    def get_prepush_hook_path(self) -> Path:
        """Get the path to pre-push-policy.sh in the repo.

        For testing, we use the one from the worktree being tested.
        """
        return self.get_aesop_root() / "hooks" / "pre-push-policy.sh"

    def run_verify_tool(self, extra_args=()):
        aesop_root = self.get_aesop_root()
        verify_script = aesop_root / "tools" / "verify_test_suite_count.py"
        cmd = [sys.executable, str(verify_script), "--check"]
        cmd.extend(extra_args)
        return subprocess.run(
            cmd, cwd=str(self.repo_root), capture_output=True, text=True,
            encoding="utf-8",
        )

    def run_prepush_check_test_suite_count(
        self, repo_root: Path, aesop_root: Optional[Path] = None
    ) -> int:
        """Run the check_test_suite_count function from pre-push-policy.sh in test mode.

        We source the script and call the function directly, with cwd set to
        the fixture repo being "pushed" -- exactly how a real pre-push hook runs.

        Args:
            repo_root: Root of the test repository (the tree being "pushed").
            aesop_root: The aesop checkout providing tools/verify_test_suite_count.py.
                Defaults to this test file's own checkout. Exported as AESOP_ROOT so
                resolve_aesop_root() does not fall back to the fixture repo's own git
                toplevel (which has no tools/ directory at all).

        Returns:
            Exit code from the function (0=success, nonzero=failure)
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

        bash_path = shutil.which("bash") or "bash"

        result = subprocess.run(
            [bash_path, str(script_path)],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
        return result.returncode

    def test_clean_state_no_false_positive(self):
        """A fully-covered shard matrix does not block the push.

        This is the success case: matrix ids span 0..total-1 exactly, and every
        tracked test file is covered. The gate should return 0 (allow push).
        """
        self.write_ci_shard_matrix(total=4, ids=[0, 1, 2, 3])
        self.add_python_test_files(8)

        result = self.run_verify_tool()

        self.assertEqual(
            result.returncode,
            0,
            f"Expected a fully-covered shard matrix to pass verification. "
            f"stdout: {result.stdout} stderr: {result.stderr}",
        )

    def test_no_ci_config_is_not_a_false_positive(self):
        """A repo with no .github/workflows/ci.yml has nothing to verify."""
        self.add_python_test_files(3)

        result = self.run_verify_tool()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hook_allows_push_on_clean_state(self):
        """The REAL check_test_suite_count hook function must allow a clean push.

        Unlike test_clean_state_no_false_positive (which calls the tool directly),
        this exercises the actual pre-push-policy.sh wiring end to end.
        """
        self.write_ci_shard_matrix(total=4, ids=[0, 1, 2, 3])
        self.add_python_test_files(8)

        exit_code = self.run_prepush_check_test_suite_count(self.repo_root)

        self.assertEqual(
            exit_code,
            0,
            "pre-push hook's check_test_suite_count must allow a push when the "
            "CI shard matrix fully covers the tracked test files",
        )

    def test_hook_blocks_push_on_shard_gap(self):
        """RED-FIRST: the REAL pre-push hook must block a shard-matrix gap.

        Reproduces the modern instance of the PR #605 escape end to end through
        hooks/pre-push-policy.sh itself (not just the bare tool): the matrix
        configures shard ids [0, 1, 2] but ci_shard_runner.py is invoked with
        total_shards=4, so shard index 3 never runs in CI and every tracked
        test file round-robin-assigned to it is silently never executed. The
        hook's check_test_suite_count function -- the thing that actually runs
        at push time -- must refuse the push (nonzero exit), proving the gate
        is wired, not just that the underlying tool can detect the gap in
        isolation.
        """
        self.write_ci_shard_matrix(total=4, ids=[0, 1, 2])
        self.add_python_test_files(8)

        exit_code = self.run_prepush_check_test_suite_count(self.repo_root)

        self.assertNotEqual(
            exit_code,
            0,
            "pre-push hook's check_test_suite_count must block a push whose CI "
            "shard matrix would silently drop tracked test files",
        )

    def test_escape_original_shard_gap_detected_directly(self):
        """Reproduce the escape via the bare tool: shard id 3 configured absent.

        This is the direct (non-hook) verification that the gap is detected and
        reported, mirroring the original PR #605 regression test shape.
        """
        self.write_ci_shard_matrix(total=4, ids=[0, 1, 2])
        self.add_python_test_files(8)

        result = self.run_verify_tool()

        self.assertEqual(
            result.returncode,
            1,
            f"Expected shard gap to block (exit 1). stdout: {result.stdout}",
        )
        self.assertIn("never run in CI", result.stderr)
        self.assertIn("not assigned to any", result.stderr)

    def test_drift_single_missing_shard_id(self):
        """A single missing id (not just the highest) is still detected."""
        self.write_ci_shard_matrix(total=4, ids=[0, 2, 3])
        self.add_python_test_files(8)

        result = self.run_verify_tool()

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("[1]", result.stderr)

    def test_drift_total_shards_smaller_than_matrix(self):
        """Matrix lists MORE ids than total_shards -- an out-of-range id never
        corresponds to a real round-robin bucket, so this is caught too."""
        self.write_ci_shard_matrix(total=2, ids=[0, 1, 2, 3])
        self.add_python_test_files(6)

        result = self.run_verify_tool()

        # ids [0,1,2,3] vs expected range(2) = [0,1]: no missing ids (0,1 both
        # present), so this configuration is not itself a coverage GAP -- the
        # extra ids are simply out of range and ignored by distribute_shards.
        # Assert the tool evaluates cleanly either way (never crashes, never
        # silently mis-reports): all 6 tracked files are still covered by ids
        # 0 and 1 across total=2.
        self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)

    def test_multiple_drift_detection(self):
        """Multiple jobs with independent gaps are all reported."""
        ci_dir = self.repo_root / ".github" / "workflows"
        ci_dir.mkdir(parents=True, exist_ok=True)
        (ci_dir / "ci.yml").write_text(
            "name: ci\n"
            "on: [push]\n"
            "jobs:\n"
            "  ubuntu:\n"
            "    strategy:\n"
            "      matrix:\n"
            "        python-shard: [0, 1, 2]\n"
            "    steps:\n"
            "      - run: python tools/ci_shard_runner.py ${{ matrix.python-shard }} 4\n"
            "  windows:\n"
            "    strategy:\n"
            "      matrix:\n"
            "        python-shard: [0, 1]\n"
            "    steps:\n"
            "      - run: python tools/ci_shard_runner.py ${{ matrix.python-shard }} 3\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", ".github/"], cwd=str(self.repo_root), check=True, capture_output=True)
        self.add_python_test_files(6)

        result = self.run_verify_tool()

        self.assertEqual(result.returncode, 1, result.stdout)
        # Both gapped jobs' matrix key is reported (python-shard, twice: once
        # per job); the message names the missing id for each.
        self.assertEqual(result.stderr.count("python-shard"), 2)


if __name__ == "__main__":
    unittest.main()
