"""Wave failure drill-down backend tests — GET /api/wave/failure?pr=N.

Contract under test:
  - GET /api/wave/failure?pr=N — CI job logs and failure details for a PR
  - Response shape: {"available": bool, "error": str|null, "pr_number": int,
                     "branch": str, "latest_run": {...}|null, "jobs": [...]}
  - Shells `gh run view --json jobs` / `gh api .../logs` (short timeout, cached)
  - Degrades to {available:false, error:...} when gh missing/unauthed
  - Honors AESOP_GH_BIN override

Isolation: every test binds serve.py to a throwaway fixture AESOP_ROOT /
AESOP_STATE_ROOT so nothing touches the real repo state.

Run: python -m unittest tests.test_serve_wave_failure
"""
import http.client
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SERVE_PATH = Path(__file__).parent.parent / "ui" / "serve.py"
UI_PATH = Path(__file__).parent.parent / "ui"

ENV_KEYS = ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_STATE_ROOT",
            "AESOP_GH_BIN", "AESOP_UI_COLLECT_INTERVAL", "PORT")


def load_serve(fixture_root, extra_env=None):
    """Import a fresh serve module instance bound to a fixture AESOP_ROOT."""
    os.environ["AESOP_ROOT"] = str(fixture_root)
    os.environ["AESOP_STATE_ROOT"] = str(fixture_root / "state")
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)
    spec = importlib.util.spec_from_file_location(
        f"serve_wave_failure_{id(fixture_root)}", SERVE_PATH)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class WaveFailureFixtureCase(unittest.TestCase):
    """Base: fixture root + live ThreadingHTTPServer bound to a fresh serve import."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-wave-failure-test-"))
        (self.fixture_root / "state").mkdir(parents=True)
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

    def _request(self, method, path, body=None, headers=None):
        """One HTTP request; retries transient Windows socket aborts."""
        last = None
        for _ in range(3):
            con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
            try:
                con.request(method, path, body=body, headers=headers or {})
                resp = con.getresponse()
                return resp.status, dict(resp.getheaders()), resp.read()
            except (ConnectionAbortedError, ConnectionResetError,
                    http.client.RemoteDisconnected, TimeoutError) as e:
                last = e
                continue
            finally:
                con.close()
        raise last

    def _get_json(self, path, headers=None):
        status, hdrs, body = self._request("GET", path, headers=headers)
        return status, hdrs, json.loads(body.decode("utf-8"))


class GetWaveFailure(WaveFailureFixtureCase):
    """GET /api/wave/failure?pr=N — CI job logs and failure details."""

    def test_wave_failure_requires_pr_param(self):
        """GET /api/wave/failure without ?pr= returns 400."""
        status, hdrs, body = self._get_json("/api/wave/failure")
        self.assertEqual(status, 400)

    def test_wave_failure_requires_numeric_pr(self):
        """GET /api/wave/failure?pr=invalid returns 400."""
        status, hdrs, body = self._get_json("/api/wave/failure?pr=notanumber")
        self.assertEqual(status, 400)

    def test_wave_failure_endpoint_structure(self):
        """GET /api/wave/failure?pr=N returns 200 and required JSON fields."""
        # Note: this will attempt to call gh, which will likely fail in the test
        # environment, but that's OK — we're testing the endpoint structure
        status, hdrs, body = self._get_json("/api/wave/failure?pr=123")
        self.assertEqual(status, 200)

        self.assertIn("available", body)
        self.assertIn("error", body)
        self.assertIn("pr_number", body)
        self.assertIn("branch", body)
        self.assertIn("latest_run", body)
        self.assertIn("jobs", body)

    def test_wave_failure_pr_number_in_response(self):
        """GET /api/wave/failure?pr=N includes the PR number in response."""
        status, hdrs, body = self._get_json("/api/wave/failure?pr=456")
        self.assertEqual(status, 200)
        self.assertEqual(body["pr_number"], 456)

    def test_wave_failure_no_cache_header(self):
        """GET /api/wave/failure returns no-cache headers."""
        status, hdrs, body = self._get_json("/api/wave/failure?pr=123")
        self.assertEqual(status, 200)

        cache_control = hdrs.get("Cache-Control", "")
        self.assertIn("no-cache", cache_control)

    def test_wave_failure_content_type_json(self):
        """GET /api/wave/failure returns application/json content type."""
        status, hdrs, body = self._get_json("/api/wave/failure?pr=123")
        self.assertEqual(status, 200)

        content_type = hdrs.get("Content-Type", "")
        self.assertIn("application/json", content_type)

    def test_wave_failure_jobs_is_array(self):
        """GET /api/wave/failure?pr=N returns jobs as an array."""
        status, hdrs, body = self._get_json("/api/wave/failure?pr=123")
        self.assertEqual(status, 200)

        self.assertIsInstance(body["jobs"], list)

    def test_wave_failure_available_field_present(self):
        """GET /api/wave/failure?pr=N always includes available boolean."""
        status, hdrs, body = self._get_json("/api/wave/failure?pr=123")
        self.assertEqual(status, 200)

        self.assertIsInstance(body["available"], bool)

    def test_wave_failure_stub_gh_bin_honored(self):
        """GET /api/wave/failure honors AESOP_GH_BIN override for stubbed gh."""
        # Create a stub gh script that always returns an error
        stub_gh = self.fixture_root / "stub-gh.sh"
        stub_gh.write_text(
            "#!/bin/bash\n"
            "echo 'stubbed gh: not found' >&2\n"
            "exit 1\n",
            encoding="utf-8"
        )
        stub_gh.chmod(0o755)

        # Reload serve with the stub gh
        os.environ["AESOP_GH_BIN"] = str(stub_gh)
        self.serve = load_serve(self.fixture_root, {"AESOP_GH_BIN": str(stub_gh)})

        # Make request — gh will be stubbed to fail
        status, hdrs, body = self._get_json("/api/wave/failure?pr=123")
        self.assertEqual(status, 200)

        # Should degrade gracefully (available:false, not 500)
        self.assertFalse(body["available"])
        self.assertIsNotNone(body["error"])


class WaveFailureDegradation(WaveFailureFixtureCase):
    """Wave failure graceful degradation when gh is unavailable."""

    def test_wave_failure_missing_gh_returns_available_false(self):
        """Wave failure degrades when gh is missing, not 500."""
        # Set AESOP_GH_BIN to a nonexistent command
        os.environ["AESOP_GH_BIN"] = "/nonexistent/gh"
        self.serve = load_serve(self.fixture_root, {"AESOP_GH_BIN": "/nonexistent/gh"})

        status, hdrs, body = self._get_json("/api/wave/failure?pr=123")
        self.assertEqual(status, 200)
        self.assertFalse(body["available"])
        self.assertIsNotNone(body["error"])


if __name__ == "__main__":
    unittest.main()
