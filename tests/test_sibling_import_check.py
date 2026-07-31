#!/usr/bin/env python3
"""
Test suite for sibling_import_check.py guardrail.

Tests verify:
  - Unguarded sibling imports are detected and flagged
  - Guarded sibling imports pass
  - from tools.X imports (already safe) pass
  - Stdlib imports are ignored
  - Third-party imports are ignored
  - Zero-files-scanned exits with code 2
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Ensure tools directory is on path
TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import sibling_import_check


class TestDetectUnguardedImports(unittest.TestCase):
    """Tests for detection of unguarded sibling imports."""

    def test_unguarded_from_import_flagged(self):
        """Test that unguarded 'from X import' is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create tools directory
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir()

            # Create a fake sibling module
            (tools_dir / "existing_module.py").write_text("# Dummy module\n", encoding="utf-8")

            # Create a module with unguarded import
            test_file = tools_dir / "test_unguarded.py"
            test_file.write_text(
                "from existing_module import something\n",
                encoding="utf-8",
            )

            findings, file_count = sibling_import_check.scan_tools_directory(str(tmp_path))
            self.assertEqual(file_count, 2)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].module_name, "existing_module")
            self.assertIn("from existing_module import", findings[0].import_form)

    def test_unguarded_import_flagged(self):
        """Test that unguarded 'import X' is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir()

            (tools_dir / "existing_module.py").write_text("# Dummy\n", encoding="utf-8")

            test_file = tools_dir / "test_unguarded.py"
            test_file.write_text("import existing_module\n", encoding="utf-8")

            findings, file_count = sibling_import_check.scan_tools_directory(str(tmp_path))
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].module_name, "existing_module")
            self.assertIn("import existing_module", findings[0].import_form)


class TestGuardedImports(unittest.TestCase):
    """Tests for imports that are properly guarded."""

    def test_guarded_sibling_import_passes(self):
        """Test that guarded sibling imports pass the check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir()

            (tools_dir / "lint_core.py").write_text("# Core module\n", encoding="utf-8")

            # Module with proper guard
            test_file = tools_dir / "test_guarded.py"
            test_file.write_text(
                """import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lint_core import something
""",
                encoding="utf-8",
            )

            findings, _ = sibling_import_check.scan_tools_directory(str(tmp_path))
            # Guard is present, so no violations
            self.assertEqual(len(findings), 0)


class TestSafeImportForms(unittest.TestCase):
    """Tests for import forms that are already path-safe."""

    def test_from_tools_package_passes(self):
        """Test that 'from tools.X' form is not flagged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir()

            (tools_dir / "lint_core.py").write_text("# Core\n", encoding="utf-8")

            # Using explicit package form (safe)
            test_file = tools_dir / "test_safe.py"
            test_file.write_text("from tools.lint_core import something\n", encoding="utf-8")

            findings, _ = sibling_import_check.scan_tools_directory(str(tmp_path))
            # Should not flag 'from tools.X' form
            self.assertEqual(len(findings), 0)

    def test_stdlib_imports_ignored(self):
        """Test that stdlib imports are not flagged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir()

            # Module with only stdlib imports
            test_file = tools_dir / "test_stdlib.py"
            test_file.write_text(
                """import sys
import os
from json import loads
from pathlib import Path
""",
                encoding="utf-8",
            )

            findings, _ = sibling_import_check.scan_tools_directory(str(tmp_path))
            self.assertEqual(len(findings), 0)

    def test_third_party_imports_ignored(self):
        """Test that third-party imports are not flagged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir()

            # Module with third-party imports (not in tools/)
            test_file = tools_dir / "test_third_party.py"
            test_file.write_text(
                """import requests
from pytest import mark
""",
                encoding="utf-8",
            )

            findings, _ = sibling_import_check.scan_tools_directory(str(tmp_path))
            self.assertEqual(len(findings), 0)


class TestExitCodes(unittest.TestCase):
    """Tests for exit code contract."""

    def test_clean_exit_zero(self):
        """Test that clean scan exits with 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir()

            # Only stdlib imports
            (tools_dir / "clean.py").write_text("import sys\n", encoding="utf-8")

            with mock.patch("sys.argv", ["sibling_import_check.py", "--paths", str(tmp_path)]):
                with mock.patch("builtins.print"):
                    exit_code = sibling_import_check.main()
                    self.assertEqual(exit_code, 0)

    def test_violations_exit_one(self):
        """Test that violations exit with 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir()

            (tools_dir / "existing.py").write_text("# Module\n", encoding="utf-8")
            (tools_dir / "bad.py").write_text("from existing import x\n", encoding="utf-8")

            with mock.patch("sys.argv", ["sibling_import_check.py", "--paths", str(tmp_path)]):
                with mock.patch("builtins.print"):
                    exit_code = sibling_import_check.main()
                    self.assertEqual(exit_code, 1)

    def test_zero_files_exit_two(self):
        """Test that zero files scanned exits with 2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Empty directory, no tools/ subdirectory
            with mock.patch("sys.argv", ["sibling_import_check.py", "--paths", tmpdir]):
                with mock.patch("builtins.print"):
                    exit_code = sibling_import_check.main()
                    self.assertEqual(exit_code, 2)


class TestJSONOutput(unittest.TestCase):
    """Tests for JSON output format."""

    def test_json_valid_on_clean(self):
        """Test that JSON output is valid when clean."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir()

            (tools_dir / "clean.py").write_text("import sys\n", encoding="utf-8")

            with mock.patch("sys.argv", ["sibling_import_check.py", "--json", "--paths", str(tmp_path)]):
                with mock.patch("builtins.print") as mock_print:
                    sibling_import_check.main()
                    output_str = mock_print.call_args[0][0]
                    output = json.loads(output_str)
                    self.assertEqual(output["status"], "PASS")
                    self.assertIsInstance(output["findings"], list)

    def test_json_includes_violations(self):
        """Test that JSON output includes violation details."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir()

            (tools_dir / "existing.py").write_text("# Module\n", encoding="utf-8")
            (tools_dir / "bad.py").write_text("from existing import x\n", encoding="utf-8")

            with mock.patch("sys.argv", ["sibling_import_check.py", "--json", "--paths", str(tmp_path)]):
                with mock.patch("builtins.print") as mock_print:
                    sibling_import_check.main()
                    output_str = mock_print.call_args[0][0]
                    output = json.loads(output_str)
                    self.assertEqual(output["status"], "FAIL")
                    self.assertGreater(len(output["findings"]), 0)
                    finding = output["findings"][0]
                    self.assertIn("file", finding)
                    self.assertIn("module", finding)
                    self.assertIn("line", finding)


if __name__ == "__main__":
    unittest.main()
