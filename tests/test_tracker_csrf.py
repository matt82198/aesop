"""CSRF-gate regression test for POST /api/tracker (wave-10 P0 security fix).

Contract under test:
  - POST /api/tracker (create) must be CSRF-gated exactly like the sibling
    mutating endpoint POST /api/tracker/<id> (update/delete): missing or
    invalid X-Aesop-Token -> 403, item NOT created; valid token -> 201,
    item created. A foreign Origin/Referer is rejected even with no token.

Before the fix, handle_tracker_create() skipped validate_csrf_request()
entirely, so any third-party site the user had open in another tab could
POST /api/tracker to silently create dashboard items (a CSRF hole that,
chained with the pr_link stored-XSS fixed separately, becomes drive-by
stored XSS).

Run: python -m unittest tests.test_tracker_csrf
"""
import http.client
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SERVE_PATH = Path(__file__).parent.parent / "ui" / "serve.py"
UI_PATH = Path(__file__).parent.parent / "ui"

ENV_KEYS = ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_STATE_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


def load_serve(fixture_root, extra_env=None):
    """Import a fresh serve module instance bound to a fixture AESOP_ROOT."""
    os.environ["AESOP_ROOT"] = str(fixture_root)
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)
    spec = importlib.util.spec_from_file_location(f"serve_tracker_csrf_{id(fixture_root)}", SERVE_PATH)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


class TrackerCSRFTestCase(unittest.TestCase):
    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-tracker-csrf-test-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

        self.serve = load_serve(self.fixture_root)
        self.token = self.serve.SESSION_TOKEN
        self.assertTrue(self.token, "fixture must produce a session token")

        # Load handler module to get QuietThreadingHTTPServer
        if str(UI_PATH) not in sys.path:
            sys.path.insert(0, str(UI_PATH))
        import handler

        # Use QuietThreadingHTTPServer to suppress socket disconnect exceptions
        # Bind to port 0 (ephemeral) to avoid collisions under CI load
        self.httpd = handler.QuietThreadingHTTPServer(("127.0.0.1", 0), self.serve.DashboardHandler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

        # TDD fix: Wait deterministically for server to be ready before tests run.
        # Root cause: Under CI load on Windows, the server thread may not have
        # completed binding and calling accept() when tests attempt to connect,
        # causing TimeoutError. Poll the server endpoint with a bounded retry
        # loop until it responds (~10s timeout).
        self._wait_for_server_ready(timeout=10)

    def tearDown(self):
        try:
            self.serve._collector_stop_event.set()
            self.httpd.shutdown()
            self.httpd.server_close()
            self.server_thread.join(timeout=3)
        finally:
            for k, v in self._saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _wait_for_server_ready(self, timeout=10):
        """Poll server endpoint until it accepts connections.

        Raises TimeoutError if server does not respond within timeout.
        This deterministic readiness check prevents TimeoutError flakes
        where the server thread hasn't yet called accept() when tests try
        to connect.
        """
        start = time.time()
        while time.time() - start < timeout:
            try:
                con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
                con.request("GET", "/api/tracker")
                resp = con.getresponse()
                resp.read()  # Ensure response is fully consumed
                con.close()
                return  # Server is ready
            except (OSError, http.client.HTTPException, TimeoutError):
                time.sleep(0.05)  # Short backoff before next attempt
        raise TimeoutError(f"Server did not become ready within {timeout}s")

    def _conn(self):
        # TDD fix: Use generous socket timeout (>=15s) to handle
        # Windows CI slowness and network delays during handshake
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)

    def _request(self, method, path, body=None, headers=None):
        # Retry transient Windows socket aborts (WSAECONNABORTED / reset) that can
        # surface when many ThreadingHTTPServer instances churn in one test process.
        last = None
        for attempt in range(3):
            con = self._conn()
            try:
                con.request(method, path, body=body, headers=headers or {})
                resp = con.getresponse()
                return resp.status, resp.read()
            except (ConnectionAbortedError, ConnectionResetError,
                    http.client.RemoteDisconnected) as e:
                last = e
                if attempt < 2:
                    # Small delay between retries to allow server to stabilize
                    time.sleep(0.1)
                continue
            finally:
                con.close()
        raise last

    def _post(self, path, body, headers=None):
        payload = json.dumps(body).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Content-Length": str(len(payload))}
        hdrs.update(headers or {})
        return self._request("POST", path, payload, hdrs)

    def _get_items(self):
        status, data = self._request("GET", "/api/tracker")
        self.assertEqual(status, 200)
        return json.loads(data.decode("utf-8"))


class TestTrackerCreateCSRF(TrackerCSRFTestCase):
    def test_create_without_token_is_rejected_and_not_created(self):
        status, body = self._post("/api/tracker", {"title": "CSRF drive-by item"})
        self.assertEqual(status, 403, body)
        self.assertIn(b"CSRF", body)

        items = self._get_items()
        titles = [i.get("title") for i in items]
        self.assertNotIn("CSRF drive-by item", titles,
                          "item must NOT be created when CSRF token is missing")

    def test_create_with_invalid_token_is_rejected_and_not_created(self):
        status, body = self._post(
            "/api/tracker", {"title": "bad token item"},
            headers={"X-Aesop-Token": "not-the-real-token"})
        self.assertEqual(status, 403, body)

        items = self._get_items()
        titles = [i.get("title") for i in items]
        self.assertNotIn("bad token item", titles)

    def test_create_with_valid_token_succeeds(self):
        status, body = self._post(
            "/api/tracker", {"title": "legit item", "priority": "P1"},
            headers={"X-Aesop-Token": self.token})
        self.assertEqual(status, 201, body)
        created = json.loads(body.decode("utf-8"))
        self.assertEqual(created["title"], "legit item")

        items = self._get_items()
        titles = [i.get("title") for i in items]
        self.assertIn("legit item", titles)

    def test_create_with_foreign_origin_is_rejected_even_with_valid_token(self):
        status, body = self._post(
            "/api/tracker", {"title": "evil.example item"},
            headers={"X-Aesop-Token": self.token, "Origin": "http://evil.example"})
        self.assertEqual(status, 403, body)

        items = self._get_items()
        titles = [i.get("title") for i in items]
        self.assertNotIn("evil.example item", titles)


class TestTrackerMutateStillGated(TrackerCSRFTestCase):
    """Regression guard: the sibling update/delete path must remain gated."""

    def _create_item(self):
        status, body = self._post(
            "/api/tracker", {"title": "seed item"},
            headers={"X-Aesop-Token": self.token})
        self.assertEqual(status, 201, body)
        return json.loads(body.decode("utf-8"))["id"]

    def test_update_without_token_is_rejected(self):
        item_id = self._create_item()
        status, body = self._post(f"/api/tracker/{item_id}", {"status": "done"})
        self.assertEqual(status, 403, body)

    def test_delete_without_token_is_rejected(self):
        item_id = self._create_item()
        con = self._conn()
        try:
            con.request("POST", f"/api/tracker/{item_id}?action=delete",
                         body=b"", headers={"Content-Length": "0"})
            resp = con.getresponse()
            self.assertEqual(resp.status, 403)
        finally:
            con.close()

    def test_update_with_valid_token_succeeds(self):
        item_id = self._create_item()
        status, body = self._post(
            f"/api/tracker/{item_id}", {"status": "done"},
            headers={"X-Aesop-Token": self.token})
        self.assertEqual(status, 200, body)


if __name__ == "__main__":
    unittest.main()
