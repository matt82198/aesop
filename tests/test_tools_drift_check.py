#!/usr/bin/env python3
"""
Test suite for tools_drift_check.py guardrail.

Tests the pre-push gate that ensures all tools/*.{py,mjs,sh} files are
documented in tools/CLAUDE.md.

Root cause regression test: Reproduces the original escape where
tools/state_rebuild.py was added without documentation.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess


class TestToolsDriftCheck(unittest.TestCase):
    """Test the tools drift checker."""

    def setUp(self):
        """Create a temporary tools directory with CLAUDE.md for testing."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tools_dir = Path(self.tmpdir.name) / "tools"
        self.tools_dir.mkdir()
        self.claude_md_path = self.tools_dir / "CLAUDE.md"

    def tearDown(self):
        """Clean up temporary directory."""
        self.tmpdir.cleanup()

    def _write_claude_md(self, content):
        """Write content to tools/CLAUDE.md."""
        self.claude_md_path.write_text(content, encoding="utf-8")

    def _create_tool_file(self, filename):
        """Create a dummy tool file."""
        (self.tools_dir / filename).write_text("# dummy", encoding="utf-8")

    def test_escape_undocumented_python_file(self):
        """Root cause regression: Python file added without documentation.

        This reproduces the original escape where tools/state_rebuild.py
        was added but not documented in tools/CLAUDE.md.
        """
        # Setup: Create state_rebuild.py (undocumented)
        self._create_tool_file("state_rebuild.py")

        # Setup: Create CLAUDE.md with documentation for other tools
        self._write_claude_md(
            "# tools/ — Build utilities\n\n"
            "## Tool index\n"
            "- `secret_scan.py` — Secret scanner\n"
            "- `lock.mjs` — Locking utility\n"
        )

        # Import and run the check
        sys.path.insert(0, str(self.tools_dir.parent.parent))
        try:
            from tools import tools_drift_check

            exit_code, findings = tools_drift_check.check_drift(
                self.tools_dir, self.claude_md_path, json_output=False
            )

            # Should detect the escape
            self.assertEqual(exit_code, 1, "Should fail when undocumented file found")
            self.assertIn(
                "state_rebuild.py", findings, "Should identify state_rebuild.py as undocumented"
            )
        finally:
            sys.path.pop(0)

    def test_clean_state_no_false_positives(self):
        """Test that the check passes when all files are documented."""
        # Setup: Create tool files
        self._create_tool_file("secret_scan.py")
        self._create_tool_file("lock.mjs")
        self._create_tool_file("run_tests.sh")

        # Setup: Create CLAUDE.md documenting all of them
        self._write_claude_md(
            "# tools/ — Build utilities\n\n"
            "## Tool index\n"
            "- `secret_scan.py` — Secret scanner\n"
            "- `lock.mjs` — Locking utility\n"
            "- `run_tests.sh` — Test runner\n"
        )

        sys.path.insert(0, str(self.tools_dir.parent.parent))
        try:
            from tools import tools_drift_check

            exit_code, findings = tools_drift_check.check_drift(
                self.tools_dir, self.claude_md_path, json_output=False
            )

            self.assertEqual(exit_code, 0, "Should pass when all files documented")
            self.assertEqual(findings, [], "Should have no undocumented files")
        finally:
            sys.path.pop(0)

    def test_multiple_undocumented_files(self):
        """Test detection of multiple undocumented files."""
        # Setup: Create multiple undocumented files
        self._create_tool_file("new_tool_1.py")
        self._create_tool_file("new_tool_2.sh")
        self._create_tool_file("documented_tool.py")

        self._write_claude_md(
            "# tools/ — Build utilities\n\n" "## Tool index\n" "- `documented_tool.py` — Documented\n"
        )

        sys.path.insert(0, str(self.tools_dir.parent.parent))
        try:
            from tools import tools_drift_check

            exit_code, findings = tools_drift_check.check_drift(
                self.tools_dir, self.claude_md_path, json_output=False
            )

            self.assertEqual(exit_code, 1, "Should fail with undocumented files")
            self.assertEqual(
                set(findings),
                {"new_tool_1.py", "new_tool_2.sh"},
                "Should identify all undocumented files",
            )
        finally:
            sys.path.pop(0)

    def test_json_output_format(self):
        """Test JSON output format."""
        # Setup: Create undocumented files
        self._create_tool_file("undoc_1.py")
        self._create_tool_file("undoc_2.mjs")

        self._write_claude_md("# tools/\n\n## Tool index\n")

        sys.path.insert(0, str(self.tools_dir.parent.parent))
        try:
            from tools import tools_drift_check

            exit_code, findings = tools_drift_check.check_drift(
                self.tools_dir, self.claude_md_path, json_output=True
            )

            self.assertEqual(exit_code, 1, "Should fail with undocumented files")
            self.assertEqual(
                set(findings), {"undoc_1.py", "undoc_2.mjs"}, "Should identify undocumented files"
            )
        finally:
            sys.path.pop(0)

    def test_missing_claude_md(self):
        """Test error handling when CLAUDE.md is missing."""
        sys.path.insert(0, str(self.tools_dir.parent.parent))
        try:
            from tools import tools_drift_check

            exit_code, findings = tools_drift_check.check_drift(
                self.tools_dir, self.claude_md_path, json_output=False
            )

            self.assertEqual(exit_code, 2, "Should return error code 2 for missing CLAUDE.md")
            self.assertEqual(findings, [], "Should return empty findings on error")
        finally:
            sys.path.pop(0)

    def test_tool_file_extensions_only(self):
        """Test that only .py, .mjs, .sh files are checked."""
        # Create files with various extensions
        self._create_tool_file("tool.py")
        self._create_tool_file("script.sh")
        self._create_tool_file("module.mjs")
        self._create_tool_file("readme.md")  # Should be ignored
        self._create_tool_file("config.json")  # Should be ignored

        self._write_claude_md(
            "# tools/\n\n"
            "## Tool index\n"
            "- `tool.py` — Python tool\n"
            "- `script.sh` — Shell script\n"
            "- `module.mjs` — Node module\n"
        )

        sys.path.insert(0, str(self.tools_dir.parent.parent))
        try:
            from tools import tools_drift_check

            exit_code, findings = tools_drift_check.check_drift(
                self.tools_dir, self.claude_md_path, json_output=False
            )

            self.assertEqual(
                exit_code, 0, "Should ignore non-{py,mjs,sh} files and find all documented"
            )
            self.assertEqual(findings, [], "Should have no undocumented .py/.mjs/.sh files")
        finally:
            sys.path.pop(0)

    def test_backtick_pattern_matching(self):
        """Test that only backtick-quoted filenames are recognized as documented."""
        # Create a tool file
        self._create_tool_file("my_tool.py")

        # Write CLAUDE.md that mentions the file but not in backticks
        self._write_claude_md(
            "# tools/\n\n"
            "## Description\n"
            "This directory contains my_tool.py (not in backticks)\n"
            "and also `my_tool.py` (in backticks)\n"
        )

        sys.path.insert(0, str(self.tools_dir.parent.parent))
        try:
            from tools import tools_drift_check

            exit_code, findings = tools_drift_check.check_drift(
                self.tools_dir, self.claude_md_path, json_output=False
            )

            # Should find it because it's in backticks
            self.assertEqual(exit_code, 0, "Should recognize backtick-quoted filename")
        finally:
            sys.path.pop(0)

    def test_partial_match_not_sufficient(self):
        """Test that partial matches don't count as documentation."""
        # Create tool files
        self._create_tool_file("my_tool_full.py")
        self._create_tool_file("my_tool.py")

        # Document only the full name
        self._write_claude_md(
            "# tools/\n\n"
            "## Tool index\n"
            "- `my_tool_full.py` — Full tool\n"
        )

        sys.path.insert(0, str(self.tools_dir.parent.parent))
        try:
            from tools import tools_drift_check

            exit_code, findings = tools_drift_check.check_drift(
                self.tools_dir, self.claude_md_path, json_output=False
            )

            self.assertEqual(exit_code, 1, "Should fail when partial match is documented")
            self.assertIn("my_tool.py", findings, "Should identify my_tool.py as undocumented")
            self.assertNotIn(
                "my_tool_full.py", findings, "Should not flag my_tool_full.py as undocumented"
            )
        finally:
            sys.path.pop(0)

    def test_empty_tools_directory(self):
        """Test behavior with an empty tools directory."""
        # No tool files created
        self._write_claude_md("# tools/\n\n## Tool index\n")

        sys.path.insert(0, str(self.tools_dir.parent.parent))
        try:
            from tools import tools_drift_check

            exit_code, findings = tools_drift_check.check_drift(
                self.tools_dir, self.claude_md_path, json_output=False
            )

            self.assertEqual(exit_code, 0, "Should pass with empty tools directory")
            self.assertEqual(findings, [], "Should have no findings")
        finally:
            sys.path.pop(0)



if __name__ == "__main__":
    import os

    unittest.main()
