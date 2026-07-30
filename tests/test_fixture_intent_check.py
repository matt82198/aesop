#!/usr/bin/env python3
"""Unit tests for tools/fixture_intent_check.py.

Tests the fixture manifest validation logic in isolated temp directories
without polluting cwd or global state.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add tools directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import fixture_intent_check as checker


class TestFixtureIntentCheck(unittest.TestCase):
    """Test fixture intent manifest validation."""

    def setUp(self):
        """Create a temporary repo directory for test isolation."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = self.temp_dir.name
        self.bench_dir = os.path.join(self.repo_root, "bench")
        self.fixtures_dir = os.path.join(self.repo_root, "bench", "fixtures")
        self.tests_fixtures_dir = os.path.join(self.repo_root, "tests", "fixtures")

        os.makedirs(self.bench_dir, exist_ok=True)
        os.makedirs(self.fixtures_dir, exist_ok=True)
        os.makedirs(self.tests_fixtures_dir, exist_ok=True)

    def tearDown(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def _write_manifest(self, data):
        """Write manifest file to temp repo."""
        manifest_path = os.path.join(self.bench_dir, "fixtures-intent.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return manifest_path

    def _create_fixture_file(self, rel_path):
        """Create a fixture file (empty) in temp repo."""
        full_path = os.path.join(self.repo_root, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        Path(full_path).touch()

    def test_load_valid_manifest(self):
        """Test loading a valid manifest."""
        manifest_data = [
            {
                "path": "tests/fixtures/test.py",
                "reason": "Test fixture",
                "fixture_type": "deliberately_broken_code",
                "added": "2026-07-30"
            }
        ]
        manifest_path = self._write_manifest(manifest_data)

        manifest, error = checker.load_manifest(manifest_path)

        self.assertIsNone(error)
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]["path"], "tests/fixtures/test.py")

    def test_load_missing_manifest(self):
        """Test loading a non-existent manifest."""
        manifest_path = os.path.join(self.bench_dir, "nonexistent.json")

        manifest, error = checker.load_manifest(manifest_path)

        self.assertIsNone(manifest)
        self.assertIn("not found", error)

    def test_load_invalid_json(self):
        """Test loading malformed JSON."""
        manifest_path = os.path.join(self.bench_dir, "fixtures-intent.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write("{ invalid json")

        manifest, error = checker.load_manifest(manifest_path)

        self.assertIsNone(manifest)
        self.assertIn("JSON parse error", error)

    def test_load_manifest_not_array(self):
        """Test that manifest must be an array."""
        manifest_data = {"not": "an array"}
        self._write_manifest(manifest_data)
        manifest_path = os.path.join(self.bench_dir, "fixtures-intent.json")

        manifest, error = checker.load_manifest(manifest_path)

        self.assertIsNone(manifest)
        self.assertIn("must be a JSON array", error)

    def test_validate_entry_missing_required_fields(self):
        """Test validation fails for missing required fields."""
        entry = {"path": "test.py"}  # missing reason, fixture_type, added

        is_valid, findings = checker.validate_manifest_entry(entry, 0, self.repo_root)

        self.assertFalse(is_valid)
        self.assertTrue(len(findings) > 0)
        self.assertTrue(any("required field" in f for f in findings))

    def test_validate_entry_missing_file(self):
        """Test validation detects missing fixture file."""
        entry = {
            "path": "nonexistent/fixture.py",
            "reason": "Test",
            "fixture_type": "deliberately_broken_code",
            "added": "2026-07-30"
        }

        is_valid, findings = checker.validate_manifest_entry(entry, 0, self.repo_root)

        self.assertFalse(is_valid)
        self.assertTrue(any("not found" in f for f in findings))

    def test_validate_entry_file_exists(self):
        """Test validation passes when file exists."""
        self._create_fixture_file("tests/fixtures/test.py")

        entry = {
            "path": "tests/fixtures/test.py",
            "reason": "Test fixture",
            "fixture_type": "deliberately_broken_code",
            "added": "2026-07-30"
        }

        is_valid, findings = checker.validate_manifest_entry(entry, 0, self.repo_root)

        self.assertTrue(is_valid)
        self.assertEqual(len(findings), 0)

    def test_validate_entry_invalid_fixture_type(self):
        """Test validation rejects invalid fixture_type."""
        self._create_fixture_file("test.py")

        entry = {
            "path": "test.py",
            "reason": "Test",
            "fixture_type": "invalid_type",
            "added": "2026-07-30"
        }

        is_valid, findings = checker.validate_manifest_entry(entry, 0, self.repo_root)

        self.assertFalse(is_valid)
        self.assertTrue(any("fixture_type" in f for f in findings))

    def test_validate_entry_invalid_date_format(self):
        """Test validation rejects invalid date format."""
        self._create_fixture_file("test.py")

        entry = {
            "path": "test.py",
            "reason": "Test",
            "fixture_type": "deliberately_broken_code",
            "added": "2026/07/30"  # Wrong format
        }

        is_valid, findings = checker.validate_manifest_entry(entry, 0, self.repo_root)

        self.assertFalse(is_valid)
        self.assertTrue(any("ISO date" in f for f in findings))

    def test_check_fixtures_valid(self):
        """Test check_fixtures with valid manifest."""
        # Create fixture files
        self._create_fixture_file("tests/fixtures/seam_s_sample_task/repo/test_sample.py")
        self._create_fixture_file("tests/fixtures/seam_sample_task/repo/main.py")
        self._create_fixture_file("bench/fixtures/mutation_fault_fixture.py")
        self._create_fixture_file("bench/fixtures/test_mutation_fault_fixture.py")

        # Create manifest
        manifest_data = [
            {
                "path": "tests/fixtures/seam_s_sample_task/repo/test_sample.py",
                "reason": "AI bug-detection benchmark",
                "fixture_type": "deliberately_broken_code",
                "added": "2026-07-30"
            },
            {
                "path": "tests/fixtures/seam_sample_task/repo/main.py",
                "reason": "AI bug-detection benchmark",
                "fixture_type": "deliberately_broken_code",
                "added": "2026-07-30"
            },
            {
                "path": "bench/fixtures/mutation_fault_fixture.py",
                "reason": "Mutation testing fixture",
                "fixture_type": "intentional_coverage_gap",
                "added": "2026-07-30"
            },
            {
                "path": "bench/fixtures/test_mutation_fault_fixture.py",
                "reason": "Mutation testing fixture",
                "fixture_type": "intentional_coverage_gap",
                "added": "2026-07-30"
            }
        ]
        manifest_path = self._write_manifest(manifest_data)

        exit_code, results = checker.check_fixtures(
            manifest_path=manifest_path,
            repo_root=self.repo_root,
            json_output=False
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(results["valid"])
        self.assertEqual(results["entry_count"], 4)
        self.assertEqual(len(results["findings"]), 0)

    def test_check_fixtures_json_output(self):
        """Test check_fixtures with JSON output."""
        # Create fixture files
        self._create_fixture_file("tests/fixtures/seam_s_sample_task/repo/test_sample.py")
        self._create_fixture_file("tests/fixtures/seam_sample_task/repo/main.py")
        self._create_fixture_file("bench/fixtures/mutation_fault_fixture.py")
        self._create_fixture_file("bench/fixtures/test_mutation_fault_fixture.py")

        # Create manifest
        manifest_data = [
            {
                "path": "tests/fixtures/seam_s_sample_task/repo/test_sample.py",
                "reason": "AI bug-detection benchmark",
                "fixture_type": "deliberately_broken_code",
                "added": "2026-07-30"
            },
            {
                "path": "tests/fixtures/seam_sample_task/repo/main.py",
                "reason": "AI bug-detection benchmark",
                "fixture_type": "deliberately_broken_code",
                "added": "2026-07-30"
            },
            {
                "path": "bench/fixtures/mutation_fault_fixture.py",
                "reason": "Mutation testing fixture",
                "fixture_type": "intentional_coverage_gap",
                "added": "2026-07-30"
            },
            {
                "path": "bench/fixtures/test_mutation_fault_fixture.py",
                "reason": "Mutation testing fixture",
                "fixture_type": "intentional_coverage_gap",
                "added": "2026-07-30"
            }
        ]
        manifest_path = self._write_manifest(manifest_data)

        exit_code, results = checker.check_fixtures(
            manifest_path=manifest_path,
            repo_root=self.repo_root,
            json_output=True
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(results["valid"])
        self.assertIn("manifest_path", results)

    def test_check_fixtures_with_findings(self):
        """Test check_fixtures reports findings."""
        # Create only some fixture files
        self._create_fixture_file("tests/fixtures/seam_s_sample_task/repo/test_sample.py")
        # Missing other fixtures

        # Create incomplete manifest
        manifest_data = [
            {
                "path": "tests/fixtures/seam_s_sample_task/repo/test_sample.py",
                "reason": "AI bug-detection benchmark",
                "fixture_type": "deliberately_broken_code",
                "added": "2026-07-30"
            }
        ]
        manifest_path = self._write_manifest(manifest_data)

        exit_code, results = checker.check_fixtures(
            manifest_path=manifest_path,
            repo_root=self.repo_root,
            json_output=False
        )

        self.assertNotEqual(exit_code, 0)
        self.assertFalse(results["valid"])
        # Should have findings about missing fixtures
        self.assertTrue(len(results["findings"]) > 0)

    def test_validate_entry_empty_reason(self):
        """Test validation rejects empty reason."""
        self._create_fixture_file("test.py")

        entry = {
            "path": "test.py",
            "reason": "",  # Empty
            "fixture_type": "deliberately_broken_code",
            "added": "2026-07-30"
        }

        is_valid, findings = checker.validate_manifest_entry(entry, 0, self.repo_root)

        self.assertFalse(is_valid)
        self.assertTrue(any("reason" in f for f in findings))

    def test_validate_entry_path_not_string(self):
        """Test validation rejects non-string path."""
        entry = {
            "path": 123,  # Not a string
            "reason": "Test",
            "fixture_type": "deliberately_broken_code",
            "added": "2026-07-30"
        }

        is_valid, findings = checker.validate_manifest_entry(entry, 0, self.repo_root)

        self.assertFalse(is_valid)
        self.assertTrue(any("path" in f.lower() for f in findings))


if __name__ == "__main__":
    unittest.main()
