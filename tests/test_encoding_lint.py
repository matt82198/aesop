"""Tests for tools.encoding_lint — Guardrail G10 encoding validation.

Covers: missing encoding= detection, binary-mode allowance, suppression-comment
handling, exit codes, JSON output, and integration across multiple files.
Fixtures are written to tempfile.TemporaryDirectory() — no cwd or global
git-config pollution.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.encoding_lint import (  # noqa: E402
    scan_file,
    scan_directory,
    run,
    violation_key,
    load_baseline,
    save_baseline,
    check_ratchet,
)


class EncodingLintTest(unittest.TestCase):
    """Tests for the encoding lint tool."""

    def setUp(self):
        """Create a temporary directory for test fixtures."""
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        for d in ("tools", "ui", "state_store"):
            (self.repo_root / d).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up temporary directory."""
        self.tmp.cleanup()

    # -- 1. Clean file with encoding= passes --

    def test_clean_file_with_encoding_passes(self):
        """File with explicit encoding= parameter should pass."""
        test_file = self.repo_root / "tools" / "test_clean.py"
        test_file.write_text(
            'with open("config.json", "r", encoding="utf-8") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 0)

    # -- 2. Missing encoding= flagged --

    def test_missing_encoding_flagged(self):
        """File with open() missing encoding= should be flagged."""
        test_file = self.repo_root / "tools" / "test_missing.py"
        test_file.write_text(
            'with open("config.json", "r") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 1)
        self.assertIn("encoding=", findings[0]["message"])
        self.assertEqual(findings[0]["line"], 1)

    # -- 3. Binary mode not flagged --

    def test_binary_mode_not_flagged(self):
        """Binary mode opens ('rb', 'wb') should not be flagged."""
        test_file = self.repo_root / "tools" / "test_binary.py"
        test_file.write_text(
            'with open("data.bin", "rb") as f:\n'
            '    data = f.read()\n'
            'with open("output.bin", "wb") as f:\n'
            '    f.write(b"binary")\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 0)

    # -- 4. Suppression comment works --

    def test_suppression_comment_works(self):
        """The # encoding-ok comment should suppress the finding."""
        test_file = self.repo_root / "tools" / "test_suppressed.py"
        test_file.write_text(
            'with open("config.json", "r") as f:  # encoding-ok\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 0)

    # -- 5. JSON output format correct --

    def test_json_output_format(self):
        """JSON output should have correct structure."""
        test_file = self.repo_root / "tools" / "test_json.py"
        test_file.write_text(
            'with open("file.txt") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        exit_code = run(
            paths=[str(test_file)],
            root=self.repo_root,
            json_output=True,
        )

        self.assertEqual(exit_code, 1)

    # -- 6. Multiple violations in one file all reported --

    def test_multiple_violations_reported(self):
        """All violations in one file should be reported."""
        test_file = self.repo_root / "tools" / "test_multiple.py"
        test_file.write_text(
            'with open("file1.txt") as f:\n'
            '    data = f.read()\n'
            'with open("file2.txt", "r") as f:\n'
            '    data = f.read()\n'
            'with open("file3.txt", "w") as f:\n'
            '    f.write("hello")\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 3)

    # -- 7. Exit code reflects findings --

    def test_exit_code_clean(self):
        """Exit code should be 0 when no findings."""
        test_file = self.repo_root / "tools" / "test_clean.py"
        test_file.write_text(
            'with open("config.json", "r", encoding="utf-8") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        exit_code = run(paths=[str(test_file)], root=self.repo_root)
        self.assertEqual(exit_code, 0)

    def test_exit_code_findings(self):
        """Exit code should be 1 when findings exist."""
        test_file = self.repo_root / "tools" / "test_missing.py"
        test_file.write_text(
            'with open("config.json") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        exit_code = run(paths=[str(test_file)], root=self.repo_root)
        self.assertEqual(exit_code, 1)

    # -- 8. Mode as keyword argument --

    def test_mode_as_keyword_argument(self):
        """Mode specified as keyword argument should work."""
        test_file = self.repo_root / "tools" / "test_mode_kw.py"
        test_file.write_text(
            'with open("file.txt", mode="r") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 1)

    # -- 9. UTF-8 with BOM --

    def test_utf8_sig_encoding(self):
        """UTF-8-sig encoding should be allowed."""
        test_file = self.repo_root / "tools" / "test_sig.py"
        test_file.write_text(
            'with open("file.txt", "r", encoding="utf-8-sig") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 0)

    # -- 10. Default mode (no mode specified) --

    def test_default_mode_no_encoding(self):
        """Default mode (no explicit mode) without encoding should be flagged."""
        test_file = self.repo_root / "tools" / "test_default_mode.py"
        test_file.write_text(
            'with open("file.txt") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 1)

    # -- 11. Scan directory with multiple files --

    def test_scan_directory_multiple_files(self):
        """Scanning a directory should find all issues."""
        # Clean file
        clean_file = self.repo_root / "tools" / "clean.py"
        clean_file.write_text(
            'with open("f.txt", encoding="utf-8") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        # Missing encoding file
        missing_file = self.repo_root / "tools" / "missing.py"
        missing_file.write_text(
            'with open("f.txt") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        # Binary file (should not be flagged)
        binary_file = self.repo_root / "tools" / "binary.py"
        binary_file.write_text(
            'with open("f.bin", "rb") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        findings = scan_directory(self.repo_root / "tools", self.repo_root)
        self.assertEqual(len(findings), 1)
        self.assertIn("missing.py", findings[0]["file"])

    # -- 12. Append mode without encoding --

    def test_append_mode_without_encoding(self):
        """Append mode ('a') without encoding should be flagged."""
        test_file = self.repo_root / "tools" / "test_append.py"
        test_file.write_text(
            'with open("log.txt", "a") as f:\n'
            '    f.write("log entry\\n")\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 1)

    # -- 13. Append binary mode --

    def test_append_binary_mode(self):
        """Append binary mode ('ab') should not be flagged."""
        test_file = self.repo_root / "tools" / "test_append_binary.py"
        test_file.write_text(
            'with open("log.bin", "ab") as f:\n'
            '    f.write(b"log entry\\n")\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 0)

    # -- 14. Errors='ignore' parameter without encoding --

    def test_errors_parameter_without_encoding(self):
        """Errors parameter doesn't eliminate the need for encoding."""
        test_file = self.repo_root / "tools" / "test_errors.py"
        test_file.write_text(
            'with open("file.txt", "r", errors="ignore") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 1)

    # -- 15. Suppression comment case-insensitive --

    def test_suppression_comment_exact_match(self):
        """Suppression comment must be exactly '# encoding-ok'."""
        test_file = self.repo_root / "tools" / "test_case.py"
        test_file.write_text(
            'with open("file.txt") as f:  # ENCODING-OK\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        # Should still be flagged because comment is case-sensitive
        self.assertEqual(len(findings), 1)

    # -- 16. Plus (+) mode --

    def test_plus_mode_without_encoding(self):
        """Mode with + (r+, w+, etc.) without encoding should be flagged."""
        test_file = self.repo_root / "tools" / "test_plus.py"
        test_file.write_text(
            'with open("file.txt", "r+") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 1)

    # -- 17. Plus binary mode --

    def test_plus_binary_mode(self):
        """Binary plus mode (rb+, wb+) should not be flagged."""
        test_file = self.repo_root / "tools" / "test_plus_binary.py"
        test_file.write_text(
            'with open("file.bin", "rb+") as f:\n'
            '    data = f.read()\n',
            encoding="utf-8",
        )

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 0)

    # -- 18. Multiple files across directories --

    def test_scan_multiple_directories(self):
        """Should scan all specified directories."""
        tools_file = self.repo_root / "tools" / "tool.py"
        tools_file.write_text('with open("f.txt") as f: pass\n', encoding="utf-8")

        ui_file = self.repo_root / "ui" / "ui.py"
        ui_file.write_text('with open("f.txt") as f: pass\n', encoding="utf-8")

        exit_code = run(
            paths=[str(self.repo_root / "tools"), str(self.repo_root / "ui")],
            root=self.repo_root,
        )
        self.assertEqual(exit_code, 1)


class EncodingLintTestsScopeTest(unittest.TestCase):
    """tests/ is a scan target: subprocess.run(text=True) in a test harness that
    spawns a tool under test hits the same Windows cp1252 decode trap as
    production code (root cause: tests/test_merge_train.py spawning
    tools/merge_train.py via subprocess.run(text=True) with no encoding=)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        for d in ("tools", "tests"):
            (self.repo_root / d).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_subprocess_text_true_without_encoding_in_tests_dir_is_flagged(self):
        """The exact defect class: a test file spawns a tool via subprocess.run
        with text=True and no encoding=, and is scanned because tests/ is in
        DEFAULT_SCAN_PATHS."""
        test_file = self.repo_root / "tests" / "test_some_tool.py"
        test_file.write_text(
            "import subprocess\n"
            "def test_it():\n"
            "    result = subprocess.run(['tool'], capture_output=True, text=True, timeout=10)\n",
            encoding="utf-8",
        )

        exit_code = run(root=self.repo_root)
        self.assertEqual(exit_code, 1)

        findings = scan_file(test_file)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['kind'], 'subprocess-run-no-encoding')

    def test_default_scan_includes_tests_directory(self):
        """DEFAULT_SCAN_PATHS scans tests/ without needing --paths tests."""
        from tools.encoding_lint import DEFAULT_SCAN_PATHS
        self.assertIn('tests', DEFAULT_SCAN_PATHS)


class EncodingLintBaselineRatchetTest(unittest.TestCase):
    """Baseline ratchet mode (mirrors tools/stateapi_lint.py conventions)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        (self.repo_root / "tools").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_violation(self, name="bad.py"):
        f = self.repo_root / "tools" / name
        f.write_text('with open("x.txt") as fh:\n    pass\n', encoding="utf-8")
        return f

    def test_violation_key_is_line_number_independent(self):
        """Padding the file with blank lines above the call must not change the key."""
        f = self.repo_root / "tools" / "padded.py"
        f.write_text(
            "\n\n\n\n" + 'with open("x.txt") as fh:\n    pass\n',
            encoding="utf-8",
        )
        findings = scan_file(f)
        self.assertEqual(len(findings), 1)
        key = violation_key(findings[0], self.repo_root)
        self.assertEqual(key, "tools/padded.py@open-no-encoding")

    def test_missing_baseline_treated_as_empty_new_violation_fails(self):
        """No baseline file on disk -> every finding reports as new (fail-closed)."""
        self._write_violation()
        exit_code = run(root=self.repo_root, paths=["tools"])
        self.assertEqual(exit_code, 1)

    def test_update_baseline_then_matches_exactly(self):
        """--update-baseline captures current findings; re-running then passes."""
        self._write_violation()
        baseline_path = self.repo_root / ".encoding-baseline.json"

        update_exit = run(root=self.repo_root, paths=["tools"], update_baseline=True)
        self.assertEqual(update_exit, 0)
        self.assertTrue(baseline_path.exists())

        check_exit = run(root=self.repo_root, paths=["tools"])
        self.assertEqual(check_exit, 0)

    def test_new_violation_not_in_baseline_fails(self):
        """A NEW finding not present in an existing baseline still fails closed."""
        self._write_violation("existing.py")
        run(root=self.repo_root, paths=["tools"], update_baseline=True)

        # Now introduce a second, un-baselined violation.
        self._write_violation("new_offender.py")
        exit_code = run(root=self.repo_root, paths=["tools"])
        self.assertEqual(exit_code, 1)

    def test_stale_baseline_entry_fails_closed(self):
        """A baseline entry whose violation was fixed must fail until --update-baseline
        is re-run -- silently accepting a shrunk violation set would hide a future
        regression re-introducing it under the same key."""
        f = self._write_violation("fixed_later.py")
        run(root=self.repo_root, paths=["tools"], update_baseline=True)

        # Fix the violation.
        f.write_text('with open("x.txt", encoding="utf-8") as fh:\n    pass\n', encoding="utf-8")
        exit_code = run(root=self.repo_root, paths=["tools"])
        self.assertEqual(exit_code, 1)

    def test_corrupt_baseline_file_is_could_not_evaluate(self):
        """A malformed baseline file must fail as COULD NOT EVALUATE (exit 2 via
        main()'s exception handler), never silently treated as empty."""
        self._write_violation()
        baseline_path = self.repo_root / ".encoding-baseline.json"
        baseline_path.write_text("{not valid json", encoding="utf-8")

        with self.assertRaises(ValueError):
            run(root=self.repo_root, paths=["tools"])

    def test_check_ratchet_exact_match(self):
        ok, stale, new = check_ratchet(["a.py@open-no-encoding"], ["a.py@open-no-encoding"])
        self.assertTrue(ok)
        self.assertEqual(stale, [])
        self.assertEqual(new, [])

    def test_check_ratchet_detects_new_and_stale(self):
        ok, stale, new = check_ratchet(["old.py@open-no-encoding"], ["new.py@open-no-encoding"])
        self.assertFalse(ok)
        self.assertEqual(stale, ["old.py@open-no-encoding"])
        self.assertEqual(new, ["new.py@open-no-encoding"])

    def test_save_and_load_baseline_roundtrip(self):
        baseline_path = self.repo_root / "custom-baseline.json"
        save_baseline(baseline_path, ["b.py@open-no-encoding", "a.py@open-no-encoding"])
        loaded = load_baseline(baseline_path)
        self.assertEqual(loaded, ["a.py@open-no-encoding", "b.py@open-no-encoding"])

    def test_load_missing_baseline_returns_empty(self):
        self.assertEqual(load_baseline(self.repo_root / "nope.json"), [])


class EncodingLintScannedNothingTest(unittest.TestCase):
    """The gate must never exit 0 having scanned zero files (repo-wide contract:
    0=clean, 1=findings, 2=COULD NOT EVALUATE; a gate that passes because it
    checked nothing is the recurrent defect class this repo tracks hardest)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_requested_paths_missing_exits_2(self):
        """None of the requested scan directories exist on disk -> exit 2, not 0."""
        exit_code = run(root=self.repo_root, paths=["nonexistent_dir_a", "nonexistent_dir_b"])
        self.assertEqual(exit_code, 2)

    def test_default_paths_all_missing_exits_2(self):
        """Even with defaults (no --paths given), an empty/foreign root scans
        nothing and must fail closed, not report a false-clean 0."""
        exit_code = run(root=self.repo_root)
        self.assertEqual(exit_code, 2)

    def test_cli_zero_input_exits_2(self):
        """CLI-level proof of the same contract via the real entry point."""
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "encoding_lint.py"),
                "--root",
                str(self.repo_root),
                "--paths",
                "nonexistent_dir",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)


class EncodingLintIntegrationTest(unittest.TestCase):
    """Integration tests using subprocess CLI."""

    def setUp(self):
        """Create temporary directory for tests."""
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        for d in ("tools",):
            (self.repo_root / d).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up."""
        self.tmp.cleanup()

    def test_cli_json_output(self):
        """CLI should produce valid JSON with --json flag."""
        test_file = self.repo_root / "tools" / "test.py"
        test_file.write_text('with open("f.txt") as f: pass\n', encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "encoding_lint.py"),
                "--json",
                "--root",
                str(self.repo_root),
                "--paths",
                str(self.repo_root / "tools"),
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        output = json.loads(result.stdout)
        self.assertIn("findings", output)
        self.assertGreater(output["count"], 0)

    def test_cli_exit_code_clean(self):
        """CLI exit code should be 0 for clean files."""
        test_file = self.repo_root / "tools" / "test.py"
        test_file.write_text(
            'with open("f.txt", encoding="utf-8") as f: pass\n',
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "encoding_lint.py"),
                "--root",
                str(self.repo_root),
                "--paths",
                str(self.repo_root / "tools"),
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0)

    def test_cli_corrupt_baseline_exits_2(self):
        """CLI-level proof: a malformed baseline file surfaces as exit 2 through
        main()'s exception handler (COULD NOT EVALUATE), never silently exit 0/1."""
        test_file = self.repo_root / "tools" / "test.py"
        test_file.write_text('with open("f.txt", encoding="utf-8") as f: pass\n', encoding="utf-8")
        baseline_path = self.repo_root / "broken-baseline.json"
        baseline_path.write_text("not json at all", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "encoding_lint.py"),
                "--root",
                str(self.repo_root),
                "--paths",
                str(self.repo_root / "tools"),
                "--baseline",
                str(baseline_path),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)

    def test_cli_exit_code_findings(self):
        """CLI exit code should be 1 for findings."""
        test_file = self.repo_root / "tools" / "test.py"
        test_file.write_text('with open("f.txt") as f: pass\n', encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "encoding_lint.py"),
                "--root",
                str(self.repo_root),
                "--paths",
                str(self.repo_root / "tools"),
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)


if __name__ == '__main__':
    unittest.main()
