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


if __name__ == '__main__':
    unittest.main()
