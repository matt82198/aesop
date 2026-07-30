#!/usr/bin/env python3
"""
Tests for G5 (CLAUDE.md sync) and test-count verification gates in pre-push-policy.sh.

Test escapes addressed:
- esc-g5-595: Agent pushed auto_merge.py + test_auto_merge_shell.py without updating
  tools/CLAUDE.md (G5 violation) or tests/CLAUDE.md (test-count drift).
  Neither local pre-push gate enforced these requirements before push.

Test strategy:
1. Reproduce the original escape (fixture with both violations)
2. Verify clean state (no violations)
3. Confirm each gate detects its violation independently
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Tuple


class TestG5AndTestCountGates(unittest.TestCase):
    """Test pre-push gate integration for G5 (CLAUDE.md sync) and test-count verification."""

    @classmethod
    def setUpClass(cls):
        """Find tools and gate scripts at class initialization."""
        repo_root = Path(__file__).parent.parent.resolve()
        cls.repo_root = repo_root
        cls.claudemd_sync_gate = repo_root / "tools" / "claudemd_sync_gate.py"
        cls.verify_test_count = repo_root / "tools" / "verify_test_suite_count.py"
        cls.pre_push_hook = repo_root / "hooks" / "pre-push-policy.sh"

        # Verify tools exist
        if not cls.claudemd_sync_gate.exists():
            raise FileNotFoundError(f"claudemd_sync_gate.py not found at {cls.claudemd_sync_gate}")
        if not cls.verify_test_count.exists():
            raise FileNotFoundError(f"verify_test_suite_count.py not found at {cls.verify_test_count}")
        if not cls.pre_push_hook.exists():
            raise FileNotFoundError(f"pre-push-policy.sh not found at {cls.pre_push_hook}")

    def _run_gate(self, gate_tool: Path, args: list, cwd: Path = None) -> Tuple[int, str, str]:
        """Run a gate tool and return (exit_code, stdout, stderr)."""
        cmd = ["python3", str(gate_tool)] + args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=cwd)
        return result.returncode, result.stdout, result.stderr

    def _setup_fixture_repo(self, tmpdir: Path) -> Path:
        """Create a minimal fixture repository structure."""
        repo = tmpdir / "fixture-repo"
        repo.mkdir(parents=True)

        # Initialize git
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, capture_output=True, check=True)

        # Create initial commit (main branch)
        (repo / "README.md").write_text("# Test Repo\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "checkout", "-b", "main"], cwd=repo, capture_output=True)

        # Create feature branch
        subprocess.run(["git", "checkout", "-b", "feature/test"], cwd=repo, capture_output=True, check=True)

        return repo

    def test_01_g5_gate_detects_missing_claudemd_update(self):
        """Test that G5 gate detects code changes without CLAUDE.md updates.

        Reproduces esc-g5-595: New tool added to tools/ without updating tools/CLAUDE.md.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            repo = self._setup_fixture_repo(tmpdir_path)

            # Create tools/ directory structure
            (repo / "tools").mkdir(exist_ok=True)
            (repo / "tools" / "CLAUDE.md").write_text("# tools/ — Build utilities\n\n## Tools\n")

            # Add initial file to git
            subprocess.run(["git", "add", "tools/CLAUDE.md"], cwd=repo, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "init tools"], cwd=repo, capture_output=True, check=True)

            # Simulate the escape: add new tool WITHOUT updating CLAUDE.md
            (repo / "tools" / "new_tool.py").write_text("#!/usr/bin/env python3\n# New tool\n")
            subprocess.run(["git", "add", "tools/new_tool.py"], cwd=repo, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "add new tool"], cwd=repo, capture_output=True, check=True)

            # Run G5 gate (should detect drift)
            exit_code, stdout, stderr = self._run_gate(
                self.claudemd_sync_gate,
                ["--check", "--root", str(repo)]
            )

            self.assertNotEqual(exit_code, 0, "G5 gate should fail (exit != 0) when code added without CLAUDE.md update")
            self.assertIn("tools", stdout + stderr, "G5 gate should report 'tools' domain drift")

    def test_02_g5_gate_passes_with_claudemd_sync(self):
        """Test that G5 gate passes when code and CLAUDE.md are both updated.

        Clean-state test: no false positives.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            repo = self._setup_fixture_repo(tmpdir_path)

            # Create tools/ directory with CLAUDE.md
            (repo / "tools").mkdir(exist_ok=True)
            (repo / "tools" / "CLAUDE.md").write_text("# tools/ — Build utilities\n\n## Tools\n")

            # Add initial files to git
            subprocess.run(["git", "add", "tools/CLAUDE.md"], cwd=repo, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "init tools"], cwd=repo, capture_output=True, check=True)

            # Add new tool AND update CLAUDE.md in same commit (proper sync)
            (repo / "tools" / "new_tool.py").write_text("#!/usr/bin/env python3\n# New tool\n")
            (repo / "tools" / "CLAUDE.md").write_text(
                "# tools/ — Build utilities\n\n## Tools\n- `new_tool.py` — A new tool\n"
            )
            subprocess.run(["git", "add", "tools/new_tool.py", "tools/CLAUDE.md"], cwd=repo, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "add new tool with CLAUDE.md sync"], cwd=repo, capture_output=True, check=True)

            # Run G5 gate (should pass)
            exit_code, stdout, stderr = self._run_gate(
                self.claudemd_sync_gate,
                ["--check", "--root", str(repo)]
            )

            self.assertEqual(exit_code, 0, f"G5 gate should pass (exit=0) when synced. stdout={stdout}, stderr={stderr}")
            self.assertIn("OK", stdout + stderr, "G5 gate should report OK when synced")

    def test_03_test_count_gate_detects_drift(self):
        """Test that test-count gate detects when test files are added without updating CLAUDE.md.

        Reproduces esc-g5-595: test_auto_merge_shell.py added without updating tests/CLAUDE.md count.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            repo = self._setup_fixture_repo(tmpdir_path)

            # Create tests/ directory with CLAUDE.md (initially says 5 shell tests, 10 node, 100 python)
            (repo / "tests").mkdir(exist_ok=True)
            (repo / "tests" / "CLAUDE.md").write_text(
                "# tests/\n\n**Shell (5 suites)**: test1\n**Node (10 suites)**: test2\n**Python (100 suites)**: test3\n"
            )

            # Create 5 shell test files to match the documented count
            for i in range(1, 6):
                (repo / "tests" / f"test_{i}.sh").write_text("#!/bin/bash\necho test\n")

            # Create 10 node test files
            for i in range(1, 11):
                (repo / "tests" / f"test_{i}.test.mjs").write_text("export default {}\n")

            # Create 100 python test files
            for i in range(1, 101):
                (repo / "tests" / f"test_{i}.py").write_text("# test file\n")

            # Add all to git
            subprocess.run(["git", "add", "tests/"], cwd=repo, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "init tests"], cwd=repo, capture_output=True, check=True)

            # Simulate the escape: add a new test file WITHOUT updating CLAUDE.md counts
            (repo / "tests" / "test_6.sh").write_text("#!/bin/bash\necho new test\n")
            subprocess.run(["git", "add", "tests/test_6.sh"], cwd=repo, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "add new shell test"], cwd=repo, capture_output=True, check=True)

            # Run test-count gate (should detect drift: documented=5, actual=6)
            exit_code, stdout, stderr = self._run_gate(
                self.verify_test_count,
                ["--check", "--repo", str(repo)],
                cwd=repo
            )

            self.assertNotEqual(exit_code, 0, "test-count gate should fail (exit != 0) when test count drifts")
            self.assertIn("DRIFT", stdout + stderr, "test-count gate should report DRIFT when counts mismatch")
            self.assertIn("Shell", stdout + stderr, "test-count gate should report Shell count drift")

    def test_04_test_count_gate_passes_when_clean(self):
        """Test that test-count gate passes when counts match disk.

        Clean-state test: no false positives.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            repo = self._setup_fixture_repo(tmpdir_path)

            # Create tests/ directory with CLAUDE.md (document 3 shell, 2 node, 1 python)
            (repo / "tests").mkdir(exist_ok=True)
            (repo / "tests" / "CLAUDE.md").write_text(
                "# tests/\n\n**Shell (3 suites)**: test docs\n**Node (2 suites)**: test docs\n**Python (1 suites)**: test docs\n"
            )

            # Create 3 shell test files to match
            for i in range(1, 4):
                (repo / "tests" / f"test_{i}.sh").write_text("#!/bin/bash\n")

            # Create 2 node test files
            for i in range(1, 3):
                (repo / "tests" / f"test_{i}.test.mjs").write_text("export default {}\n")

            # Create 1 python test file
            (repo / "tests" / "test_one.py").write_text("# test\n")

            # Add all to git
            subprocess.run(["git", "add", "tests/"], cwd=repo, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "init tests"], cwd=repo, capture_output=True, check=True)

            # Run test-count gate (should pass; run from fixture repo cwd to isolate git context)
            exit_code, stdout, stderr = self._run_gate(
                self.verify_test_count,
                ["--check", "--repo", str(repo)],
                cwd=repo
            )

            self.assertEqual(exit_code, 0, f"test-count gate should pass (exit=0) when counts match. stdout={stdout}, stderr={stderr}")
            self.assertIn("OK", stdout + stderr, "test-count gate should report OK when counts match")

    def test_05_combined_escape_fixture(self):
        """Reproduce the full esc-g5-595 escape: both violations present.

        Both G5 drift AND test-count drift in a single scenario.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            repo = self._setup_fixture_repo(tmpdir_path)

            # Initialize tools/ and tests/ with CLAUDE.md files
            (repo / "tools").mkdir(exist_ok=True)
            (repo / "tools" / "CLAUDE.md").write_text("# tools/ — Build utilities\n")

            (repo / "tests").mkdir(exist_ok=True)
            (repo / "tests" / "CLAUDE.md").write_text(
                "# tests/\n\n**Shell (1 suites)**: \n**Node (1 suites)**: \n**Python (1 suites)**: \n"
            )

            # Create 1 of each test file
            (repo / "tests" / "test_1.sh").write_text("#!/bin/bash\n")
            (repo / "tests" / "test_1.test.mjs").write_text("export default {}\n")
            (repo / "tests" / "test_1.py").write_text("# test\n")

            # Add all to git
            subprocess.run(["git", "add", "tools/", "tests/"], cwd=repo, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

            # Simulate esc-g5-595: add auto_merge.py + test_auto_merge_shell.py without updates
            (repo / "tools" / "auto_merge.py").write_text("#!/usr/bin/env python3\n# Auto merge tool\n")
            (repo / "tests" / "test_auto_merge_shell.sh").write_text("#!/bin/bash\n# Test auto merge\n")
            subprocess.run(["git", "add", "tools/auto_merge.py", "tests/test_auto_merge_shell.sh"], cwd=repo, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "add auto_merge tool and test"], cwd=repo, capture_output=True, check=True)

            # Verify G5 gate detects tools/ drift
            g5_exit, g5_stdout, g5_stderr = self._run_gate(
                self.claudemd_sync_gate,
                ["--check", "--root", str(repo)]
            )
            self.assertNotEqual(g5_exit, 0, "G5 gate should fail: tools/auto_merge.py added without CLAUDE.md update")

            # Verify test-count gate detects drift (now 2 shell, but documented as 1)
            count_exit, count_stdout, count_stderr = self._run_gate(
                self.verify_test_count,
                ["--check", "--repo", str(repo)],
                cwd=repo
            )
            self.assertNotEqual(count_exit, 0, "test-count gate should fail: test_auto_merge_shell.sh added without CLAUDE.md count update")


if __name__ == "__main__":
    unittest.main()
