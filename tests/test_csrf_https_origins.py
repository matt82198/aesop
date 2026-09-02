"""CSRF https:// origin validation test (wave-15 P1 security fix).

Contract under test:
  - validate_csrf_request() must accept both http:// and https:// origins
    for local loopback addresses (127.0.0.1, localhost, [::1])
  - Foreign origins (both http:// and https://) must still be rejected
  - All existing behavior (X-Aesop-Token requirement) must remain unchanged

Before the fix, validate_csrf_request() and _is_local_origin() only accepted
http:// scheme for local origins, causing legitimate https:// development
and reverse-proxy setups to fail CSRF checks (P1 security issue).

Run: python -m unittest tests.test_csrf_https_origins
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

# Socket budget for one request against the fixture server.
#
# This is not a latency assertion -- nothing in this file measures performance.
# It exists so a genuinely wedged server fails with a local, readable error
# instead of hanging until pytest's 120s per-test kill.
#
# It used to be 5s, which sat inside the ordinary noise band of the Windows CI
# runner. Measured in windows-shard 0 of CI job 91574576558: this file's tests
# normally complete in ~0.51s end to end, but the runner went through a latency
# excursion during which consecutive tests in one class took 4.05s, 3.03s and
# 1.52s -- all passing -- and the next one spent the full 5s blocked inside
# getresponse() and failed. The request had been written and simply was not
# answered in time. A *passing* neighbour consuming 81% of the budget means the
# budget was wrong, not the server. 30s clears the observed excursion by a wide
# margin and still fails long before the pytest timeout, so a truly hung server
# is still caught here rather than by the harness.
REQUEST_TIMEOUT_S = 30


def load_serve(fixture_root, extra_env=None):
    """Import a fresh serve module instance bound to a fixture AESOP_ROOT."""
    os.environ["AESOP_ROOT"] = str(fixture_root)
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)
    spec = importlib.util.spec_from_file_location(f"serve_csrf_https_{id(fixture_root)}", SERVE_PATH)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


class CSRFHttpsTestCase(unittest.TestCase):
    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-csrf-https-test-"))
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
        self.httpd = handler.QuietThreadingHTTPServer(("127.0.0.1", 0), self.serve.DashboardHandler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

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

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=REQUEST_TIMEOUT_S)

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
            except TimeoutError as e:
                # Deliberately not retried: the request may already have been
                # applied, and these are mutating endpoints. Re-raise with the
                # diagnosis attached so a recurrence is classifiable on sight
                # rather than surfacing as a bare "TimeoutError: timed out"
                # halfway down a socket traceback.
                raise AssertionError(
                    f"{method} {path}: the request was written to the fixture "
                    f"server on 127.0.0.1:{self.port} but no response arrived "
                    f"within {REQUEST_TIMEOUT_S}s. This is a wedged or starved "
                    f"server, not a CSRF verdict -- see REQUEST_TIMEOUT_S."
                ) from e
            except (ConnectionAbortedError, ConnectionResetError,
                    http.client.RemoteDisconnected) as e:
                last = e
                if attempt < 2:
                    # Small delay between retries to allow server to stabilize
                    time.sleep(0.1)
                continue
            finally:
                # Closing the client socket must never change the outcome. This
                # finally runs on the way out of a successful `return` too, and
                # on Windows close() can raise WSAECONNABORTED against a peer
                # that already went away -- which would discard a response we
                # had already read and escape the retry loop entirely.
                try:
                    con.close()
                except OSError:
                    pass
        raise last

    def _post(self, path, body, headers=None):
        payload = json.dumps(body).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Content-Length": str(len(payload))}
        hdrs.update(headers or {})
        return self._request("POST", path, payload, hdrs)


class TestHTTPSLoopbackOriginAccepted(CSRFHttpsTestCase):
    """HTTPS loopback origins must be accepted (the P1 fix)."""

    def test_https_127_0_0_1_is_accepted(self):
        """https://127.0.0.1:<port> with valid token must succeed."""
        status, body = self._post(
            "/api/tracker", {"title": "https 127.0.0.1 item"},
            headers={
                "X-Aesop-Token": self.token,
                "Origin": "https://127.0.0.1:8080"
            })
        self.assertEqual(status, 201, body)
        created = json.loads(body.decode("utf-8"))
        self.assertEqual(created["title"], "https 127.0.0.1 item")

    def test_https_localhost_is_accepted(self):
        """https://localhost:<port> with valid token must succeed."""
        status, body = self._post(
            "/api/tracker", {"title": "https localhost item"},
            headers={
                "X-Aesop-Token": self.token,
                "Origin": "https://localhost:8443"
            })
        self.assertEqual(status, 201, body)
        created = json.loads(body.decode("utf-8"))
        self.assertEqual(created["title"], "https localhost item")

    def test_https_ipv6_loopback_is_accepted(self):
        """https://[::1]:<port> with valid token must succeed."""
        status, body = self._post(
            "/api/tracker", {"title": "https ipv6 item"},
            headers={
                "X-Aesop-Token": self.token,
                "Origin": "https://[::1]:9000"
            })
        self.assertEqual(status, 201, body)
        created = json.loads(body.decode("utf-8"))
        self.assertEqual(created["title"], "https ipv6 item")

    def test_https_foreign_is_rejected(self):
        """https://evil.example must still be rejected even with valid token."""
        status, body = self._post(
            "/api/tracker", {"title": "https foreign item"},
            headers={
                "X-Aesop-Token": self.token,
                "Origin": "https://evil.example"
            })
        self.assertEqual(status, 403, body)
        self.assertIn(b"CSRF", body)


class TestHTTPBehaviorUnchanged(CSRFHttpsTestCase):
    """Existing http:// behavior must remain unchanged."""

    def test_http_127_0_0_1_still_accepted(self):
        """http://127.0.0.1:<port> must continue to work."""
        status, body = self._post(
            "/api/tracker", {"title": "http 127.0.0.1 item"},
            headers={
                "X-Aesop-Token": self.token,
                "Origin": "http://127.0.0.1:8080"
            })
        self.assertEqual(status, 201, body)

    def test_http_localhost_still_accepted(self):
        """http://localhost:<port> must continue to work."""
        status, body = self._post(
            "/api/tracker", {"title": "http localhost item"},
            headers={
                "X-Aesop-Token": self.token,
                "Origin": "http://localhost:3000"
            })
        self.assertEqual(status, 201, body)

    def test_http_ipv6_still_accepted(self):
        """http://[::1]:<port> must continue to work."""
        status, body = self._post(
            "/api/tracker", {"title": "http ipv6 item"},
            headers={
                "X-Aesop-Token": self.token,
                "Origin": "http://[::1]:5000"
            })
        self.assertEqual(status, 201, body)

    def test_http_foreign_still_rejected(self):
        """http://evil.example must still be rejected."""
        status, body = self._post(
            "/api/tracker", {"title": "http foreign item"},
            headers={
                "X-Aesop-Token": self.token,
                "Origin": "http://evil.example"
            })
        self.assertEqual(status, 403, body)


class TestTokenRequirementStillEnforced(CSRFHttpsTestCase):
    """X-Aesop-Token requirement must remain unchanged for https loopback."""

    def test_https_127_0_0_1_without_token_is_rejected(self):
        """https://127.0.0.1 without X-Aesop-Token must fail."""
        status, body = self._post(
            "/api/tracker", {"title": "https no token item"},
            headers={"Origin": "https://127.0.0.1:8080"})
        self.assertEqual(status, 403, body)
        self.assertIn(b"X-Aesop-Token", body)

    def test_https_localhost_with_invalid_token_is_rejected(self):
        """https://localhost with wrong token must fail."""
        status, body = self._post(
            "/api/tracker", {"title": "https bad token item"},
            headers={
                "Origin": "https://localhost:8443",
                "X-Aesop-Token": "wrong-token"
            })
        self.assertEqual(status, 403, body)


class TestAPISessionEndpointWithHTTPS(CSRFHttpsTestCase):
    """GET /api/session must also accept https:// local origins."""

    def test_https_127_0_0_1_can_fetch_session_token(self):
        """GET /api/session with https://127.0.0.1 must return token."""
        status, body = self._request(
            "GET", "/api/session",
            headers={"Origin": "https://127.0.0.1:8080"})
        self.assertEqual(status, 200, body)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["token"], self.token)

    def test_https_localhost_can_fetch_session_token(self):
        """GET /api/session with https://localhost must return token."""
        status, body = self._request(
            "GET", "/api/session",
            headers={"Origin": "https://localhost:8443"})
        self.assertEqual(status, 200, body)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["token"], self.token)

    def test_https_foreign_cannot_fetch_session_token(self):
        """GET /api/session with https://evil.example must fail."""
        status, body = self._request(
            "GET", "/api/session",
            headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403, body)


if __name__ == "__main__":
    unittest.main()
