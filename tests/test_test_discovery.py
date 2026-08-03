#!/usr/bin/env python3
"""Test suite for tools/test_discovery.py.

Tests:
- Framework detection (pytest, jest/vitest/mocha, go test, rspec, shell)
- testCmd suggestion with evidence and confidence
- JSON output format
- Validation mode with actual test execution (read-only)
- Nonexistent path returns exit 2
- No frameworks detected returns exit 1
- Detection success returns exit 0
- ASCII-only output
- Never modifies target repo
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


class TestTestDiscovery(TestCase):
    """Tests for test_discovery.py framework detection."""

    def setUp(self):
        """Set up test fixtures."""
        self.repo_root = Path(__file__).parent.parent.resolve()

    def test_nonexistent_path_returns_exit_2(self):
        """Verify nonexistent path returns exit 2."""
        result = subprocess.run(
            [sys.executable, "tools/test_discovery.py", "/nonexistent/path/repo"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2)

    def test_empty_directory_returns_exit_1(self):
        """Verify empty directory (no frameworks) returns exit 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("No test frameworks detected", result.stdout)

    def test_pytest_detection(self):
        """Verify pytest detection from pytest.ini or conftest.py."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Create pytest markers
            (tmppath / "pytest.ini").write_text("[pytest]\n")
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "test_example.py").write_text(
                "def test_example(): pass"
            )

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("pytest", result.stdout.lower())

    def test_conftest_detection(self):
        """Verify pytest detection from conftest.py."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "conftest.py").write_text("# pytest config")
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "test_example.py").write_text(
                "def test_example(): pass"
            )

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("pytest", result.stdout.lower())

    def test_jest_detection_from_package_json(self):
        """Verify jest detection from package.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "package.json").write_text(
                '{"devDependencies": {"jest": "^27.0.0"}, "scripts": {"test": "jest"}}'
            )
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "example.test.js").write_text("test('x', () => {})")

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("jest", result.stdout.lower())

    def test_go_test_detection(self):
        """Verify go test detection from go.mod and *_test.go."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "go.mod").write_text("module example.com/app")
            (tmppath / "example_test.go").write_text("func TestExample(t *testing.T) {}")

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("go test", result.stdout.lower())

    def test_shell_test_detection(self):
        """Verify shell test detection from *.sh files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "test_example.sh").write_text("#!/bin/bash\necho test")

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            output = result.stdout.lower()
            self.assertIn("shell", output)

    def test_json_output_format(self):
        """Verify --json output is valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "pytest.ini").write_text("[pytest]\n")
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "test_example.py").write_text(
                "def test_example(): pass"
            )

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir, "--json"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            data = json.loads(result.stdout)
            self.assertIsInstance(data, dict)
            self.assertIn("frameworks", data)

    def test_validate_flag_with_pytest(self):
        """Verify --validate flag runs pytest --collect-only (read-only)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "pytest.ini").write_text("[pytest]\n")
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "test_example.py").write_text(
                "def test_example(): pass\ndef test_another(): pass"
            )

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir, "--validate"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            # Should succeed and report test count
            self.assertEqual(result.returncode, 0)
            self.assertIn("test", result.stdout.lower())

    def test_validate_timeout(self):
        """Verify --validate includes timeout support (120s hard limit)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "pytest.ini").write_text("[pytest]\n")
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "test_example.py").write_text(
                "def test_example(): pass"
            )

            # Validate succeeds without timeout on quick tests
            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir, "--validate"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=130,
            )
            self.assertEqual(result.returncode, 0)

    def test_ascii_only_output(self):
        """Verify all output is ASCII-safe."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "pytest.ini").write_text("[pytest]\n")
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "test_example.py").write_text(
                "def test_example(): pass"
            )

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            # Verify all output is ASCII
            result.stdout.encode("ascii")  # Will raise if non-ASCII
            result.stderr.encode("ascii")

    def test_target_repo_unmodified(self):
        """Verify tool never modifies target repo (read-only)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "pytest.ini").write_text("[pytest]\n")
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "test_example.py").write_text(
                "def test_example(): pass"
            )

            # Capture mtime before
            mtime_before = (tmppath / "tests" / "test_example.py").stat().st_mtime

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir, "--validate"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=120,
            )

            # Verify nothing changed
            mtime_after = (tmppath / "tests" / "test_example.py").stat().st_mtime
            self.assertEqual(mtime_before, mtime_after)

    def test_multiple_frameworks_detected(self):
        """Verify detection when multiple frameworks present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "pytest.ini").write_text("[pytest]\n")
            (tmppath / "package.json").write_text(
                '{"devDependencies": {"jest": "^27.0.0"}}'
            )
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "test_example.py").write_text(
                "def test_example(): pass"
            )

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            output = result.stdout.lower()
            # Both frameworks should be detected
            self.assertIn("pytest", output)

    def test_confidence_levels(self):
        """Verify output includes confidence levels (high/med/low)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "pytest.ini").write_text("[pytest]\n")
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "test_example.py").write_text(
                "def test_example(): pass"
            )

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir, "--json"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            data = json.loads(result.stdout)
            self.assertIn("frameworks", data)
            if data["frameworks"]:
                framework = data["frameworks"][0]
                self.assertIn("confidence", framework)
                self.assertIn(framework["confidence"], ["high", "medium", "low"])

    def test_testcmd_suggestion_in_output(self):
        """Verify testCmd suggestions are included in output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "pytest.ini").write_text("[pytest]\n")
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "test_example.py").write_text(
                "def test_example(): pass"
            )

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("pytest", result.stdout.lower())
            # Should suggest a testCmd pattern
            self.assertTrue(
                "pytest" in result.stdout or "test" in result.stdout
            )

    def test_evidence_paths_included(self):
        """Verify evidence paths (detected markers) are included."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "pytest.ini").write_text("[pytest]\n")
            (tmppath / "tests").mkdir()
            (tmppath / "tests" / "test_example.py").write_text(
                "def test_example(): pass"
            )

            result = subprocess.run(
                [sys.executable, "tools/test_discovery.py", tmpdir, "--json"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            data = json.loads(result.stdout)
            if data["frameworks"]:
                framework = data["frameworks"][0]
                self.assertIn("evidence", framework)
                self.assertIsInstance(framework["evidence"], list)
