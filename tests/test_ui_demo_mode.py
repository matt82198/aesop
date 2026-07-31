"""Zero-key demo mode tests -- `python ui/serve.py --demo` (ui/demo.py).

Contract under test:
  - `python ui/serve.py --demo` serves a POPULATED dashboard from a seeded,
    self-identifying demo snapshot (no API key, no gh, no live fleet needed):
      * GET /api/state -> 200 with agents count >= 8, wave phase present,
        healthy heartbeats, non-empty tracker/backlog/cost, and a top-level
        "demo": true marker.
      * GET / -> the served HTML carries a visible "DEMO DATA" banner so the
        demo can never be mistaken for live state (honesty requirement).
      * GET /api/wave/prs|telemetry|dispatch -> populated, available=true.
      * GET /events -> SSE still ticks (a "data" section frame arrives).
  - Default mode (no flag, no AESOP_DEMO env) is byte-compatible: no "demo"
    key in /api/state, no banner in the HTML.
  - Demo timestamps are always fresh: seeded heartbeats/agent activity are
    generated now-relative at call time, never hardcoded.

Hygiene: sys.executable subprocess, ephemeral port via PORT env, explicit
timeouts, temp-dir demo root via AESOP_DEMO_ROOT, no cwd pollution.

Run: python -m unittest tests.test_ui_demo_mode
"""
import http.client
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVE_PATH = REPO_ROOT / "ui" / "serve.py"
DEMO_PATH = REPO_ROOT / "ui" / "demo.py"
UI_PATH = REPO_ROOT / "ui"

SERVER_START_DEADLINE_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 10

ENV_KEYS = ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_STATE_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT", "AESOP_DEMO",
            "AESOP_DEMO_ROOT", "AESOP_AUDIT_BACKLOG",
            "AESOP_WATCHDOG_HEARTBEAT", "AESOP_MONITOR_HEARTBEAT",
            "AESOP_CONDUCTOR3_ROOT")


def _free_port():
    """Ask the OS for a free ephemeral port (bind, read, close)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _clean_env():
    """Subprocess env with no inherited AESOP_*/PORT overrides."""
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("AESOP_") or key == "PORT":
            env.pop(key, None)
    return env


def _wait_for_port(port, deadline_seconds):
    """Poll until the server accepts connections, or fail."""
    deadline = time.time() + deadline_seconds
    last_err = None
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except OSError as e:
            last_err = e
            time.sleep(0.2)
    raise AssertionError(f"server on port {port} never came up: {last_err}")


def _http_get(port, path, timeout=REQUEST_TIMEOUT_SECONDS):
    """One GET; returns (status, headers dict, body bytes). Retries transient
    Windows socket aborts (same pattern as tests/test_api_state.py)."""
    last = None
    for _ in range(3):
        con = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            con.request("GET", path)
            resp = con.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        except (ConnectionAbortedError, ConnectionResetError,
                http.client.RemoteDisconnected) as e:
            last = e
            continue
        finally:
            con.close()
    raise last


def _get_json(port, path):
    status, hdrs, body = _http_get(port, path)
    return status, json.loads(body.decode("utf-8"))


# ==============================================================================
# Subprocess: python ui/serve.py --demo
# ==============================================================================

class TestDemoModeServer(unittest.TestCase):
    """One shared --demo server process for all populated-endpoint assertions."""

    proc = None

    @classmethod
    def setUpClass(cls):
        cls.demo_root = Path(tempfile.mkdtemp(prefix="aesop-demo-mode-test-"))
        cls.port = _free_port()
        env = _clean_env()
        env["PORT"] = str(cls.port)
        env["AESOP_DEMO_ROOT"] = str(cls.demo_root)
        env["AESOP_UI_COLLECT_INTERVAL"] = "0.2"
        cls.proc = subprocess.Popen(
            [sys.executable, str(SERVE_PATH), "--demo"],
            cwd=str(cls.demo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_port(cls.port, SERVER_START_DEADLINE_SECONDS)
        except Exception:
            cls._stop()
            raise

    @classmethod
    def _stop(cls):
        if cls.proc is not None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
                cls.proc.wait(timeout=10)
            if cls.proc.stdout:
                cls.proc.stdout.close()
            cls.proc = None

    @classmethod
    def tearDownClass(cls):
        cls._stop()
        shutil.rmtree(cls.demo_root, ignore_errors=True)

    # ---- /api/state -------------------------------------------------------

    def test_api_state_is_200_and_demo_marked(self):
        status, state = _get_json(self.port, "/api/state")
        self.assertEqual(status, 200)
        self.assertIs(state.get("demo"), True,
                      "demo mode must self-identify in the /api/state payload")

    def test_api_state_agents_populated_mixed_states(self):
        status, state = _get_json(self.port, "/api/state")
        self.assertEqual(status, 200)
        agents = state["agents"]
        self.assertGreaterEqual(len(agents), 8,
                                "seeded fleet must show 8-10 agents")
        statuses = {a.get("status") for a in agents}
        self.assertIn("running", statuses)
        self.assertIn("idle", statuses)
        for a in agents:
            self.assertTrue(a.get("taskLabel"),
                            "every demo agent needs a plausible task label")

    def test_api_state_heartbeats_healthy_and_fresh(self):
        status, state = _get_json(self.port, "/api/state")
        self.assertEqual(status, 200)
        watchdog = state["data"]["watchdog"]
        monitor = state["data"]["monitor"]
        self.assertEqual(watchdog["alive"], "ALIVE")
        self.assertGreaterEqual(watchdog["age"], 0)
        self.assertLess(watchdog["age"], watchdog["threshold"],
                        "demo watchdog heartbeat must always read fresh")
        self.assertEqual(monitor["alive"], "ALIVE")
        self.assertGreaterEqual(monitor["age"], 0)
        self.assertLess(monitor["age"], monitor["threshold"])

    def test_api_state_wave_banner_present(self):
        status, state = _get_json(self.port, "/api/state")
        self.assertEqual(status, 200)
        orchestrators = state["status"]["orchestrators"]
        self.assertGreaterEqual(len(orchestrators), 1)
        phase = orchestrators[0].get("phase", "")
        self.assertIn("wave-2", phase)
        self.assertFalse(orchestrators[0].get("stale"),
                         "demo orchestrator status must never read stale")

    def test_api_state_tracker_backlog_cost_populated(self):
        status, state = _get_json(self.port, "/api/state")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(state["tracker"]["items"]), 5)
        tiers = state["backlog"]["tiers"]
        self.assertGreaterEqual(len(tiers), 2,
                                "demo backlog must show wave progress tiers")
        # Wave mid-execution: at least one tier has both done and open work.
        self.assertTrue(any(t["done"] > 0 for t in tiers))
        self.assertTrue(any(t["inflight"] + t["todo"] > 0 for t in tiers))
        scorecard = state["cost"]["overall_scorecard"]
        self.assertGreater(scorecard["total_runs"], 0,
                           "demo cost panel must be non-zero")
        self.assertGreater(scorecard["ok_count"], 0)

    def test_api_state_events_feed_populated(self):
        status, state = _get_json(self.port, "/api/state")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(state["data"]["events"]), 3)

    # ---- honesty banner -----------------------------------------------------

    def test_root_html_carries_demo_data_banner(self):
        status, hdrs, body = _http_get(self.port, "/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8", errors="replace")
        self.assertIn("DEMO DATA", html,
                      "demo mode must label the UI so it is never mistaken "
                      "for live state")
        self.assertIn("aesop-demo-banner", html)

    # ---- wave endpoints -----------------------------------------------------

    def test_wave_pr_board_populated(self):
        status, payload = _get_json(self.port, "/api/wave/prs")
        self.assertEqual(status, 200)
        self.assertTrue(payload["available"])
        self.assertGreaterEqual(len(payload["prs"]), 4)
        titles = [p["title"] for p in payload["prs"]]
        self.assertTrue(all(titles), "every demo PR needs a real-looking title")

    def test_wave_telemetry_mid_execution(self):
        status, payload = _get_json(self.port, "/api/wave/telemetry")
        self.assertEqual(status, 200)
        self.assertEqual(payload["wave"], "wave-2")
        self.assertGreater(payload["tokens_used"], 0)
        self.assertNotEqual(payload["blocker"], "unknown")

    def test_wave_dispatch_shows_live_agents(self):
        status, payload = _get_json(self.port, "/api/wave/dispatch")
        self.assertEqual(status, 200)
        self.assertTrue(payload["available"])
        self.assertGreaterEqual(len(payload["agents"]), 8)
        self.assertIn("wave-2", payload.get("wave_phase") or "")

    # ---- SSE still ticks -----------------------------------------------------

    def test_sse_stream_emits_data_section(self):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            con.request("GET", "/events")
            resp = con.getresponse()
            self.assertEqual(resp.status, 200)
            current = None
            data_payload = None
            for _ in range(400):
                line = resp.fp.readline().decode("utf-8", errors="replace")
                if not line:
                    break
                if line.startswith("event: "):
                    current = line.strip().split(" ", 1)[1]
                elif line.startswith("data: ") and current == "data":
                    data_payload = line[len("data: "):].strip()
                    break
            self.assertIsNotNone(data_payload,
                                 "SSE must still tick in demo mode")
            parsed = json.loads(data_payload)
            self.assertEqual(parsed["watchdog"]["alive"], "ALIVE")
        finally:
            con.close()

    # ---- containment ---------------------------------------------------------

    def test_demo_state_lands_in_demo_root_only(self):
        self.assertTrue((self.demo_root / "state" / "tracker.json").exists())
        self.assertTrue((self.demo_root / "AUDIT-BACKLOG.md").exists())
        self.assertTrue((self.demo_root / "transcripts").is_dir())


# ==============================================================================
# Default mode untouched (in-process fixture, pattern from test_api_state.py)
# ==============================================================================

DIST_INDEX_HTML = """<!doctype html>
<html>
<head><title>Aesop Dashboard</title>
<script>window.__AESOP_CSRF_TOKEN__ = __AESOP_CSRF_SENTINEL__;</script>
</head>
<body><div id="root">DEMO-DEFAULT-MARKER</div>
</body>
</html>
"""


class TestDefaultModeUnaffected(unittest.TestCase):
    """No flag, no AESOP_DEMO env: payload and HTML stay demo-free."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-demo-default-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()
        (self.fixture_root / "conductor3" / "state").mkdir(parents=True)
        (self.fixture_root / "conductor3" / "monitor").mkdir(parents=True)
        dist_dir = self.fixture_root / "ui" / "web" / "dist"
        dist_dir.mkdir(parents=True)
        (dist_dir / "index.html").write_text(DIST_INDEX_HTML, encoding="utf-8")

        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ.pop("AESOP_DEMO", None)
        os.environ.pop("AESOP_DEMO_ROOT", None)
        os.environ.pop("AESOP_AUDIT_BACKLOG", None)
        os.environ.pop("AESOP_WATCHDOG_HEARTBEAT", None)
        os.environ.pop("AESOP_MONITOR_HEARTBEAT", None)
        os.environ["AESOP_ROOT"] = str(self.fixture_root)
        os.environ["AESOP_STATE_ROOT"] = str(self.fixture_root / "state")
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_CONDUCTOR3_ROOT"] = str(self.fixture_root / "conductor3")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

        spec = importlib.util.spec_from_file_location(
            f"serve_demo_default_{id(self.fixture_root)}", SERVE_PATH)
        self.serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.serve)

        if str(UI_PATH) not in sys.path:
            sys.path.insert(0, str(UI_PATH))
        import handler
        self.httpd = handler.QuietThreadingHTTPServer(
            ("127.0.0.1", 0), self.serve.DashboardHandler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True)
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

    def test_api_state_has_no_demo_marker(self):
        status, state = _get_json(self.port, "/api/state")
        self.assertEqual(status, 200)
        self.assertNotIn("demo", state,
                         "default mode payload must be untouched")

    def test_root_html_has_no_demo_banner(self):
        status, hdrs, body = _http_get(self.port, "/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8", errors="replace")
        self.assertNotIn("DEMO DATA", html)
        self.assertNotIn("aesop-demo-banner", html)


# ==============================================================================
# Snapshot freshness (unit level, no server)
# ==============================================================================

def _load_demo_module():
    spec = importlib.util.spec_from_file_location("aesop_ui_demo_unit", DEMO_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDemoSnapshotFreshness(unittest.TestCase):
    """Seeded timestamps are generated now-relative, never baked in."""

    def setUp(self):
        self.demo = _load_demo_module()
        self.tmp = Path(tempfile.mkdtemp(prefix="aesop-demo-fresh-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_seeded_heartbeats_are_now(self):
        self.demo.seed(self.tmp)
        for name in (".watchdog-heartbeat", ".monitor-heartbeat"):
            raw = (self.tmp / "state" / name).read_text(encoding="utf-8").strip()
            age = time.time() - int(raw)
            self.assertGreaterEqual(age, -2)
            self.assertLess(age, 10,
                            f"{name} must be seeded with a fresh epoch")

    def test_seeded_ledger_uses_current_dates(self):
        self.demo.seed(self.tmp)
        ledger = (self.tmp / "state" / "ledger" / "OUTCOMES-LEDGER.md")
        text = ledger.read_text(encoding="utf-8")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.assertIn(today, text,
                      "demo cost ledger must carry today's date")

    def test_demo_agents_activity_tracks_now(self):
        first = self.demo.get_demo_agents()
        self.assertGreaterEqual(len(first), 8)
        self.assertLessEqual(len(first), 10)
        now = datetime.now(timezone.utc)
        for agent in first:
            last = datetime.fromisoformat(
                agent["lastActivity"].replace("Z", "+00:00"))
            delta = (now - last).total_seconds()
            self.assertGreaterEqual(delta, 0)
            self.assertLess(delta, 1800,
                            "demo agent activity must always read recent")
        time.sleep(1.1)
        second = self.demo.get_demo_agents()
        for a1, a2 in zip(first, second):
            self.assertGreaterEqual(a2["lastActivity"], a1["lastActivity"],
                                    "activity timestamps must move with now")

    def test_orchestrator_status_updated_at_is_now(self):
        self.demo.seed(self.tmp)
        status_file = self.tmp / "state" / "orchestrator-status.json"
        data = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertIn("wave-2", data["phase"])
        updated = datetime.fromisoformat(
            data["updated_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        self.assertLess(abs(age), 10)


if __name__ == "__main__":
    unittest.main()
