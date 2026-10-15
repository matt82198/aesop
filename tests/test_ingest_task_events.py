"""Tests for tools/ingest_task_events.py — task-events.jsonl -> state store `task` stream.

Hermetic: every test uses a temp state dir and a temp JSONL. No test touches
~/.claude/state or the real aesop state DB.
"""
import json

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from state_store.api import StateAPI  # noqa: E402
from state_store.paths import resolve_db_path  # noqa: E402
from tools import ingest_task_events as ite  # noqa: E402

SCRIPT = str(REPO_ROOT / "tools" / "ingest_task_events.py")


def _rec(agent_id, agent_type="", summary="working", ts="2026-10-15T01:00:00.000Z", sess="s1"):
    return json.dumps({
        "ts": ts,
        "session_id": sess,
        "agent_id": agent_id,
        "agent_type": agent_type,
        "stop_reason": None,
        "summary": summary,
        "context_used_pct": 10,
    })


class TaskEventIngestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.jsonl = self.root / "task-events.jsonl"
        self.cursor = self.state / "task_events.cursor.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, lines, trailing_newline=True):
        body = "\n".join(lines) + ("\n" if trailing_newline else "")
        with open(self.jsonl, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(body)

    def _task_events(self):
        api = StateAPI(resolve_db_path(self.state, "tracker"))
        try:
            return api.get("task")
        finally:
            api.close()

    def _ingest(self):
        return ite.ingest(self.jsonl, self.state)

    # --- events land in the store -------------------------------------------
    def test_events_land_in_task_stream(self):
        self._write([_rec("a1", "general-purpose", "lane work"), _rec("a2", "", "Reading ci.yml")])
        result = self._ingest()
        self.assertEqual(result["ingested"], 2)
        events = self._task_events()
        self.assertEqual(len(events), 2)
        payloads = [e["payload"] for e in events]
        self.assertEqual({p["agent_id"] for p in payloads}, {"a1", "a2"})
        self.assertTrue(all(p["event_id"] for p in payloads))

    def test_agent_type_recorded_on_payload(self):
        self._write([_rec("a1", "general-purpose", "lane work")])
        self._ingest()
        payload = self._task_events()[0]["payload"]
        self.assertEqual(payload["agent_type"], "general-purpose")
        self.assertEqual(payload["kind"], "agent")

    # --- idempotency ----------------------------------------------------------
    def test_rerun_is_idempotent(self):
        self._write([_rec("a1", "general-purpose"), _rec("a2", "")])
        self.assertEqual(self._ingest()["ingested"], 2)
        second = self._ingest()
        self.assertEqual(second["ingested"], 0)
        self.assertEqual(len(self._task_events()), 2)

    def test_appended_line_ingested_once(self):
        self._write([_rec("a1", "general-purpose")])
        self._ingest()
        self._write([_rec("a2", "general-purpose")])
        self.assertEqual(self._ingest()["ingested"], 1)
        self.assertEqual(self._ingest()["ingested"], 0)
        self.assertEqual(len(self._task_events()), 2)

    def test_cursor_lost_still_no_duplicates(self):
        # Simulates a crash after appends but before the cursor write: the
        # event_id dedupe must prevent a second copy of every event.
        self._write([_rec("a1", "general-purpose"), _rec("a2", "general-purpose")])
        self._ingest()
        if self.cursor.exists():
            self.cursor.unlink()
        result = self._ingest()
        self.assertEqual(result["ingested"], 0)
        self.assertEqual(len(self._task_events()), 2)

    # --- fail closed ----------------------------------------------------------
    def test_malformed_line_fails_closed(self):
        self._write([_rec("a1", "general-purpose"), "{not json", _rec("a2", "general-purpose")])
        result = self._ingest()
        self.assertEqual(result["malformed"], 1)
        self.assertEqual(result["exit_code"], 3)
        # The cursor must not move past the bad line: the next run reports it again.
        again = self._ingest()
        self.assertEqual(again["malformed"], 1)
        self.assertEqual(again["exit_code"], 3)

    def test_malformed_line_is_never_silently_skipped(self):
        self._write(["[]", "not even json", _rec("a1", "general-purpose")])
        result = self._ingest()
        self.assertEqual(result["malformed"], 2)
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(len(self._task_events()), 1)

    def test_record_missing_required_fields_is_malformed(self):
        self._write([json.dumps({"ts": "2026-10-15T01:00:00.000Z"})])
        result = self._ingest()
        self.assertEqual(result["malformed"], 1)
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(self._task_events(), [])

    def test_partial_trailing_line_is_not_consumed(self):
        self._write([_rec("a1", "general-purpose")])
        with open(self.jsonl, "a", encoding="utf-8", newline="\n") as fh:
            fh.write('{"ts":"2026-10-15T01:0')  # hook mid-write, no newline yet
        result = self._ingest()
        self.assertEqual(result["ingested"], 1)
        self.assertEqual(result["malformed"], 0)
        self.assertEqual(result["exit_code"], 0)

    def test_truncated_jsonl_fails_closed(self):
        self._write([_rec("a1", "general-purpose"), _rec("a2", "general-purpose")])
        self._ingest()
        with open(self.jsonl, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_rec("z9", "general-purpose") + "\n")
        result = self._ingest()
        self.assertEqual(result["exit_code"], 3)
        self.assertIn("shrank", result["error"])

    # --- internal-kind tagging ------------------------------------------------
    def test_internal_suggestion_records_tagged_internal(self):
        self._write([
            _rec("i1", "", "/mcp"),
            _rec("i2", "", "chrome is connected now, go"),
            _rec("i3", "", "The user has been frustrated and correcting the assistant's approach"),
            _rec("i4", "", "ok retry the chrome connection"),
        ])
        self._ingest()
        kinds = {e["payload"]["agent_id"]: e["payload"]["kind"] for e in self._task_events()}
        self.assertEqual(set(kinds.values()), {"internal"})

    def test_real_work_with_empty_type_stays_agent(self):
        self._write([_rec("r1", "", "Checking open() calls in power_selftest.py")])
        self._ingest()
        self.assertEqual(self._task_events()[0]["payload"]["kind"], "agent")

    def test_named_agent_type_never_internal(self):
        self._write([_rec("n1", "general-purpose", "/mcp")])
        self._ingest()
        self.assertEqual(self._task_events()[0]["payload"]["kind"], "agent")

    # --- check mode -----------------------------------------------------------
    def test_check_fails_before_ingest_and_passes_after(self):
        self._write([_rec("a1", "general-purpose")])
        self.assertNotEqual(ite.check(self.jsonl, self.state), 0)
        self._ingest()
        self.assertEqual(ite.check(self.jsonl, self.state), 0)

    def test_check_fails_on_malformed_tail(self):
        self._write([_rec("a1", "general-purpose"), "garbage"])
        self._ingest()
        self.assertEqual(ite.check(self.jsonl, self.state), 1)

    # --- heartbeat ------------------------------------------------------------
    def test_heartbeat_written_only_on_clean_run(self):
        hb = self.state / ".task-ingest-heartbeat"
        self._write([_rec("a1", "general-purpose"), "bad"])
        self._ingest()
        self.assertFalse(hb.exists())
        with open(self.jsonl, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_rec("a1", "general-purpose") + "\n")
        self.cursor.unlink(missing_ok=True)
        self._ingest()
        self.assertTrue(hb.exists())
        self.assertTrue(hb.read_text().strip().isdigit())

    # --- CLI exit codes -------------------------------------------------------
    def _cli(self, *extra):
        return subprocess.run(
            [sys.executable, SCRIPT, "--jsonl", str(self.jsonl), "--state-dir", str(self.state), *extra],
            capture_output=True, text=True, timeout=120,
        )

    def test_cli_exit_codes(self):
        self._write([_rec("a1", "general-purpose")])
        self.assertEqual(self._cli("--check").returncode, 1)
        self.assertEqual(self._cli().returncode, 0)
        self.assertEqual(self._cli("--check").returncode, 0)
        self._write(["broken"])
        self.assertEqual(self._cli().returncode, 3)


if __name__ == "__main__":
    unittest.main()
