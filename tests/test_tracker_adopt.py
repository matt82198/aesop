"""Explicit, provenance-stamped adopt mode for disk-only tracker items.

Defect: tracker.json held items written straight to disk by an external script
(no item_created event). Every projection render was refused by the OCC guard
("Unexplained disk state"), and no sanctioned path could bring those items into
the event log. WriteAPI.tracker_adopt_disk_items() and
tools/tracker_guard.py --adopt-disk-items is that path: fail-closed, requires an
explicit reason, stamps provenance, and verifies the render before it lands.

All fixtures live in temp dirs; the live store under aesop/state is never touched.

Run: python -m unittest tests.test_tracker_adopt -v
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tracker_guard  # noqa: E402
from state_store.write_api import WriteAPI, WriteConflict  # noqa: E402
from state_store.store import EventStore  # noqa: E402
from state_store.projections import project_tracker_with_snapshot  # noqa: E402
from state_store.materialize import materialize_tracker  # noqa: E402

PROVENANCE_KEYS = {"adopted_from_disk", "adopt_reason", "adopt_actor", "adopt_ts"}
ADOPT_REASON = "orphaned by external autofiler; migrating to event log"


def _disk_item(item_id, status="open", **extra):
    item = {
        "id": item_id,
        "title": f"Disk item {item_id}",
        "priority": "P2",
        "status": status,
        "lane": "proposed",
        "source": "cmc_error_autofiler",
        "tags": ["cmc-error"],
        "notes": "planted by external script",
        "pr_link": None,
        "created_at": "2026-10-15T01:02:03Z",
        "completed_at": None,
    }
    item.update(extra)
    return item


class TrackerAdoptTestBase(unittest.TestCase):
    """Isolated temp state root with a hand-planted disk-only item."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="tracker-adopt-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)
        self._saved_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

    def tearDown(self):
        if self._saved_root is None:
            os.environ.pop("AESOP_STATE_ROOT", None)
        else:
            os.environ["AESOP_STATE_ROOT"] = self._saved_root
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    # -- fixture helpers -------------------------------------------------
    def plant_disk_items(self, items):
        """Write tracker.json directly, the way the external autofiler does."""
        payload = {"version": 1, "items": items}
        (self.state_dir / "tracker.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def tracker_bytes(self):
        return (self.state_dir / "tracker.json").read_bytes()

    def disk_items(self):
        return json.loads(self.tracker_bytes().decode("utf-8"))["items"]

    def tracker_events(self):
        store = EventStore(str(self.state_dir / "tracker_events.db"))
        try:
            return store.read("tracker")
        finally:
            store.close()

    def event_count(self):
        db = self.state_dir / "tracker_events.db"
        if not db.exists():
            return 0
        return len(self.tracker_events())

    def rendered_from_log(self):
        """Render tracker.json bytes exactly as WriteAPI would from the event log."""
        store = EventStore(str(self.state_dir / "tracker_events.db"))
        try:
            events = store.read("tracker")
            projection = project_tracker_with_snapshot(store, "tracker", events)
        finally:
            store.close()
        return materialize_tracker(projection)

    def assert_rendered_matches_disk_item(self, disk_item, rendered_item):
        """Disk fields must match exactly; only provenance keys may be extra."""
        for key, value in disk_item.items():
            self.assertIn(key, rendered_item, f"rendered item lost disk field {key!r}")
            self.assertEqual(rendered_item[key], value, f"field {key!r} drifted")
        extra = set(rendered_item) - set(disk_item)
        self.assertEqual(extra, PROVENANCE_KEYS, f"unexpected extra fields {extra}")


class TestRenderRefusedBeforeAdopt(TrackerAdoptTestBase):

    def test_render_refused_with_remedy_pointer_when_disk_item_unexplained(self):
        self.plant_disk_items([_disk_item("cmc-err-planted-1")])
        api = WriteAPI(self.state_dir)
        try:
            with self.assertRaises(WriteConflict) as ctx:
                api.tracker_append_item({"title": "fresh item"}, actor="test")
        finally:
            api.close()
        message = str(ctx.exception)
        self.assertIn("Unexplained disk state", message)
        self.assertIn("cmc-err-planted-1", message)
        self.assertIn("tracker_guard.py --adopt-disk-items --dry-run", message)


class TestDryRun(TrackerAdoptTestBase):

    def test_dry_run_lists_disk_only_item_and_writes_nothing(self):
        # Seed a log so the event store exists before the dry run.
        api = WriteAPI(self.state_dir)
        try:
            api.tracker_append_item({"id": "already-logged-1", "title": "logged"}, actor="test")
        finally:
            api.close()
        self.plant_disk_items(
            self.disk_items() + [_disk_item("cmc-err-planted-2")]
        )
        bytes_before = self.tracker_bytes()
        events_before = self.event_count()

        api = WriteAPI(self.state_dir)
        try:
            plan = api.tracker_adopt_disk_items(
                actor="test", reason=ADOPT_REASON, dry_run=True
            )
        finally:
            api.close()

        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["adopted_ids"], ["cmc-err-planted-2"])
        self.assertEqual(self.tracker_bytes(), bytes_before)
        self.assertEqual(self.event_count(), events_before)


class TestAdoptRefusals(TrackerAdoptTestBase):

    def test_adopt_without_reason_is_refused_and_writes_nothing(self):
        self.plant_disk_items([_disk_item("cmc-err-planted-3")])
        bytes_before = self.tracker_bytes()
        api = WriteAPI(self.state_dir)
        try:
            with self.assertRaises(ValueError):
                api.tracker_adopt_disk_items(actor="test", reason="")
            with self.assertRaises(ValueError):
                api.tracker_adopt_disk_items(actor="test", reason="   ")
            with self.assertRaises(ValueError):
                api.tracker_adopt_disk_items(actor="test", reason=None)
        finally:
            api.close()
        self.assertEqual(self.tracker_bytes(), bytes_before)
        self.assertEqual(self.event_count(), 0)

    def test_adopting_id_already_in_log_is_refused(self):
        api = WriteAPI(self.state_dir)
        try:
            api.tracker_append_item({"id": "logged-item-9", "title": "logged"}, actor="test")
            events_before = self.event_count()
            with self.assertRaises(ValueError):
                api.tracker_adopt_disk_items(
                    actor="test", reason=ADOPT_REASON, ids=["logged-item-9"]
                )
        finally:
            api.close()
        self.assertEqual(self.event_count(), events_before)

    def test_explicit_ids_that_are_not_disk_only_fail_the_whole_adopt(self):
        self.plant_disk_items([_disk_item("cmc-err-planted-4")])
        events_before = self.event_count()
        api = WriteAPI(self.state_dir)
        try:
            with self.assertRaises(ValueError):
                api.tracker_adopt_disk_items(
                    actor="test",
                    reason=ADOPT_REASON,
                    ids=["cmc-err-planted-4", "no-such-item"],
                )
        finally:
            api.close()
        self.assertEqual(self.event_count(), events_before)


class TestAdoptExecution(TrackerAdoptTestBase):

    def test_adopt_appends_exactly_one_item_created_with_provenance(self):
        self.plant_disk_items([_disk_item("cmc-err-planted-5")])
        api = WriteAPI(self.state_dir)
        try:
            result = api.tracker_adopt_disk_items(actor="tester", reason=ADOPT_REASON)
        finally:
            api.close()

        self.assertEqual(result["adopted_ids"], ["cmc-err-planted-5"])
        events = self.tracker_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "item_created")
        payload = events[0]["payload"]
        self.assertEqual(payload["id"], "cmc-err-planted-5")
        self.assertIs(payload["adopted_from_disk"], True)
        self.assertEqual(payload["adopt_reason"], ADOPT_REASON)
        self.assertEqual(payload["adopt_actor"], "tester")
        self.assertTrue(payload["adopt_ts"])
        self.assertEqual(events[0]["actor"], "tester")

    def test_render_succeeds_after_adopt_and_state_rebuild_style_check_is_clean(self):
        self.plant_disk_items([_disk_item("cmc-err-planted-6")])
        api = WriteAPI(self.state_dir)
        try:
            api.tracker_adopt_disk_items(actor="tester", reason=ADOPT_REASON)
            # A later ordinary write must now render without the OCC refusal.
            api.tracker_append_item({"id": "fresh-after-adopt", "title": "fresh"}, actor="test")
        finally:
            api.close()

        self.assertEqual(self.tracker_bytes(), self.rendered_from_log())
        ids = {item["id"] for item in self.disk_items()}
        self.assertIn("cmc-err-planted-6", ids)
        self.assertIn("fresh-after-adopt", ids)

    def test_open_item_adopt_renders_disk_record_exactly(self):
        disk = _disk_item("cmc-err-planted-7", status="open")
        self.plant_disk_items([disk])
        api = WriteAPI(self.state_dir)
        try:
            api.tracker_adopt_disk_items(actor="tester", reason=ADOPT_REASON)
        finally:
            api.close()
        rendered = {i["id"]: i for i in self.disk_items()}["cmc-err-planted-7"]
        self.assert_rendered_matches_disk_item(disk, rendered)

    def test_done_status_round_trips_exactly(self):
        disk = _disk_item(
            "cmc-err-planted-8",
            status="done",
            completed_at="2026-10-14T09:00:00Z",
        )
        self.plant_disk_items([disk])
        api = WriteAPI(self.state_dir)
        try:
            api.tracker_adopt_disk_items(actor="tester", reason=ADOPT_REASON)
        finally:
            api.close()
        rendered = {i["id"]: i for i in self.disk_items()}["cmc-err-planted-8"]
        self.assert_rendered_matches_disk_item(disk, rendered)
        types = [e["type"] for e in self.tracker_events()]
        self.assertEqual(types, ["item_created", "item_updated"])

    def test_closed_foreign_status_round_trips_exactly(self):
        disk = _disk_item(
            "cmc-err-planted-9",
            status="closed-foreign",
            completed_at="2026-10-13T12:30:00Z",
        )
        self.plant_disk_items([disk])
        api = WriteAPI(self.state_dir)
        try:
            api.tracker_adopt_disk_items(actor="tester", reason=ADOPT_REASON)
        finally:
            api.close()
        rendered = {i["id"]: i for i in self.disk_items()}["cmc-err-planted-9"]
        self.assert_rendered_matches_disk_item(disk, rendered)


class TestTrackerGuardAdoptCli(TrackerAdoptTestBase):

    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = tracker_guard.main(argv)
        return code, out.getvalue()

    def test_cli_dry_run_prints_plan_json_and_exits_zero(self):
        self.plant_disk_items([_disk_item("cmc-err-planted-10")])
        bytes_before = self.tracker_bytes()
        code, out = self._run(["--adopt-disk-items", "--dry-run"])
        self.assertEqual(code, 0)
        plan = json.loads(out)
        self.assertEqual(plan["adopted_ids"], ["cmc-err-planted-10"])
        self.assertEqual(self.tracker_bytes(), bytes_before)

    def test_cli_adopt_without_reason_exits_two(self):
        self.plant_disk_items([_disk_item("cmc-err-planted-11")])
        code, out = self._run(["--adopt-disk-items"])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(out)["ok"])
        self.assertEqual(self.event_count(), 0)

    def test_cli_partial_ids_refused_before_any_write(self):
        # Adopting only 12 would leave 13 disk-only, and the render would then
        # silently drop it. The adopt must refuse before appending anything.
        self.plant_disk_items([_disk_item("cmc-err-planted-12"), _disk_item("cmc-err-planted-13")])
        bytes_before = self.tracker_bytes()
        code, out = self._run([
            "--adopt-disk-items",
            "--reason", ADOPT_REASON,
            "--ids", "cmc-err-planted-12",
        ])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(out)["ok"])
        self.assertEqual(self.event_count(), 0)
        self.assertEqual(self.tracker_bytes(), bytes_before)

    def test_cli_adopt_with_reason_and_all_ids_exits_zero(self):
        self.plant_disk_items([_disk_item("cmc-err-planted-12"), _disk_item("cmc-err-planted-13")])
        code, out = self._run([
            "--adopt-disk-items",
            "--reason", ADOPT_REASON,
            "--ids", "cmc-err-planted-12,cmc-err-planted-13",
        ])
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertTrue(result["ok"])
        self.assertEqual(result["adopted_ids"], ["cmc-err-planted-12", "cmc-err-planted-13"])
        ids_logged = [e["payload"]["id"] for e in self.tracker_events()]
        self.assertEqual(ids_logged, ["cmc-err-planted-12", "cmc-err-planted-13"])

    def test_cli_reconcile_journal_still_available(self):
        code, _ = self._run(["--reconcile-journal"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
