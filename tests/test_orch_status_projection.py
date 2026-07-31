"""Tests for orchestrator_status projection (Inc 2).

Test strategy:
1. Projector fold order: phase_changed, activity_changed, status_cleared
2. Byte-compatibility: projection must match current orchestrator-status.json shape
3. Historical events: meta/phase_set folding
4. Write/materialize path: event append then view render
5. Read path: projection-first with file fallback
6. CLI contract: stdout byte-identical
7. Idempotent backfill from file
"""
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Add repo root to path
import sys
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from state_store.projections import project_orchestrator_status
from state_store.api import StateAPI
from state_store.write_api import WriteAPI
from state_store.read_api import ReadAPI
from state_store.materialize import materialize_orchestrator_status


class TestOrchestrationStatusProjector(unittest.TestCase):
    """Test the orchestrator_status projector folding logic."""

    def test_empty_stream(self):
        """Empty stream produces default status."""
        result = project_orchestrator_status([])
        self.assertEqual(result["id"], "main")
        self.assertEqual(result["role"], "orchestrator")
        self.assertIsNone(result["activity"])
        self.assertIsNone(result["phase"])
        self.assertIsNone(result["updated_at"])

    def test_phase_changed_event(self):
        """phase_changed event updates phase."""
        events = [
            {
                "version": 1,
                "type": "phase_changed",
                "payload": {
                    "phase": "execute",
                    "timestamp": "2026-07-30T12:00:00Z",
                    "actor": "test",
                },
            }
        ]
        result = project_orchestrator_status(events)
        self.assertEqual(result["phase"], "execute")
        self.assertEqual(result["updated_at"], "2026-07-30T12:00:00Z")
        self.assertIsNone(result["activity"])

    def test_activity_changed_event(self):
        """activity_changed event updates activity."""
        events = [
            {
                "version": 1,
                "type": "activity_changed",
                "payload": {
                    "activity": "dispatching fleet",
                    "timestamp": "2026-07-30T12:01:00Z",
                    "actor": "test",
                },
            }
        ]
        result = project_orchestrator_status(events)
        self.assertEqual(result["activity"], "dispatching fleet")
        self.assertEqual(result["updated_at"], "2026-07-30T12:01:00Z")
        self.assertIsNone(result["phase"])

    def test_status_cleared_event(self):
        """status_cleared event resets all fields."""
        events = [
            {
                "version": 1,
                "type": "phase_changed",
                "payload": {"phase": "audit", "timestamp": "2026-07-30T12:00:00Z"},
            },
            {
                "version": 2,
                "type": "activity_changed",
                "payload": {"activity": "auditing", "timestamp": "2026-07-30T12:01:00Z"},
            },
            {
                "version": 3,
                "type": "status_cleared",
                "payload": {},
            },
        ]
        result = project_orchestrator_status(events)
        self.assertIsNone(result["phase"])
        self.assertIsNone(result["activity"])
        self.assertIsNone(result["updated_at"])

    def test_multiple_phase_changes(self):
        """Last phase_changed wins."""
        events = [
            {
                "version": 1,
                "type": "phase_changed",
                "payload": {"phase": "plan", "timestamp": "2026-07-30T12:00:00Z"},
            },
            {
                "version": 2,
                "type": "phase_changed",
                "payload": {"phase": "execute", "timestamp": "2026-07-30T12:01:00Z"},
            },
        ]
        result = project_orchestrator_status(events)
        self.assertEqual(result["phase"], "execute")

    def test_historical_meta_phase_set(self):
        """Historical meta/phase_set events are folded forward."""
        events = [
            {
                "version": 1,
                "type": "meta",
                "payload": {"phase": "legacy_phase"},
            },
            {
                "version": 2,
                "type": "phase_set",
                "payload": {"phase": "audit"},
            },
        ]
        result = project_orchestrator_status(events)
        # Last phase_set wins
        self.assertEqual(result["phase"], "audit")

    def test_mixed_event_types(self):
        """Projector handles mixed event types correctly."""
        events = [
            {
                "version": 1,
                "type": "phase_changed",
                "payload": {"phase": "plan", "timestamp": "2026-07-30T12:00:00Z"},
            },
            {
                "version": 2,
                "type": "activity_changed",
                "payload": {"activity": "planning", "timestamp": "2026-07-30T12:01:00Z"},
            },
            {
                "version": 3,
                "type": "unknown_type",  # Unknown types are ignored
                "payload": {"foo": "bar"},
            },
            {
                "version": 4,
                "type": "phase_changed",
                "payload": {"phase": "execute", "timestamp": "2026-07-30T12:02:00Z"},
            },
        ]
        result = project_orchestrator_status(events)
        self.assertEqual(result["phase"], "execute")
        self.assertEqual(result["activity"], "planning")

    def test_unknown_event_types_ignored(self):
        """Unknown event types do not crash the projector."""
        events = [
            {
                "version": 1,
                "type": "some_future_event",
                "payload": {"data": "ignored"},
            }
        ]
        result = project_orchestrator_status(events)
        # Should still have defaults
        self.assertEqual(result["id"], "main")
        self.assertIsNone(result["phase"])


class TestOrchestrationStatusMaterialization(unittest.TestCase):
    """Test materialization of orchestrator_status to JSON bytes."""

    def test_materialize_empty(self):
        """Empty projection materializes to valid JSON."""
        projection = {
            "id": "main",
            "role": "orchestrator",
            "activity": None,
            "phase": None,
            "updated_at": None,
        }
        content = materialize_orchestrator_status(projection)
        data = json.loads(content)
        self.assertEqual(data["id"], "main")
        self.assertEqual(data["role"], "orchestrator")
        self.assertIsNone(data["activity"])

    def test_materialize_with_values(self):
        """Populated projection materializes correctly."""
        projection = {
            "id": "main",
            "role": "orchestrator",
            "activity": "dispatching fleet",
            "phase": "execute",
            "updated_at": "2026-07-30T12:00:00Z",
        }
        content = materialize_orchestrator_status(projection)
        data = json.loads(content)
        self.assertEqual(data["activity"], "dispatching fleet")
        self.assertEqual(data["phase"], "execute")
        self.assertEqual(data["updated_at"], "2026-07-30T12:00:00Z")

    def test_materialize_idempotent(self):
        """Same projection materializes to same bytes."""
        projection = {
            "id": "main",
            "role": "orchestrator",
            "activity": "testing",
            "phase": "audit",
            "updated_at": "2026-07-30T12:00:00Z",
        }
        content1 = materialize_orchestrator_status(projection)
        content2 = materialize_orchestrator_status(projection)
        self.assertEqual(content1, content2)


class TestOrchestrationStatusWriteAPI(unittest.TestCase):
    """Test WriteAPI orchestrator_status methods."""

    def setUp(self):
        """Create isolated temp state dir."""
        self.state_dir = Path(tempfile.mkdtemp(prefix="test-orch-status-"))

    def tearDown(self):
        """Clean up temp dir."""
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_set_orchestrator_status_appends_events(self):
        """set_orchestrator_status appends events to the store."""
        api = WriteAPI(self.state_dir)
        try:
            result = api.set_orchestrator_status(
                activity="dispatching",
                phase="execute",
            )
            self.assertEqual(result["activity"], "dispatching")
            self.assertEqual(result["phase"], "execute")
        finally:
            api.close()

    def test_set_orchestrator_status_materializes_view(self):
        """set_orchestrator_status materializes the JSON view."""
        api = WriteAPI(self.state_dir)
        try:
            api.set_orchestrator_status(
                activity="testing",
                phase="audit",
            )
            status_file = self.state_dir / "orchestrator-status.json"
            self.assertTrue(status_file.exists())
            data = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertEqual(data["activity"], "testing")
            self.assertEqual(data["phase"], "audit")
        finally:
            api.close()

    def test_clear_orchestrator_status(self):
        """clear_orchestrator_status clears all fields."""
        api = WriteAPI(self.state_dir)
        try:
            # Set status first
            api.set_orchestrator_status(activity="test", phase="plan")
            status_file = self.state_dir / "orchestrator-status.json"
            self.assertTrue(status_file.exists())

            # Clear it
            api.clear_orchestrator_status()

            # Verify cleared
            data = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertIsNone(data["activity"])
            self.assertIsNone(data["phase"])
        finally:
            api.close()

    def test_set_activity_only(self):
        """set_orchestrator_status with activity only."""
        api = WriteAPI(self.state_dir)
        try:
            api.set_orchestrator_status(activity="testing")
            status_file = self.state_dir / "orchestrator-status.json"
            data = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertEqual(data["activity"], "testing")
            self.assertIsNone(data["phase"])
        finally:
            api.close()

    def test_set_phase_only(self):
        """set_orchestrator_status with phase only."""
        api = WriteAPI(self.state_dir)
        try:
            api.set_orchestrator_status(phase="execute")
            status_file = self.state_dir / "orchestrator-status.json"
            data = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertIsNone(data["activity"])
            self.assertEqual(data["phase"], "execute")
        finally:
            api.close()


class TestOrchestrationStatusReadAPI(unittest.TestCase):
    """Test ReadAPI orchestrator_status methods."""

    def setUp(self):
        """Create isolated temp state dir."""
        self.state_dir = Path(tempfile.mkdtemp(prefix="test-orch-status-read-"))

    def tearDown(self):
        """Clean up temp dir."""
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_read_from_projection(self):
        """ReadAPI reads from projection when DB is present."""
        # Create status via WriteAPI
        write_api = WriteAPI(self.state_dir)
        write_api.set_orchestrator_status(activity="test", phase="plan")
        write_api.close()

        # Read via ReadAPI (should use projection)
        read_api = ReadAPI(self.state_dir)
        status = read_api.read_orchestrator_status()
        self.assertIsNotNone(status)
        self.assertEqual(status["activity"], "test")
        self.assertEqual(status["phase"], "plan")

    def test_read_fallback_to_file(self):
        """ReadAPI falls back to file when DB is absent."""
        # Create status file directly (no DB)
        status_file = self.state_dir / "orchestrator-status.json"
        status_data = {
            "id": "main",
            "role": "orchestrator",
            "activity": "fallback_test",
            "phase": "audit",
            "updated_at": "2026-07-30T12:00:00Z",
        }
        status_file.write_text(json.dumps(status_data, indent=2), encoding="utf-8")

        # Read via ReadAPI (should use file fallback)
        read_api = ReadAPI(self.state_dir)
        status = read_api.read_orchestrator_status()
        self.assertIsNotNone(status)
        self.assertEqual(status["activity"], "fallback_test")
        self.assertEqual(status["phase"], "audit")

    def test_read_missing_returns_none(self):
        """read_orchestrator_status returns None if neither source exists."""
        read_api = ReadAPI(self.state_dir)
        status = read_api.read_orchestrator_status()
        self.assertIsNone(status)

    def test_is_fresh_check_on_projection(self):
        """is_orchestrator_status_fresh checks projection timestamp."""
        # Create fresh status
        write_api = WriteAPI(self.state_dir)
        write_api.set_orchestrator_status(activity="fresh", phase="test")
        write_api.close()

        # Check freshness (should be fresh)
        read_api = ReadAPI(self.state_dir)
        is_fresh = read_api.is_orchestrator_status_fresh(threshold_s=300)
        self.assertTrue(is_fresh)


class TestOrchestrationStatusIntegration(unittest.TestCase):
    """Integration tests for orchestrator_status end-to-end."""

    def setUp(self):
        """Create isolated temp state dir."""
        self.state_dir = Path(tempfile.mkdtemp(prefix="test-orch-status-int-"))

    def tearDown(self):
        """Clean up temp dir."""
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_end_to_end_write_and_read(self):
        """Write status via API and read via another API instance."""
        # Write
        write_api = WriteAPI(self.state_dir)
        write_api.set_orchestrator_status(
            activity="e2e_test",
            phase="execute",
        )
        write_api.close()

        # Read via fresh ReadAPI instance
        read_api = ReadAPI(self.state_dir)
        status = read_api.read_orchestrator_status()
        self.assertEqual(status["activity"], "e2e_test")
        self.assertEqual(status["phase"], "execute")

    def test_backfill_idempotence(self):
        """Backfilling from file is idempotent."""
        # Create initial status file (simulating legacy state)
        status_file = self.state_dir / "orchestrator-status.json"
        status_data = {
            "id": "main",
            "role": "orchestrator",
            "activity": "legacy",
            "phase": "plan",
            "updated_at": "2026-07-30T12:00:00Z",
        }
        status_file.write_text(json.dumps(status_data, indent=2), encoding="utf-8")

        # Read it (this should seed the event store on first migration)
        read_api = ReadAPI(self.state_dir)
        status = read_api.read_orchestrator_status()
        self.assertEqual(status["activity"], "legacy")

        # Read again (should still work and be idempotent)
        status2 = read_api.read_orchestrator_status()
        self.assertEqual(status2["activity"], "legacy")


if __name__ == "__main__":
    unittest.main()
