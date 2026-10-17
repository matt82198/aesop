"""Tests for tools/inprocess_http_timeout_lint.py (escape main-full-windows-shard2-ui-acceptance-timeout).

Each test builds its own fixture repo under tempfile.mkdtemp; nothing is written
under the checkout. The reproduction fixture recreates the offending state of
tests/test_ui_acceptance_criteria.py before the fix: an in-process
QuietThreadingHTTPServer on a daemon thread with a 5 s client timeout.

v2 (guard/inprocess-http-timeout-lint-v2) closes three resolver escapes:
module-level constant timeouts, servers started in a helper module, and
non-literal timeouts; the baseline now carries a dated header that expires.
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

FIXED_TODAY = "2026-10-17"

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

# Remote literal host with no in-process server: out of scope, never flagged.
NO_SERVER_STATE = '''\
import http.client


def test_remote_status():
    conn = http.client.HTTPConnection("example.invalid", 80, timeout=5)
    conn.close()
'''

# Escape 1: the short timeout hides behind a module-level constant.
CONSTANT_STATE = ESCAPED_STATE.replace(
    "import http.client\n", "import http.client\n\nCLIENT_TIMEOUT = 5\n", 1
).replace("timeout=5)", "timeout=CLIENT_TIMEOUT)")

CONSTANT_CLEAN_STATE = CONSTANT_STATE.replace("CLIENT_TIMEOUT = 5", "CLIENT_TIMEOUT = 30")

# Escape 2: the server is started in a helper module, so this module never names
# serve_forever. The loopback client call alone must be checked.
HELPER_SERVER_STATE = '''\
import http.client
import unittest

from helper_server import start


class HelperServerTest(unittest.TestCase):
    def setUp(self):
        self.port = start()

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
'''

# Escape 3: a non-literal timeout the lint cannot resolve is banned outright.
IMPORTED_TIMEOUT_STATE = HELPER_SERVER_STATE.replace(
    "from helper_server import start\n",
    "from helper_server import start\nfrom settings import TIMEOUT\n",
).replace("timeout=5)", "timeout=TIMEOUT)")

URLOPEN_LOOPBACK_STATE = '''\
import urllib.request


def test_status(port):
    return urllib.request.urlopen("http://127.0.0.1:%d/status" % port, timeout=5)
'''

REQUESTS_LOCALHOST_STATE = '''\
import requests


def test_status(port):
    return requests.get("http://localhost:%d/status" % port, timeout=5)
'''

URLOPEN_REMOTE_STATE = '''\
import urllib.request


def test_remote(port):
    return urllib.request.urlopen("https://example.com/", timeout=5)
'''


class InprocessHttpTimeoutLintTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="aesop-timeout-lint-"))
        (self.root / "tests").mkdir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_test(self, name, text):
        (self.root / "tests" / name).write_text(text, encoding="utf-8")

    def _write_baseline(self, payload, generated=FIXED_TODAY, name="baseline.json"):
        data = {"_generated": generated}
        data.update(payload)
        path = self.root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _run(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--root", str(self.root), "--today", FIXED_TODAY, *extra]
        with redirect_stdout(out), redirect_stderr(err):
            code = lint.main(argv)
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

    def test_remote_literal_host_without_server_is_not_flagged(self):
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

    def test_module_constant_timeout_is_resolved(self):
        self._write_test("test_constant.py", CONSTANT_STATE)
        code, out, _ = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn("tests/test_constant.py:", out)
        self.assertIn("client timeout 5 < 30", out)

    def test_module_constant_at_threshold_is_clean(self):
        self._write_test("test_constant_ok.py", CONSTANT_CLEAN_STATE)
        code, out, _ = self._run()
        self.assertEqual(code, 0, out)

    def test_server_in_helper_module_still_checks_loopback_client(self):
        self._write_test("test_helper_server.py", HELPER_SERVER_STATE)
        code, out, _ = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn("client timeout 5 < 30", out)

    def test_unresolvable_timeout_name_is_banned(self):
        self._write_test("test_imported_timeout.py", IMPORTED_TIMEOUT_STATE)
        code, out, _ = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn("non-literal client timeout", out)

    def test_urlopen_loopback_short_timeout_is_flagged(self):
        self._write_test("test_urlopen.py", URLOPEN_LOOPBACK_STATE)
        code, out, _ = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn("client timeout 5 < 30", out)

    def test_requests_localhost_short_timeout_is_flagged(self):
        self._write_test("test_requests.py", REQUESTS_LOCALHOST_STATE)
        code, out, _ = self._run()
        self.assertEqual(code, 1, out)
        self.assertIn("client timeout 5 < 30", out)

    def test_urlopen_remote_literal_url_is_not_flagged(self):
        self._write_test("test_remote_url.py", URLOPEN_REMOTE_STATE)
        code, out, _ = self._run()
        self.assertEqual(code, 0, out)

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
        baseline = self._write_baseline({"escapes": 0, "tests/test_legacy.py": 1})
        code, out, _ = self._run("--baseline", str(baseline))
        self.assertEqual(code, 0, out)

    def test_ratchet_blocks_a_new_hit_in_a_recorded_file(self):
        self._write_test("test_legacy.py", ESCAPED_STATE + '''
    def _conn_two(self):
        return http.client.HTTPConnection("127.0.0.1", self.actual_port, timeout=2)
''')
        baseline = self._write_baseline({"escapes": 0, "tests/test_legacy.py": 1})
        code, out, _ = self._run("--baseline", str(baseline))
        self.assertEqual(code, 1, out)

    def test_ratchet_blocks_escape_growth(self):
        text = ESCAPED_STATE.replace(
            "timeout=5)", "timeout=5)  # timing-ok: reason one")
        self._write_test("test_escaped.py", text)
        baseline = self._write_baseline({"escapes": 0})
        code, out, _ = self._run("--baseline", str(baseline))
        self.assertEqual(code, 1, out)
        self.assertIn("exceed baseline 0", out)

    def test_ratchet_reports_burn_down(self):
        self._write_test("test_clean.py", CLEAN_STATE)
        baseline = self._write_baseline({"escapes": 0, "tests/test_clean.py": 2})
        code, out, _ = self._run("--baseline", str(baseline))
        self.assertEqual(code, 0, out)
        self.assertIn("burned down: tests/test_clean.py (baseline 2, current 0)", out)

    def test_expired_baseline_fails_closed(self):
        self._write_test("test_clean.py", CLEAN_STATE)
        baseline = self._write_baseline({"escapes": 0}, generated="2026-08-01")
        code, out, _ = self._run("--baseline", str(baseline))
        self.assertEqual(code, 1, out)
        self.assertIn("baseline expired", out)

    def test_baseline_inside_thirty_day_window_passes(self):
        self._write_test("test_clean.py", CLEAN_STATE)
        baseline = self._write_baseline({"escapes": 0}, generated="2026-09-20")
        code, out, _ = self._run("--baseline", str(baseline))
        self.assertEqual(code, 0, out)

    def test_baseline_without_date_fails_closed(self):
        self._write_test("test_clean.py", CLEAN_STATE)
        path = self.root / "undated.json"
        path.write_text(json.dumps({"escapes": 0}), encoding="utf-8")
        code, _, err = self._run("--baseline", str(path))
        self.assertEqual(code, 2)
        self.assertIn("_generated", err)

    def test_malformed_baseline_fails_closed(self):
        self._write_test("test_clean.py", CLEAN_STATE)
        baseline = self.root / "baseline.json"
        baseline.write_text('{"_generated": "2026-10-17", "escapes": "lots"}', encoding="utf-8")
        code, _, err = self._run("--baseline", str(baseline))
        self.assertEqual(code, 2)
        self.assertIn("baseline", err)

    def test_json_reports_stale_ceiling_for_liveness_check(self):
        self._write_test("test_clean.py", CLEAN_STATE)
        baseline = self._write_baseline({"escapes": 2, "tests/test_clean.py": 1})
        code, out, _ = self._run("--baseline", str(baseline), "--json")
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["stale"], [
            "tests/test_clean.py (baseline 1, current 0)",
            "escapes (baseline 2, current 0)",
        ])

    def test_emit_baseline_round_trips_to_a_passing_ratchet(self):
        self._write_test("test_legacy.py", ESCAPED_STATE)
        code, out, _ = self._run("--emit-baseline")
        self.assertEqual(code, 0, out)
        emitted = json.loads(out)
        self.assertEqual(emitted["_generated"], FIXED_TODAY)
        self.assertEqual(emitted["tests/test_legacy.py"], 1)
        baseline = self.root / "emitted.json"
        baseline.write_text(out, encoding="utf-8")
        code, out, _ = self._run("--baseline", str(baseline))
        self.assertEqual(code, 0, out)

    def test_checkout_wiring_is_clean_after_fix(self):
        # Real tree: the repaired acceptance test must not carry a short timeout.
        hits, _escapes, _count = lint.scan_tree(REPO)
        flagged = [h for h in hits if h[0].endswith("test_ui_acceptance_criteria.py")]
        self.assertEqual(flagged, [])


if __name__ == "__main__":
    unittest.main()
