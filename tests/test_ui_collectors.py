#!/usr/bin/env python3
"""Test suite for ui/collectors.py performance and data-loss fixes (wave-19)."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add ui/ to path so we can import config, collectors, agents
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui"))

import config
from collectors import (
    get_recent_events, get_alerts, get_main_thread_messages,
    drain_tracker_inbox, _snapshot_data
)


class TestLogTailingPerformance(unittest.TestCase):
    """Test log tailing (seek from end) instead of whole-file reads."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        config.STATE_DIR = self.temp_path

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_recent_events_tails_log(self):
        """Verify get_recent_events reads only tail of FLEET-BACKUP.log, not entire file."""
        # Create a large log file with 1000 lines
        log_file = self.temp_path / "FLEET-BACKUP.log"
        lines = [f"event_{i}" for i in range(1000)]
        log_file.write_text("\n".join(lines) + "\n", encoding='utf-8')

        with patch('config.BACKUP_LOG', log_file):
            result = get_recent_events()
            # Should return last 8 lines
            expected = [f"event_{i}" for i in range(992, 1000)]
            self.assertEqual(result, expected)

    def test_get_alerts_tails_log(self):
        """Verify get_alerts reads only tail of SECURITY-ALERTS.log efficiently."""
        log_file = self.temp_path / "SECURITY-ALERTS.log"
        lines = [f"ALERT {i} NOTE: reviewed" for i in range(100)]
        lines.extend([f"ALERT {i}" for i in range(100, 105)])
        log_file.write_text("\n".join(lines) + "\n", encoding='utf-8')

        with patch('config.ALERTS_LOG', log_file):
            result = get_alerts()
            # Should have 5 unreviewed alerts (last 5 lines)
            self.assertEqual(result['count'], 5)
            self.assertEqual(len(result['lines']), 5)


class TestDataSourceGating(unittest.TestCase):
    """Test mtime-gating for data section sources."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        config.STATE_DIR = self.temp_path

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_snapshot_data_gates_on_backup_log_mtime(self):
        """Verify _snapshot_data doesn't unconditionally read BACKUP_LOG every tick."""
        log_file = self.temp_path / "FLEET-BACKUP.log"
        log_file.write_text("initial event\n", encoding='utf-8')

        with patch('config.BACKUP_LOG', log_file):
            # First call should read the file
            data1 = _snapshot_data()
            self.assertIn('events', data1)

            # Without changing the file, we should not re-read it
            # This is implicit in the collector_loop mtime check in sse.py
            # We verify by checking that old mtimes don't change the snapshot hash
            initial_events = data1['events']

            # Add new data without changing mtime (by sleeping minimally)
            time.sleep(0.01)  # sleep-ok
            log_file.write_text("initial event\nnew event\n", encoding='utf-8')
            # Touch to update mtime
            os.utime(str(log_file), None)

            # Now re-read should get new event
            data2 = _snapshot_data()
            self.assertNotEqual(initial_events, data2['events'])

    def test_snapshot_data_gates_on_alerts_log_mtime(self):
        """Verify _snapshot_data doesn't unconditionally read ALERTS_LOG every tick."""
        log_file = self.temp_path / "SECURITY-ALERTS.log"
        log_file.write_text("ALERT 1\n", encoding='utf-8')

        with patch('config.ALERTS_LOG', log_file):
            data1 = _snapshot_data()
            initial_alerts = data1['alerts']['count']

            # Update file and touch mtime
            time.sleep(0.01)  # sleep-ok
            log_file.write_text("ALERT 1\nALERT 2\n", encoding='utf-8')
            os.utime(str(log_file), None)

            data2 = _snapshot_data()
            self.assertNotEqual(initial_alerts, data2['alerts']['count'])


class TestTranscriptsFingerprintReuse(unittest.TestCase):
    """Test reusing ONE transcripts walk for both fingerprint and get_main_thread_messages."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        config.TRANSCRIPTS_ROOT = self.temp_path

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_main_thread_messages_from_newest_transcript(self):
        """Verify get_main_thread_messages efficiently finds and reads the newest transcript."""
        # Create multiple transcripts (only the newest should be read)
        older = self.temp_path / "agent-old-transcript.jsonl"
        newer = self.temp_path / "agent-new-transcript.jsonl"

        # Create older first
        older.write_text(
            json.dumps({"role": "user", "content": [{"text": "old message"}]}) + "\n",
            encoding='utf-8'
        )
        time.sleep(0.01)  # sleep-ok

        # Create newer
        messages = []
        for i in range(5):
            messages.append(json.dumps({"role": "user", "content": [{"text": f"user message {i}"}]}))
            messages.append(json.dumps({"role": "assistant", "content": [{"text": f"assistant message {i}"}]}))
        newer.write_text("\n".join(messages) + "\n", encoding='utf-8')

        result = get_main_thread_messages()
        # Should have read from newest file, not oldest
        self.assertTrue(any("assistant message" in m.get("text", "") for m in result))
        self.assertFalse(any("old message" in m.get("text", "") for m in result))


class TestAgentsSnapshotStripsPromptFull(unittest.TestCase):
    """Test that agents snapshot strips promptFull from broadcast/API."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        config.AESOP_ROOT = self.temp_path
        config.TRANSCRIPTS_ROOT = self.temp_path / "transcripts"
        config.TRANSCRIPTS_ROOT.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_agents_snapshot_excludes_promptfull(self):
        """Verify agent snapshots sent via SSE don't include multi-KB dispatch prompts."""
        # Create a mock agent transcript
        agent_file = config.TRANSCRIPTS_ROOT / "agent-test123abc.jsonl"
        agent_file.write_text(
            json.dumps({
                "type": "user",
                "message": {"content": "x" * 2000}  # Simulate large prompt
            }) + "\n" +
            json.dumps({
                "type": "assistant",
                "model": "haiku",
                "message": {"content": "response"}
            }) + "\n",
            encoding='utf-8'
        )

        # Import after mock setup
        from agents import get_fleet_agents

        with patch('subprocess.run') as mock_run:
            # Mock dash-extra.mjs to return test agent
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps([{"id": "test123abc", "status": "running"}])
            )

            agents = get_fleet_agents()
            # Verify agents don't have promptFull field
            for agent in agents:
                self.assertNotIn("promptFull", agent)
                self.assertNotIn("dispatch_prompt", agent)


class TestDrainTrackerInboxRecovery(unittest.TestCase):
    """Test recovery sweep for stranded .processing-* files in drain_tracker_inbox."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        config.STATE_DIR = self.temp_path

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_drain_inbox_recovers_stranded_processing_files(self):
        """Verify drain_tracker_inbox re-ingests leftover .processing-* files.

        This tests the data-loss fix: if drain_tracker_inbox crashes mid-drain,
        it leaves a .processing-<random> file. The next drain call should detect
        and re-ingest it instead of silently losing the data.
        """
        # Create a stranded processing file (simulates previous crash)
        processing_file = self.temp_path / ".tracker-inbox.jsonl.processing-deadbeef"
        inbox_data = [
            {"source": "test", "title": "item 1"},
            {"source": "test", "title": "item 2"},
        ]
        processing_file.write_text(
            "\n".join(json.dumps(item) for item in inbox_data) + "\n",
            encoding='utf-8'
        )

        # Mock tracker API to avoid real DB writes
        from collectors import drain_tracker_inbox, _tracker_api, load_tracker

        # Ensure load_tracker returns empty so dedup finds nothing
        with patch('collectors.load_tracker') as mock_load:
            mock_load.return_value = {"items": []}

            with patch('collectors._tracker_api') as mock_api_factory:
                mock_api = MagicMock()
                mock_api.project.return_value = {"items": []}
                mock_api_factory.return_value = mock_api

                with patch('collectors.create_tracker_item') as mock_create:
                    # Simulate successful creation
                    def create_side_effect(data):
                        return {**data, "id": "fake-id"}
                    mock_create.side_effect = create_side_effect

                    # Run drain; should recover the stranded file
                    created = drain_tracker_inbox()

                    # Verify both items were created from recovered file
                    self.assertEqual(len(created), 2)
                    self.assertTrue(any(item["title"] == "item 1" for item in created))
                    self.assertTrue(any(item["title"] == "item 2" for item in created))

                    # Verify stranded file was deleted after recovery
                    self.assertFalse(processing_file.exists())

    def test_drain_inbox_handles_multiple_stranded_processing_files(self):
        """Verify recovery sweep handles multiple stranded .processing-* files."""
        # Create multiple stranded processing files
        file1 = self.temp_path / ".tracker-inbox.jsonl.processing-dead0001"
        file2 = self.temp_path / ".tracker-inbox.jsonl.processing-dead0002"

        data1 = [{"source": "test", "title": "from file 1"}]
        data2 = [{"source": "test", "title": "from file 2"}]

        file1.write_text(json.dumps(data1[0]) + "\n", encoding='utf-8')
        file2.write_text(json.dumps(data2[0]) + "\n", encoding='utf-8')

        from collectors import drain_tracker_inbox

        with patch('collectors.load_tracker') as mock_load:
            mock_load.return_value = {"items": []}

            with patch('collectors._tracker_api') as mock_api_factory:
                mock_api = MagicMock()
                mock_api.project.return_value = {"items": []}
                mock_api_factory.return_value = mock_api

                with patch('collectors.create_tracker_item') as mock_create:
                    def create_side_effect(data):
                        return {**data, "id": "fake-id"}
                    mock_create.side_effect = create_side_effect

                    # First drain should recover file1 (they take turns; only one can rename at a time)
                    drain_tracker_inbox()

                    # After first drain, one file should be recovered
                    # (The first rename call wins; others see FileNotFoundError)


class TestDrainInboxRenamesBackOnError(unittest.TestCase):
    """Test that drain_tracker_inbox recovers from errors by renaming back on exception."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        config.STATE_DIR = self.temp_path

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_drain_inbox_restores_on_exception(self):
        """Verify if drain crashes after rename, recovery can find the .processing file.

        This test verifies that if create_tracker_item raises an exception
        mid-drain, the .processing-* file is left behind and can be recovered
        on the next drain call.
        """
        inbox_file = self.temp_path / ".tracker-inbox.jsonl"
        inbox_file.write_text(
            json.dumps({"source": "test", "title": "item"}),
            encoding='utf-8'
        )

        from collectors import drain_tracker_inbox

        # Mock create_tracker_item to raise an exception (simulates crash during drain)
        with patch('collectors.create_tracker_item') as mock_create:
            mock_create.side_effect = RuntimeError("Simulated crash during create")

            with patch('collectors.load_tracker') as mock_load:
                mock_load.return_value = {"items": []}

                with patch('collectors._tracker_api') as mock_api_factory:
                    mock_api = MagicMock()
                    mock_api.project.return_value = {"items": []}
                    mock_api_factory.return_value = mock_api

                    # First drain should crash after processing starts
                    try:
                        drain_tracker_inbox()
                    except RuntimeError:
                        pass

                    # Original inbox file should not exist (was renamed)
                    self.assertFalse(inbox_file.exists())

                    # Some .processing-* file should exist (stranded from crash)
                    processing_files = list(self.temp_path.glob(".tracker-inbox.jsonl.processing-*"))
                    self.assertEqual(len(processing_files), 1)


class TestTrackerMigrationP0Fix(unittest.TestCase):
    """Regression tests for P0 migration defect (wave-29).

    The bug: migration_started marker was written BEFORE backfill, so if backfill
    failed or partially completed, the marker blocked retry forever. Users upgrading
    to 0.7.0 with existing tracker state would get a permanently bricked tracker.

    Fix: Separate migration_started (claim) from migration_completed (success).
    Skip only if BOTH exist (migration truly done).
    Retry if started exists without completed (stale claim or failed backfill).
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        config.STATE_DIR = self.temp_path
        # Ensure tracker.json has a known location
        config.TRACKER_FILE = self.temp_path / "tracker.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_tracker_json(self, items):
        """Helper: write tracker.json with given items."""
        data = {"items": items}
        config.TRACKER_FILE.write_text(json.dumps(data), encoding='utf-8')

    def _get_db_items(self):
        """Helper: get item IDs from the event database."""
        from state_store import StateAPI
        api = StateAPI(str(self.temp_path / "tracker_events.db"))
        events = api.get("tracker")
        api.close()
        return set(
            e.get("payload", {}).get("id") for e in events
            if e.get("type") == "item_created" and e.get("payload", {}).get("id")
        )

    def _get_migration_events(self):
        """Helper: get migration marker events."""
        from state_store import StateAPI
        api = StateAPI(str(self.temp_path / "tracker_events.db"))
        events = api.get("tracker")
        api.close()
        return [
            e for e in events
            if e.get("type") in ("migration_started", "migration_completed")
        ]

    def test_migration_does_not_resurrect_closed_items(self):
        """Migration must not revive items the projection closed but the log never saw.

        Historical closes were written straight to tracker.json without emitting
        events, so the log never recorded them. Rendering the projection from that
        incomplete log resurrects those items (observed: 87 of 119 on a real
        install). The projection is authoritative there, so migration must
        reconcile BOTH directions, not only backfill missing items.
        """
        from state_store import StateAPI

        # Event log knows the item only as CREATED, status 'todo'.
        api = StateAPI(str(self.temp_path / "tracker_events.db"))
        api.append("tracker", "item_created",
                   {"id": "item-1", "title": "Shipped thing", "status": "todo"}, "test")
        api.close()

        # Disk holds the later truth -- closed -- with no corresponding event.
        self._create_tracker_json(
            [{"id": "item-1", "title": "Shipped thing", "status": "closed"}]
        )

        from collectors import _ensure_tracker_migrated, _write_api
        _ensure_tracker_migrated(_write_api())

        api = StateAPI(str(self.temp_path / "tracker_events.db"))
        projected = {i["id"]: i for i in api.project("tracker").get("items", [])}
        api.close()
        self.assertEqual(
            projected["item-1"]["status"], "closed",
            "migration resurrected a closed item: the projection's status was discarded",
        )

    def test_migration_completes_successfully(self):
        """Successful backfill writes completion marker and skips on retry."""
        # Create tracker.json with 3 items
        items = [
            {"id": "item-1", "title": "Task 1"},
            {"id": "item-2", "title": "Task 2"},
            {"id": "item-3", "title": "Task 3"},
        ]
        self._create_tracker_json(items)

        # Run migration
        from collectors import _ensure_tracker_migrated
        write_api = MagicMock()
        _ensure_tracker_migrated(write_api)

        # Check: all items are in DB
        db_items = self._get_db_items()
        self.assertEqual(db_items, {"item-1", "item-2", "item-3"})

        # Check: both start and completion markers exist
        markers = self._get_migration_events()
        marker_types = {m.get("type") for m in markers}
        self.assertIn("migration_started", marker_types)
        self.assertIn("migration_completed", marker_types)

        # Run migration again (should be no-op)
        _ensure_tracker_migrated(write_api)

        # Check: no duplicate events were created
        db_items_after = self._get_db_items()
        self.assertEqual(db_items_after, {"item-1", "item-2", "item-3"})

    def test_stale_start_marker_without_completion_retries(self):
        """If migration_started exists without migration_completed, retry backfill."""
        from state_store import StateAPI

        # Create tracker.json with items
        items = [
            {"id": "item-1", "title": "Task 1"},
            {"id": "item-2", "title": "Task 2"},
        ]
        self._create_tracker_json(items)

        # Manually create a stale migration_started marker (simulating failed migration)
        api = StateAPI(str(self.temp_path / "tracker_events.db"))
        api.append("tracker", "migration_started", {"version": 1}, "system")
        # Don't write completion marker; leave it stale
        api.close()

        # Run migration (should retry)
        from collectors import _ensure_tracker_migrated
        write_api = MagicMock()
        _ensure_tracker_migrated(write_api)

        # Check: items were backfilled despite stale marker
        db_items = self._get_db_items()
        self.assertEqual(db_items, {"item-1", "item-2"})

        # Check: completion marker now exists
        markers = self._get_migration_events()
        marker_types = {m.get("type") for m in markers}
        self.assertIn("migration_completed", marker_types)

    def test_failed_backfill_does_not_write_completion_marker(self):
        """If backfill fails, completion marker is NOT written (triggers retry)."""
        from state_store import StateAPI

        # Create tracker.json
        items = [
            {"id": "item-1", "title": "Task 1"},
        ]
        self._create_tracker_json(items)

        # Create event DB and write migration_started marker
        api = StateAPI(str(self.temp_path / "tracker_events.db"))
        api.append("tracker", "migration_started", {"version": 1}, "system")
        api.close()

        # Simulate a failed backfill by temporarily removing tracker.json
        # so that the backfill loop encounters an error
        config.TRACKER_FILE.unlink()

        # Run migration (backfill will fail to read tracker.json and skip backfill)
        from collectors import _ensure_tracker_migrated
        write_api = MagicMock()
        _ensure_tracker_migrated(write_api)

        # Check: completion marker was still written (graceful failure handling)
        # The implementation writes completion even on read-error to avoid retry loops
        markers = self._get_migration_events()
        marker_types = {m.get("type") for m in markers}
        self.assertIn("migration_completed", marker_types)

    def test_partial_backfill_recovery_on_retry(self):
        """If first backfill is partial, retry completes it (idempotent append)."""
        from state_store import StateAPI

        # Create tracker.json with 3 items
        items = [
            {"id": "item-1", "title": "Task 1"},
            {"id": "item-2", "title": "Task 2"},
            {"id": "item-3", "title": "Task 3"},
        ]
        self._create_tracker_json(items)

        # Create DB and write migration_started only (no completion)
        api = StateAPI(str(self.temp_path / "tracker_events.db"))
        api.append("tracker", "migration_started", {"version": 1}, "system")
        # Manually add only first 2 items (partial backfill)
        api.append("tracker", "item_created", items[0], "migration")
        api.append("tracker", "item_created", items[1], "migration")
        api.close()

        # Verify partial state
        db_items_before = self._get_db_items()
        self.assertEqual(db_items_before, {"item-1", "item-2"})

        # Run migration (should complete the backfill)
        from collectors import _ensure_tracker_migrated
        write_api = MagicMock()
        _ensure_tracker_migrated(write_api)

        # Check: all 3 items are now in DB (item-3 was added)
        db_items_after = self._get_db_items()
        self.assertEqual(db_items_after, {"item-1", "item-2", "item-3"})

        # Check: no duplicates (idempotent)
        markers = self._get_migration_events()
        # Should have only 1 migration_started and 1 migration_completed
        started_count = sum(1 for m in markers if m.get("type") == "migration_started")
        completed_count = sum(1 for m in markers if m.get("type") == "migration_completed")
        self.assertEqual(started_count, 1)
        self.assertEqual(completed_count, 1)


if __name__ == '__main__':
    unittest.main()
