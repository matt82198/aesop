"""Tests for tools/inprocess_http_timeout_lint.py (escape main-full-windows-shard2-ui-acceptance-timeout).

Each test builds its own fixture repo under tempfile.mkdtemp; nothing is written
under the checkout. The reproduction fixture recreates the offending state of
tests/test_ui_acceptance_criteria.py before the fix: an in-process
QuietThreadingHTTPServer on a daemon thread with a 5 s client timeout.
"""
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LINT_PATH = REPO / "tools" / "inprocess_http_timeout_lint.py"

spec = importlib.util.spec_from_file_location("inprocess_http_timeout_lint", LINT_PATH)
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)

# Recreates tests/test_ui_acceptance_criteria.py before the fix.
ESCAPED_STATE = '''\
import http.client
import threading
import unittest


class AcceptanceCriteriaTestCase(unittest.TestCase):
    def setUp(self):
        self.httpd = handler.QuietThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.actual_port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.actual_port, timeout=5)
'''

CLEAN_STATE = ESCAPED_STATE.replace("timeout=5)", "timeout=30)")

NO_SERVER_STATE = '''\
import http.client


def test_remote_status():
    conn = http.client.HTTPConnection("example.invalid", 80, timeout=5)
    conn.close()
'''


class InprocessHttpTimeoutLintTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="aesop-timeout-lint-"))
        (self.root / "tests").mkdir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_test(self, name, text):
        (self.root / "tests" / name).write_text(text, encoding="utf-8")

    def _run(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = lint.main(["--root", str(self.root), *extra])
        return code, out.getvalue(), err.getvalue()

    def test_reproduces_original_escape(self):
        self._write_test("test_ui_acceptance_criteria.py", ESCAPED_STATE)
        code, out, _ = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn("tests/test_ui_acceptance_criteria.py:14:", out)
        self.assertIn("client timeout 5 < 30", out)

    def test_clean_state_has_no_false_positive(self):
        self._write_test("test_ui_acceptance_criteria.py", CLEAN_STATE)
        code, out, _ = self._run()
        self.assertEqual(code, 0, out)
        self.assertNotIn("client timeout", out)

    def test_short_timeout_without_in_process_server_is_not_flagged(self):
        self._write_test("test_remote_status.py", NO_SERVER_STATE)
        code, out, _ = self._run()
        self.assertEqual(code, 0, out)

    def test_positional_timeout_is_checked(self):
        text = ESCAPED_STATE.replace(
            'HTTPConnection("127.0.0.1", self.actual_port, timeout=5)',
            'HTTPConnection("127.0.0.1", self.actual_port, 2)')
        self._write_test("test_positional.py", text)
        code, out, _ = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn("tests/test_positional.py:14:", out)

    def test_escape_with_reason_is_counted_not_flagged(self):
        text = ESCAPED_STATE.replace(
            "timeout=5)",
            "timeout=5)  # timing-ok: contract test, server is single-threaded")
        self._write_test("test_escaped.py", text)
        code, out, _ = self._run()
        self.assertEqual(code, 0, out)
        self.assertIn("1 escape(s)", out)

    def test_escape_without_reason_still_fails(self):
        text = ESCAPED_STATE.replace("timeout=5)", "timeout=5)  # timing-ok:   ")
        self._write_test("test_empty_reason.py", text)
        code, out, _ = self._run()
        self.assertEqual(code, 1, out)

    def test_unparseable_module_fails_closed(self):
        self._write_test("test_broken.py", "def broken(:\n")
        code, out, _ = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn("unparseable", out)

    def test_missing_tests_dir_is_usage_error(self):
        empty = Path(tempfile.mkdtemp(prefix="aesop-timeout-empty-"))
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = lint.main(["--root", str(empty)])
            self.assertEqual(code, 2)
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    def test_ratchet_allows_recorded_hits_and_escapes(self):
        self._write_test("test_legacy.py", ESCAPED_STATE)
        baseline = self.root / "baseline.json"
        baseline.write_text(json.dumps(
            {"escapes": 0, "tests/test_legacy.py": 1}), encoding="utf-8")
        code, out, _ = self._run("--baseline", str(baseline))
        self.assertEqual(code, 0, out)

    def test_ratchet_blocks_a_new_hit_in_a_recorded_file(self):
        self._write_test("test_legacy.py", ESCAPED_STATE + '''
    def _conn_two(self):
        return http.client.HTTPConnection("127.0.0.1", self.actual_port, timeout=2)
''')
        baseline = self.root / "baseline.json"
        baseline.write_text(json.dumps(
            {"escapes": 0, "tests/test_legacy.py": 1}), encoding="utf-8")
        code, out, _ = self._run("--baseline", str(baseline))
        self.assertEqual(code, 1, out)

    def test_ratchet_blocks_escape_growth(self):
        text = ESCAPED_STATE.replace(
            "timeout=5)", "timeout=5)  # timing-ok: reason one")
        self._write_test("test_escaped.py", text)
        baseline = self.root / "baseline.json"
        baseline.write_text(json.dumps({"escapes": 0}), encoding="utf-8")
        code, out, _ = self._run("--baseline", str(baseline))
        self.assertEqual(code, 1, out)
        self.assertIn("exceed baseline 0", out)

    def test_ratchet_reports_burn_down(self):
        self._write_test("test_clean.py", CLEAN_STATE)
        baseline = self.root / "baseline.json"
        baseline.write_text(json.dumps(
            {"escapes": 0, "tests/test_clean.py": 2}), encoding="utf-8")
        code, out, _ = self._run("--baseline", str(baseline))
        self.assertEqual(code, 0, out)
        self.assertIn("burned down: tests/test_clean.py (baseline 2, current 0)", out)

    def test_malformed_baseline_fails_closed(self):
        self._write_test("test_clean.py", CLEAN_STATE)
        baseline = self.root / "baseline.json"
        baseline.write_text('{"escapes": "lots"}', encoding="utf-8")
        code, _, err = self._run("--baseline", str(baseline))
        self.assertEqual(code, 2)
        self.assertIn("baseline", err)

    def test_json_reports_stale_ceiling_for_liveness_check(self):
        self._write_test("test_clean.py", CLEAN_STATE)
        (self.root / "baseline.json").write_text(json.dumps(
            {"escapes": 2, "tests/test_clean.py": 1}), encoding="utf-8")
        code, out, _ = self._run("--baseline", str(self.root / "baseline.json"), "--json")
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["stale"], [
            "tests/test_clean.py (baseline 1, current 0)",
            "escapes (baseline 2, current 0)",
        ])

    def test_checkout_wiring_is_clean_after_fix(self):
        # Real tree: the repaired acceptance test must not carry a short timeout.
        hits, _escapes, _count = lint.scan_tree(REPO)
        flagged = [h for h in hits if h[0].endswith("test_ui_acceptance_criteria.py")]
        self.assertEqual(flagged, [])


if __name__ == "__main__":
    unittest.main()
