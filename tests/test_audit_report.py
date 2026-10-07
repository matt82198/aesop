#!/usr/bin/env python3
"""
Tests for tools/audit_report.py — deterministic audit report generator.

TDD: These tests verify:
1. Report aggregates defect_escape, mutation_test, claudemd_lint, and ledger outputs
2. Missing sources are noted but don't crash (graceful degradation)
3. --strict mode fails on missing sources
4. Markdown output is deterministic and well-formatted
5. Test hygiene: temp directories only, no pollution
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))


class TestAuditReportFixtures(unittest.TestCase):
    """Test audit_report tool with fixture data."""

    def setUp(self):
        """Set up temporary directory for fixtures."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir_path = Path(self.tmpdir.name)

    def tearDown(self):
        """Clean up temporary directory."""
        self.tmpdir.cleanup()

    def _create_fixture_defect_escape(self):
        """Create a sample defect_escape.py JSON output."""
        return {
            "window": {"since": "2026-07-01", "total_commits": 10},
            "feature_commits": 8,
            "fixforward_commits": 2,
            "fixforward_rate": 0.25,
            "first_try_estimate": 0.75,
        }

    def _create_fixture_mutation_test(self):
        """Create a sample mutation_test.py JSON output."""
        return {
            "killed": 45,
            "survived": 5,
            "mutations": [
                {
                    "file": "tools/audit_report.py",
                    "line": 42,
                    "original": "==",
                    "mutated": "!=",
                }
            ],
        }

    def _create_fixture_claudemd_lint(self):
        """Create a sample claudemd_lint.py JSON output."""
        return {
            "findings": [
                {
                    "type": "phantom-path",
                    "line": "?",
                    "message": "tools/CLAUDE.md: references non-existent 'tools/nonexistent.py'",
                }
            ],
            "count": 1,
            "repo_root": "/repo",
        }

    def _create_fixture_ledger(self):
        """Create a sample OUTCOMES-LEDGER.md file."""
        return """| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |
|--------|------------|-------|--------------|-----------|------------|--------|-------|------|
| 2026-07-21T10:00:00Z | haiku | claude-3-5-haiku | 30 | 1000 | 2000 | OK | build | 26 |
| 2026-07-21T10:31:00Z | haiku | claude-3-5-haiku | 45 | 1500 | 3000 | OK | verify | 26 |
"""

    def test_report_with_all_sources(self):
        """Test report generation with all sources present."""
        # Create fixture files
        defect_file = self.tmpdir_path / "defect_escape.json"
        mutation_file = self.tmpdir_path / "mutation_test.json"
        claudemd_file = self.tmpdir_path / "claudemd_lint.json"
        ledger_file = self.tmpdir_path / "OUTCOMES-LEDGER.md"

        defect_file.write_text(json.dumps(self._create_fixture_defect_escape()))
        mutation_file.write_text(json.dumps(self._create_fixture_mutation_test()))
        claudemd_file.write_text(json.dumps(self._create_fixture_claudemd_lint()))
        ledger_file.write_text(self._create_fixture_ledger())

        # Run tool with fixture files
        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "audit_report.py"),
            f"--defect-escape={defect_file}",
            f"--mutation-test={mutation_file}",
            f"--claudemd-lint={claudemd_file}",
            f"--ledger={ledger_file}",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        # Verify markdown output structure
        output = result.stdout
        self.assertIn("# Audit Report", output)
        self.assertIn("Defect Escape", output)
        self.assertIn("Mutation Testing", output)
        self.assertIn("CLAUDE.md Lint", output)
        self.assertIn("Fleet Ledger Summary", output)

    def test_report_missing_sources_graceful(self):
        """Test report generation with missing sources (graceful degradation)."""
        # Create only one fixture file
        defect_file = self.tmpdir_path / "defect_escape.json"
        defect_file.write_text(json.dumps(self._create_fixture_defect_escape()))

        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "audit_report.py"),
            f"--defect-escape={defect_file}",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        # Should note missing sources but still output
        output = result.stdout
        self.assertIn("# Audit Report", output)
        self.assertIn("Defect Escape", output)

    def test_report_strict_mode_fails_missing(self):
        """Test that --strict mode fails when sources are missing."""
        defect_file = self.tmpdir_path / "defect_escape.json"
        defect_file.write_text(json.dumps(self._create_fixture_defect_escape()))

        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "audit_report.py"),
            f"--defect-escape={defect_file}",
            "--strict",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, "Should fail with --strict and missing sources")

    def test_output_file(self):
        """Test --out flag to write to file."""
        defect_file = self.tmpdir_path / "defect_escape.json"
        defect_file.write_text(json.dumps(self._create_fixture_defect_escape()))
        out_file = self.tmpdir_path / "report.md"

        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "audit_report.py"),
            f"--defect-escape={defect_file}",
            f"--out={out_file}",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)

        # Should write to file, not stdout
        self.assertTrue(out_file.exists())
        report_content = out_file.read_text()
        self.assertIn("# Audit Report", report_content)

    def test_deterministic_output(self):
        """Test that output is deterministic (same input → same output with fixed timestamp)."""
        defect_file = self.tmpdir_path / "defect_escape.json"
        defect_file.write_text(json.dumps(self._create_fixture_defect_escape()))

        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "audit_report.py"),
            f"--defect-escape={defect_file}",
            "--timestamp=2026-07-21T00:00:00Z",
        ]

        # Run twice
        result1 = subprocess.run(cmd, capture_output=True, text=True)
        result2 = subprocess.run(cmd, capture_output=True, text=True)

        self.assertEqual(result1.stdout, result2.stdout, "Output should be deterministic")

    def test_timestamp_in_report(self):
        """Test that report includes a timestamp."""
        defect_file = self.tmpdir_path / "defect_escape.json"
        defect_file.write_text(json.dumps(self._create_fixture_defect_escape()))

        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "audit_report.py"),
            f"--defect-escape={defect_file}",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        output = result.stdout

        # Should have a date/timestamp
        self.assertRegex(output, r"\d{4}-\d{2}-\d{2}")


if __name__ == "__main__":
    unittest.main()
