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
)
from tools import encoding_lint as el  # noqa: E402


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


class SubprocessErrorHandlerTest(unittest.TestCase):
    """G10 second half: `encoding=` alone is not enough on a subprocess call.

    The merge queue crashed on 24+ consecutive scheduled passes while THIS
    lint reported clean, because the rule only ever required `encoding=`.
    Strict decoding of a cp1252 em-dash (0x97) killed subprocess's reader
    thread, `stdout` became None, and the caller died on `.strip()`. A rule a
    linter does not enforce is how that shipped, so the handler is now part
    of the rule.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        (self.repo_root / "tools").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _scan(self, source: str):
        test_file = self.repo_root / "tools" / "sample.py"
        test_file.write_text("import subprocess\n" + source, encoding="utf-8")
        return scan_file(test_file)

    def test_encoding_without_errors_is_flagged(self):
        """The exact shape that took the queue down must now fail the lint."""
        findings = self._scan(
            "subprocess.run(['git', 'log'], capture_output=True, "
            "text=True, encoding='utf-8', timeout=60)\n"
        )
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("errors=", findings[0]["message"])

    def test_errors_replace_passes(self):
        findings = self._scan(
            "subprocess.run(['git', 'log'], capture_output=True, text=True, "
            "encoding='utf-8', errors='replace', timeout=60)\n"
        )
        self.assertEqual(findings, [])

    def test_errors_ignore_is_rejected(self):
        """'ignore' DELETES the bad byte; a corrupted ref must stay visible."""
        findings = self._scan(
            "subprocess.run(['git', 'log'], capture_output=True, text=True, "
            "encoding='utf-8', errors='ignore')\n"
        )
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("ignore", findings[0]["message"])

    def test_errors_strict_is_rejected(self):
        """Spelling the unsafe default out loud does not make it safe."""
        findings = self._scan(
            "subprocess.run(['git', 'log'], capture_output=True, text=True, "
            "encoding='utf-8', errors='strict')\n"
        )
        self.assertEqual(len(findings), 1, findings)

    def test_lossless_handlers_pass(self):
        for handler in ("backslashreplace", "surrogateescape"):
            with self.subTest(handler=handler):
                findings = self._scan(
                    "subprocess.run(['git'], text=True, encoding='utf-8', "
                    "errors=%r)\n" % handler
                )
                self.assertEqual(findings, [])

    def test_popen_and_check_output_covered(self):
        for fn in ("Popen", "check_output"):
            with self.subTest(fn=fn):
                findings = self._scan(
                    "subprocess.%s(['git'], text=True, encoding='utf-8')\n" % fn
                )
                self.assertEqual(len(findings), 1, findings)
                self.assertIn("errors=", findings[0]["message"])

    def test_suppression_comment_still_honoured(self):
        findings = self._scan(
            "subprocess.run(['git'], text=True, encoding='utf-8')  "
            "# encoding-ok\n"
        )
        self.assertEqual(findings, [])

    def test_open_calls_are_not_subject_to_the_handler_rule(self):
        """G10's handler requirement is scoped to subprocess, not open()."""
        findings = self._scan("open('f.txt', encoding='utf-8').read()\n")
        self.assertEqual(findings, [])

    def test_repo_is_clean_under_the_extended_rule(self):
        """The whole repo must satisfy the new rule, not just the queue path.

        The pre-push hook runs this lint over the WHOLE repo and fail-closes
        on any finding, so a rule the repo does not satisfy would block every
        Python-touching push. This asserts the sweep was actually completed
        rather than deferred.
        """
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "encoding_lint.py"), "--check",
             "--root", str(ROOT)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class AsciiScopeTest(unittest.TestCase):
    """tools/*.py is ASCII-only (LANE-CONTRACT section 6), with a shrink-only ratchet.

    The baseline lists the tools/*.py files that were already non-ASCII when the
    rule landed. It may only shrink: a new non-ASCII file is a finding, and a
    baselined file that became clean is a stale-entry finding.
    """

    # Non-ASCII tools/*.py files when the ratchet was introduced. The baseline
    # may shrink freely; raising this ceiling needs a measured reason.
    BASELINE_CEILING = 102

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tools").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def _baseline(self, files):
        (self.root / "tools" / "encoding-ascii-baseline.json").write_text(
            json.dumps({"files": files}), encoding="utf-8")

    def test_em_dash_in_tools_file_is_flagged(self):
        self._write("tools/x.py", "# a — b\nprint('ok')\n")
        findings = el.ascii_findings(self.root)
        self.assertEqual(len(findings), 1, findings)
        self.assertEqual(findings[0]["line"], 1)
        self.assertIn("U+2014", findings[0]["message"])

    def test_em_dash_fails_the_cli_with_ascii_only_output(self):
        self._write("tools/x.py", "# a — b\n")
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "encoding_lint.py"),
             "--root", str(self.root), "--paths", str(self.root / "tools")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"tools[\\/]x\.py:1:")
        self.assertIn("\\u2014", result.stdout)

    def test_ascii_tools_file_passes(self):
        self._write("tools/x.py", "print('plain ascii -- ok')\n")
        self.assertEqual(el.ascii_findings(self.root), [])

    def test_bom_character_in_tools_file_is_flagged(self):
        self._write("tools/x.py", "﻿print('x')\n")
        self.assertEqual(len(el.ascii_findings(self.root)), 1)

    def test_only_tools_top_level_python_is_in_scope(self):
        self._write("ui/x.py", "# a — b\n")
        self._write("tools/sub/y.py", "# a — b\n")
        self.assertEqual(el.ascii_findings(self.root), [])

    def test_baselined_non_ascii_file_is_allowed(self):
        self._write("tools/x.py", "# a — b\n")
        self._baseline(["tools/x.py"])
        self.assertEqual(el.ascii_findings(self.root), [])

    def test_new_non_ascii_file_is_flagged_even_with_a_baseline(self):
        self._write("tools/old.py", "# a — b\n")
        self._write("tools/new.py", "# a → b\n")
        self._baseline(["tools/old.py"])
        findings = el.ascii_findings(self.root)
        self.assertEqual([f["file"].endswith("new.py") for f in findings], [True])

    def test_baselined_file_that_became_clean_is_a_stale_entry(self):
        self._write("tools/x.py", "print('clean')\n")
        self._baseline(["tools/x.py"])
        findings = el.ascii_findings(self.root)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("remove", findings[0]["message"])

    def test_baseline_entry_for_missing_file_is_a_stale_entry(self):
        self._baseline(["tools/gone.py"])
        findings = el.ascii_findings(self.root)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("remove", findings[0]["message"])

    def test_malformed_baseline_fails_closed_with_exit_2(self):
        self._write("tools/x.py", "print('clean')\n")
        (self.root / "tools" / "encoding-ascii-baseline.json").write_text(
            "{not json", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "encoding_lint.py"),
             "--root", str(self.root), "--paths", str(self.root / "tools")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def _baseline_query(self, baseline_path):
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "encoding_lint.py"),
             "--baseline", str(baseline_path), "--json", "--root", str(self.root)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )

    def test_baseline_query_reports_stale_entries_as_json(self):
        self._write("tools/x.py", "print('clean')\n")
        self._baseline(["tools/x.py", "tools/gone.py"])
        result = self._baseline_query(self.root / "tools" / "encoding-ascii-baseline.json")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["stale"], ["tools/gone.py", "tools/x.py"])

    def test_baseline_query_is_empty_when_every_entry_is_live(self):
        self._write("tools/x.py", "# a — b\n")
        self._baseline(["tools/x.py"])
        result = self._baseline_query(self.root / "tools" / "encoding-ascii-baseline.json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["stale"], [])

    def test_repo_baseline_is_fully_live(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "encoding_lint.py"),
             "--baseline", str(ROOT / "tools" / "encoding-ascii-baseline.json"), "--json",
             "--root", str(ROOT)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        self.assertEqual(json.loads(result.stdout)["stale"], [])

    def test_repo_baseline_cannot_grow(self):
        baseline = json.loads(
            (ROOT / "tools" / "encoding-ascii-baseline.json").read_text(encoding="utf-8"))
        files = baseline["files"]
        self.assertLessEqual(len(files), self.BASELINE_CEILING)
        self.assertEqual(len(files), len(set(files)))

    def test_repo_tools_are_ascii_under_the_ratchet(self):
        self.assertEqual(el.ascii_findings(ROOT), [])


if __name__ == '__main__':
    unittest.main()
