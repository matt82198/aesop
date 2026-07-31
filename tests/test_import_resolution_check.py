#!/usr/bin/env python3
"""
Test suite for tools/import_resolution_check.py (guardrail G5).

Tests:
1. Reproduction: unresolvable imports (state_store.materialize) are caught
2. Clean state: valid imports (stdlib, repo packages) pass
3. Edge cases: relative imports, partial resolution
4. AST parsing edge cases: syntax errors, empty files
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestImportResolutionCheck(unittest.TestCase):
    """Test suite for import resolution validator."""

    def setUp(self):
        """Create a temporary repo for testing."""
        self.tmpdir = tempfile.mkdtemp()
        self.repo_root = Path(self.tmpdir)

        # Initialize git repo
        subprocess.run(
            ["git", "init"],
            cwd=self.repo_root,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_root,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.repo_root,
            capture_output=True,
            check=True,
        )

        # Create basic repo structure
        (self.repo_root / "state_store").mkdir()
        (self.repo_root / "state_store" / "__init__.py").write_text("")
        (self.repo_root / "tools").mkdir()
        (self.repo_root / "tools" / "__init__.py").write_text("")
        (self.repo_root / "state").mkdir()

        # Stage and commit initial files
        subprocess.run(
            ["git", "add", "."],
            cwd=self.repo_root,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=self.repo_root,
            capture_output=True,
            check=True,
        )

    def tearDown(self):
        """Clean up temporary repo."""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _stage_file(self, filepath, content):
        """Create and stage a file with content."""
        file_path = self.repo_root / filepath
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        subprocess.run(
            ["git", "add", filepath],
            cwd=self.repo_root,
            capture_output=True,
            check=True,
        )

    def _run_check(self):
        """Run the import resolution check."""
        # Get path to the check script (from the repo)
        check_script = Path(__file__).parent.parent / "tools" / "import_resolution_check.py"

        result = subprocess.run(
            [sys.executable, str(check_script)],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
        )

        return result.returncode, result.stdout, result.stderr

    def _read_audit_log(self):
        """Read the audit log if it exists."""
        audit_log = self.repo_root / "state" / "IMPORT-AUDIT.log"
        if not audit_log.exists():
            return []

        records = []
        with open(audit_log, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
        return records

    def test_clean_state_stdlib_imports(self):
        """Test that stdlib imports pass without error."""
        self._stage_file(
            "test_stdlib.py",
            "import sys\nimport json\nfrom pathlib import Path\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, f"Expected exit 0 for stdlib imports, got {rc}. stderr: {stderr}")

        # Verify audit log recorded pass
        audit_records = self._read_audit_log()
        self.assertTrue(
            any(r["event"] == "import_check_pass" for r in audit_records),
            "Audit log should record import_check_pass",
        )

    def test_clean_state_repo_imports(self):
        """Test that repo package imports pass without error."""
        self._stage_file(
            "consumer.py",
            "from state_store import api\nimport tools\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, f"Expected exit 0 for repo imports, got {rc}. stderr: {stderr}")

        audit_records = self._read_audit_log()
        self.assertTrue(
            any(r["event"] == "import_check_pass" for r in audit_records),
            "Audit log should record import_check_pass",
        )

    def test_reproduction_unresolvable_import(self):
        """
        Reproduce the original escape: unresolvable import.

        Root cause: Agent wrote file with "from state_store.materialize import ..."
        to primary tree. state_store.materialize does not exist.
        This test verifies the guardrail catches it.
        """
        self._stage_file(
            "broken_import.py",
            "from state_store.materialize import something\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertNotEqual(rc, 0, "Expected non-zero exit for unresolvable import")
        self.assertIn("materialize", stderr, "Error should mention the unresolvable module")

        # Verify audit log recorded failure
        audit_records = self._read_audit_log()
        self.assertTrue(
            any(r["event"] == "import_check_fail" for r in audit_records),
            "Audit log should record import_check_fail",
        )

        # Verify finding details
        fail_record = next(r for r in audit_records if r["event"] == "import_check_fail")
        self.assertFalse(fail_record["is_valid"], "is_valid should be False")
        self.assertGreater(fail_record["finding_count"], 0, "Should have at least one finding")

    def test_unresolvable_nonexistent_package(self):
        """Test that non-existent package imports are caught."""
        self._stage_file(
            "bad_import.py",
            "import nonexistent_package\nfrom another_missing_module import Thing\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertNotEqual(rc, 0, "Expected non-zero exit for non-existent package")
        self.assertIn("nonexistent_package", stderr, "Error should mention missing package")

    def test_mixed_imports_valid_and_invalid(self):
        """Test file with both valid and invalid imports."""
        self._stage_file(
            "mixed_imports.py",
            """import sys
import json
from state_store import api
from missing_module import Something
import os
""",
        )

        rc, stdout, stderr = self._run_check()
        self.assertNotEqual(rc, 0, "Expected non-zero exit when any import is invalid")
        self.assertIn("missing_module", stderr, "Error should mention the missing module")

    def test_no_staged_files(self):
        """Test with no staged Python files."""
        # Don't stage anything, just run the check
        rc, stdout, stderr = self._run_check()
        # Should pass (no files to check)
        self.assertEqual(rc, 0, "Should pass when no Python files staged")

    def test_syntax_error_handling(self):
        """Test that syntax errors in Python files are handled gracefully."""
        self._stage_file(
            "syntax_error.py",
            "def foo(\n  this is not valid python",
        )

        # Should return 0 because syntax errors only generate warnings, not failures
        rc, stdout, stderr = self._run_check()
        # The check logs a warning but continues
        self.assertIn("syntax error", stderr.lower(), "Should report syntax error warning")

    def test_empty_file(self):
        """Test that empty files pass."""
        self._stage_file("empty.py", "")

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, "Empty files should pass")

    def test_comments_only_file(self):
        """Test that files with only comments pass."""
        self._stage_file(
            "comments.py",
            "# This is a comment\n# No real code here\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, "Files with only comments should pass")

    def test_relative_imports_in_package(self):
        """Test relative imports within a package."""
        # Create package structure
        pkg_dir = self.repo_root / "mypackage"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "module_a.py").write_text("def func_a(): pass")
        (pkg_dir / "module_b.py").write_text("from . import module_a\nfrom .module_a import func_a")

        subprocess.run(
            ["git", "add", "."],
            cwd=self.repo_root,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add package"],
            cwd=self.repo_root,
            capture_output=True,
            check=True,
        )

        # Stage a new file with relative imports
        self._stage_file(
            "mypackage/module_c.py",
            "from . import module_a\nfrom . import module_b\n",
        )

        rc, stdout, stderr = self._run_check()
        # Relative imports (from . import) don't have a module_name, so they're skipped
        self.assertEqual(rc, 0, "Relative imports should not cause failures")

    def test_audit_log_created(self):
        """Test that audit log is created and has valid JSON."""
        self._stage_file("valid.py", "import sys\n")

        rc, stdout, stderr = self._run_check()

        audit_log = self.repo_root / "state" / "IMPORT-AUDIT.log"
        self.assertTrue(audit_log.exists(), "Audit log should be created")

        # Verify JSON validity
        records = self._read_audit_log()
        self.assertGreater(len(records), 0, "Audit log should have entries")

        for record in records:
            self.assertIn("event", record, "Each audit record should have 'event' field")
            self.assertIn("is_valid", record, "Each audit record should have 'is_valid' field")
            self.assertIn("finding_count", record, "Each audit record should have 'finding_count' field")

    def test_multiple_staging_isolation(self):
        """Test that multiple runs with different staged files work correctly."""
        # First run with valid import
        self._stage_file("run1.py", "import json\n")
        rc1, _, _ = self._run_check()
        self.assertEqual(rc1, 0, "First run should pass")

        # Reset for second run
        subprocess.run(
            ["git", "reset", "HEAD", "."],
            cwd=self.repo_root,
            capture_output=True,
        )

        # Second run with invalid import
        self._stage_file("run2.py", "from missing import Something\n")
        rc2, _, stderr2 = self._run_check()
        self.assertNotEqual(rc2, 0, "Second run should fail")
        self.assertIn("missing", stderr2, "Should mention missing module")

    def test_windows_path_handling(self):
        """Test that Windows-style paths are handled correctly."""
        # This test creates a file with a path that might look like Windows
        self._stage_file(
            "subdir/nested/file.py",
            "import sys\nfrom state_store import api\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, "Should handle nested paths correctly")


if __name__ == "__main__":
    unittest.main()
