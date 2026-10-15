"""Tests for tools/goal_state.py -- the orchestrator GOAL recorded as event-store events.

Contract under test:
  - `set` appends one `goal_set` event to stream `orchestrator_goal` through the real
    StateAPI append path (never a plain file copy, never a patch).
  - `show` prints the projection of the latest goal event as a short markdown block.
  - `history --limit N` prints the last N goal events, oldest of those first.
  - Setting the same goal twice appends two events (idempotent-by-content is NOT a goal;
    append-only is).
  - Input validation fails closed before anything is appended.
  - An unreadable store exits 2 with one stderr line and no stdout.

Isolation: every run uses a temp state dir passed explicitly AND pinned through
AESOP_STATE_ROOT in the child environment, so the live store is never touched.

Run: python -m unittest tests.test_goal_state
"""
import os
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
from tools import goal_state  # noqa: E402

TOOL = REPO_ROOT / "tools" / "goal_state.py"


def _run(args, state_dir):
    """Run the goal_state CLI in a child process pinned to ``state_dir``."""
    env = dict(os.environ)
    env["AESOP_STATE_ROOT"] = str(state_dir)
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
    )


class GoalStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name) / "state"
        self.state_dir.mkdir()
        self.db = resolve_db_path(self.state_dir, "tracker")

    def tearDown(self):
        self._tmp.cleanup()

    def _stream(self):
        with StateAPI(self.db) as api:
            return api.get(goal_state.GOAL_STREAM)

    def test_set_show_round_trip(self):
        r = _run(
            ["set", "--goal", "Ship boot brief", "--phase", "build",
             "--next-action", "run tests", "--ref", "PR #1044", "--ref", "sha:abc123",
             "--session", "sess-7"],
            self.state_dir,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        events = self._stream()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "goal_set")
        self.assertEqual(events[0]["payload"]["goal"], "Ship boot brief")
        self.assertEqual(events[0]["payload"]["refs"], ["PR #1044", "sha:abc123"])
        self.assertEqual(events[0]["payload"]["session_id"], "sess-7")

        s = _run(["show"], self.state_dir)
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertIn("GOAL: Ship boot brief", s.stdout)
        self.assertIn("phase: build", s.stdout)
        self.assertIn("next_action: run tests", s.stdout)
        self.assertIn("PR #1044", s.stdout)
        self.assertIn("sha:abc123", s.stdout)
        self.assertIn("sess-7", s.stdout)

    def test_history_prints_last_n_oldest_first(self):
        for g in ("goal one", "goal two", "goal three"):
            self.assertEqual(_run(["set", "--goal", g], self.state_dir).returncode, 0)
        h = _run(["history", "--limit", "2"], self.state_dir)
        self.assertEqual(h.returncode, 0, h.stderr)
        self.assertNotIn("goal one", h.stdout)
        self.assertLess(h.stdout.index("goal two"), h.stdout.index("goal three"))

        h_all = _run(["history", "--limit", "10"], self.state_dir)
        self.assertLess(h_all.stdout.index("goal one"), h_all.stdout.index("goal two"))
        self.assertLess(h_all.stdout.index("goal two"), h_all.stdout.index("goal three"))

    def test_same_goal_twice_appends_two_events(self):
        for _ in range(2):
            self.assertEqual(_run(["set", "--goal", "Same goal"], self.state_dir).returncode, 0)
        events = self._stream()
        self.assertEqual([e["version"] for e in events], [1, 2])
        self.assertEqual(events[0]["payload"]["goal"], events[1]["payload"]["goal"])

    def test_append_only_first_event_never_patched(self):
        _run(["set", "--goal", "first"], self.state_dir)
        before = self._stream()[0]["payload"]
        _run(["set", "--goal", "second"], self.state_dir)
        after = self._stream()
        self.assertEqual(after[0]["payload"], before)
        self.assertEqual(after[1]["payload"]["goal"], "second")

    def test_show_without_goal_says_none_recorded(self):
        # An existing but goal-less store (other streams only) is the no-goal case.
        with StateAPI(self.db) as api:
            api.append("task", "task_event", {"summary": "unrelated"})
        s = _run(["show"], self.state_dir)
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertEqual(s.stdout.strip(), "GOAL: none recorded")

    def test_multiline_goal_rejected_nothing_appended(self):
        r = _run(["set", "--goal", "line one\nline two"], self.state_dir)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self._stream(), [])

    def test_empty_goal_rejected_nothing_appended(self):
        r = _run(["set", "--goal", "   "], self.state_dir)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self._stream(), [])

    def test_unreadable_store_exits_2_on_show(self):
        bad = Path(self._tmp.name) / "bad"
        bad.mkdir()
        Path(resolve_db_path(bad, "tracker")).write_bytes(b"this is not sqlite" * 50)
        r = _run(["show"], bad)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)

    def test_missing_store_exits_2_on_show(self):
        empty = Path(self._tmp.name) / "nothing-here"
        r = _run(["show"], empty)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")

    def test_project_goal_uses_latest_version(self):
        events = [
            {"version": 1, "type": "goal_set", "payload": {"goal": "a", "phase": "", "next_action": "",
                                                          "refs": [], "set_at": "t1", "session_id": None}},
            {"version": 2, "type": "goal_set", "payload": {"goal": "b", "phase": "p", "next_action": "n",
                                                          "refs": ["r"], "set_at": "t2", "session_id": "s"}},
        ]
        proj = goal_state.project_goal(events)
        self.assertEqual(proj["goal"], "b")
        self.assertEqual(proj["version"], 2)
        self.assertIsNone(goal_state.project_goal([]))


if __name__ == "__main__":
    unittest.main()
