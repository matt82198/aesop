"""Unit tests for ui/api/ (wave-10 P0): the shared mutation helper +
ui.api.tracker + ui.api.submit, called DIRECTLY (no HTTP server).

These exercise the logic extracted out of ui/handler.py's DashboardHandler
methods: api.validate_mutation() (shared CSRF + Content-Length + JSON-parse
gate), api.tracker.{list_items,create,update,delete}, and
api.submit.append_to_inbox. HTTP-level behavior (status codes on the wire,
exact routing) is already covered by tests/test_tracker_csrf.py and
tests/test_tracker_sse.py; this file targets the pure functions those
handlers now delegate to.

Run: python -m pytest tests/test_api_tracker.py -q
     python -m unittest tests.test_api_tracker
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

SERVE_PATH = Path(__file__).parent.parent / "ui" / "serve.py"

ENV_KEYS = ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_STATE_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


def load_serve(fixture_root, extra_env=None):
    """Import a fresh serve module instance bound to a fixture AESOP_ROOT.

    Loading serve.py (a) inserts ui/ onto sys.path so `import api`,
    `import api.tracker`, `import api.submit` resolve as sibling top-level
    packages/modules, and (b) calls config.reload() + csrf.init() so the
    shared `config` and `csrf` modules point at this fixture.
    """
    os.environ["AESOP_ROOT"] = str(fixture_root)
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)
    spec = importlib.util.spec_from_file_location(f"serve_api_tracker_{id(fixture_root)}", SERVE_PATH)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


class ApiTrackerTestCase(unittest.TestCase):
    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-api-tracker-test-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

        self.serve = load_serve(self.fixture_root)
        self.token = self.serve.SESSION_TOKEN
        self.assertTrue(self.token, "fixture must produce a session token")

        # ui/ is now on sys.path (serve.py's shim) -- import the package
        # under test directly, bypassing HTTP entirely.
        import api
        import api.tracker
        import api.submit
        import config as ui_config
        self.api = api
        self.tracker_api = api.tracker
        self.submit_api = api.submit
        self.config = ui_config

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _headers(self, valid_token=True, extra=None):
        h = {}
        if valid_token:
            h["X-Aesop-Token"] = self.token
        h.update(extra or {})
        return h

    def _body(self, obj):
        return json.dumps(obj).encode("utf-8")

    def _headers_for_body(self, body_bytes, valid_token=True, extra=None):
        h = self._headers(valid_token=valid_token, extra=extra)
        h["Content-Length"] = str(len(body_bytes))
        return h


class TestValidateMutation(ApiTrackerTestCase):
    """Direct tests of the shared api.validate_mutation() gate."""

    def test_rejects_missing_csrf_token(self):
        body = self._body({"title": "x"})
        headers = {"Content-Length": str(len(body))}  # no X-Aesop-Token
        ok, result = self.api.validate_mutation(headers, body, expected_port=8770)
        self.assertFalse(ok)
        status, error = result
        self.assertEqual(status, 403)
        self.assertIn("CSRF", error["error"])

    def test_rejects_invalid_csrf_token(self):
        body = self._body({"title": "x"})
        headers = self._headers_for_body(body, valid_token=False,
                                          extra={"X-Aesop-Token": "not-the-real-token"})
        ok, result = self.api.validate_mutation(headers, body, expected_port=8770)
        self.assertFalse(ok)
        status, error = result
        self.assertEqual(status, 403)

    def test_rejects_foreign_origin_even_with_valid_token(self):
        body = self._body({"title": "x"})
        headers = self._headers_for_body(body, extra={"Origin": "http://evil.example"})
        ok, result = self.api.validate_mutation(headers, body, expected_port=8770)
        self.assertFalse(ok)
        status, error = result
        self.assertEqual(status, 403)

    def test_rejects_missing_content_length(self):
        headers = self._headers()  # no Content-Length
        ok, result = self.api.validate_mutation(headers, b'{"title":"x"}', expected_port=8770)
        self.assertFalse(ok)
        status, error = result
        self.assertEqual(status, 400)
        self.assertEqual(error["error"], "Invalid Content-Length")

    def test_rejects_oversized_content_length(self):
        headers = self._headers(extra={"Content-Length": "10001"})
        ok, result = self.api.validate_mutation(headers, b'{}', expected_port=8770)
        self.assertFalse(ok)
        status, error = result
        self.assertEqual(status, 400)

    def test_custom_content_length_error_message_preserved(self):
        headers = self._headers()  # no Content-Length
        ok, result = self.api.validate_mutation(
            headers, b'', content_length_error="Invalid Content-Length (must be 1-10000 bytes)", expected_port=8770)
        self.assertFalse(ok)
        status, error = result
        self.assertEqual(status, 400)
        self.assertEqual(error["error"], "Invalid Content-Length (must be 1-10000 bytes)")

    def test_success_parses_json_body(self):
        body = self._body({"title": "legit", "priority": "P1"})
        headers = self._headers_for_body(body)
        ok, parsed = self.api.validate_mutation(headers, body, expected_port=8770)
        self.assertTrue(ok)
        self.assertEqual(parsed, {"title": "legit", "priority": "P1"})

    def test_bad_json_raises_decode_error(self):
        body = b'{not valid json'
        headers = self._headers_for_body(body)
        with self.assertRaises(json.JSONDecodeError):
            self.api.validate_mutation(headers, body, expected_port=8770)


class TestTrackerCreate(ApiTrackerTestCase):
    def test_create_without_token_is_rejected_and_not_created(self):
        body = self._body({"title": "CSRF drive-by item"})
        headers = {"Content-Length": str(len(body))}
        status, result = self.tracker_api.create(headers, body, expected_port=8770)
        self.assertEqual(status, 403)
        self.assertIn("CSRF", result["error"])

        list_status, items = self.tracker_api.list_items()
        self.assertEqual(list_status, 200)
        self.assertNotIn("CSRF drive-by item", [i.get("title") for i in items])

    def test_create_with_bad_json_returns_400(self):
        body = b'{"title": bad}'
        headers = self._headers_for_body(body)
        status, result = self.tracker_api.create(headers, body, expected_port=8770)
        self.assertEqual(status, 400)
        self.assertEqual(result["error"], "Invalid JSON")

    def test_create_happy_path(self):
        body = self._body({"title": "legit item", "priority": "P1"})
        headers = self._headers_for_body(body)
        status, item = self.tracker_api.create(headers, body, expected_port=8770)
        self.assertEqual(status, 201)
        self.assertEqual(item["title"], "legit item")
        self.assertEqual(item["priority"], "P1")
        self.assertIn("id", item)

        list_status, items = self.tracker_api.list_items()
        self.assertIn("legit item", [i.get("title") for i in items])


class TestTrackerUpdate(ApiTrackerTestCase):
    def _create(self, title="seed item"):
        body = self._body({"title": title})
        headers = self._headers_for_body(body)
        status, item = self.tracker_api.create(headers, body, expected_port=8770)
        self.assertEqual(status, 201, item)
        return item["id"]

    def test_update_happy_path(self):
        item_id = self._create()
        body = self._body({"status": "done"})
        status, item = self.tracker_api.update(item_id, body)
        self.assertEqual(status, 200)
        self.assertEqual(item["status"], "done")
        self.assertIsNotNone(item.get("completed_at"))

    def test_update_unknown_id_returns_404(self):
        body = self._body({"status": "done"})
        status, result = self.tracker_api.update("does-not-exist", body)
        self.assertEqual(status, 404)
        self.assertIn("404", result["error"])


class TestTrackerDelete(ApiTrackerTestCase):
    def _create(self, title="seed item"):
        body = self._body({"title": title})
        headers = self._headers_for_body(body)
        status, item = self.tracker_api.create(headers, body, expected_port=8770)
        self.assertEqual(status, 201, item)
        return item["id"]

    def test_delete_happy_path(self):
        item_id = self._create()
        status, item = self.tracker_api.delete(item_id)
        self.assertEqual(status, 200)
        self.assertEqual(item["status"], "archived")

    def test_delete_unknown_id_returns_404(self):
        status, result = self.tracker_api.delete("does-not-exist")
        self.assertEqual(status, 404)
        self.assertIn("404", result["error"])

    def test_delete_is_idempotent(self):
        item_id = self._create()
        status1, item1 = self.tracker_api.delete(item_id)
        status2, item2 = self.tracker_api.delete(item_id)
        self.assertEqual(status1, 200)
        self.assertEqual(status2, 200)
        self.assertEqual(item1["status"], "archived")
        self.assertEqual(item2["status"], "archived")
        self.assertEqual(item1["id"], item2["id"])


class TestSubmitAppendToInbox(ApiTrackerTestCase):
    def test_append_creates_file_and_writes_utf8(self):
        # NOTE: only the initial header write is forced LF (newline='\n');
        # the append itself uses text-mode 'a' with no newline= override,
        # matching the pre-split handler.py exactly (this preserves an
        # existing behavior, not introduces one -- out of scope to change here).
        ok, result = self.submit_api.append_to_inbox("hello — em dash")
        self.assertTrue(ok)
        self.assertIsNone(result)

        raw = self.config.INBOX_FILE.read_bytes()
        text = raw.decode("utf-8")
        self.assertIn("hello — em dash", text)
        self.assertTrue(text.startswith("# UI Inbox"))
        # Header line (written with newline='\n') must not have been
        # translated to CRLF.
        header_line = raw.split(b"\n", 1)[0]
        self.assertNotIn(b"\r", header_line)

    def test_append_twice_appends_both_lines(self):
        self.submit_api.append_to_inbox("first line")
        self.submit_api.append_to_inbox("second line")
        text = self.config.INBOX_FILE.read_text(encoding="utf-8")
        self.assertIn("first line", text)
        self.assertIn("second line", text)

    def test_append_rejects_symlinked_inbox(self):
        target = self.fixture_root / "state" / "not-the-inbox.md"
        target.write_text("attacker-controlled content\n", encoding="utf-8")
        try:
            os.symlink(str(target), str(self.config.INBOX_FILE))
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks not supported on this system")

        ok, result = self.submit_api.append_to_inbox("should not be written")
        self.assertFalse(ok)
        status, error = result
        self.assertEqual(status, 400)
        self.assertIn("symlink", error["error"])

        # The symlink target must NOT have been written to.
        self.assertNotIn("should not be written", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
