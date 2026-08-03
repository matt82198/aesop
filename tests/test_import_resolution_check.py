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
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _find_bash():
    """Locate a usable bash cross-platform.

    On Windows, plain "bash" on PATH can resolve to the WSL launcher
    (C:\\Windows\\System32\\bash.exe), which fails where no WSL distribution is
    installed. Prefer Git for Windows' bash, derived from the git location.
    (Same resolver as tests/test_encoding_cp1252.py.)
    """
    if os.name != "nt":
        return shutil.which("bash")

    git_exe = shutil.which("git")
    if git_exe:
        git_root = Path(git_exe).parent.parent
        for candidate in (git_root / "bin" / "bash.exe",
                          git_root / "usr" / "bin" / "bash.exe"):
            if candidate.exists():
                return str(candidate)

    path_bash = shutil.which("bash")
    if path_bash and "system32" not in path_bash.lower():
        return path_bash
    return None


class _ImportCheckFixture:
    """Shared temp-repo fixture for the import-resolution suites."""

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


class TestImportResolutionCheck(_ImportCheckFixture, unittest.TestCase):
    """Test suite for import resolution validator (staged/index mode)."""

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


class _PushRangeFixture(_ImportCheckFixture):
    """Fixture helpers that COMMIT (leaving the index empty, as at push time)."""

    def _run_check_args(self, *args):
        check_script = Path(__file__).parent.parent / "tools" / "import_resolution_check.py"
        result = subprocess.run(
            [sys.executable, str(check_script), *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.returncode, result.stdout, result.stderr

    def _commit_file(self, filepath, content, message="commit"):
        """Create, stage AND COMMIT a file -- leaving the index empty, exactly
        as it is when git invokes the pre-push hook."""
        self._stage_file(filepath, content)
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.repo_root,
            capture_output=True,
            check=True,
        )

    def _head(self, rev="HEAD"):
        return subprocess.run(
            ["git", "rev-parse", rev],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.strip()


class TestImportResolutionPushRange(_PushRangeFixture, unittest.TestCase):
    """The vacuity class: at PUSH time the index is empty, so an index-only
    gate never evaluates anything.

    A pre-push hook runs AFTER the commit. `git diff --cached` is empty at that
    point, so the G5 gate printed "No staged Python files found" and exited 0 on
    every normal push -- it had never actually run. These tests pin the range
    mode (the files ACTUALLY being pushed) that closes that hole.
    """

    def test_staged_mode_is_vacuous_after_commit(self):
        """RED-FIRST EVIDENCE: the staged/index mode sees NOTHING at push time.

        This is the finding, not a bug being introduced: with a deliberately
        unresolvable import already COMMITTED (index empty, as at push time),
        --staged reports no files and exits 0.
        """
        base = self._head()
        self._commit_file(
            "pushed_broken.py",
            "from state_store.materialize import something\n",
            "add broken import",
        )

        rc, _, stderr = self._run_check_args("--staged")
        self.assertEqual(
            rc, 0,
            "Documented vacuity: --staged is blind to committed-but-unpushed content",
        )
        self.assertIn("No staged Python files found", stderr)

        # ...and the SAME content is caught once the gate looks at the range
        # actually being pushed.
        rc2, _, stderr2 = self._run_check_args("--range", "%s..HEAD" % base)
        self.assertNotEqual(
            rc2, 0,
            "range mode MUST catch the unresolvable import in the pushed commits",
        )
        self.assertIn("materialize", stderr2)

    def test_repo_owned_package_missing_submodule_is_caught(self):
        """The environment must never bless a missing submodule of a package the
        repo itself owns -- find_spec('state_store') succeeding says nothing
        about state_store.materialize existing. This is THE original escape."""
        base = self._head()
        self._commit_file("owner.py", "from state_store.materialize import x\n")
        rc, _, stderr = self._run_check_args("--range", "%s..HEAD" % base)
        self.assertNotEqual(rc, 0, "repo-owned package must be authoritative")
        self.assertIn("materialize", stderr)

    def test_range_mode_clean_push_passes(self):
        base = self._head()
        self._commit_file("pushed_ok.py", "import sys\nfrom state_store import api\n")
        rc, _, stderr = self._run_check_args("--range", "%s..HEAD" % base)
        self.assertEqual(rc, 0, "clean pushed content must pass. stderr: %s" % stderr)

    def test_range_mode_reads_blob_at_tip_not_worktree(self):
        """Content must come from the pushed commit, not the dirty worktree."""
        base = self._head()
        self._commit_file("tip_content.py", "from state_store.materialize import x\n")
        # Worktree "fixes" the file but the fix is NOT committed/pushed.
        (self.repo_root / "tip_content.py").write_text("import sys\n", encoding="utf-8")

        rc, _, stderr = self._run_check_args("--range", "%s..HEAD" % base)
        self.assertNotEqual(rc, 0, "must read the pushed blob, not the worktree copy")
        self.assertIn("materialize", stderr)

    def test_range_mode_ignores_deleted_files(self):
        self._commit_file("doomed.py", "import sys\n")
        base = self._head()
        subprocess.run(["git", "rm", "-q", "doomed.py"], cwd=self.repo_root,
                       capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "delete"], cwd=self.repo_root,
                       capture_output=True, check=True)
        rc, _, stderr = self._run_check_args("--range", "%s..HEAD" % base)
        self.assertEqual(rc, 0, "deleted files must not error the gate. stderr: %s" % stderr)

    def test_range_mode_unresolvable_range_fails_closed(self):
        """An unresolvable ref must NOT be reported as 'zero files changed'."""
        rc, _, stderr = self._run_check_args("--range", "deadbeefdeadbeefdeadbeef..HEAD")
        self.assertNotEqual(rc, 0, "unresolvable range must fail closed")
        self.assertIn("FATAL", stderr.upper())

    def test_relative_import_level_is_honoured(self):
        """`from .api import X` must not be read as top-level module 'api'."""
        base = self._head()
        self._commit_file("state_store/api.py", "VALUE = 1\n")
        self._commit_file(
            "state_store/consumer.py",
            "from .api import VALUE\nfrom . import api\n",
        )
        rc, _, stderr = self._run_check_args("--range", "%s..HEAD" % base)
        self.assertEqual(rc, 0, "relative imports must resolve. stderr: %s" % stderr)

    def test_importerror_guarded_import_is_optional(self):
        """`try: import X except ImportError:` is optional by construction."""
        base = self._head()
        self._commit_file(
            "soft.py",
            "try:\n    import definitely_not_installed_pkg\n"
            "except ImportError:\n    definitely_not_installed_pkg = None\n",
        )
        rc, _, stderr = self._run_check_args("--range", "%s..HEAD" % base)
        self.assertEqual(rc, 0, "guarded optional import must pass. stderr: %s" % stderr)

    def test_unguarded_missing_third_party_is_still_reported(self):
        """The guard is a property of the source, not an allowlist: the SAME
        module imported without a handler is still a finding."""
        base = self._head()
        self._commit_file("hard.py", "import definitely_not_installed_pkg\n")
        rc, _, stderr = self._run_check_args("--range", "%s..HEAD" % base)
        self.assertNotEqual(rc, 0, "unguarded missing module must be reported")
        self.assertIn("definitely_not_installed_pkg", stderr)

    def test_sibling_import_resolves(self):
        """sys.path[0] semantics: tools/a.py can `import b` for tools/b.py."""
        base = self._head()
        self._commit_file("tools/helper_mod.py", "X = 1\n")
        self._commit_file("tools/user_mod.py", "import helper_mod\n")
        rc, _, stderr = self._run_check_args("--range", "%s..HEAD" % base)
        self.assertEqual(rc, 0, "sibling import must resolve. stderr: %s" % stderr)

    def test_conftest_syspath_root_resolves(self):
        """pytest imports conftest.py before the tests beside it."""
        base = self._head()
        self._commit_file("bench/case/repo/widget.py", "X = 1\n")
        self._commit_file(
            "bench/case/oracle/conftest.py",
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).parent.parent / \"repo\"))\n",
        )
        self._commit_file("bench/case/oracle/test_widget.py", "from widget import X\n")
        rc, _, stderr = self._run_check_args("--range", "%s..HEAD" % base)
        self.assertEqual(rc, 0, "conftest sys.path root must apply. stderr: %s" % stderr)

    def test_files_mode(self):
        """Explicit --files mode for manual invocation."""
        (self.repo_root / "manual.py").write_text(
            "from state_store.materialize import x\n", encoding="utf-8"
        )
        rc, _, stderr = self._run_check_args("--files", "manual.py")
        self.assertNotEqual(rc, 0, "explicit --files must evaluate worktree content")
        self.assertIn("materialize", stderr)

    def test_mutually_exclusive_modes_rejected(self):
        rc, _, stderr = self._run_check_args("--staged", "--range", "a..b")
        self.assertEqual(rc, 2, "conflicting modes must be a usage error")

    def test_unknown_flag_fails_closed(self):
        rc, _, _ = self._run_check_args("--not-a-flag")
        self.assertNotEqual(rc, 0, "unknown flags must not silently pass")


class TestPrePushHookWiring(_PushRangeFixture, unittest.TestCase):
    """The wiring test: the HOOK itself must evaluate the pushed range.

    Fixing the tool alone would leave the gate vacuous, because the hook called
    it with no arguments. This drives the real `check_import_resolution` bash
    function with simulated git pre-push stdin.
    """

    def _run_hook_check(self, stdin_text):
        bash = _find_bash()
        if not bash:
            self.skipTest("no usable bash interpreter found")
        hook = Path(__file__).parent.parent / "hooks" / "pre-push-policy.sh"
        script = (
            'set -uo pipefail; source "%s"; '
            'check_import_resolution <<< "$PREPUSH_STDIN"'
        ) % hook.as_posix()
        env = dict(os.environ)
        env["AESOP_ROOT"] = self.repo_root.as_posix()
        env["PREPUSH_STDIN"] = stdin_text
        result = subprocess.run(
            [bash, "-c", script],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=180,
        )
        return result.returncode, result.stdout, result.stderr

    def _install_tool(self):
        """Copy the real tool into the fixture repo so resolve_aesop_root finds it."""
        src = Path(__file__).parent.parent / "tools" / "import_resolution_check.py"
        dst = self.repo_root / "tools" / "import_resolution_check.py"
        shutil.copy2(src, dst)

    def test_hook_blocks_push_of_committed_unresolvable_import(self):
        """THE gate test: a real push tuple whose commits contain an
        unresolvable import must be blocked."""
        self._install_tool()
        base = self._head()
        self._commit_file("hook_broken.py", "from state_store.materialize import x\n")
        head = self._head()
        stdin_text = "refs/heads/feature/x %s refs/heads/feature/x %s" % (head, base)

        rc, _, stderr = self._run_hook_check(stdin_text)
        self.assertNotEqual(
            rc, 0,
            "pre-push hook MUST block a push carrying an unresolvable import "
            "(this was vacuously green before). stderr: %s" % stderr,
        )
        self.assertIn("materialize", stderr)

    def test_hook_allows_push_of_clean_committed_content(self):
        self._install_tool()
        base = self._head()
        self._commit_file("hook_ok.py", "import sys\nfrom state_store import api\n")
        head = self._head()
        stdin_text = "refs/heads/feature/x %s refs/heads/feature/x %s" % (head, base)

        rc, _, stderr = self._run_hook_check(stdin_text)
        self.assertEqual(rc, 0, "clean push must pass. stderr: %s" % stderr)

    def test_hook_scans_every_ref_tuple_not_just_the_first(self):
        """Mirrors the wave-25 secret-scan multi-ref fix: the dirty ref is
        deliberately SECOND."""
        self._install_tool()
        base = self._head()
        self._commit_file("clean_a.py", "import sys\n")
        clean_head = self._head()
        self._commit_file("dirty_b.py", "from state_store.materialize import x\n")
        dirty_head = self._head()

        stdin_text = (
            "refs/heads/a %s refs/heads/a %s\n"
            "refs/heads/b %s refs/heads/b %s"
        ) % (clean_head, base, dirty_head, clean_head)

        rc, _, stderr = self._run_hook_check(stdin_text)
        self.assertNotEqual(rc, 0, "must scan non-first ref tuples too. stderr: %s" % stderr)

    def test_hook_allows_delete_only_push(self):
        self._install_tool()
        zeros = "0" * 40
        head = self._head()
        stdin_text = "(delete) %s refs/heads/gone %s" % (zeros, head)
        rc, _, stderr = self._run_hook_check(stdin_text)
        self.assertEqual(rc, 0, "delete-only push has no content to check. stderr: %s" % stderr)

    def test_hook_fails_closed_on_malformed_stdin(self):
        self._install_tool()
        rc, _, stderr = self._run_hook_check("refs/heads/x deadbeef")
        self.assertNotEqual(rc, 0, "malformed pre-push stdin must fail closed")


if __name__ == "__main__":
    unittest.main()
