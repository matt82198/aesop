"""Wave-14 U2 backend tests — static dist serving + /api/state + /api/session + /api/cost.

Contract under test (plan D3, unit U2):
  - GET /api/state    — consolidated first-paint snapshot {data, backlog, agents,
                        tracker, status, cost} in one round trip (reuses the
                        collectors' latest-snapshot mechanism).
  - GET /api/session  — {token} for the Vite dev server; Origin-checked
                        FAIL-CLOSED against the same local allowlist csrf.py
                        uses (no Origin -> refuse; foreign Origin -> refuse).
  - GET /api/cost     — ui/cost.py get_cost_summary() over the fixture ledger.
  - GET /assets/*     — static files from WEB_DIST/assets with path-traversal
                        containment (resolve + is_relative_to), correct MIME,
                        immutable cache headers. Traversal attempts (../,
                        absolute, URL-encoded) must all be refused (403/404).
  - GET /             — renders dist/index.html through the CSRF sentinel
                        substitution; if the dist is missing, returns a hard
                        500 with a clear error message (wave-14 U9 cutover).
  - SSE               — "cost" is emitted as a 6th section.

Isolation: every test binds serve.py to a throwaway fixture AESOP_ROOT /
AESOP_STATE_ROOT (pattern from tests/test_tracker_isolation.py) so nothing
touches the real repo state.

Run: python -m unittest tests.test_api_state
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
from unittest import mock

SERVE_PATH = Path(__file__).parent.parent / "ui" / "serve.py"
UI_PATH = Path(__file__).parent.parent / "ui"

ENV_KEYS = ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_STATE_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")

# Vite index.html shape: the sentinel-carrying inline script survives the
# build verbatim (plan D3.4), so the backend substitutes it exactly like the
# legacy template.
DIST_INDEX_HTML = """<!doctype html>
<html>
<head><title>Aesop Dashboard</title>
<script>window.__AESOP_CSRF_TOKEN__ = __AESOP_CSRF_SENTINEL__;</script>
<link rel="stylesheet" href="/assets/index-abc123.css">
</head>
<body><div id="root">WAVE14-DIST-MARKER</div>
<script type="module" src="/assets/index-abc123.js"></script>
</body>
</html>
"""

FIXTURE_LEDGER = """| timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict |
|---|---|---|---|---|---|---|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5 | 12 | 100 | 200 | OK |
| 2026-07-11T23:00:00 | Agent | claude-haiku-4-5 | 30 | 50 | 75 | FAILED |
| 2026-07-12T01:00:00 | Agent | claude-sonnet-4-5 | 9 | 10 | 20 | OK |
"""


def load_serve(fixture_root, extra_env=None):
    """Import a fresh serve module instance bound to a fixture AESOP_ROOT."""
    os.environ["AESOP_ROOT"] = str(fixture_root)
    os.environ["AESOP_STATE_ROOT"] = str(fixture_root / "state")
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)
    spec = importlib.util.spec_from_file_location(
        f"serve_api_state_{id(fixture_root)}", SERVE_PATH)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


class ApiStateFixtureCase(unittest.TestCase):
    """Base: fixture root + live ThreadingHTTPServer bound to a fresh serve import."""

    # Subclasses toggle whether a fake built dist exists in the fixture.
    with_dist = False
    with_ledger = False

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-api-state-test-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

        if self.with_dist:
            self.dist_dir = self.fixture_root / "ui" / "web" / "dist"
            self.assets_dir = self.dist_dir / "assets"
            self.assets_dir.mkdir(parents=True)
            (self.dist_dir / "index.html").write_text(DIST_INDEX_HTML, encoding="utf-8")
            (self.assets_dir / "index-abc123.js").write_text(
                "console.log('wave14');\n", encoding="utf-8")
            (self.assets_dir / "index-abc123.css").write_text(
                "body{color:red}\n", encoding="utf-8")
            (self.assets_dir / "logo.svg").write_text(
                "<svg xmlns='http://www.w3.org/2000/svg'/>\n", encoding="utf-8")
            # Files OUTSIDE assets/ that a traversal would reach if containment fails.
            (self.dist_dir / "private.txt").write_text(
                "OUTSIDE-ASSETS-MARKER\n", encoding="utf-8")
            (self.fixture_root / "outside.txt").write_text(
                "OUTSIDE-DIST-MARKER\n", encoding="utf-8")

        if self.with_ledger:
            ledger_dir = self.fixture_root / "state" / "ledger"
            ledger_dir.mkdir(parents=True)
            (ledger_dir / "OUTCOMES-LEDGER.md").write_text(
                FIXTURE_LEDGER, encoding="utf-8")

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
            con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            try:
                con.request(method, path, body=body, headers=headers or {})
                resp = con.getresponse()
                return resp.status, dict(resp.getheaders()), resp.read()
            except (ConnectionAbortedError, ConnectionResetError,
                    http.client.RemoteDisconnected) as e:
                last = e
                continue
            finally:
                con.close()
        raise last

    def _get_json(self, path, headers=None):
        status, hdrs, body = self._request("GET", path, headers=headers)
        return status, hdrs, json.loads(body.decode("utf-8"))


# ==============================================================================
# GET /api/state
# ==============================================================================

class TestApiState(ApiStateFixtureCase):
    with_ledger = True

    def test_state_returns_all_six_sections(self):
        status, hdrs, state = self._get_json("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(
            set(state.keys()),
            {"data", "backlog", "agents", "tracker", "status", "cost"},
            "consolidated snapshot must carry exactly the 6 SSE sections",
        )

    def test_state_content_type_is_json_utf8(self):
        status, hdrs, body = self._request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIn("charset=utf-8", hdrs.get("Content-Type", ""))
        self.assertIn("application/json", hdrs.get("Content-Type", ""))

    def test_state_sections_have_expected_shapes(self):
        status, hdrs, state = self._get_json("/api/state")
        self.assertEqual(status, 200)
        # data: the SSE "data" section shape (collectors._snapshot_data —
        # agents live in their own section, not inside data)
        for key in ("watchdog", "monitor", "repos", "events",
                    "alerts", "messages"):
            self.assertIn(key, state["data"])
        self.assertIn("tiers", state["backlog"])
        self.assertIsInstance(state["agents"], list)
        self.assertIn("items", state["tracker"])
        self.assertIn("orchestrators", state["status"])
        self.assertIn("overall_scorecard", state["cost"])

    def test_state_tracker_reflects_fixture_items(self):
        item = self.serve.create_tracker_item(
            {"title": "api-state fixture item", "priority": "P1"})
        self.assertIsNotNone(item)
        status, hdrs, state = self._get_json("/api/state")
        self.assertEqual(status, 200)
        titles = [i.get("title") for i in state["tracker"]["items"]]
        self.assertIn("api-state fixture item", titles)

    def test_state_cost_reflects_fixture_ledger(self):
        status, hdrs, state = self._get_json("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["cost"]["overall_scorecard"]["total_runs"], 3)


# ==============================================================================
# GET /api/session — Origin fail-closed
# ==============================================================================

class TestApiSession(ApiStateFixtureCase):
    def test_local_origin_gets_token(self):
        status, hdrs, body = self._get_json(
            "/api/session", headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)
        self.assertEqual(body.get("token"), self.token)
        self.assertIn("charset=utf-8", hdrs.get("Content-Type", ""))

    def test_localhost_origin_gets_token(self):
        # Vite dev server (ui/web/vite.config.ts port 5173) proxies /api, so the
        # forwarded Origin is http://localhost:5173; admitted only by explicit opt-in.
        with mock.patch.dict(os.environ, {"AESOP_DASH_DEV_ORIGINS": "http://localhost:5173"}):
            status, hdrs, body = self._get_json(
                "/api/session", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(status, 200)
        self.assertEqual(body.get("token"), self.token)

    def test_missing_origin_is_refused_fail_closed(self):
        status, hdrs, body = self._request("GET", "/api/session")
        self.assertEqual(status, 403,
                         "no Origin header must be refused (FAIL-CLOSED)")
        self.assertNotIn(self.token.encode("utf-8"), body,
                         "token must never leak on a refused request")

    def test_foreign_origin_is_refused(self):
        status, hdrs, body = self._request(
            "GET", "/api/session", headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertNotIn(self.token.encode("utf-8"), body)

    def test_lookalike_origin_is_refused(self):
        # Prefix-lookalike host: starts with the local host string but is foreign.
        status, hdrs, body = self._request(
            "GET", "/api/session",
            headers={"Origin": "http://localhost.evil.example:8770"})
        self.assertEqual(status, 403)
        self.assertNotIn(self.token.encode("utf-8"), body)


# ==============================================================================
# GET /api/cost
# ==============================================================================

class TestApiCost(ApiStateFixtureCase):
    with_ledger = True

    def test_cost_aggregates_fixture_ledger(self):
        status, hdrs, cost = self._get_json("/api/cost")
        self.assertEqual(status, 200)
        self.assertIn("charset=utf-8", hdrs.get("Content-Type", ""))
        self.assertEqual(cost["overall_scorecard"]["total_runs"], 3)
        self.assertEqual(cost["overall_scorecard"]["ok_count"], 2)
        self.assertEqual(cost["overall_scorecard"]["failed_count"], 1)
        haiku = cost["models"]["claude-haiku-4-5"]
        self.assertEqual(haiku["runs"], 2)
        self.assertEqual(haiku["tokens_in"], 150)
        self.assertEqual(haiku["tokens_out"], 275)
        self.assertEqual(cost["models"]["claude-sonnet-4-5"]["runs"], 1)
        self.assertIn("2026-07-11", cost["daily_totals"])
        self.assertIn("2026-07-12", cost["daily_totals"])
        # No pricing map in the fixture config -> tokens only, no dollar figures.
        self.assertFalse(cost["has_pricing"])
        self.assertEqual(cost["estimates_by_model"], {})


class TestApiCostEmpty(ApiStateFixtureCase):
    def test_cost_without_ledger_returns_empty_summary(self):
        status, hdrs, cost = self._get_json("/api/cost")
        self.assertEqual(status, 200)
        self.assertEqual(cost["overall_scorecard"]["total_runs"], 0)
        self.assertEqual(cost["models"], {})


# ==============================================================================
# GET /assets/* — static serving + traversal containment
# ==============================================================================

class TestAssetsServing(ApiStateFixtureCase):
    with_dist = True

    def test_js_asset_served_with_mime_and_immutable_cache(self):
        status, hdrs, body = self._request("GET", "/assets/index-abc123.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", hdrs.get("Content-Type", ""))
        self.assertEqual(hdrs.get("Cache-Control"),
                         "public, max-age=31536000, immutable")
        self.assertIn(b"wave14", body)

    def test_css_asset_served_with_css_mime(self):
        status, hdrs, body = self._request("GET", "/assets/index-abc123.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", hdrs.get("Content-Type", ""))

    def test_svg_asset_served_with_svg_mime(self):
        status, hdrs, body = self._request("GET", "/assets/logo.svg")
        self.assertEqual(status, 200)
        self.assertIn("image/svg+xml", hdrs.get("Content-Type", ""))

    def test_missing_asset_is_404(self):
        status, hdrs, body = self._request("GET", "/assets/no-such-file.js")
        self.assertEqual(status, 404)

    # ---- traversal negatives: every one must be refused and must never leak
    # ---- the contents of files outside dist/assets/.

    def _assert_refused(self, path):
        status, hdrs, body = self._request("GET", path)
        self.assertIn(status, (403, 404),
                      f"{path!r} must be refused, got {status}")
        self.assertNotIn(b"OUTSIDE-ASSETS-MARKER", body)
        self.assertNotIn(b"OUTSIDE-DIST-MARKER", body)
        return status

    def test_dotdot_traversal_refused(self):
        self._assert_refused("/assets/../private.txt")

    def test_double_dotdot_traversal_refused(self):
        self._assert_refused("/assets/../../outside.txt")

    def test_encoded_dotdot_traversal_refused(self):
        self._assert_refused("/assets/%2e%2e/private.txt")
        self._assert_refused("/assets/..%2fprivate.txt")
        self._assert_refused("/assets/%2e%2e%2f%2e%2e%2foutside.txt")

    def test_backslash_traversal_refused(self):
        self._assert_refused("/assets/..%5cprivate.txt")

    def test_absolute_path_refused(self):
        self._assert_refused("/assets//etc/passwd")
        self._assert_refused("/assets/C:/Windows/win.ini")
        self._assert_refused("/assets/%2fetc%2fpasswd")


class TestAssetsWithoutDist(ApiStateFixtureCase):
    def test_assets_404_when_no_dist(self):
        status, hdrs, body = self._request("GET", "/assets/index-abc123.js")
        self.assertEqual(status, 404)


# ==============================================================================
# GET / — dist-or-fallback
# ==============================================================================

class TestRootWithDist(ApiStateFixtureCase):
    with_dist = True

    def test_root_serves_dist_index_with_token_substituted(self):
        status, hdrs, body = self._request("GET", "/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("WAVE14-DIST-MARKER", html,
                      "with a dist present, / must serve dist/index.html")
        self.assertNotIn("__AESOP_CSRF_SENTINEL__", html,
                         "the CSRF sentinel must not survive rendering")
        self.assertIn(f"window.__AESOP_CSRF_TOKEN__ = {json.dumps(self.token)};",
                      html)


class TestRootHardErrorWithoutDist(ApiStateFixtureCase):
    def test_root_returns_500_when_dist_missing(self):
        status, hdrs, body = self._request("GET", "/")
        self.assertEqual(status, 500,
                         "without a dist present, / must return 500 (wave-14 U9 cutover)")
        error_msg = body.decode("utf-8")
        self.assertIn("built dashboard missing", error_msg,
                      "error message must mention the missing dashboard")
        self.assertIn("npm run build", error_msg,
                      "error message must guide toward the fix")


# ==============================================================================
# SSE — cost as 6th section
# ==============================================================================

class TestCostSSESection(ApiStateFixtureCase):
    with_ledger = True

    def test_latest_snapshots_registry_has_cost_key(self):
        self.assertIn("cost", self.serve._latest_snapshots)

    def test_events_stream_emits_cost_section(self):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            con.request("GET", "/events")
            resp = con.getresponse()
            self.assertEqual(resp.status, 200)
            seen = set()
            cost_payload = None
            current = None
            try:
                for _ in range(400):
                    line = resp.fp.readline().decode("utf-8", errors="replace")
                    if not line:
                        break
                    if line.startswith("event: "):
                        current = line.strip().split(" ", 1)[1]
                        seen.add(current)
                    elif line.startswith("data: ") and current == "cost":
                        cost_payload = line[len("data: "):].strip()
                    if "cost" in seen and cost_payload is not None:
                        break
            except socket.timeout:
                pass
            self.assertIn("cost", seen,
                          "SSE stream must emit a 'cost' section frame")
            self.assertIsNotNone(cost_payload)
            parsed = json.loads(cost_payload)
            self.assertEqual(parsed["overall_scorecard"]["total_runs"], 3)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
