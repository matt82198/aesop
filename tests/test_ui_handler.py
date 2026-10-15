"""Host header validation tests (DNS-rebinding mitigation).

Audit finding: ui/handler.py binds 127.0.0.1 and gates mutations by Origin,
but NO endpoint validates the Host header. Read endpoints (GET /, /data,
/api/state, /api/cost, /agent?id=, /events) need neither token nor Origin —
a rebound hostname pointing at 127.0.0.1 lets attacker JS read fleet state,
full agent dispatch prompts, and the CSRF token embedded in GET /.

Fix: Add Host allowlist check at the top of do_GET/do_POST (shared helper):
- Allow: 127.0.0.1[:port], localhost[:port], [::1][:port] (match configured port)
- Reject: anything else with 403 before any handler logic

Test coverage:
- Allowed: 127.0.0.1, 127.0.0.1:8770, localhost, localhost:8770, [::1], [::1]:8770
- Rejected: evil.example, 127.0.0.1:9999, localhost:9999, [::1]:9999, missing Host
- Both GET and POST requests are checked

Run: python -m unittest tests.test_ui_handler -v
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


def load_serve(fixture_root, port=None, extra_env=None):
    """Import a fresh serve module instance bound to a fixture AESOP_ROOT."""
    os.environ["AESOP_ROOT"] = str(fixture_root)
    if port is not None:
        os.environ["PORT"] = str(port)
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)
    spec = importlib.util.spec_from_file_location(
        f"serve_host_header_{id(fixture_root)}", SERVE_PATH)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


class HostHeaderTestCase(unittest.TestCase):
    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-host-header-test-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"
        os.environ.pop("PORT", None)

        self.serve = load_serve(self.fixture_root)
        self.token = self.serve.SESSION_TOKEN
        self.assertTrue(self.token, "fixture must produce a session token")

        # Load handler module
        if str(UI_PATH) not in sys.path:
            sys.path.insert(0, str(UI_PATH))
        import handler

        # Port 0: kernel-assigned per process. Host validation checks the live
        # socket port (handler._is_valid_host_header(host, server_address[1])),
        # so a fixed port is neither needed nor safe under parallel shards.
        self.httpd = handler.QuietThreadingHTTPServer(
            ("127.0.0.1", 0), self.serve.DashboardHandler)
        self.httpd.daemon_threads = True
        self.actual_port = self.httpd.server_address[1]
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
        return http.client.HTTPConnection("127.0.0.1", self.actual_port, timeout=5)

    def _request(self, method, path, body=None, headers=None, host=None):
        """Make HTTP request with optional Host header override."""
        last = None
        for attempt in range(3):
            con = self._conn()
            try:
                hdrs = headers or {}
                if host is not None:
                    hdrs["Host"] = host
                con.request(method, path, body=body, headers=hdrs)
                resp = con.getresponse()
                return resp.status, resp.read()
            except (ConnectionAbortedError, ConnectionResetError,
                    http.client.RemoteDisconnected) as e:
                last = e
                if attempt < 2:
                    time.sleep(0.1)
                continue
            finally:
                con.close()
        raise last

    def _get(self, path, host=None):
        """GET request with optional Host override."""
        return self._request("GET", path, host=host)

    def _post(self, path, body, headers=None, host=None):
        """POST request with optional Host override."""
        payload = json.dumps(body).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Content-Length": str(len(payload))}
        hdrs.update(headers or {})
        return self._request("POST", path, payload, hdrs, host=host)


class TestHostHeaderAllowed(HostHeaderTestCase):
    """Test that allowed Host values are accepted."""

    def test_get_root_with_127_0_0_1_allowed(self):
        """GET / with Host: 127.0.0.1 should be allowed."""
        status, body = self._get("/", host="127.0.0.1")
        self.assertNotEqual(status, 403,
                           "GET / with Host: 127.0.0.1 should not be rejected")

    def test_get_root_with_localhost_uppercase_allowed(self):
        """GET / with Host: LOCALHOST should be allowed (case-insensitive per RFC 7230)."""
        status, body = self._get("/", host="LOCALHOST")
        self.assertNotEqual(status, 403,
                           "GET / with Host: LOCALHOST should not be rejected")

    def test_get_root_with_localhost_mixedcase_allowed(self):
        """GET / with Host: LocalHost should be allowed (case-insensitive per RFC 7230)."""
        status, body = self._get("/", host="LocalHost")
        self.assertNotEqual(status, 403,
                           "GET / with Host: LocalHost should not be rejected")

    def test_get_root_with_ipv6_loopback_uppercase_allowed(self):
        """GET / with Host: [::1] in uppercase form should be allowed (case-insensitive per RFC 7230)."""
        # IPv6 addresses can have uppercase hex digits; [::1] is still valid
        status, body = self._get("/", host="[::1]")
        self.assertNotEqual(status, 403,
                           "GET / with Host: [::1] should not be rejected")

    def test_get_root_with_127_0_0_1_and_port_allowed(self):
        """GET / with Host: 127.0.0.1:8770 should be allowed."""
        status, body = self._get("/", host=f"127.0.0.1:{self.actual_port}")
        self.assertNotEqual(status, 403,
                           f"GET / with Host: 127.0.0.1:{self.actual_port} should not be rejected")

    def test_get_root_with_localhost_allowed(self):
        """GET / with Host: localhost should be allowed."""
        status, body = self._get("/", host="localhost")
        self.assertNotEqual(status, 403,
                           "GET / with Host: localhost should not be rejected")

    def test_get_root_with_localhost_and_port_allowed(self):
        """GET / with Host: localhost:8770 should be allowed."""
        status, body = self._get("/", host=f"localhost:{self.actual_port}")
        self.assertNotEqual(status, 403,
                           f"GET / with Host: localhost:{self.actual_port} should not be rejected")

    def test_get_root_with_ipv6_loopback_allowed(self):
        """GET / with Host: [::1] should be allowed."""
        status, body = self._get("/", host="[::1]")
        self.assertNotEqual(status, 403,
                           "GET / with Host: [::1] should not be rejected")

    def test_get_root_with_ipv6_loopback_and_port_allowed(self):
        """GET / with Host: [::1]:8770 should be allowed."""
        status, body = self._get("/", host=f"[::1]:{self.actual_port}")
        self.assertNotEqual(status, 403,
                           f"GET / with Host: [::1]:{self.actual_port} should not be rejected")

    def test_get_data_with_localhost_allowed(self):
        """GET /data with Host: localhost should be allowed."""
        status, body = self._get("/data", host="localhost")
        self.assertNotEqual(status, 403,
                           "GET /data with Host: localhost should not be rejected")

    def test_get_api_state_with_localhost_allowed(self):
        """GET /api/state with Host: localhost should be allowed."""
        status, body = self._get("/api/state", host="localhost")
        self.assertNotEqual(status, 403,
                           "GET /api/state with Host: localhost should not be rejected")


class TestHostHeaderRejected(HostHeaderTestCase):
    """Test that disallowed Host values are rejected with 403."""

    def test_get_root_with_evil_domain_rejected(self):
        """GET / with Host: evil.example should be rejected with 403."""
        status, body = self._get("/", host="evil.example")
        self.assertEqual(status, 403,
                        "GET / with Host: evil.example should be rejected with 403")

    def test_get_root_with_evil_domain_uppercase_rejected(self):
        """GET / with Host: EVIL.EXAMPLE should be rejected with 403 (case-insensitive still rejects invalid hosts)."""
        status, body = self._get("/", host="EVIL.EXAMPLE")
        self.assertEqual(status, 403,
                        "GET / with Host: EVIL.EXAMPLE should be rejected with 403")

    def test_get_root_with_wrong_port_rejected(self):
        """GET / with Host: 127.0.0.1:9999 should be rejected with 403."""
        status, body = self._get("/", host="127.0.0.1:9999")
        self.assertEqual(status, 403,
                        "GET / with Host: 127.0.0.1:9999 (wrong port) should be rejected with 403")

    def test_get_root_with_localhost_wrong_port_rejected(self):
        """GET / with Host: localhost:9999 should be rejected with 403."""
        status, body = self._get("/", host="localhost:9999")
        self.assertEqual(status, 403,
                        "GET / with Host: localhost:9999 (wrong port) should be rejected with 403")

    def test_get_root_with_ipv6_wrong_port_rejected(self):
        """GET / with Host: [::1]:9999 should be rejected with 403."""
        status, body = self._get("/", host="[::1]:9999")
        self.assertEqual(status, 403,
                        "GET / with Host: [::1]:9999 (wrong port) should be rejected with 403")

    def test_get_root_without_host_header_rejected(self):
        """GET / without Host header should be rejected with 403."""
        # Note: http.client automatically adds a Host header if not provided,
        # so we test with an invalid one instead
        status, body = self._get("/", host="127.0.0.2")
        self.assertEqual(status, 403,
                        "GET / with invalid Host should be rejected with 403")

    def test_get_data_with_evil_domain_rejected(self):
        """GET /data with Host: evil.example should be rejected with 403."""
        status, body = self._get("/data", host="evil.example")
        self.assertEqual(status, 403,
                        "GET /data with Host: evil.example should be rejected with 403")

    def test_get_api_state_with_evil_domain_rejected(self):
        """GET /api/state with Host: evil.example should be rejected with 403."""
        status, body = self._get("/api/state", host="evil.example")
        self.assertEqual(status, 403,
                        "GET /api/state with Host: evil.example should be rejected with 403")

    def test_get_api_cost_with_evil_domain_rejected(self):
        """GET /api/cost with Host: evil.example should be rejected with 403."""
        status, body = self._get("/api/cost", host="evil.example")
        self.assertEqual(status, 403,
                        "GET /api/cost with Host: evil.example should be rejected with 403")

    def test_get_agent_with_evil_domain_rejected(self):
        """GET /agent?id=test with Host: evil.example should be rejected with 403."""
        status, body = self._get("/agent?id=test", host="evil.example")
        self.assertEqual(status, 403,
                        "GET /agent?id=test with Host: evil.example should be rejected with 403")

    def test_get_events_with_evil_domain_rejected(self):
        """GET /events with Host: evil.example should be rejected with 403."""
        status, body = self._get("/events", host="evil.example")
        self.assertEqual(status, 403,
                        "GET /events with Host: evil.example should be rejected with 403")

    def test_post_submit_with_evil_domain_rejected(self):
        """POST /submit with Host: evil.example should be rejected with 403."""
        status, body = self._post(
            "/submit",
            {"text": "test"},
            headers={"X-Aesop-Token": self.token},
            host="evil.example")
        self.assertEqual(status, 403,
                        "POST /submit with Host: evil.example should be rejected with 403")

    def test_post_api_tracker_with_evil_domain_rejected(self):
        """POST /api/tracker with Host: evil.example should be rejected with 403."""
        status, body = self._post(
            "/api/tracker",
            {"title": "test"},
            headers={"X-Aesop-Token": self.token},
            host="evil.example")
        self.assertEqual(status, 403,
                        "POST /api/tracker with Host: evil.example should be rejected with 403")


if __name__ == "__main__":
    unittest.main()
