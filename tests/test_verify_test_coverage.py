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
