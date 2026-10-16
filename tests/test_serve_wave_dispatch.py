"""Wave dispatch backend tests — GET /api/wave/dispatch.

Contract under test:
  - GET /api/wave/dispatch — live per-agent phase, activity age, and token burn.
  - Response shape: {
      "available": bool,
      "wave_phase": str | null,
      "agents": [
        {
          "id": str,
          "phase": str,
          "last_activity_age_sec": int,
          "token_estimate": int,
          "warnings": [str] (optional)
        }
      ],
      "at": str (ISO 8601)
    }
  - Reads recursively from agent transcripts (mtime, size) in {transcripts_root}/**/agent-*.jsonl
    Real Claude Code layout: ~/.claude/projects/*/subagents/agent-*.jsonl or {session}/subagents/agent-*.jsonl
    Filters to <30 min mtime to avoid stale entries.
  - Degrades to {available:false} when no active workflow.

Isolation: every test binds serve.py to a throwaway fixture AESOP_ROOT /
AESOP_STATE_ROOT / AESOP_TRANSCRIPTS_ROOT so nothing touches the real repo state.

Run: python -m unittest tests.test_serve_wave_dispatch
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

ENV_KEYS = ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_STATE_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


def load_serve(fixture_root, extra_env=None):
    """Import a fresh serve module instance bound to a fixture AESOP_ROOT."""
    os.environ["AESOP_ROOT"] = str(fixture_root)
    os.environ["AESOP_STATE_ROOT"] = str(fixture_root / "state")
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)
    spec = importlib.util.spec_from_file_location(
        f"serve_wave_dispatch_{id(fixture_root)}", SERVE_PATH)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class WaveDispatchFixtureCase(unittest.TestCase):
    """Base: fixture root + live ThreadingHTTPServer bound to a fresh serve import."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-wave-dispatch-test-"))
        (self.fixture_root / "state").mkdir(parents=True)
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

        # Create fixture transcripts
        self.transcripts_root = self.fixture_root / "transcripts"
        self._create_fixture_transcripts()

        # Set transcripts root
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.transcripts_root)

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

    def _create_fixture_transcripts(self):
        """Create fixture agent transcripts for testing.

        Uses real Claude Code layout: {project}/subagents/agent-*.jsonl
        (matching ui/agents.py recursive glob pattern and real orchestration structure).
        """
        # Create aesop project with subagents dir (real layout, not memory/)
        aesop_subagents = self.transcripts_root / "aesop" / "subagents"
        aesop_subagents.mkdir(parents=True, exist_ok=True)

        # Agent 1: active, tool-use phase (newest turn is an assistant tool_use block)
        agent1_path = aesop_subagents / "agent-fleet-fix-0.jsonl"
        agent1_content = self._make_agent_jsonl(
            self._user_prompt("User dispatch"),
            self._assistant_text("Assistant thinking", stop_reason="end_turn"),
            self._assistant_tool_use("write"),
        )
        agent1_path.write_text(agent1_content, encoding='utf-8')

        # Agent 2: stalled, many entries (newest turn is a finished assistant reply)
        agent2_path = aesop_subagents / "agent-fleet-fix-1.jsonl"
        agent2_content = self._make_agent_jsonl(
            self._user_prompt("User dispatch"),
            self._assistant_text("Assistant thinking", stop_reason=None),
            self._assistant_text("Assistant completion", stop_reason="end_turn"),
            self._user_tool_result("Tool result"),
            self._assistant_text("Another completion", stop_reason="end_turn"),
        )
        agent2_path.write_text(agent2_content, encoding='utf-8')
        # Make it older (stalled)
        old_time = time.time() - 500  # 500 seconds old
        os.utime(agent2_path, (old_time, old_time))

        # Agent 3: new, thinking phase (assistant turn still streaming, no stop_reason yet)
        agent3_path = aesop_subagents / "agent-fleet-review-0.jsonl"
        agent3_content = self._make_agent_jsonl(
            self._user_prompt("User dispatch"),
            self._assistant_text("Assistant thinking", stop_reason=None),
        )
        agent3_path.write_text(agent3_content, encoding='utf-8')

    @staticmethod
    def _user_prompt(text):
        """A plain user prompt record (message.content is a string)."""
        return {"type": "user", "message": {"role": "user", "content": text}}

    @staticmethod
    def _user_tool_result(text):
        """A user record carrying a structured tool_result content block."""
        block = {"type": "tool_result", "tool_use_id": "toolu_1", "content": text}
        return {"type": "user", "message": {"role": "user", "content": [block]}}

    @staticmethod
    def _assistant_text(text, stop_reason):
        """An assistant record with a text block and the given stop_reason."""
        message = {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
        }
        return {"type": "assistant", "message": message}

    @staticmethod
    def _assistant_tool_use(tool_name):
        """An assistant turn whose content holds a tool_use block (stop_reason tool_use)."""
        block = {"type": "tool_use", "id": "toolu_1", "name": tool_name, "input": {}}
        message = {"role": "assistant", "content": [block], "stop_reason": "tool_use"}
        return {"type": "assistant", "message": message}

    def _make_agent_jsonl(self, *records):
        """Create NDJSON transcript content from structured record dicts."""
        return "\n".join(json.dumps(record) for record in records) + "\n"

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


class GetWaveDispatch(WaveDispatchFixtureCase):
    """GET /api/wave/dispatch — live per-agent phase and activity."""

    def test_wave_dispatch_endpoint_exists(self):
        """GET /api/wave/dispatch returns 200 and valid JSON."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        self.assertIn("available", body)
        self.assertIn("wave_phase", body)
        self.assertIn("agents", body)
        self.assertIn("at", body)

    def test_wave_dispatch_available_true(self):
        """Wave dispatch is available when agents are found."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        # Fixture has active agents, so available should be true
        self.assertTrue(body["available"])

    def test_wave_dispatch_returns_agents(self):
        """Wave dispatch returns list of agents."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        agents = body["agents"]
        self.assertIsInstance(agents, list)
        # Fixture created 3 agents
        self.assertGreater(len(agents), 0)

    def test_wave_dispatch_agent_shape(self):
        """Each agent has required fields."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        agents = body["agents"]
        self.assertGreater(len(agents), 0)

        for agent in agents:
            self.assertIn("id", agent)
            self.assertIn("phase", agent)
            self.assertIn("last_activity_age_sec", agent)
            self.assertIn("token_estimate", agent)

            # Validate types
            self.assertIsInstance(agent["id"], str)
            self.assertIsInstance(agent["phase"], str)
            self.assertIsInstance(agent["last_activity_age_sec"], int)
            self.assertIsInstance(agent["token_estimate"], int)

    def test_wave_dispatch_phase_inference(self):
        """Agent phase is inferred from transcript content."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        agents = body["agents"]
        phases = {a["id"]: a["phase"] for a in agents}

        # Agent 1 has tool_use marker
        self.assertEqual(phases.get("fleet-fix-0"), "tool-use")

    def test_wave_dispatch_age_sec(self):
        """Last activity age is computed from file mtime."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        agents = body["agents"]
        ages = {a["id"]: a["last_activity_age_sec"] for a in agents}

        # Agent 1 is fresh (created now)
        self.assertLess(ages.get("fleet-fix-0", 999), 10)

        # Agent 2 is old (500 seconds)
        agent2_age = ages.get("fleet-fix-1", 0)
        self.assertGreater(agent2_age, 450)

    def test_wave_dispatch_token_estimate(self):
        """Token estimate is derived from file size."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        agents = body["agents"]
        tokens = {a["id"]: a["token_estimate"] for a in agents}

        # All should have >0 tokens (size-based estimate)
        for agent_id, token_count in tokens.items():
            self.assertGreater(token_count, 0, f"Agent {agent_id} has no tokens")

    def test_wave_dispatch_warnings_inactive(self):
        """Warnings include 'inactive >5min' for age > 300s."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        agents = body["agents"]
        agent2 = next((a for a in agents if a["id"] == "fleet-fix-1"), None)
        self.assertIsNotNone(agent2)

        # Agent 2 is 500 seconds old, should have warning
        self.assertIn("warnings", agent2)
        self.assertIn("inactive >5min", agent2["warnings"])

    def test_wave_dispatch_timestamp_format(self):
        """Timestamp is ISO 8601 UTC."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        ts = body["at"]
        self.assertIsInstance(ts, str)
        # Should end with Z or +00:00
        self.assertTrue(ts.endswith("Z") or "+00:00" in ts)

    def test_wave_dispatch_no_cache_header(self):
        """GET /api/wave/dispatch returns no-cache headers."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        cache_control = hdrs.get("Cache-Control", "")
        self.assertIn("no-cache", cache_control)

    def test_wave_dispatch_content_type_json(self):
        """GET /api/wave/dispatch returns application/json content type."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        content_type = hdrs.get("Content-Type", "")
        self.assertIn("application/json", content_type)


class NoActiveWorkflow(WaveDispatchFixtureCase):
    """Wave dispatch graceful degradation when no workflow active."""

    def setUp(self):
        # Initialize fixtures but don't create transcripts
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-wave-dispatch-empty-test-"))
        (self.fixture_root / "state").mkdir(parents=True)
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

        # Create empty transcripts root (no agents)
        self.transcripts_root = self.fixture_root / "transcripts"
        self.transcripts_root.mkdir(parents=True)

        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.transcripts_root)

        self.serve = load_serve(self.fixture_root)
        self.token = self.serve.SESSION_TOKEN
        self.assertTrue(self.token, "fixture must produce a session token")

        if str(UI_PATH) not in sys.path:
            sys.path.insert(0, str(UI_PATH))
        import handler

        self.httpd = handler.QuietThreadingHTTPServer(("127.0.0.1", 0), self.serve.DashboardHandler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

    def test_wave_dispatch_unavailable_no_agents(self):
        """Wave dispatch is unavailable when no agents found."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        self.assertFalse(body["available"])
        self.assertEqual(len(body["agents"]), 0)

    def test_wave_dispatch_returns_valid_response_when_unavailable(self):
        """Response is still well-formed when unavailable."""
        status, hdrs, body = self._get_json("/api/wave/dispatch")
        self.assertEqual(status, 200)

        self.assertIn("available", body)
        self.assertIn("agents", body)
        self.assertIn("at", body)


if __name__ == "__main__":
    unittest.main()
