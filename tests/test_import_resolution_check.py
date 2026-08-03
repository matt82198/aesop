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


class _CheckerFixture(unittest.TestCase):
    """Temp-repo fixture + helpers shared by the import-resolution suites."""

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


class TestImportResolutionCheck(_CheckerFixture):
    """Test suite for import resolution validator."""

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


class TestSysPathIdiom(_CheckerFixture):
    """
    The sanctioned repo idiom: a file puts a repo directory on sys.path via
    `sys.path.insert(0, <path derived from __file__>)` and then imports the
    modules living in that directory directly.

    The checker must resolve those imports against the inserted directory
    (no false positives), while keeping every falsifiability case red:
    a module that does not exist under the inserted directory, and a
    sys.path target that is not a literal __file__-derived repo path.
    """

    def _make_dir_module(self, dirname, modname, body="X = 1\n"):
        """Create + commit <dirname>/<modname>.py in the test repo."""
        d = self.repo_root / dirname
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{modname}.py").write_text(body, encoding="utf-8")
        subprocess.run(
            ["git", "add", "."], cwd=self.repo_root, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", f"add {dirname}/{modname}"],
            cwd=self.repo_root,
            capture_output=True,
            check=True,
        )

    def test_syspath_pathlib_literal_resolves(self):
        """sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))."""
        self._make_dir_module("tools", "mymod")
        self._stage_file(
            "tests/test_thing.py",
            "import sys\n"
            "from pathlib import Path\n"
            'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))\n'
            "import mymod\n"
            "from mymod import X\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, f"sanctioned sys.path idiom must resolve. stderr: {stderr}")

    def test_syspath_via_module_variable_resolves(self):
        """The DRIVER_DIR = REPO / "driver" indirection used across tests/."""
        self._make_dir_module("driver", "agent_driver")
        self._stage_file(
            "tests/test_driver_thing.py",
            "import sys\n"
            "from pathlib import Path\n"
            "REPO = Path(__file__).resolve().parent.parent\n"
            'DRIVER_DIR = REPO / "driver"\n'
            "if str(DRIVER_DIR) not in sys.path:\n"
            "    sys.path.insert(0, str(DRIVER_DIR))\n"
            "import agent_driver\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, f"variable-indirected idiom must resolve. stderr: {stderr}")

    def test_syspath_ospath_join_resolves(self):
        """os.path.join(os.path.dirname(__file__), '..', 'tools') form."""
        self._make_dir_module("tools", "othermod")
        self._stage_file(
            "tests/test_join.py",
            "import os\n"
            "import sys\n"
            "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))\n"
            "import othermod\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, f"os.path.join idiom must resolve. stderr: {stderr}")

    def test_syspath_append_form_resolves(self):
        """sys.path.append takes the path as its first argument."""
        self._make_dir_module("tools", "appended_mod")
        self._stage_file(
            "tests/test_append.py",
            "import sys\n"
            "from pathlib import Path\n"
            'sys.path.append(str(Path(__file__).parent.parent / "tools"))\n'
            "import appended_mod\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, f"sys.path.append idiom must resolve. stderr: {stderr}")

    def test_syspath_dotted_submodule_resolves(self):
        """Dotted imports resolve against the inserted directory too."""
        pkg = self.repo_root / "ui" / "api"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "tracker.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "."], cwd=self.repo_root, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "add ui/api"],
            cwd=self.repo_root,
            capture_output=True,
            check=True,
        )
        self._stage_file(
            "tests/test_api.py",
            "import sys\n"
            "from pathlib import Path\n"
            'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ui"))\n'
            "import api\n"
            "from api.tracker import VALUE\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, f"dotted import under inserted dir must resolve. stderr: {stderr}")

    def test_syspath_parents_index_resolves(self):
        """Path(__file__).resolve().parents[1] / "ui" form."""
        self._make_dir_module("ui", "collectors")
        self._stage_file(
            "tests/test_parents.py",
            "import sys\n"
            "from pathlib import Path\n"
            "ROOT = Path(__file__).resolve().parents[1]\n"
            'UI_DIR = ROOT / "ui"\n'
            "if str(UI_DIR) not in sys.path:\n"
            "    sys.path.insert(0, str(UI_DIR))\n"
            "import collectors\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, f"parents[N] idiom must resolve. stderr: {stderr}")

    def test_syspath_for_loop_over_dir_tuple_resolves(self):
        """The `for _p in (DRIVER_DIR, TOOLS_DIR): sys.path.insert(...)` form."""
        self._make_dir_module("driver", "wave_loop")
        self._make_dir_module("tools", "halt")
        self._stage_file(
            "tests/test_loop_insert.py",
            "import sys\n"
            "from pathlib import Path\n"
            "REPO = Path(__file__).resolve().parent.parent\n"
            'DRIVER_DIR = REPO / "driver"\n'
            'TOOLS_DIR = REPO / "tools"\n'
            "for _p in (DRIVER_DIR, TOOLS_DIR):\n"
            "    if str(_p) not in sys.path:\n"
            "        sys.path.insert(0, str(_p))\n"
            "import wave_loop\n"
            "import halt\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, f"for-loop insert idiom must resolve. stderr: {stderr}")

    def test_syspath_inline_import_pathlib_resolves(self):
        """__import__("pathlib").Path(__file__) spelling."""
        self._make_dir_module("tools", "commit_lint")
        self._stage_file(
            "tests/test_inline.py",
            "import sys\n"
            "sys.path.insert(0, str(__import__('pathlib')"
            '.Path(__file__).resolve().parent.parent / "tools"))\n'
            "import commit_lint\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertEqual(rc, 0, f"inline-import Path idiom must resolve. stderr: {stderr}")

    # ---- falsifiability: the idiom must not become a blanket exemption ----

    def test_for_loop_over_dynamic_sequence_still_flagged(self):
        """FALSIFIABILITY: a loop over a non-literal sequence binds nothing."""
        self._make_dir_module("tools", "loop_decoy")
        self._stage_file(
            "tests/test_loop_dynamic.py",
            "import os\n"
            "import sys\n"
            'for _p in os.environ["DIRS"].split(os.pathsep):\n'
            "    sys.path.insert(0, _p)\n"
            "import loop_decoy\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertNotEqual(rc, 0, "dynamic loop sequence must not bless imports")
        self.assertIn("loop_decoy", stderr)

    def test_parents_index_with_dynamic_depth_still_flagged(self):
        """FALSIFIABILITY: parents[<non-literal>] is not a literal path."""
        self._make_dir_module("tools", "depth_decoy")
        self._stage_file(
            "tests/test_dynamic_depth.py",
            "import os\n"
            "import sys\n"
            "from pathlib import Path\n"
            'DEPTH = int(os.environ.get("DEPTH", "1"))\n'
            'TARGET = Path(__file__).resolve().parents[DEPTH] / "tools"\n'
            "sys.path.insert(0, str(TARGET))\n"
            "import depth_decoy\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertNotEqual(rc, 0, "non-literal parents index must not bless imports")
        self.assertIn("depth_decoy", stderr)

    def test_missing_module_under_inserted_dir_still_flagged(self):
        """
        FALSIFIABILITY: inserting tools/ on sys.path must NOT bless an import
        of a module that does not exist there. Without this the fix would be a
        blanket exemption rather than a resolution rule.
        """
        self._make_dir_module("tools", "present_mod")
        self._stage_file(
            "tests/test_missing.py",
            "import sys\n"
            "from pathlib import Path\n"
            'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))\n'
            "import present_mod\n"
            "from absent_mod import Thing\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertNotEqual(rc, 0, "module absent from the inserted dir must still fail")
        self.assertIn("absent_mod", stderr)
        self.assertNotIn("present_mod", stderr, "the present module must not be flagged")

    def test_missing_submodule_of_present_package_still_flagged(self):
        """FALSIFIABILITY: dotted resolution checks the full path, not the root."""
        pkg = self.repo_root / "ui" / "api"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        subprocess.run(
            ["git", "add", "."], cwd=self.repo_root, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "add ui/api"],
            cwd=self.repo_root,
            capture_output=True,
            check=True,
        )
        self._stage_file(
            "tests/test_api_missing.py",
            "import sys\n"
            "from pathlib import Path\n"
            'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ui"))\n'
            "from api.nonexistent import Thing\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertNotEqual(rc, 0, "missing submodule must still fail")
        self.assertIn("api.nonexistent", stderr)

    def test_dynamic_syspath_target_still_flagged(self):
        """
        FALSIFIABILITY: a sys.path target computed at runtime is not the
        sanctioned idiom -- imports behind it stay unresolvable.
        """
        self._stage_file(
            "tests/test_dynamic.py",
            "import sys\n"
            "import tempfile\n"
            "sys.path.insert(0, tempfile.mkdtemp())\n"
            "import mystery_mod\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertNotEqual(rc, 0, "dynamic sys.path target must not bless imports")
        self.assertIn("mystery_mod", stderr)

    def test_env_var_syspath_target_still_flagged(self):
        """FALSIFIABILITY: os.environ-derived sys.path targets stay unresolvable."""
        self._make_dir_module("tools", "decoy_mod")
        self._stage_file(
            "tests/test_env.py",
            "import os\n"
            "import sys\n"
            'sys.path.insert(0, os.environ["SOME_DIR"])\n'
            "import decoy_mod\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertNotEqual(rc, 0, "env-var sys.path target must not bless imports")
        self.assertIn("decoy_mod", stderr)

    def test_syspath_target_outside_repo_still_flagged(self):
        """FALSIFIABILITY: a literal path that is not __file__-derived is ignored."""
        self._stage_file(
            "tests/test_outside.py",
            "import sys\n"
            'sys.path.insert(0, "/opt/vendor")\n'
            "import vendored_thing\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertNotEqual(rc, 0, "out-of-repo sys.path target must not bless imports")
        self.assertIn("vendored_thing", stderr)

    def test_rebound_syspath_variable_still_flagged(self):
        """FALSIFIABILITY: a name rebound to a non-literal value goes ambiguous."""
        self._make_dir_module("tools", "rebound_mod")
        self._stage_file(
            "tests/test_rebound.py",
            "import os\n"
            "import sys\n"
            "from pathlib import Path\n"
            'TARGET = Path(__file__).resolve().parent.parent / "tools"\n'
            'TARGET = os.environ.get("OVERRIDE", "")\n'
            "sys.path.insert(0, str(TARGET))\n"
            "import rebound_mod\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertNotEqual(rc, 0, "ambiguous rebound target must not bless imports")
        self.assertIn("rebound_mod", stderr)

    def test_original_escape_still_caught_with_syspath_present(self):
        """
        FALSIFIABILITY: the G5 origin escape (state_store.materialize) is still
        caught in a file that also uses the sanctioned idiom.
        """
        self._make_dir_module("tools", "helper_mod")
        self._stage_file(
            "tests/test_regression.py",
            "import sys\n"
            "from pathlib import Path\n"
            'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))\n'
            "import helper_mod\n"
            "from state_store.materialize import something\n",
        )

        rc, stdout, stderr = self._run_check()
        self.assertNotEqual(rc, 0, "the origin escape must still be caught")
        self.assertIn("state_store.materialize", stderr)


if __name__ == "__main__":
    unittest.main()
