#!/usr/bin/env python3
"""
Test suite for gen_suite_counts.py (generated artifact builder).

Contract under test:
- --check / default is READ-ONLY validation and NEVER writes. Drift = exit 1.
- --regenerate is the only writing mode. Produces tests/SUITE-COUNTS.json.
- Fail-closed preserved: non-git-repo = exit 2, vacuous zero derivation = exit 2.
- JSON artifact is idempotent (running --regenerate twice produces identical output).
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestGenSuiteCounts(unittest.TestCase):
    """Test gen_suite_counts.py artifact generation."""

    @classmethod
    def setUpClass(cls):
        """Set up class-level fixtures."""
        cls.repo_root = Path(__file__).parent.parent

    def setUp(self):
        """Create temporary isolated repo for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)

        # Create tools and tests directories
        tools_dir = self.temp_root / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)

        tests_dir = self.temp_root / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        # Copy the tool
        tool_path = self.repo_root / "tools" / "gen_suite_counts.py"
        if tool_path.exists():
            (tools_dir / "gen_suite_counts.py").write_text(tool_path.read_text())

        # Create test files
        (tests_dir / "test_a.py").touch()
        (tests_dir / "test_b.py").touch()
        (tests_dir / "test_a.test.mjs").touch()
        (tests_dir / "test_a.sh").touch()

        # Initialize git repo
        subprocess.run(
            ["git", "init"],
            cwd=str(self.temp_root),
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(self.temp_root),
            capture_output=True,
            check=False,
        )

    def tearDown(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def _run_tool(self, *args):
        """Run gen_suite_counts.py in the isolated temp repo."""
        cmd = [sys.executable, str(self.repo_root / "tools" / "gen_suite_counts.py")]
        cmd.extend(args)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            cwd=str(self.temp_root),
        )
        return result

    def test_check_mode_fails_when_file_missing(self):
        """--check fails (exit 1) when tests/SUITE-COUNTS.json doesn't exist."""
        result = self._run_tool("--check", "--repo", str(self.temp_root))
        self.assertEqual(result.returncode, 1, f"stderr: {result.stderr}")
        self.assertIn("not found", result.stderr)

    def test_regenerate_creates_json_file(self):
        """--regenerate creates tests/SUITE-COUNTS.json with correct structure."""
        result = self._run_tool("--regenerate", "--repo", str(self.temp_root))
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        json_path = self.temp_root / "tests" / "SUITE-COUNTS.json"
        self.assertTrue(json_path.exists(), "tests/SUITE-COUNTS.json was not created")

        # Verify JSON structure
        content = json_path.read_text()
        self.assertIn("GENERATED-BY", content)
        self.assertIn("gen_suite_counts.py", content)

        # Extract and parse JSON
        start = content.find("{")
        end = content.rfind("}") + 1
        data = json.loads(content[start:end])

        self.assertIn("Node", data)
        self.assertIn("Shell", data)
        self.assertIn("Python", data)
        self.assertEqual(data["Node"], 1)  # test_a.test.mjs
        self.assertEqual(data["Shell"], 1)  # test_a.sh
        self.assertEqual(data["Python"], 2)  # test_a.py, test_b.py

    def test_check_mode_passes_when_counts_match(self):
        """--check passes (exit 0) when counts match."""
        # First regenerate
        result = self._run_tool("--regenerate", "--repo", str(self.temp_root))
        self.assertEqual(result.returncode, 0)

        # Then check
        result = self._run_tool("--check", "--repo", str(self.temp_root))
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("counts match", result.stdout)

    def test_check_mode_fails_on_drift(self):
        """--check fails (exit 1) when counts drift from actual files."""
        # Regenerate
        result = self._run_tool("--regenerate", "--repo", str(self.temp_root))
        self.assertEqual(result.returncode, 0)

        # Corrupt the JSON
        json_path = self.temp_root / "tests" / "SUITE-COUNTS.json"
        content = json_path.read_text()
        corrupted = content.replace('"Node": 1', '"Node": 5')
        json_path.write_text(corrupted)

        # Check should fail
        result = self._run_tool("--check", "--repo", str(self.temp_root))
        self.assertEqual(result.returncode, 1, f"stderr: {result.stderr}")
        self.assertIn("DRIFT", result.stdout)

    def test_regenerate_is_idempotent(self):
        """Running --regenerate twice produces identical output."""
        result1 = self._run_tool("--regenerate", "--repo", str(self.temp_root))
        self.assertEqual(result1.returncode, 0)

        json_path = self.temp_root / "tests" / "SUITE-COUNTS.json"
        first_content = json_path.read_text()

        # Run again
        result2 = self._run_tool("--regenerate", "--repo", str(self.temp_root))
        self.assertEqual(result2.returncode, 0)
        self.assertIn("already match", result2.stdout)

        second_content = json_path.read_text()
        self.assertEqual(first_content, second_content, "Output is not idempotent")

    def test_json_mode_outputs_json(self):
        """--json outputs JSON to stdout (read-only)."""
        result = self._run_tool("--json", "--repo", str(self.temp_root))
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        data = json.loads(result.stdout)
        self.assertEqual(data["Node"], 1)
        self.assertEqual(data["Shell"], 1)
        self.assertEqual(data["Python"], 2)

        # Verify no file was created
        json_path = self.temp_root / "tests" / "SUITE-COUNTS.json"
        self.assertFalse(json_path.exists(), "--json should not create file")

    def test_fail_closed_on_non_git_repo(self):
        """Fail-closed (exit 2) when repo is not a git work tree."""
        # Create a directory without git
        temp_dir2 = tempfile.TemporaryDirectory()
        temp_root2 = Path(temp_dir2.name)

        tests_dir = temp_root2 / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_real.py").touch()

        # Try to use --json mode (doesn't need file)
        result = subprocess.run(
            [sys.executable, str(self.repo_root / "tools" / "gen_suite_counts.py"),
             "--json", "--repo", str(temp_root2)],
            capture_output=True,
            text=True,
            cwd=str(temp_root2),
        )

        # Should fail-close (exit 2) because not a git repo
        self.assertEqual(result.returncode, 2, f"stderr: {result.stderr}")
        self.assertIn("not a git repository", result.stderr)
        temp_dir2.cleanup()

    def test_dry_run_with_regenerate(self):
        """--dry-run with --regenerate shows changes without writing."""
        result = self._run_tool("--regenerate", "--dry-run", "--repo", str(self.temp_root))
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertIn("DRY-RUN", result.stdout)
        self.assertIn("Node: 1 suites", result.stdout)

        # Verify no file was created
        json_path = self.temp_root / "tests" / "SUITE-COUNTS.json"
        self.assertFalse(json_path.exists(), "--dry-run should not create file")


if __name__ == "__main__":
    unittest.main()
