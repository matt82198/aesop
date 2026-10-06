#!/usr/bin/env python3
"""Unit tests for tools/status_bucket_lint.py -- status/conclusion fail-open linter.

Covers the detector (AST bucketing analysis), the suppression contract,
the CLI surface (text/JSON/exit codes), and a live regression scan of the
repository's own tools/ directory.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_PATH = REPO_ROOT / "tools" / "status_bucket_lint.py"


def _load_module():
    """Load status_bucket_lint.py as a module without importing tools/ as a package."""
    spec = importlib.util.spec_from_file_location("status_bucket_lint", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SourceCase(unittest.TestCase):
    """Base class providing an in-tempdir source scan helper."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def scan_source(self, source, filename="sample.py"):
        """Run the linter over a source string; return (findings, suppressions)."""
        return self.mod.lint_source(source, filename)

    def categories(self, findings):
        return sorted(f["category"] for f in findings)


class TestFailOpenDetection(_SourceCase):
    """The core fail-open class: unknown status falls through to a non-failure."""

    def test_missing_terminal_else_falls_through_to_pending(self):
        # The exact crossos_drift.job_conclusion shape.
        source = (
            'def job_conclusion(job):\n'
            '    status = job.get("status", "").upper()\n'
            '    conclusion = job.get("conclusion", "").upper()\n'
            '    if status != "COMPLETED":\n'
            '        return "PENDING"\n'
            '    if conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED"):\n'
            '        return "PASS"\n'
            '    elif conclusion in ("FAILURE", "TIMED_OUT", "CANCELLED"):\n'
            '        return "FAIL"\n'
            '    return "PENDING"\n'
        )
        findings, suppressed = self.scan_source(source)
        self.assertEqual(suppressed, 0)
        self.assertIn("no-terminal-else", self.categories(findings))
        finding = [f for f in findings if f["category"] == "no-terminal-else"][0]
        self.assertEqual(finding["file"], "sample.py")
        self.assertEqual(finding["line"], 6)  # chain root line
        self.assertIn("PENDING", finding["message"])

    def test_terminal_else_returning_green_token(self):
        source = (
            'def bucket_conclusion(conclusion):\n'
            '    if conclusion == "SUCCESS":\n'
            '        return "PASS"\n'
            '    elif conclusion == "FAILURE":\n'
            '        return "FAIL"\n'
            '    else:\n'
            '        return "PASS"\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(self.categories(findings), ["green-default"])

    def test_implicit_none_fallthrough_is_flagged(self):
        source = (
            'def classify_status(status):\n'
            '    if status == "SUCCESS":\n'
            '        return "PASS"\n'
            '    elif status == "FAILURE":\n'
            '        return "FAIL"\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(self.categories(findings), ["implicit-none-default"])

    def test_assignment_bucketing_is_flagged(self):
        source = (
            'def summarize(conclusion):\n'
            '    if conclusion == "SUCCESS":\n'
            '        bucket = "PASS"\n'
            '    elif conclusion == "CANCELLED":\n'
            '        bucket = "FAIL"\n'
            '    else:\n'
            '        bucket = "PENDING"\n'
            '    return bucket\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(self.categories(findings), ["green-default"])

    def test_arg_named_state_makes_function_a_candidate(self):
        source = (
            'def bucketize(run_state):\n'
            '    if run_state == "SUCCESS":\n'
            '        return "PASS"\n'
            '    elif run_state == "TIMED_OUT":\n'
            '        return "FAIL"\n'
            '    else:\n'
            '        return True\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(self.categories(findings), ["green-default"])

    def test_nested_function_is_scanned(self):
        source = (
            'def outer():\n'
            '    def inner_status(conclusion):\n'
            '        if conclusion == "SUCCESS":\n'
            '            return "PASS"\n'
            '        elif conclusion == "FAILURE":\n'
            '            return "FAIL"\n'
            '        return "PENDING"\n'
            '    return inner_status\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(self.categories(findings), ["no-terminal-else"])

    def test_async_function_is_scanned(self):
        source = (
            'async def fetch_status(conclusion):\n'
            '    if conclusion == "SUCCESS":\n'
            '        return "PASS"\n'
            '    elif conclusion == "FAILURE":\n'
            '        return "FAIL"\n'
            '    return "OK"\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(self.categories(findings), ["no-terminal-else"])


class TestCleanCases(_SourceCase):
    """Precision: shapes that must NOT be flagged."""

    def test_terminal_else_returning_failure_is_clean(self):
        source = (
            'def job_conclusion(job):\n'
            '    conclusion = job.get("conclusion", "").upper()\n'
            '    if conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED"):\n'
            '        return "PASS"\n'
            '    return "FAIL"\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(findings, [])

    def test_terminal_else_raising_is_clean(self):
        source = (
            'def bucket_status(status):\n'
            '    if status == "SUCCESS":\n'
            '        return "PASS"\n'
            '    elif status == "FAILURE":\n'
            '        return "FAIL"\n'
            '    raise ValueError(status)\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(findings, [])

    def test_nonzero_sys_exit_fallthrough_is_clean(self):
        source = (
            'import sys\n'
            'def gate_status(status):\n'
            '    if status == "SUCCESS":\n'
            '        return "PASS"\n'
            '    elif status == "FAILURE":\n'
            '        return "FAIL"\n'
            '    sys.exit(2)\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(findings, [])

    def test_unrelated_function_name_and_args_is_clean(self):
        source = (
            'def pick_colour(name):\n'
            '    if name == "SUCCESS":\n'
            '        return "PASS"\n'
            '    elif name == "FAILURE":\n'
            '        return "FAIL"\n'
            '    return "PENDING"\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(findings, [])

    def test_chain_without_known_status_constants_is_clean(self):
        source = (
            'def render_status(status):\n'
            '    if status == "banana":\n'
            '        return "yellow"\n'
            '    elif status == "cherry":\n'
            '        return "red"\n'
            '    return "unknown"\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(findings, [])

    def test_single_known_token_is_not_a_bucketing_chain(self):
        source = (
            'def read_status(status):\n'
            '    if status == "SUCCESS":\n'
            '        return "PASS"\n'
            '    return "PENDING"\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(findings, [])

    def test_unclassifiable_fallthrough_is_not_flagged(self):
        source = (
            'def bucket_conclusion(conclusion):\n'
            '    if conclusion == "SUCCESS":\n'
            '        return "PASS"\n'
            '    elif conclusion == "FAILURE":\n'
            '        return "FAIL"\n'
            '    return derive_from(conclusion)\n'
        )
        findings, _ = self.scan_source(source)
        self.assertEqual(findings, [])

    def test_syntax_error_source_raises(self):
        with self.assertRaises(SyntaxError):
            self.scan_source('def broken(status:\n')


class TestSuppression(_SourceCase):
    """`# bucket-lint: ok <reason>` suppresses, and is counted."""

    CHAIN_SUPPRESSED = (
        'def job_conclusion(conclusion):\n'
        '    if conclusion == "SUCCESS":  # bucket-lint: ok aggregation only\n'
        '        return "PASS"\n'
        '    elif conclusion == "FAILURE":\n'
        '        return "FAIL"\n'
        '    return "PENDING"\n'
    )

    def test_marker_on_chain_line_suppresses_and_counts(self):
        findings, suppressed = self.scan_source(self.CHAIN_SUPPRESSED)
        self.assertEqual(findings, [])
        self.assertEqual(suppressed, 1)

    def test_marker_on_def_line_suppresses(self):
        source = (
            'def job_conclusion(conclusion):  # bucket-lint: ok reviewed\n'
            '    if conclusion == "SUCCESS":\n'
            '        return "PASS"\n'
            '    elif conclusion == "FAILURE":\n'
            '        return "FAIL"\n'
            '    return "PENDING"\n'
        )
        findings, suppressed = self.scan_source(source)
        self.assertEqual(findings, [])
        self.assertEqual(suppressed, 1)

    def test_marker_on_fallthrough_line_suppresses(self):
        source = (
            'def job_conclusion(conclusion):\n'
            '    if conclusion == "SUCCESS":\n'
            '        return "PASS"\n'
            '    elif conclusion == "FAILURE":\n'
            '        return "FAIL"\n'
            '    return "PENDING"  # bucket-lint: ok caller re-checks\n'
        )
        findings, suppressed = self.scan_source(source)
        self.assertEqual(findings, [])
        self.assertEqual(suppressed, 1)

    def test_unrelated_comment_does_not_suppress(self):
        source = (
            'def job_conclusion(conclusion):\n'
            '    if conclusion == "SUCCESS":\n'
            '        return "PASS"  # ok\n'
            '    elif conclusion == "FAILURE":\n'
            '        return "FAIL"\n'
            '    return "PENDING"\n'
        )
        findings, suppressed = self.scan_source(source)
        self.assertEqual(len(findings), 1)
        self.assertEqual(suppressed, 0)


class TestCli(unittest.TestCase):
    """CLI contract: exit codes, text output, JSON shape, suppression visibility."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def run_cli(self, *args):
        cmd = [sys.executable, str(TOOL_PATH)] + list(args)
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=120
        )

    def _tempdir_with(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "pkg").mkdir()
        for name, body in files.items():
            (root / "pkg" / name).write_text(body, encoding="utf-8")
        return root

    DIRTY = (
        'def job_conclusion(conclusion):\n'
        '    if conclusion == "SUCCESS":\n'
        '        return "PASS"\n'
        '    elif conclusion == "FAILURE":\n'
        '        return "FAIL"\n'
        '    return "PENDING"\n'
    )
    CLEAN = (
        'def job_conclusion(conclusion):\n'
        '    if conclusion in ("SUCCESS", "SKIPPED"):\n'
        '        return "PASS"\n'
        '    return "FAIL"\n'
    )

    def test_help_exits_zero(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("status_bucket_lint", result.stdout)

    def test_unknown_flag_exits_two(self):
        result = self.run_cli("--not-a-flag")
        self.assertEqual(result.returncode, 2)

    def test_clean_scan_exits_zero(self):
        root = self._tempdir_with({"clean.py": self.CLEAN})
        result = self.run_cli("--root", str(root), "--paths", "pkg")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_findings_exit_one_and_report_file_line(self):
        root = self._tempdir_with({"dirty.py": self.DIRTY})
        result = self.run_cli("--root", str(root), "--paths", "pkg")
        self.assertEqual(result.returncode, 1)
        self.assertIn("pkg/dirty.py:2", result.stdout)
        self.assertIn("no-terminal-else", result.stdout)

    def test_missing_path_exits_two(self):
        root = self._tempdir_with({})
        result = self.run_cli("--root", str(root), "--paths", "does-not-exist")
        self.assertEqual(result.returncode, 2)

    def test_unparseable_file_exits_two(self):
        root = self._tempdir_with({"broken.py": "def f(status:\n"})
        result = self.run_cli("--root", str(root), "--paths", "pkg")
        self.assertEqual(result.returncode, 2)

    def test_json_output_shape(self):
        root = self._tempdir_with({"dirty.py": self.DIRTY})
        result = self.run_cli("--root", str(root), "--paths", "pkg", "--json")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["scanned_files"], 1)
        self.assertEqual(payload["suppressed"], 0)
        self.assertEqual(len(payload["findings"]), 1)
        finding = payload["findings"][0]
        for key in ("file", "line", "category", "message"):
            self.assertIn(key, finding)
        self.assertEqual(finding["file"], "pkg/dirty.py")

    def test_suppressions_are_counted_and_printed(self):
        suppressed_src = self.DIRTY.replace(
            'if conclusion == "SUCCESS":',
            'if conclusion == "SUCCESS":  # bucket-lint: ok reviewed 2026-08-02',
        )
        root = self._tempdir_with({"sup.py": suppressed_src})
        text = self.run_cli("--root", str(root), "--paths", "pkg")
        self.assertEqual(text.returncode, 0)
        self.assertIn("1 suppression", text.stdout)
        js = self.run_cli("--root", str(root), "--paths", "pkg", "--json")
        self.assertEqual(json.loads(js.stdout)["suppressed"], 1)

    def test_green_tokens_are_configurable(self):
        source = (
            'def job_conclusion(conclusion):\n'
            '    if conclusion == "SUCCESS":\n'
            '        return "PASS"\n'
            '    elif conclusion == "FAILURE":\n'
            '        return "FAIL"\n'
            '    return "MAYBE"\n'
        )
        root = self._tempdir_with({"custom.py": source})
        default = self.run_cli("--root", str(root), "--paths", "pkg")
        self.assertEqual(default.returncode, 0)
        widened = self.run_cli(
            "--root", str(root), "--paths", "pkg", "--green-tokens", "MAYBE"
        )
        self.assertEqual(widened.returncode, 1)


class TestRepositoryIsClean(unittest.TestCase):
    """Gate: the repository's own tools/ tree must have no status fail-opens."""

    def test_tools_directory_scan_is_clean(self):
        result = subprocess.run(
            [sys.executable, str(TOOL_PATH), "--root", str(REPO_ROOT),
             "--paths", "tools", "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=300,
        )
        self.assertNotEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(
            payload["ok"],
            "status-bucket fail-open(s) in tools/: "
            + json.dumps(payload["findings"], indent=2),
        )
        self.assertGreater(payload["scanned_files"], 50)


if __name__ == "__main__":
    unittest.main()
