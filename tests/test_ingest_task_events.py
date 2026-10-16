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
from state_store.projections import project_agent_lifecycle  # noqa: E402
from tools import ingest_task_events as ite  # noqa: E402

SCRIPT = str(REPO_ROOT / "tools" / "ingest_task_events.py")


def _rec(agent_id, agent_type="", summary="working", ts="2026-10-15T01:00:00.000Z", sess="s1", stop_reason=None):
    return json.dumps({
        "ts": ts,
        "session_id": sess,
        "agent_id": agent_id,
        "agent_type": agent_type,
        "stop_reason": stop_reason,
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
    def test_malformed_line_with_newer_data_is_quarantined_not_wedged(self):
        # A complete malformed line that has valid lines after it can never parse.
        # It is quarantined (raw + offset kept on disk), not silently skipped and
        # not held forever. The valid lines around it are ingested.
        self._write([_rec("a1", "general-purpose"), "{not json", _rec("a2", "general-purpose")])
        result = self._ingest()
        self.assertEqual(result["malformed"], 0)
        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(len(self._task_events()), 2)
        again = self._ingest()
        self.assertEqual((again["quarantined"], again["exit_code"]), (0, 0))

    def test_malformed_line_is_never_silently_skipped(self):
        # Every malformed line is recorded in the quarantine file. None vanish.
        self._write(["[]", "not even json", _rec("a1", "general-purpose")])
        result = self._ingest()
        self.assertEqual(result["quarantined"], 2)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(len(self._task_events()), 1)
        self.assertEqual([r["raw"] for r in self._quarantine_rows()], ["[]", "not even json"])

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

    def test_missing_source_is_clean_noop(self):
        # A box/CI runner without the brain's task-events hook has no source file.
        # That is "nothing to ingest", not a failure: --once must not exit non-zero.
        self.assertFalse(self.jsonl.exists())
        result = self._ingest()
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["ingested"], 0)
        self.assertNotIn("error", result)

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

    # --- torn-line recovery (glued fragment) ----------------------------------
    def _quarantine_path(self):
        return self.root / "task-events.jsonl.quarantine.jsonl"

    def _quarantine_rows(self):
        if not self._quarantine_path().exists():
            return []
        return [json.loads(l) for l in self._quarantine_path().read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_glued_torn_line_does_not_wedge_ingest(self):
        # Writer died mid-record; the next record was appended with no newline,
        # so the torn fragment and the next record form ONE malformed line that
        # is followed by a valid line. Must quarantine, not hold forever.
        torn = '{"ts":"2026-10-15T01:0'
        self._write([_rec("a1", "general-purpose")])
        self._write([torn + _rec("glued", "general-purpose")])
        self._write([_rec("a3", "general-purpose")])
        hb = self.state / ".task-ingest-heartbeat"
        result = self._ingest()
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(result["malformed"], 0)
        self.assertEqual({e["payload"]["agent_id"] for e in self._task_events()}, {"a1", "a3"})
        self.assertTrue(hb.exists())
        rows = self._quarantine_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn(torn, rows[0]["raw"])
        self.assertIsInstance(rows[0]["offset"], int)
        self.assertTrue(rows[0]["ts"])
        # Second run is idempotent: nothing re-quarantined, nothing re-ingested.
        again = self._ingest()
        self.assertEqual((again["exit_code"], again["quarantined"], again["ingested"]), (0, 0, 0))
        self.assertEqual(len(self._quarantine_rows()), 1)

    def test_glued_torn_line_cli_exits_zero_and_writes_heartbeat(self):
        self._write([_rec("a1", "general-purpose")])
        self._write(['{"ts":"2026-10-15T01:0' + _rec("glued", "general-purpose")])
        self._write([_rec("a3", "general-purpose")])
        self.assertEqual(self._cli().returncode, 0)
        self.assertTrue((self.state / ".task-ingest-heartbeat").exists())
        self.assertEqual(self._cli("--check").returncode, 0)

    def test_newline_terminated_last_malformed_line_is_held_not_quarantined(self):
        # Slow writer case: a malformed newline-terminated line with NO newer data
        # is ambiguous -- hold it (exit 3), do not punish the writer by quarantining.
        self._write([_rec("a1", "general-purpose"), "garbage-last"])
        result = self._ingest()
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["quarantined"], 0)
        self.assertFalse(self._quarantine_path().exists())
        self.assertEqual(ite._read_cursor(self.cursor), len(_rec("a1", "general-purpose")) + 1)
        # Once newer data arrives, the held line is no longer last and is quarantined.
        self._write([_rec("a3", "general-purpose")])
        result = self._ingest()
        self.assertEqual((result["exit_code"], result["quarantined"], result["ingested"]), (0, 1, 1))

    def test_malformed_line_before_unterminated_tail_is_quarantined(self):
        # A partial record being written after a complete malformed line means the
        # malformed line is not the file's last line: quarantine it, hold the tail.
        self._write([_rec("a1", "general-purpose"), "garbage-mid"])
        with open(self.jsonl, "a", encoding="utf-8", newline="\n") as fh:
            fh.write('{"ts":"2026-10-15T01:0')
        result = self._ingest()
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(len(self._quarantine_rows()), 1)

    def test_quarantine_is_idempotent_when_cursor_is_lost(self):
        self._write(['{"ts":"2026-10-15T01:0' + _rec("glued", "general-purpose"), _rec("a3", "general-purpose")])
        self._ingest()
        self.cursor.unlink()
        result = self._ingest()
        self.assertEqual(result["quarantined"], 0)
        self.assertEqual(len(self._quarantine_rows()), 1)
        self.assertEqual(len(self._task_events()), 1)

    # --- dedupe key: identical raw lines at distinct offsets ------------------
    def test_byte_identical_lines_are_both_ingested(self):
        line = _rec("dup", "general-purpose", "same bytes")
        self._write([line, line])
        result = self._ingest()
        self.assertEqual(result["ingested"], 2)
        self.assertEqual(result["duplicates"], 0)
        self.assertEqual(len(self._task_events()), 2)

    def test_legacy_raw_hash_rows_still_suppress_replay(self):
        # Rows written before the offset-keyed dedupe carry sha256(raw) as event_id.
        # After a lost cursor they must still prevent a duplicate append.
        import hashlib
        line = _rec("old", "general-purpose")
        self._write([line])
        api = StateAPI(resolve_db_path(self.state, "tracker"))
        try:
            api.append("task", "task_event", dict(json.loads(line), event_id=hashlib.sha256(line.encode()).hexdigest(), kind="agent", agent_type="general-purpose"), "task_ingest")
        finally:
            api.close()
        result = self._ingest()
        self.assertEqual(result["ingested"], 0)
        self.assertEqual(len(self._task_events()), 1)

    # --- agent lifecycle stream (agent_lifecycle) -----------------------------
    def _lifecycle_events(self):
        api = StateAPI(resolve_db_path(self.state, "tracker"))
        try:
            return api.get("agent_lifecycle")
        finally:
            api.close()

    def _lifecycle_agents(self):
        return {a["id"]: a for a in project_agent_lifecycle(self._lifecycle_events())["agents"]}

    def test_lane_stop_projects_agent_done(self):
        self._write([_rec("lane1", "general-purpose", "lane work", ts="2026-10-15T01:00:00.000Z")])
        self._ingest()
        agents = self._lifecycle_agents()
        self.assertEqual(set(agents), {"lane1"})
        self.assertEqual(agents["lane1"]["state"], "done")
        self.assertEqual(agents["lane1"]["transitions"], [{"state": "done", "at": "2026-10-15T01:00:00.000Z"}])
        self.assertEqual(len(self._lifecycle_events()), 1)

    def test_second_ingest_adds_zero_lifecycle_rows(self):
        self._write([_rec("lane1", "general-purpose")])
        self._ingest()
        self.cursor.unlink()  # lost cursor forces a full re-scan through the dedupe
        result = self._ingest()
        self.assertEqual(result["ingested"], 0)
        self.assertEqual(len(self._lifecycle_events()), 1)
        self.assertEqual(self._lifecycle_agents()["lane1"]["state"], "done")

    def test_error_stop_reason_projects_agent_stalled(self):
        self._write([_rec("lane2", "general-purpose", stop_reason="error")])
        self._ingest()
        self.assertEqual(self._lifecycle_agents()["lane2"]["state"], "stalled")

    def test_internal_records_emit_no_lifecycle_rows(self):
        self._write([_rec("helper", "", "/mcp")])
        self._ingest()
        self.assertEqual(self._lifecycle_events(), [])

    def test_lifecycle_row_emitted_when_task_row_already_present(self):
        # Crash between the task append and the lifecycle append leaves a task row
        # with no lifecycle row. A re-run must emit the lifecycle row exactly once.
        line = _rec("lane3", "general-purpose")
        self._write([line])
        api = StateAPI(resolve_db_path(self.state, "tracker"))
        try:
            api.append("task", "task_event", dict(json.loads(line), event_id=ite._event_id(line.encode(), 0), kind="agent"), "task_ingest")
        finally:
            api.close()
        self._ingest()
        self._ingest()
        self.assertEqual(len(self._lifecycle_events()), 1)
        self.assertEqual(self._lifecycle_agents()["lane3"]["state"], "done")

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
