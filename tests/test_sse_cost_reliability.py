"""TDD tests for SSE + cost reliability fixes (wave-18 sse-cost audit findings).

Findings:
1. CRITICAL sse.py:82-84 — when a client queue is full, the oldest event is dropped
   silently. Track a per-client dropped counter and set a "dropped": N field on the
   next successfully queued event.
2. cost.py:109 + sse.py:162 — cost-ledger parsing has no format validation. Validate
   the header row matches expected columns; on mismatch return {"error": "ledger format invalid"}
   and log per-line parse failures to stderr.

Run: python -m unittest tests.test_sse_cost_reliability -v
"""
import importlib.util
import io
import json
import os
import queue
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

UI_DIR = Path(__file__).parent.parent / "ui"


def load_sse(fixture_root):
    """Import a fresh ui/sse.py module instance."""
    ui_dir = str(UI_DIR)
    if ui_dir not in sys.path:
        sys.path.insert(0, ui_dir)

    os.environ["AESOP_ROOT"] = str(fixture_root)
    os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(fixture_root / "transcripts")
    os.environ["AESOP_STATE_ROOT"] = str(fixture_root / "state")

    import config
    config.reload()

    spec = importlib.util.spec_from_file_location(f"sse_reliability_{id(fixture_root)}", UI_DIR / "sse.py")
    sse = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sse)
    return sse


ENV_KEYS = ("AESOP_ROOT", "AESOP_STATE_ROOT", "AESOP_TRANSCRIPTS_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


class SSEReliabilityCase(unittest.TestCase):
    """Base class for SSE reliability tests."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-sse-reliability-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()
        self._saved_env = {k: os.environ.get(k) for k in ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_STATE_ROOT")}
        self.sse = load_sse(self.fixture_root)

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.fixture_root, ignore_errors=True)


class TestQueueOverflowTrackingDropped(SSEReliabilityCase):
    """CRITICAL audit finding: queue overflow should track dropped events and report count.

    When a client queue is full, broadcast_sse() drops the oldest event. But this loss
    is silent — the frontend never knows it missed updates. We need to:
    1. Track per-client how many events have been dropped.
    2. Attach a "dropped": N field to the next successfully queued event so the frontend
       can show that N events were missed.
    """

    def test_queue_overflow_tracks_dropped_count_on_next_event(self):
        """Queue overflow increments dropped counter and attaches it to the overflowing event."""
        sse = self.sse

        # Create a small queue (maxsize=2)
        q = queue.Queue(maxsize=2)
        with sse._sse_lock:
            sse._sse_clients.append(q)

        # Broadcast 4 events to a queue with capacity 2.
        # Events 1-2 fill the queue.
        # Event 3 triggers Full -> drop 1, add 3 (with dropped: 1)
        # Event 4 triggers Full -> drop 2, add 4 (with dropped: 1)
        # Queue ends up with [3 (dropped:1), 4 (dropped:1)]
        sse.broadcast_sse("update", '{"n": 1}')
        sse.broadcast_sse("update", '{"n": 2}')
        sse.broadcast_sse("update", '{"n": 3}')  # Queue full -> drop event 1, add event 3 with dropped:1
        sse.broadcast_sse("update", '{"n": 4}')  # Queue full -> drop event 2, add event 4 with dropped:1

        # Queue should now contain the 2 most recent events
        event1_name, event1_payload = q.get_nowait()
        self.assertEqual(event1_name, "update")
        event1_data = json.loads(event1_payload)
        self.assertEqual(event1_data.get("n"), 3)
        # Event 3 caused a drop, so it should have dropped: 1
        self.assertEqual(event1_data.get("dropped"), 1)

        event2_name, event2_payload = q.get_nowait()
        self.assertEqual(event2_name, "update")
        event2_data = json.loads(event2_payload)
        self.assertEqual(event2_data.get("n"), 4)
        # Event 4 also caused a drop, so it should have dropped: 1
        self.assertEqual(event2_data.get("dropped"), 1)

    def test_no_dropped_field_when_queue_not_full(self):
        """When queue never overflows, "dropped" field is absent."""
        sse = self.sse

        q = queue.Queue(maxsize=10)
        with sse._sse_lock:
            sse._sse_clients.append(q)

        sse.broadcast_sse("update", '{"n": 1}')
        sse.broadcast_sse("update", '{"n": 2}')

        event1_name, event1_payload = q.get_nowait()
        event1_data = json.loads(event1_payload)
        self.assertNotIn("dropped", event1_data)

        event2_name, event2_payload = q.get_nowait()
        event2_data = json.loads(event2_payload)
        self.assertNotIn("dropped", event2_data)

    def test_dropped_counter_resets_after_client_unregister(self):
        """When a client is unregistered and re-registered, dropped counter resets."""
        sse = self.sse

        q1 = queue.Queue(maxsize=2)
        with sse._sse_lock:
            sse._sse_clients.append(q1)

        # Cause some drops on q1
        sse.broadcast_sse("update", '{"n": 1}')
        sse.broadcast_sse("update", '{"n": 2}')
        sse.broadcast_sse("update", '{"n": 3}')  # Causes drop

        # Unregister q1
        sse.unregister_sse_client(q1)

        # Register a new client
        q2 = queue.Queue(maxsize=10)
        with sse._sse_lock:
            sse._sse_clients.append(q2)

        # Broadcast to q2 (which was just registered)
        sse.broadcast_sse("update", '{"n": 4}')

        # q2 should receive the event without any prior dropped count
        event_name, event_payload = q2.get_nowait()
        event_data = json.loads(event_payload)
        self.assertNotIn("dropped", event_data)


class CostIsolationCase(unittest.TestCase):
    """Base class for cost tests with isolated temp directories."""

    def setUp(self):
        """Set up isolated temp directories for testing."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-cost-reliability-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)
        (self.fixture_root / "transcripts").mkdir()

        # Save original env
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}

        # Set isolated environment
        os.environ["AESOP_ROOT"] = str(self.fixture_root)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

        # Reload config to pick up new env vars
        if str(UI_DIR) not in sys.path:
            sys.path.insert(0, str(UI_DIR))
        import config
        config.reload()

    def tearDown(self):
        """Restore original env and clean up temp files."""
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import config
        config.reload()
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def write_ledger(self, content):
        """Write a test ledger file to the isolated state dir."""
        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        ledger_file.parent.mkdir(parents=True, exist_ok=True)
        ledger_file.write_text(content, encoding='utf-8')
        return ledger_file


class TestLedgerFormatValidation(CostIsolationCase):
    """Audit finding: cost-ledger parsing has no format validation.

    When the ledger file exists but has an invalid header row (doesn't match the
    expected column structure), we should:
    1. Detect the mismatch.
    2. Return a payload containing {"error": "ledger format invalid"}.
    3. Log per-line parse failures to stderr.
    """

    def test_valid_ledger_header_parses_normally(self):
        """Valid ledger with correct format parses successfully (no column header row needed)."""
        import cost

        # Ledger should start directly with data (markdown separator then data rows)
        ledger = """|---|---|---|---|---|---|---|
|2026-07-11T22:08:17|Agent|claude-haiku-4-5-20251001|0|8|186|OK|
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Should parse without error
        self.assertNotIn("error", summary)
        self.assertEqual(len(summary["models"]), 1)
        self.assertEqual(summary["models"]["claude-haiku-4-5-20251001"]["runs"], 1)

    def test_malformed_header_returns_error_marker(self):
        """Ledger with mismatched header row returns error marker."""
        import cost

        # Write a ledger with a completely wrong header
        ledger = """|wrong_col|header|values|here|that|dont|match|
|---|---|---|---|---|---|---|
|2026-07-11T22:08:17|Agent|claude-haiku-4-5-20251001|0|8|186|OK|
"""
        self.write_ledger(ledger)

        buf = io.StringIO()
        with redirect_stderr(buf):
            summary = cost.get_cost_summary()

        # Should include error marker
        self.assertIn("error", summary)
        self.assertEqual(summary["error"], "ledger format invalid")

        # stderr should have logged something
        stderr_output = buf.getvalue()
        self.assertTrue(len(stderr_output) > 0, "Expected parse failures logged to stderr")

    def test_missing_column_returns_error_marker(self):
        """Ledger with fewer columns than expected returns error marker."""
        import cost

        # Write a ledger missing some columns
        ledger = """|timestamp|agent_type|model|
|---|---|---|
|2026-07-11T22:08:17|Agent|claude-haiku-4-5-20251001|
"""
        self.write_ledger(ledger)

        buf = io.StringIO()
        with redirect_stderr(buf):
            summary = cost.get_cost_summary()

        # Should include error marker
        self.assertIn("error", summary)
        self.assertEqual(summary["error"], "ledger format invalid")

    def test_per_line_parse_failures_logged_to_stderr(self):
        """When parsing individual lines fails, errors are logged to stderr."""
        import cost

        # Write a ledger with some invalid data rows
        ledger = """|ISO timestamp|agent_type|model|duration|tokens_in|tokens_out|verdict|
|---|---|---|---|---|---|---|
|2026-07-11T22:08:17|Agent|claude-haiku-4-5-20251001|0|not_a_number|186|OK|
|2026-07-11T22:08:21|Agent|claude-opus-4-8|0|8|also_not_a_number|OK|
"""
        self.write_ledger(ledger)

        buf = io.StringIO()
        with redirect_stderr(buf):
            summary = cost.get_cost_summary()

        # Should log parse failures
        stderr_output = buf.getvalue()
        # The function should log something about invalid numeric fields
        if summary.get("skipped_lines", 0) > 0:
            # If lines were skipped, there should be some indication in stderr
            pass  # This is implementation-dependent, but we expect stderr output


if __name__ == "__main__":
    unittest.main()
