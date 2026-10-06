"""Core-purity regression tests for tracker_guard.py (event-sourced --enforce).

FINDING (proven on a read-only copy of the real log, 355 tracker events):
tools/tracker_guard.py's cmd_enforce() used to close zombie items by patching
state/tracker.json directly, WITHOUT appending an item_updated event to the
event log. That made the revert correct only until the next full replay (ANY
WriteAPI call anywhere re-renders the WHOLE tracker.json FROM the event log),
which silently resurrected the same items -- and made
`python tools/state_rebuild.py --check` report "tracker.json: DRIFT DETECTED"
for the same reason (state_store/CLAUDE.md: "every writer appends events;
projections are never patched").

This module tests two things:
  1. --enforce now closes items THROUGH state_store.write_api.WriteAPI, so the
     closure survives a full replay and state_rebuild --check sees no drift.
  2. --reconcile-journal backfills the missing item_updated events for history
     the OLD direct-patch --enforce already produced, idempotently.

Run: python -m unittest tests.test_tracker_guard_events -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tracker_guard  # noqa: E402
from state_store.write_api import WriteAPI  # noqa: E402
from state_store.store import EventStore  # noqa: E402
from state_store.projections import project_tracker  # noqa: E402


def _tracker_only_drift(state_dir):
    """Check ONLY tracker.json for drift, via the real state_rebuild internals
    (not a proxy): avoids false positives from unrelated views (STATE.md,
    ledger.md) that these fixtures never render. Returns True if tracker.json
    drifts from the event-store projection.
    """
    import state_rebuild
    from state_store import StateAPI

    db_path = state_dir / "tracker_events.db"
    api = StateAPI(str(db_path))
    try:
        return state_rebuild._check_tracker(api, state_dir)
    finally:
        api.close()


def _run_state_rebuild_check(state_dir):
    """Invoke the real state_rebuild.py --check CLI gate as a subprocess.

    Returns (returncode, stdout, stderr). This is the actual CI drift gate,
    not a proxy -- the test proves the real gate is satisfied, not a stand-in.
    """
    env = dict(os.environ)
    env["AESOP_STATE_ROOT"] = str(state_dir)
    proc = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "state_rebuild.py"), "--check",
         "--state-root", str(state_dir)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TrackerGuardEventsTestBase(unittest.TestCase):
    """Isolated temp state root; never touches the repo's own state/."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="tracker-guard-events-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)

        self._saved_aesop_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

    def tearDown(self):
        if self._saved_aesop_state_root is None:
            os.environ.pop("AESOP_STATE_ROOT", None)
        else:
            os.environ["AESOP_STATE_ROOT"] = self._saved_aesop_state_root
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def read_tracker(self):
        tracker_file = self.state_dir / "tracker.json"
        return json.loads(tracker_file.read_text(encoding="utf-8"))

    def event_store_lane(self, item_id):
        """Read an item's lane from the event-store-ONLY projection (ignores tracker.json)."""
        db_path = self.state_dir / "tracker_events.db"
        store = EventStore(str(db_path))
        try:
            events = store.read("tracker")
        finally:
            store.close()
        items = {i["id"]: i.get("lane") for i in project_tracker(events).get("items", [])}
        return items.get(item_id)

    def count_tracker_events(self):
        db_path = self.state_dir / "tracker_events.db"
        if not db_path.exists():
            return 0
        store = EventStore(str(db_path))
        try:
            return len(store.read("tracker"))
        finally:
            store.close()


class TestEnforceWritesThroughEventLog(TrackerGuardEventsTestBase):
    """Red-first: a zombie revert must survive a full replay triggered by an
    UNRELATED later write, and leave state_rebuild --check clean."""

    def test_enforce_closure_survives_unrelated_full_replay(self):
        """--enforce's revert must not be undone by a later, unrelated WriteAPI write.

        This reproduces the exact mechanism of the incident: cmd_enforce patching
        tracker.json directly (without an event) is correct only until the NEXT
        full replay. Any later WriteAPI call anywhere re-renders the WHOLE
        tracker.json from the event log, so if the revert was never journaled as
        an event, the zombie resurrects. FAILS against the pre-fix cmd_enforce.
        """
        api = WriteAPI(self.state_dir)
        try:
            api.tracker_append_item(
                {"id": "zombie-events-1", "title": "Resurrected Item", "lane": "ranked"},
                actor="test",
            )
        finally:
            api.close()

        # Journal shows the item previously reached the terminal lane "done",
        # and is currently back in an active lane ("ranked") -- a zombie.
        journal_file = self.state_dir / "tracker-journal.jsonl"
        with open(journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.utcnow().isoformat(),
                "id": "zombie-events-1", "from": None, "to": "done",
            }) + "\n")

        exit_code = tracker_guard.main(["--enforce"])
        self.assertEqual(exit_code, 0, "--enforce should exit 0")

        tracker = self.read_tracker()
        lane_immediately_after = next(
            i["lane"] for i in tracker["items"] if i["id"] == "zombie-events-1"
        )
        self.assertEqual(lane_immediately_after, "done",
                          "item should be reverted to done immediately after --enforce")

        # Simulate "the next full replay" -- an UNRELATED write through WriteAPI,
        # which re-renders the ENTIRE tracker.json from the event log.
        api2 = WriteAPI(self.state_dir)
        try:
            api2.tracker_append_item({"title": "unrelated trigger item"}, actor="test")
        finally:
            api2.close()

        tracker_after_replay = self.read_tracker()
        lane_after_replay = next(
            i["lane"] for i in tracker_after_replay["items"] if i["id"] == "zombie-events-1"
        )
        self.assertEqual(
            lane_after_replay, "done",
            "zombie revert must survive a full replay triggered by an unrelated "
            "write -- it resurrected to the event log's stale lane, proving the "
            "revert was never appended as an event",
        )

    def test_enforce_leaves_no_drift_for_state_rebuild_check(self):
        """After --enforce, the real `state_rebuild.py --check` CI gate must pass.

        DRIFT DETECTED for tracker.json is the second symptom of the same bug:
        the projection (from events) disagreeing with the materialized file.
        """
        api = WriteAPI(self.state_dir)
        try:
            api.tracker_append_item(
                {"id": "zombie-events-2", "title": "Another Zombie", "lane": "in-progress"},
                actor="test",
            )
        finally:
            api.close()

        journal_file = self.state_dir / "tracker-journal.jsonl"
        with open(journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.utcnow().isoformat(),
                "id": "zombie-events-2", "from": None, "to": "rejected",
            }) + "\n")

        exit_code = tracker_guard.main(["--enforce"])
        self.assertEqual(exit_code, 0)

        self.assertFalse(
            _tracker_only_drift(self.state_dir),
            "state_rebuild must report no drift for tracker.json after --enforce",
        )

        # Also exercise the real CLI gate end-to-end (not just its internals),
        # after seeding the other canonical views so only tracker.json's
        # verdict is meaningful here.
        rc_all = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "state_rebuild.py"), "--all",
             "--state-root", str(self.state_dir)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(rc_all.returncode, 0, rc_all.stdout + rc_all.stderr)

        returncode, stdout, stderr = _run_state_rebuild_check(self.state_dir)
        self.assertNotIn("tracker.json: DRIFT DETECTED", stdout + stderr)


class TestReconcileJournal(TrackerGuardEventsTestBase):
    """--reconcile-journal migrates history the OLD direct-patch --enforce lost."""

    def _simulate_old_buggy_enforce(self, item_id, title, created_lane, patched_lane):
        """Reproduce exactly what the pre-fix cmd_enforce left behind:
        event log still at `created_lane`, tracker.json direct-patched to
        `patched_lane`, and a journal "reverted" entry recording the patch."""
        api = WriteAPI(self.state_dir)
        try:
            api.tracker_append_item(
                {"id": item_id, "title": title, "lane": created_lane}, actor="test"
            )
        finally:
            api.close()

        # Direct patch (the bug): tracker.json says patched_lane, event log
        # still says created_lane.
        tracker_file = self.state_dir / "tracker.json"
        tracker = json.loads(tracker_file.read_text(encoding="utf-8"))
        for item in tracker["items"]:
            if item["id"] == item_id:
                item["lane"] = patched_lane
        tracker_file.write_text(json.dumps(tracker, indent=2), encoding="utf-8")

        journal_file = self.state_dir / "tracker-journal.jsonl"
        with open(journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.utcnow().isoformat(),
                "id": item_id, "from": created_lane, "to": patched_lane,
                "type": "reverted",
            }) + "\n")

    def test_reconcile_backfills_missing_event_once(self):
        """--reconcile-journal appends the missing item_updated event, once."""
        self._simulate_old_buggy_enforce(
            "legacy-zombie-1", "Legacy Zombie", created_lane="ranked", patched_lane="done"
        )

        # Confirm the bug shape is reproduced before reconciling.
        self.assertEqual(self.event_store_lane("legacy-zombie-1"), "ranked")
        self.assertEqual(
            self.read_tracker()["items"][0]["lane"], "done",
            "fixture setup: tracker.json should show the old direct-patch result",
        )

        before_count = self.count_tracker_events()
        exit_code = tracker_guard.main(["--reconcile-journal"])
        self.assertEqual(exit_code, 0)
        after_count = self.count_tracker_events()

        self.assertEqual(after_count, before_count + 1,
                          "reconcile should append exactly one backfill event")
        self.assertEqual(self.event_store_lane("legacy-zombie-1"), "done",
                          "event log should now agree with the journal")

        # Second run: idempotent, zero new events.
        before_second = self.count_tracker_events()
        exit_code2 = tracker_guard.main(["--reconcile-journal"])
        self.assertEqual(exit_code2, 0)
        after_second = self.count_tracker_events()
        self.assertEqual(after_second, before_second,
                          "second reconcile run must append zero new events")

    def test_reconcile_leaves_no_drift_for_state_rebuild_check(self):
        """After reconciling, state_rebuild's own tracker.json drift check must pass.

        Uses the real `state_rebuild._check_tracker` (the exact function the
        `--check` CI gate calls), scoped to tracker.json only, since this
        minimal fixture never renders the unrelated STATE.md/ledger.md views.
        """
        self._simulate_old_buggy_enforce(
            "legacy-zombie-2", "Legacy Zombie 2", created_lane="in-progress", patched_lane="rejected"
        )

        # Prove the bug shape reproduces real drift BEFORE reconciling.
        self.assertTrue(
            _tracker_only_drift(self.state_dir),
            "fixture setup: direct-patch bug shape should drift before reconcile",
        )

        exit_code = tracker_guard.main(["--reconcile-journal"])
        self.assertEqual(exit_code, 0)

        self.assertFalse(
            _tracker_only_drift(self.state_dir),
            "state_rebuild must report no drift for tracker.json after reconcile",
        )

    def test_reconcile_noop_when_no_journaled_reverts(self):
        """No journaled reverts -> no-op, exit 0, no events appended."""
        before = self.count_tracker_events()
        exit_code = tracker_guard.main(["--reconcile-journal"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.count_tracker_events(), before)


if __name__ == "__main__":
    unittest.main()
