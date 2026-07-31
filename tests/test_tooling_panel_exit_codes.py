#!/usr/bin/env python3
"""
test_tooling_panel_exit_codes.py -- Test that tooling_panel correctly distinguishes
between findings-exit (exit 1) and error-exit (exit 2+).

This test verifies the fix for the dead_code_check.py bug where exit code 1
(findings found) was incorrectly treated as a tool failure instead of a valid result.

Findings-tools (dead_code_check.py, import_cycle_check.py, encoding_lint.py) use:
  - Exit 0: clean (no findings)
  - Exit 1: findings found (valid result, outputs JSON with findings)
  - Exit 2+: actual error

The caller must distinguish these and report exit 1 as findings, not as an error.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add ui/ to path for import
sys.path.insert(0, str(Path(__file__).parent.parent / "ui"))

import tooling_panel


class TestFindingsExitCodes(unittest.TestCase):
    """Test that findings-exit codes (exit 1) are treated as valid results."""

    def test_is_acceptable_exit_code_dead_code_check_clean(self):
        """Exit 0 from dead_code_check.py is acceptable (no findings)."""
        self.assertTrue(
            tooling_panel._is_acceptable_exit_code("dead_code_check.py", 0)
        )

    def test_is_acceptable_exit_code_dead_code_check_findings(self):
        """Exit 1 from dead_code_check.py is acceptable (findings found)."""
        self.assertTrue(
            tooling_panel._is_acceptable_exit_code("dead_code_check.py", 1)
        )

    def test_is_acceptable_exit_code_dead_code_check_error(self):
        """Exit 2+ from dead_code_check.py is NOT acceptable (error)."""
        self.assertFalse(
            tooling_panel._is_acceptable_exit_code("dead_code_check.py", 2)
        )
        self.assertFalse(
            tooling_panel._is_acceptable_exit_code("dead_code_check.py", 3)
        )

    def test_is_acceptable_exit_code_import_cycle_check(self):
        """import_cycle_check.py follows same pattern (0=clean, 1=findings, 2+=error)."""
        self.assertTrue(
            tooling_panel._is_acceptable_exit_code("import_cycle_check.py", 0)
        )
        self.assertTrue(
            tooling_panel._is_acceptable_exit_code("import_cycle_check.py", 1)
        )
        self.assertFalse(
            tooling_panel._is_acceptable_exit_code("import_cycle_check.py", 2)
        )

    def test_is_acceptable_exit_code_encoding_lint(self):
        """encoding_lint.py follows same pattern (0=clean, 1=findings, 2+=error)."""
        self.assertTrue(
            tooling_panel._is_acceptable_exit_code("encoding_lint.py", 0)
        )
        self.assertTrue(
            tooling_panel._is_acceptable_exit_code("encoding_lint.py", 1)
        )
        self.assertFalse(
            tooling_panel._is_acceptable_exit_code("encoding_lint.py", 2)
        )

    def test_is_acceptable_exit_code_other_tools(self):
        """Other tools only accept exit 0 (no findings-exit convention)."""
        self.assertTrue(
            tooling_panel._is_acceptable_exit_code("todo_tracker.py", 0)
        )
        self.assertFalse(
            tooling_panel._is_acceptable_exit_code("todo_tracker.py", 1)
        )
        self.assertFalse(
            tooling_panel._is_acceptable_exit_code("todo_tracker.py", 2)
        )


class TestRunToolWithFindingsExit(unittest.TestCase):
    """Test that _run_tool correctly handles findings-exit (exit 1)."""

    def setUp(self):
        """Set up mock config."""
        self.config_patch = patch("tooling_panel.config")
        self.mock_config = self.config_patch.start()
        self.mock_config.AESOP_ROOT = "/fake/root"

    def tearDown(self):
        """Clean up patches."""
        self.config_patch.stop()

    def test_run_tool_with_findings_exit_parses_json(self):
        """When dead_code_check exits 1 with JSON, the JSON is parsed (findings found)."""
        findings_data = [
            {"name": "unused_func", "type": "function", "file": "module.py", "line": 10}
        ]
        json_output = json.dumps(findings_data)

        with patch("tooling_panel.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1  # Findings found
            mock_result.stdout = json_output
            mock_run.return_value = mock_result

            # Mock Path.is_file to return True
            with patch("tooling_panel.Path.is_file", return_value=True):
                result = tooling_panel._run_tool("dead_code_check.py")

        # The JSON should be parsed and returned (not treated as an error)
        self.assertEqual(result, findings_data)

    def test_run_tool_with_error_exit_raises_error(self):
        """When dead_code_check exits 2+ with error, ToolError is raised."""
        with patch("tooling_panel.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 2  # Actual error
            mock_result.stdout = ""
            mock_run.return_value = mock_result

            with patch("tooling_panel.Path.is_file", return_value=True):
                with self.assertRaises(tooling_panel.ToolError) as cm:
                    tooling_panel._run_tool("dead_code_check.py")

        self.assertEqual(cm.exception.error_class, "tool-exit-nonzero")
        self.assertIn("exited with code 2", cm.exception.message)

    def test_run_tool_with_findings_parses_multiple_items(self):
        """Multiple findings are correctly parsed when tool exits 1."""
        findings_data = [
            {"name": "unused_func1", "type": "function", "file": "module.py", "line": 10},
            {"name": "unused_func2", "type": "function", "file": "module.py", "line": 20},
            {"name": "dead_class", "type": "class", "file": "other.py", "line": 5},
        ]
        json_output = json.dumps(findings_data)

        with patch("tooling_panel.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1  # Findings found
            mock_result.stdout = json_output
            mock_run.return_value = mock_result

            with patch("tooling_panel.Path.is_file", return_value=True):
                result = tooling_panel._run_tool("dead_code_check.py")

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["name"], "unused_func1")
        self.assertEqual(result[2]["name"], "dead_class")

    def test_extract_dead_code_counts_findings(self):
        """_extract_dead_code correctly counts items from findings."""
        findings_list = [
            {"name": "func1", "type": "function", "file": "a.py", "line": 1},
            {"name": "func2", "type": "function", "file": "b.py", "line": 2},
        ]
        count = tooling_panel._extract_dead_code(findings_list)
        self.assertEqual(count, 2)

    def test_extract_dead_code_from_dict_with_count(self):
        """_extract_dead_code handles dict with 'count' key."""
        findings_dict = {"count": 5, "items": [...]}
        count = tooling_panel._extract_dead_code(findings_dict)
        self.assertEqual(count, 5)

    def test_extract_dead_code_from_dict_with_dead_key(self):
        """_extract_dead_code handles dict with 'dead' key."""
        findings_dict = {"dead": [{"name": "x"}, {"name": "y"}, {"name": "z"}]}
        count = tooling_panel._extract_dead_code(findings_dict)
        self.assertEqual(count, 3)

    def test_extract_dead_code_empty_list(self):
        """_extract_dead_code returns 0 for empty findings."""
        self.assertEqual(tooling_panel._extract_dead_code([]), 0)

    def test_extract_dead_code_none_returns_none(self):
        """_extract_dead_code returns None when passed None."""
        self.assertIsNone(tooling_panel._extract_dead_code(None))


class TestScanToolingWithFindings(unittest.TestCase):
    """Test _scan_tooling correctly aggregates results including findings."""

    def setUp(self):
        """Set up mock config."""
        self.config_patch = patch("tooling_panel.config")
        self.mock_config = self.config_patch.start()
        self.mock_config.AESOP_ROOT = "/fake/root"

    def tearDown(self):
        """Clean up patches."""
        self.config_patch.stop()

    def test_scan_tooling_with_dead_code_findings(self):
        """_scan_tooling correctly reports dead code count when tool exits 1."""
        dead_code_findings = [
            {"name": "unused_func", "type": "function", "file": "module.py", "line": 10}
        ]

        def mock_run_tool(tool_name, args=None):
            if tool_name == "dead_code_check.py":
                return dead_code_findings
            return None

        with patch("tooling_panel._run_tool", side_effect=mock_run_tool):
            result = tooling_panel._scan_tooling()

        # dead_code_count should be 1 (one item found)
        self.assertEqual(result["dead_code_count"], 1)
        # Other counts should be None (no data)
        self.assertIsNone(result["todo_count"])
        self.assertIsNone(result["coverage_pct"])

    def test_scan_tooling_gracefully_handles_tool_error(self):
        """_scan_tooling logs error and continues gracefully on ToolError."""
        def mock_run_tool(tool_name, args=None):
            if tool_name == "dead_code_check.py":
                raise tooling_panel.ToolError(
                    "tool-exit-nonzero",
                    "dead_code_check.py exited with code 2"
                )
            return None

        with patch("tooling_panel._run_tool", side_effect=mock_run_tool):
            with patch("sys.stderr"):
                result = tooling_panel._scan_tooling()

        # Should gracefully degrade: dead_code_count is None
        self.assertIsNone(result["dead_code_count"])
        # Other metrics should still be attempted/None
        self.assertIsNone(result["todo_count"])
        self.assertIn("scanned_at", result)


if __name__ == "__main__":
    unittest.main()
