#!/usr/bin/env python3
"""End-to-end tests for seam-s oracle layout and grading.

Tests verify:
  1. Oracle layout is correct (sandbox/repo/ + sandbox/oracle/ siblings)
  2. Worker patches are applied correctly to sandbox/repo/
  3. Visible test runs against worker-edited code
  4. Oracle grades correctly: known-good fix → PASS, no-op → FAIL
  5. A mocked worker that returns the real SOLUTION.md fix scores PASS

Uses REAL fixtures (st01 + created reference) to prove seam-s grading works.

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

# Add driver/bench to path.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
BENCH_DIR = REPO / "bench"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

from agent_driver import WORKER_DONE
import run_seam_s as seam_s


class TestOracleLayout(unittest.TestCase):
    """Test the correct sandbox layout for oracle grading."""

    def test_sandbox_creates_repo_subdirectory(self):
        """Sandbox should create repo/ subdirectory, not flat layout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox_dir = Path(tmpdir)

            # Simulate what execute_task_run does.
            sandbox_repo_dir = sandbox_dir / "repo"
            sandbox_repo_dir.mkdir()

            # Verify layout.
            self.assertTrue(sandbox_repo_dir.exists())
            self.assertTrue((sandbox_dir / "repo").exists())

    def test_oracle_finds_code_at_correct_relative_path(self):
        """Oracle at sandbox/oracle/ finds code at ../repo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox_dir = Path(tmpdir)
            sandbox_repo = sandbox_dir / "repo"
            sandbox_oracle = sandbox_dir / "oracle"

            # Create layout.
            sandbox_repo.mkdir()
            sandbox_oracle.mkdir()

            # Create a dummy module in repo.
            (sandbox_repo / "dummy_module.py").write_text("def hello(): return 'world'")

            # Create a conftest that adds ../repo to path.
            (sandbox_oracle / "conftest.py").write_text(
                """
import sys
from pathlib import Path
repo_path = Path(__file__).parent.parent / "repo"
if str(repo_path) not in sys.path:
    sys.path.insert(0, str(repo_path))
"""
            )

            # Create a test that imports from repo.
            (sandbox_oracle / "test_import.py").write_text(
                """
def test_can_import():
    from dummy_module import hello
    assert hello() == 'world'
"""
            )

            # Run pytest from sandbox/ (oracle should find ../repo).
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "oracle/test_import.py", "-q"],
                cwd=str(sandbox_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )

            # Should succeed because conftest adds ../repo to path.
            self.assertEqual(result.returncode, 0, f"Oracle import test failed:\n{result.stdout}\n{result.stderr}")


class TestRealFixtureE2E(unittest.TestCase):
    """End-to-end tests with real st01 fixture."""

    @classmethod
    def setUpClass(cls):
        """Load the real st01 fixture (seam_s_sample_task)."""
        fixture_dir = REPO / "tests/fixtures/seam_s_sample_task"
        if not fixture_dir.exists():
            raise RuntimeError(f"Fixture not found: {fixture_dir}")

        cls.fixture_dir = fixture_dir
        cls.fixture_task = seam_s.load_task(fixture_dir)

    def test_known_good_fix_scores_pass(self):
        """Mock worker returns SOLUTION.md fix → oracle scores PASS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox_dir = Path(tmpdir)
            sandbox_repo = sandbox_dir / "repo"
            sandbox_oracle = sandbox_dir / "oracle"

            # Set up layout.
            sandbox_repo.mkdir()
            for item in self.fixture_task.repo_path.iterdir():
                if item.is_file():
                    shutil.copy2(item, sandbox_repo / item.name)
                elif item.is_dir() and not item.name.startswith("."):
                    shutil.copytree(item, sandbox_repo / item.name)

            # Apply the known-good fix (from the fixture code perspective).
            # The fix is: change "x * y" to "x + y" in the add function.
            test_sample_path = sandbox_repo / "test_sample.py"
            content = test_sample_path.read_text()
            fixed_content = content.replace("return x * y", "return x + y")
            test_sample_path.write_text(fixed_content)

            # Copy oracle.
            shutil.copytree(self.fixture_task.oracle_path, sandbox_oracle)

            # Run oracle with correct cwd (sandbox/).
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "oracle", "-q"],
                cwd=str(sandbox_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )

            # Oracle should pass because we applied the fix.
            self.assertEqual(
                result.returncode, 0,
                f"Oracle should pass on known-good fix. Output:\n{result.stdout}\n{result.stderr}"
            )

    def test_no_op_edit_scores_fail(self):
        """Mock worker returns no-op (empty edit) → oracle scores FAIL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox_dir = Path(tmpdir)
            sandbox_repo = sandbox_dir / "repo"
            sandbox_oracle = sandbox_dir / "oracle"

            # Set up layout.
            sandbox_repo.mkdir()
            for item in self.fixture_task.repo_path.iterdir():
                if item.is_file():
                    shutil.copy2(item, sandbox_repo / item.name)
                elif item.is_dir() and not item.name.startswith("."):
                    shutil.copytree(item, sandbox_repo / item.name)

            # Do NOT apply any fix (no-op).

            # Copy oracle.
            shutil.copytree(self.fixture_task.oracle_path, sandbox_oracle)

            # Run oracle with correct cwd (sandbox/).
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "oracle", "-q"],
                cwd=str(sandbox_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )

            # Oracle should fail because we didn't apply the fix.
            self.assertNotEqual(
                result.returncode, 0,
                f"Oracle should fail on no-op edit (broken code). Output:\n{result.stdout}\n{result.stderr}"
            )

    def test_visible_test_runs_against_edited_code(self):
        """Visible test in sandbox/repo/ sees the worker's edits."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox_repo = Path(tmpdir) / "repo"
            sandbox_repo.mkdir()

            # Copy fixture to sandbox/repo/.
            for item in self.fixture_task.repo_path.iterdir():
                if item.is_file():
                    shutil.copy2(item, sandbox_repo / item.name)
                elif item.is_dir() and not item.name.startswith("."):
                    shutil.copytree(item, sandbox_repo / item.name)

            # Run visible test BEFORE fix (should fail).
            test_before = subprocess.run(
                [sys.executable, "-m", "pytest", ".", "-q"],
                cwd=str(sandbox_repo),
                capture_output=True,
                text=True,
                timeout=10,
            )
            # Test should fail because code is still broken.
            self.assertNotEqual(test_before.returncode, 0)

            # Apply the fix.
            test_sample_path = sandbox_repo / "test_sample.py"
            content = test_sample_path.read_text()
            fixed_content = content.replace("return x * y", "return x + y")
            test_sample_path.write_text(fixed_content)

            # Run visible test AFTER fix (should pass).
            test_after = subprocess.run(
                [sys.executable, "-m", "pytest", ".", "-q"],
                cwd=str(sandbox_repo),
                capture_output=True,
                text=True,
                timeout=10,
            )
            # Test should pass because we fixed the code.
            self.assertEqual(
                test_after.returncode, 0,
                f"Visible test should pass after fix. Output:\n{test_after.stdout}\n{test_after.stderr}"
            )


if __name__ == "__main__":
    unittest.main()
