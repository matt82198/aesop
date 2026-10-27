#!/usr/bin/env python3
"""
Test suite for verify_test_coverage.py — CI gate that verifies all on-disk
test files are run by some CI job or script.
"""
import subprocess
import tempfile
import unittest
import json
from pathlib import Path


class TestVerifyTestCoverage(unittest.TestCase):
    """Test the verify_test_coverage.py tool."""

    def setUp(self):
        """Set up test fixtures."""
        self.repo_root = Path(__file__).resolve().parent.parent
        self.tool_path = self.repo_root / "tools" / "verify_test_coverage.py"
        self.tests_dir = self.repo_root / "tests"

    def test_tool_imports(self):
        """Verify the tool can be imported without errors."""
        result = subprocess.run(
            ["python", str(self.tool_path), "--help"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, f"Tool failed: {result.stderr}")
        self.assertIn("--check", result.stdout)
        self.assertIn("--fix", result.stdout)

    def test_all_current_tests_covered(self):
        """Test that the real repo has no orphaned test files.

        This branch wired the historically-orphaned shell tests
        (dash-watchdog-gui.test.sh, test-run-watchdog-smoke-signal.sh,
        test_waveguard.sh) into package.json test:sh, so the repo must now
        pass the Guardrail G2 gate cleanly.
        """
        result = subprocess.run(
            ["python", str(self.tool_path), "--check"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(self.repo_root),
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Expected no orphaned test files, but the gate failed:\n"
            f"{result.stdout}\n{result.stderr}",
        )

    def test_historical_orphans_detected(self):
        """Regression: the 3 historically-orphaned shell tests are detected.

        Reproduces the original bug this guardrail was built to catch:
        - tests/dash-watchdog-gui.test.sh
        - tests/test-run-watchdog-smoke-signal.sh
        - tests/test_waveguard.sh
        existed on disk but were not run by any CI job. Rebuilt here as a
        hermetic temp fixture because the real repo has since wired them in.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            tests_dir = tmpdir_path / "tests"
            tests_dir.mkdir()
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()

            # package.json test:sh missing the three orphans (the historical state)
            (tmpdir_path / "package.json").write_text(json.dumps({
                "scripts": {
                    "test:sh": "bash tests/test_pre_push_policy.sh && bash hooks/pre-push-policy.sh --test"
                }
            }))

            # Covered test
            (tests_dir / "test_pre_push_policy.sh").write_text("#!/bin/bash\necho ok")

            # The three historically-orphaned shell tests
            for name in (
                "dash-watchdog-gui.test.sh",
                "test-run-watchdog-smoke-signal.sh",
                "test_waveguard.sh",
            ):
                (tests_dir / name).write_text("#!/bin/bash\necho orphan")

            tools_dir.joinpath("verify_test_coverage.py").write_text(
                self.tool_path.read_text()
            )

            result = subprocess.run(
                ["python", str(tools_dir / "verify_test_coverage.py"), "--check"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(tmpdir_path),
            )

            self.assertNotEqual(
                result.returncode,
                0,
                f"Expected to detect orphaned test files but none were found:\n"
                f"{result.stdout}\n{result.stderr}",
            )
            self.assertIn("dash-watchdog-gui.test.sh", result.stdout)
            self.assertIn("test-run-watchdog-smoke-signal.sh", result.stdout)
            self.assertIn("test_waveguard.sh", result.stdout)

    def test_orphan_detection_python(self):
        """Test that an orphaned Python test file is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create a minimal test structure
            tests_dir = tmpdir_path / "tests"
            tests_dir.mkdir()
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()

            # Create a fake Python test file that won't be discovered by ci_shard_runner
            orphan_test = tests_dir / "test_orphan_python.py"
            orphan_test.write_text("import unittest\nclass TestOrphan(unittest.TestCase): pass")

            # Copy the runner tool and verify_test_coverage tool to temp
            ci_runner = self.repo_root / "tools" / "ci_shard_runner.py"
            tools_dir.joinpath("ci_shard_runner.py").write_text(ci_runner.read_text())

            tool_content = self.tool_path.read_text()
            tools_dir.joinpath("verify_test_coverage.py").write_text(tool_content)

            # Run verify_test_coverage on the temp repo
            result = subprocess.run(
                ["python", str(tools_dir / "verify_test_coverage.py"), "--check"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(tmpdir_path),
            )

            # Should detect the orphan
            self.assertNotEqual(
                result.returncode, 0,
                f"Failed to detect orphaned Python test:\n{result.stdout}\n{result.stderr}",
            )
            self.assertIn("test_orphan_python.py", result.stdout)

    def test_orphan_detection_node(self):
        """Test that an orphaned Node test file is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create minimal structure
            tests_dir = tmpdir_path / "tests"
            tests_dir.mkdir()
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()

            # Create package.json without the orphan test
            package_json = tmpdir_path / "package.json"
            package_json.write_text(json.dumps({
                "scripts": {
                    "test:node": "node --test tests/test_covered.test.mjs"
                }
            }))

            # Create a covered test
            (tests_dir / "test_covered.test.mjs").write_text("export const x = 1;")

            # Create an orphaned test not in package.json
            orphan = tests_dir / "test_orphan_node.test.mjs"
            orphan.write_text("export const y = 2;")

            # Copy tool
            tool_content = self.tool_path.read_text()
            tools_dir.joinpath("verify_test_coverage.py").write_text(tool_content)

            # Run
            result = subprocess.run(
                ["python", str(tools_dir / "verify_test_coverage.py"), "--check"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(tmpdir_path),
            )

            # Should detect the orphan
            self.assertNotEqual(
                result.returncode, 0,
                f"Failed to detect orphaned Node test:\n{result.stdout}\n{result.stderr}",
            )
            self.assertIn("test_orphan_node.test.mjs", result.stdout)

    def test_hooks_node_test_outside_runner_glob_is_orphan(self):
        """Regression (TCI-4/TCI-5, R-AESOP-FOCUS-1): a *.test.mjs under hooks/
        that no test:node glob names is run by nothing and must be reported as
        an orphan. Before the fix Node discovery enumerated only tests/*.test.mjs,
        so hooks/claude/*.test.mjs were invisible to the gate (fake green).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            tests_dir = tmpdir_path / "tests"
            tests_dir.mkdir()
            hooks_dir = tmpdir_path / "hooks" / "claude"
            hooks_dir.mkdir(parents=True)
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()

            (tmpdir_path / "package.json").write_text(json.dumps({
                "scripts": {
                    "test:node": "node --test tests/*.test.mjs"
                }
            }))
            (tests_dir / "covered.test.mjs").write_text("export const x = 1;")
            (hooks_dir / "dark_hook.test.mjs").write_text("export const y = 2;")
            tools_dir.joinpath("verify_test_coverage.py").write_text(
                self.tool_path.read_text()
            )

            result = subprocess.run(
                ["python", str(tools_dir / "verify_test_coverage.py"), "--check"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                cwd=str(tmpdir_path),
            )

            self.assertEqual(
                result.returncode, 1,
                f"Hook Node test outside the runner glob must fail the gate closed:\n"
                f"{result.stdout}\n{result.stderr}",
            )
            self.assertIn("hooks/claude/dark_hook.test.mjs", result.stdout)
            self.assertNotIn("tests/covered.test.mjs", result.stdout)

    def test_hooks_glob_anchored_inside_tests_path_is_not_coverage(self):
        """Regression (R-AUDIT-TCI-5): a test:node token that names
        tests/hooks/*.test.mjs must not also cover the top-level hooks/ tree.
        Unanchored, the hooks/ token regex matched the 'hooks/...' suffix inside
        'tests/hooks/...' and gave hooks/x.test.mjs false coverage (fake green).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            (tmpdir_path / "tests" / "hooks").mkdir(parents=True)
            (tmpdir_path / "hooks").mkdir()
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()

            (tmpdir_path / "package.json").write_text(json.dumps({
                "scripts": {
                    "test:node": "node --test tests/hooks/*.test.mjs"
                }
            }))
            (tmpdir_path / "tests" / "hooks" / "covered.test.mjs").write_text("export const x = 1;")
            (tmpdir_path / "hooks" / "x.test.mjs").write_text("export const y = 2;")
            tools_dir.joinpath("verify_test_coverage.py").write_text(
                self.tool_path.read_text()
            )

            result = subprocess.run(
                ["python", str(tools_dir / "verify_test_coverage.py"), "--check"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                cwd=str(tmpdir_path),
            )

            self.assertEqual(
                result.returncode, 1,
                f"hooks/x.test.mjs is not named by test:node and must fail the gate closed:\n"
                f"{result.stdout}\n{result.stderr}",
            )
            self.assertIn("hooks/x.test.mjs", result.stdout)

    def test_hooks_glob_with_dot_slash_prefix_is_covered(self):
        """Regression (R-AUDIT-TCI-6): a test:node token written as ./hooks/... names
        the top-level hooks/ tree and must be credited. The anchored regex of
        R-AUDIT-TCI-5 rejected the leading './' and reported live hook tests as
        orphans (false red). Token-level matching credits it.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            (tmpdir_path / "tests").mkdir()
            (tmpdir_path / "hooks" / "claude").mkdir(parents=True)
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()

            (tmpdir_path / "package.json").write_text(json.dumps({
                "scripts": {
                    "test:node": "node --test ./hooks/*.test.mjs ./hooks/claude/*.test.mjs"
                }
            }))
            (tmpdir_path / "hooks" / "x.test.mjs").write_text("export const x = 1;")
            (tmpdir_path / "hooks" / "claude" / "y.test.mjs").write_text("export const y = 2;")
            tools_dir.joinpath("verify_test_coverage.py").write_text(
                self.tool_path.read_text()
            )

            result = subprocess.run(
                ["python", str(tools_dir / "verify_test_coverage.py"), "--check"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                cwd=str(tmpdir_path),
            )

            self.assertEqual(
                result.returncode, 0,
                f"./hooks/ tokens name live hook tests and must be covered:\n"
                f"{result.stdout}\n{result.stderr}",
            )

    def test_orphan_detection_shell(self):
        """Test that an orphaned shell test file is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create structure
            tests_dir = tmpdir_path / "tests"
            tests_dir.mkdir()
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()

            # Create package.json with only one shell test
            package_json = tmpdir_path / "package.json"
            package_json.write_text(json.dumps({
                "scripts": {
                    "test:sh": "bash tests/test_covered.sh"
                }
            }))

            # Create covered test
            (tests_dir / "test_covered.sh").write_text("#!/bin/bash\necho ok")

            # Create orphan
            orphan = tests_dir / "test_orphan.sh"
            orphan.write_text("#!/bin/bash\necho orphan")

            # Copy tool
            tool_content = self.tool_path.read_text()
            tools_dir.joinpath("verify_test_coverage.py").write_text(tool_content)

            # Run
            result = subprocess.run(
                ["python", str(tools_dir / "verify_test_coverage.py"), "--check"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(tmpdir_path),
            )

            # Should detect the orphan
            self.assertNotEqual(
                result.returncode, 0,
                f"Failed to detect orphaned shell test:\n{result.stdout}\n{result.stderr}",
            )
            self.assertIn("test_orphan.sh", result.stdout)

    def test_orphan_detection_playwright(self):
        """Test that an orphaned Playwright test file is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create structure
            tests_dir = tmpdir_path / "tests"
            tests_dir.mkdir()
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()

            # Create playwright.config.ts with limited testMatch
            playwright_config = tmpdir_path / "playwright.config.ts"
            playwright_config.write_text(
                "export default { testDir: './tests', testMatch: 'test_covered.spec.ts' };"
            )

            # Create covered test
            (tests_dir / "test_covered.spec.ts").write_text("import { test } from '@playwright/test';")

            # Create orphan
            orphan = tests_dir / "test_orphan.spec.ts"
            orphan.write_text("import { test } from '@playwright/test';")

            # Copy tool
            tool_content = self.tool_path.read_text()
            tools_dir.joinpath("verify_test_coverage.py").write_text(tool_content)

            # Run
            result = subprocess.run(
                ["python", str(tools_dir / "verify_test_coverage.py"), "--check"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(tmpdir_path),
            )

            # Should detect the orphan
            self.assertNotEqual(
                result.returncode, 0,
                f"Failed to detect orphaned Playwright test:\n{result.stdout}\n{result.stderr}",
            )
            self.assertIn("test_orphan.spec.ts", result.stdout)

    def test_check_exit_code(self):
        """Test that --check returns proper exit codes."""
        # Run with --check on the real repo
        result = subprocess.run(
            ["python", str(self.tool_path), "--check"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(self.repo_root),
        )

        # Should be 0 (no orphans) or 1 (orphans found), not any other code
        self.assertIn(
            result.returncode, [0, 1],
            f"Unexpected exit code: {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )

    def test_misnamed_testcase_file_fails_closed(self):
        """Regression (F-03, 2026-10-14): a TestCase file outside the collected
        glob must be reported as an orphan, not silently treated as covered.

        Before the fix the Python disk set was built from the same tests/test_*.py
        glob the runner uses, so a misnamed file (init_project_worktree_test.py,
        7 tests) could never be flagged. Hermetic fixture: a real git repo whose
        tracked tests/ holds one collected module, one misnamed TestCase module,
        and one helper with no tests (which must NOT be flagged).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            subprocess.run(["git", "init", "-q", str(tmpdir_path)], check=True)
            tests_dir = tmpdir_path / "tests"
            tests_dir.mkdir()
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()

            (tests_dir / "test_covered.py").write_text(
                "import unittest\n"
                "class TestCovered(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n"
            )
            (tests_dir / "misnamed_check.py").write_text(
                "import unittest\n"
                "class TestMisnamed(unittest.TestCase):\n"
                "    def test_hidden(self):\n"
                "        self.assertTrue(True)\n"
            )
            (tests_dir / "helper_fixture.py").write_text("VALUE = 1\n")
            subprocess.run(["git", "-C", str(tmpdir_path), "add", "-A"], check=True)

            tools_dir.joinpath("verify_test_coverage.py").write_text(
                self.tool_path.read_text()
            )

            result = subprocess.run(
                ["python", str(tools_dir / "verify_test_coverage.py"), "--check"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                cwd=str(tmpdir_path),
            )

            self.assertEqual(
                result.returncode, 1,
                f"Misnamed TestCase file must fail the gate closed:\n"
                f"{result.stdout}\n{result.stderr}",
            )
            self.assertIn("tests/misnamed_check.py", result.stdout)
            self.assertNotIn("tests/test_covered.py", result.stdout)
            self.assertNotIn("tests/helper_fixture.py", result.stdout)

    def test_fix_mode_suggests_commands(self):
        """Test that --fix mode suggests how to add orphaned tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create structure
            tests_dir = tmpdir_path / "tests"
            tests_dir.mkdir()
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()

            # Create package.json
            package_json = tmpdir_path / "package.json"
            package_json.write_text(json.dumps({
                "scripts": {
                    "test:sh": "bash tests/test_covered.sh"
                }
            }))

            # Create orphan shell test
            orphan = tests_dir / "test_orphan.sh"
            orphan.write_text("#!/bin/bash\necho orphan")

            # Create covered test
            (tests_dir / "test_covered.sh").write_text("#!/bin/bash\necho ok")

            # Copy tool
            tool_content = self.tool_path.read_text()
            tools_dir.joinpath("verify_test_coverage.py").write_text(tool_content)

            # Run with --fix
            result = subprocess.run(
                ["python", str(tools_dir / "verify_test_coverage.py"), "--fix"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(tmpdir_path),
            )

            # Should output suggestions
            self.assertIn(
                "bash tests/test_orphan.sh",
                result.stdout,
                f"Failed to suggest fix command:\n{result.stdout}\n{result.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
