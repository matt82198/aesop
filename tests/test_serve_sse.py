"""Unit tests for ui/serve.py realtime SSE model, agent-id dedup, and prompt extraction.

Contracts under test:
  - GET /events streams an immediate full snapshot (data, backlog, agents) with no
    CSRF token, then pushes a new section event when the underlying input changes
    (change-hash gated) — no client polling.
  - get_fleet_agents() disambiguates colliding truncated agent ids (id, id-2, ...)
    so DOM row keys and click-to-expand lookups never merge two agents.
  - extract_agent_dispatch_prompt() prefix-matches truncated ids against full-id
    agent-<id>.jsonl transcripts and returns the first user message as the dispatch prompt.

Run: python -m unittest tests.test_serve_sse
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
import time
import unittest
from pathlib import Path

SERVE_PATH = Path(__file__).parent.parent / "ui" / "serve.py"
UI_PATH = Path(__file__).parent.parent / "ui"

FIXTURE_BACKLOG = """# Audit backlog — test fixture

**Status legend:** ⬜ unclaimed · 🔵 dispatched · ✅ merged · ⏸ user call

## P0 — correctness / security

- ✅ **[sec] Seed item one.** already done.
- 🔵 **[js] Seed item two.** in flight.

## Landing log
- fixture
"""

ENV_KEYS = ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_STATE_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT", "CONDUCTOR_ROOT",
            "AESOP_WATCHDOG_HEARTBEAT")

# Pinned epoch for heartbeat-age tests: the age is computed against an injected
# clock, never the process wall clock (a 3s bucket boundary crossed between two
# real-time reads made the age bucket change under parallel shard load).
FIXED_NOW = 1_800_000_000


def load_serve(fixture_root, extra_env=None):
    """Import a fresh serve module instance bound to a fixture AESOP_ROOT."""
    os.environ["AESOP_ROOT"] = str(fixture_root)
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)
    spec = importlib.util.spec_from_file_location(f"serve_sse_{id(fixture_root)}", SERVE_PATH)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


class EnvFixtureCase(unittest.TestCase):
    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-sse-test-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.fixture_root, ignore_errors=True)


class TestAgentIdDedup(EnvFixtureCase):
    """dash-extra.mjs truncates ids to 13 chars; collisions must be disambiguated."""

    def test_colliding_ids_are_suffixed(self):
        dash_dir = self.fixture_root / "dash"
        dash_dir.mkdir()
        agents = [{"id": "aaaabbbbccccd", "status": "running", "age_s": 1, "hint": "x"},
                  {"id": "aaaabbbbccccd", "status": "running", "age_s": 2, "hint": "y"},
                  {"id": "eeeeffffgggg1", "status": "done", "age_s": 3, "hint": "z"}]
        (dash_dir / "dash-extra.mjs").write_text(
            "console.log(JSON.stringify(" + json.dumps(agents) + "));", encoding="utf-8")

        serve = load_serve(self.fixture_root)
        result = serve.get_fleet_agents()
        ids = [a["id"] for a in result]
        self.assertEqual(len(ids), len(set(ids)), f"ids not unique: {ids}")
        self.assertIn("aaaabbbbccccd", ids)
        self.assertIn("aaaabbbbccccd-2", ids)
        self.assertIn("eeeeffffgggg1", ids)


class TestDispatchPromptExtraction(EnvFixtureCase):
    """Truncated dashboard ids must prefix-match full-id transcript files."""

    def test_prefix_match_and_prompt(self):
        full_id = "abc123def456fedcba9876"
        transcript = self.fixture_root / "transcripts" / f"agent-{full_id}.jsonl"
        lines = [
            json.dumps({"type": "user", "parentUuid": None,
                        "message": {"content": "FIXTURE DISPATCH PROMPT: fix the widget"}}),
            json.dumps({"type": "assistant", "model": "claude-haiku-4-5",
                        "message": {"content": "ok"}}),
        ]
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        serve = load_serve(self.fixture_root)
        data = serve.extract_agent_dispatch_prompt(full_id[:13])
        self.assertNotIn("error", data, data.get("error", ""))
        self.assertIn("FIXTURE DISPATCH PROMPT", data["dispatch_prompt"])
        self.assertEqual(data["dispatcher"], "main thread")
        self.assertEqual(data["message_count"], 2)

    def test_missing_transcript_is_graceful(self):
        serve = load_serve(self.fixture_root)
        data = serve.extract_agent_dispatch_prompt("nonexistent000")
        self.assertIn("error", data)


class SSEFrameReader:
    """Incremental parser for an SSE byte stream on an http.client response."""

    def __init__(self, resp):
        # Per-read blocking is bounded by the HTTPConnection's constructor
        # timeout (set to 1s for /events connections below).
        self.resp = resp
        self.event = None
        self.data_lines = []

    def read_frames(self, deadline_s):
        """Yield (event, data) tuples until the deadline passes."""
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            try:
                raw = self.resp.readline()
            except (socket.timeout, TimeoutError):
                continue
            except (ConnectionError, OSError):
                return
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith(":"):
                continue  # keepalive comment
            if line.startswith("event:"):
                self.event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                self.data_lines.append(line[len("data:"):].strip())
            elif line == "" and self.event is not None:
                ev, data = self.event, "\n".join(self.data_lines)
                self.event, self.data_lines = None, []
                yield ev, data


class TestSSERealtime(EnvFixtureCase):
    """The realtime contract: initial snapshot on connect, push on change, no token."""

    def setUp(self):
        super().setUp()
        # Wave-14 U9: Create minimal dist for serve_html to work
        dist_dir = self.fixture_root / "ui" / "web" / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        (dist_dir / "index.html").write_text(
            "<!DOCTYPE html><html><head>"
            "<script>window.__AESOP_CSRF_TOKEN__ = __AESOP_CSRF_SENTINEL__;</script>"
            "</head><body>test</body></html>",
            encoding="utf-8"
        )

        self.backlog_file = self.fixture_root / "AUDIT-BACKLOG.md"
        self.backlog_file.write_text(FIXTURE_BACKLOG, encoding="utf-8")
        # Hermetic heartbeat: a fixture file, never the box-global conductor3 one
        # (CONDUCTOR_ROOT would otherwise resolve the live daemon heartbeat).
        os.environ.pop("CONDUCTOR_ROOT", None)
        self.heartbeat = self.fixture_root / "state" / ".watchdog-heartbeat"
        self.heartbeat.write_text(str(FIXED_NOW - 100), encoding="utf-8")
        os.environ["AESOP_WATCHDOG_HEARTBEAT"] = str(self.heartbeat)
        self.serve = load_serve(self.fixture_root)
        self.serve.config.reload()  # re-read the env-resolved heartbeat path

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
            super().tearDown()

    def _connect_events(self):
        # Retry transient Windows socket aborts (WSAECONNABORTED / reset) on connect.
        last = None
        for _ in range(3):
            con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
            try:
                con.request("GET", "/events")  # deliberately NO token header
                resp = con.getresponse()
                return con, resp
            except (ConnectionAbortedError, ConnectionResetError,
                    http.client.RemoteDisconnected) as e:
                last = e
                con.close()
                continue
        raise last

    def test_initial_snapshot_then_live_backlog_update(self):
        con, resp = self._connect_events()
        try:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream", resp.getheader("Content-Type", ""))

            reader = SSEFrameReader(resp)
            seen = {}
            for ev, data in reader.read_frames(deadline_s=6):
                seen.setdefault(ev, data)
                if {"data", "backlog", "agents"} <= set(seen):
                    break
            self.assertLessEqual({"data", "backlog", "agents"}, set(seen),
                                 f"initial snapshot incomplete, saw: {sorted(seen)}")
            backlog = json.loads(seen["backlog"])
            titles = json.dumps(backlog)
            self.assertIn("Seed item one", titles)

            # Live update: insert an item INSIDE the P0 section (the parser stops at
            # "## Landing log", so an EOF append would be correctly ignored).
            content = self.backlog_file.read_text(encoding="utf-8")
            content = content.replace(
                "## Landing log",
                "- ⬜ **[test] LIVE-UPDATE-MARKER item.** appeared live.\n\n## Landing log")
            self.backlog_file.write_text(content, encoding="utf-8")
            got_update = False
            for ev, data in reader.read_frames(deadline_s=6):
                if ev == "backlog" and "LIVE-UPDATE-MARKER" in data:
                    got_update = True
                    break
            self.assertTrue(got_update, "backlog change was not pushed over SSE within 6s")
        finally:
            con.close()

    def test_regular_endpoints_still_work_while_sse_held(self):
        """ThreadingHTTPServer contract: a held /events connection must not block others."""
        con, resp = self._connect_events()
        try:
            con2 = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            con2.request("GET", "/api/backlog")
            r2 = con2.getresponse()
            self.assertEqual(r2.status, 200)
            body = r2.read().decode("utf-8")
            self.assertIn("Seed item one", body)
            con2.close()
        finally:
            con.close()

    def test_concurrent_connection_cap_returns_503(self):
        """Exceeding SSE_MAX_CLIENTS must return HTTP 503 Service Unavailable."""
        # Hold SSE_MAX_CLIENTS connections simultaneously
        held_connections = []
        max_clients = self.serve.SSE_MAX_CLIENTS

        try:
            # Fill up the connection pool
            for i in range(max_clients):
                con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
                con.request("GET", "/events")
                resp = con.getresponse()
                self.assertEqual(resp.status, 200, f"Client {i} should succeed")
                held_connections.append((con, resp))

            # The (max_clients + 1)-th connection should get 503
            con_extra = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
            con_extra.request("GET", "/events")
            resp_extra = con_extra.getresponse()
            self.assertEqual(resp_extra.status, 503, "Cap-exceeded client should get 503")
            con_extra.close()
        finally:
            for con, resp in held_connections:
                con.close()

    def test_sse_queue_bounded_maxsize(self):
        """Per-client SSE queue must be bounded (maxsize), not unbounded."""
        con, resp = self._connect_events()
        try:
            # Verify queue has a maxsize by checking the serve module's constant
            self.assertTrue(hasattr(self.serve, 'SSE_QUEUE_MAXSIZE'),
                          "SSE_QUEUE_MAXSIZE constant must be defined")
            self.assertGreater(self.serve.SSE_QUEUE_MAXSIZE, 0,
                             "SSE_QUEUE_MAXSIZE must be positive")
        finally:
            con.close()

    def test_age_bucketing_reduces_hash_churn(self):
        """Heartbeat age must be bucketed to prevent every-tick hash change."""
        serve = self.serve
        # Heartbeat written 100s before FIXED_NOW. Two reads 1s apart share the
        # 99s bucket (100//3*3 == 99, 101//3*3 == 99): the snapshot must not churn.
        status1 = serve.get_heartbeat_status(clock=lambda: FIXED_NOW)
        status2 = serve.get_heartbeat_status(clock=lambda: FIXED_NOW + 1)

        self.assertNotEqual(status1["alive"], "not configured",
                            "fixture heartbeat must be read, or the bucketing check is vacuous")
        self.assertEqual(status1["age"], 99, "age must bucket to the 3-second floor")
        self.assertEqual(status1["age"], status2["age"],
                         "Age must bucket to reduce hash churn from per-tick updates")

    def test_dist_index_serves_successfully(self):
        """Wave-14 U9: GET / serves the built dist/index.html with CSRF token."""
        # Verify that the dist-based dashboard loads successfully
        con2 = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        con2.request("GET", "/")
        r = con2.getresponse()
        self.assertEqual(r.status, 200, "GET / should return 200 with dist present")
        html = r.read().decode("utf-8")
        con2.close()

        # Verify CSRF token is substituted (not left as sentinel)
        self.assertNotIn("__AESOP_CSRF_SENTINEL__", html,
                        "CSRF sentinel must be replaced with actual token")
        self.assertIn("window.__AESOP_CSRF_TOKEN__", html,
                     "HTML must contain the CSRF token variable")


if __name__ == "__main__":
    unittest.main()
