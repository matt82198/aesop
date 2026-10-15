"""Tests for the staleness surface of tools/ingest_task_events.py.

Covers: ingest(max_bytes=...) bounded runs, status() freshness math
(heartbeat age vs threshold, lag bytes vs cap, missing heartbeat, cursor past
EOF, missing source), the --status CLI exit codes, and that --status is
read-only.

Hermetic: every test uses a temp dir for the state and the JSONL. Nothing
touches ~/.claude/state or the live aesop state.

Run: python -m unittest tests.test_ingest_status
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

from tools import ingest_task_events as ite  # noqa: E402

SCRIPT = str(REPO_ROOT / "tools" / "ingest_task_events.py")
NOW = 1_800_000_000


def _rec(agent_id, summary="working"):
    return json.dumps({
        "ts": "2026-10-15T01:00:00.000Z",
        "session_id": "s1",
        "agent_id": agent_id,
        "agent_type": "general-purpose",
        "stop_reason": None,
        "summary": summary,
        "context_used_pct": 10,
    })


class IngestStatusTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.jsonl = self.root / "task-events.jsonl"
        self.cursor = self.state / ite.CURSOR_FILENAME
        self.heartbeat = self.state / ite.HEARTBEAT_FILENAME

    def tearDown(self):
        self._tmp.cleanup()

    def _write_lines(self, n):
        with open(self.jsonl, "a", encoding="utf-8", newline="\n") as fh:
            for i in range(n):
                fh.write(_rec(f"a{i}") + "\n")

    def _set_heartbeat(self, epoch):
        self.heartbeat.write_text(f"{epoch}\n", encoding="utf-8")

    def _set_cursor(self, offset):
        self.cursor.write_text(json.dumps({"offset": offset, "source": str(self.jsonl)}), encoding="utf-8")

    def _source_bytes(self):
        return self.jsonl.stat().st_size

    # --- bounded ingest (max_bytes) -----------------------------------------
    def test_max_bytes_bounds_one_run_then_catches_up(self):
        self._write_lines(5)
        line_len = len(_rec("a0").encode("utf-8")) + 1
        first = ite.ingest(self.jsonl, self.state, max_bytes=line_len * 2)
        self.assertEqual(first["ingested"], 2)
        self.assertEqual(first["cursor"], line_len * 2)
        self.assertEqual(first["exit_code"], ite.EXIT_OK)
        second = ite.ingest(self.jsonl, self.state)
        self.assertEqual(second["ingested"], 3)
        self.assertEqual(second["cursor"], self._source_bytes())

    def test_max_bytes_smaller_than_one_line_ingests_nothing_cleanly(self):
        self._write_lines(2)
        result = ite.ingest(self.jsonl, self.state, max_bytes=1)
        self.assertEqual(result["ingested"], 0)
        self.assertEqual(result["cursor"], 0)
        self.assertEqual(result["exit_code"], ite.EXIT_OK)

    def test_cli_max_bytes_flag_is_honoured(self):
        self._write_lines(3)
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--jsonl", str(self.jsonl), "--state-dir", str(self.state), "--max-bytes", "1"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ingested=0", proc.stdout)

    # --- freshness math ------------------------------------------------------
    def test_fresh_when_heartbeat_recent_and_no_lag(self):
        self._write_lines(2)
        ite.ingest(self.jsonl, self.state)
        st = ite.status(self.jsonl, self.state, now=self._heartbeat_epoch() + 10)
        self.assertFalse(st["stale"], st)
        self.assertEqual(st["state"], "fresh")
        self.assertEqual(st["lag_bytes"], 0)
        self.assertEqual(st["heartbeat_age_s"], 10)

    def test_stale_when_heartbeat_older_than_threshold(self):
        self._write_lines(1)
        self._set_cursor(self._source_bytes())
        self._set_heartbeat(NOW - ite.STALE_HEARTBEAT_SECONDS - 1)
        st = ite.status(self.jsonl, self.state, now=NOW)
        self.assertTrue(st["stale"], st)
        self.assertEqual(st["state"], "STALE")
        self.assertTrue(any("heartbeat" in r for r in st["reasons"]), st["reasons"])

    def test_heartbeat_exactly_at_threshold_is_not_stale(self):
        self._write_lines(1)
        self._set_cursor(self._source_bytes())
        self._set_heartbeat(NOW - ite.STALE_HEARTBEAT_SECONDS)
        st = ite.status(self.jsonl, self.state, now=NOW)
        self.assertFalse(st["stale"], st)

    def test_stale_when_lag_exceeds_cap(self):
        self._write_lines(3)
        self._set_heartbeat(NOW)
        st = ite.status(self.jsonl, self.state, now=NOW, lag_cap_bytes=10)
        self.assertTrue(st["stale"], st)
        self.assertEqual(st["lag_bytes"], self._source_bytes())
        self.assertTrue(any("lag" in r for r in st["reasons"]), st["reasons"])

    def test_lag_exactly_at_cap_is_not_stale(self):
        self._write_lines(3)
        self._set_heartbeat(NOW)
        st = ite.status(self.jsonl, self.state, now=NOW, lag_cap_bytes=self._source_bytes())
        self.assertFalse(st["stale"], st)

    def test_missing_heartbeat_is_stale(self):
        self._write_lines(1)
        self._set_cursor(self._source_bytes())
        st = ite.status(self.jsonl, self.state, now=NOW)
        self.assertTrue(st["stale"], st)
        self.assertIsNone(st["heartbeat_age_s"])

    def test_cursor_past_eof_is_stale(self):
        self._write_lines(1)
        self._set_cursor(self._source_bytes() + 500)
        self._set_heartbeat(NOW)
        st = ite.status(self.jsonl, self.state, now=NOW)
        self.assertTrue(st["stale"], st)

    def test_missing_source_is_no_source_not_stale(self):
        st = ite.status(self.jsonl, self.state, now=NOW)
        self.assertFalse(st["stale"], st)
        self.assertEqual(st["state"], "NO-SOURCE")

    def test_status_line_carries_the_numbers(self):
        self._write_lines(2)
        self._set_heartbeat(NOW)
        st = ite.status(self.jsonl, self.state, now=NOW)
        line = st["line"]
        self.assertTrue(line.startswith("INGEST: fresh"), line)
        for token in ("cursor=0", f"source_bytes={self._source_bytes()}", f"lag_bytes={self._source_bytes()}", "heartbeat_age_s=0"):
            self.assertIn(token, line)

    def test_status_is_read_only(self):
        self._write_lines(2)
        self._set_cursor(0)
        self._set_heartbeat(NOW - 5000)
        before = (self.cursor.read_bytes(), self.heartbeat.read_bytes())
        ite.status(self.jsonl, self.state, now=NOW)
        after = (self.cursor.read_bytes(), self.heartbeat.read_bytes())
        self.assertEqual(before, after)
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), sorted([self.cursor.name, self.heartbeat.name]))

    # --- CLI exit codes ------------------------------------------------------
    def _status_cli(self):
        return subprocess.run(
            [sys.executable, SCRIPT, "--status", "--jsonl", str(self.jsonl), "--state-dir", str(self.state)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )

    def test_status_cli_fresh_exits_0(self):
        self._write_lines(1)
        ite.ingest(self.jsonl, self.state)
        proc = self._status_cli()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("INGEST: fresh", proc.stdout)

    def test_status_cli_stale_exits_1(self):
        self._write_lines(1)
        self._set_cursor(self._source_bytes())
        self._set_heartbeat(0)
        proc = self._status_cli()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("INGEST: STALE", proc.stdout)

    def test_status_cli_no_source_exits_0(self):
        proc = self._status_cli()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("NO-SOURCE", proc.stdout)

    def _heartbeat_epoch(self):
        return int(self.heartbeat.read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
