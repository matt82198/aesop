"""Healthcheck tool tests — aggregates heartbeats, tracker items, alerts, orchestrator status.

Test strategy (TDD):
1. Create isolated temp state directories with fixtures for each health ball color
2. Test heartbeat staleness detection (green/yellow/red thresholds)
3. Test security alert counting (HIGH=red, MED=yellow)
4. Test orchestrator status age/phase checking
5. Test tracker lane/item counting
6. Test missing files (graceful handling)
7. Test --json output format
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from time import time
from unittest.mock import patch

UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

# We'll import healthcheck after tests set up env
TOOLS_DIR = Path(__file__).parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

ENV_KEYS = (
    "AESOP_ROOT",
    "AESOP_STATE_ROOT",
    "AESOP_TRANSCRIPTS_ROOT",
    "AESOP_UI_COLLECT_INTERVAL",
    "AESOP_CONDUCTOR3_ROOT",
    "AESOP_WATCHDOG_HEARTBEAT",
    "AESOP_MONITOR_HEARTBEAT",
)


class HealthcheckTestCase(unittest.TestCase):
    """Base class for healthcheck tests with isolated temp state."""

    def setUp(self):
        """Set up isolated temp directories for testing."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-healthcheck-test-"))
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
        # Point conductor3 to temp dir to prevent resolving to real ~/conductor3
        os.environ["AESOP_CONDUCTOR3_ROOT"] = str(self.fixture_root / "conductor3")

    def tearDown(self):
        """Restore original env and clean up temp files."""
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _write_heartbeat(self, name, age_seconds=0):
        """Write a heartbeat file with given age."""
        heartbeat_file = self.state_dir / f".{name}-heartbeat"
        epoch = int(time()) - age_seconds
        heartbeat_file.write_text(str(epoch), encoding="utf-8")
        return heartbeat_file

    def _write_alerts(self, alert_lines):
        """Write SECURITY-ALERTS.log with given lines."""
        alerts_file = self.state_dir / "SECURITY-ALERTS.log"
        alerts_file.write_text("\n".join(alert_lines) + "\n", encoding="utf-8")
        return alerts_file

    def _write_tracker_items(self, items):
        """Write tracker.json with given items."""
        tracker_file = self.state_dir / "tracker.json"
        tracker = {"version": 1, "items": items}
        tracker_file.write_text(json.dumps(tracker, indent=2), encoding="utf-8")
        return tracker_file

    def _write_orchestrator_status(self, activity=None, phase=None, age_seconds=0):
        """Write orchestrator-status.json with given values."""
        status_file = self.state_dir / "orchestrator-status.json"
        now = datetime.now(timezone.utc)
        if age_seconds > 0:
            now = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() - age_seconds,
                tz=timezone.utc
            )
        status = {
            "id": "main",
            "role": "orchestrator",
            "activity": activity or "idle",
            "phase": phase or "waiting",
            "updated_at": now.isoformat().replace("+00:00", "Z"),
        }
        status_file.write_text(json.dumps(status, indent=2), encoding="utf-8")
        return status_file


class TestHealthcheckGreen(HealthcheckTestCase):
    """Tests for green health status."""

    def test_green_all_heartbeats_fresh_no_alerts(self):
        """Green when all heartbeats fresh and no HIGH alerts."""
        # Import inside test so env is set
        import sys
        if "healthcheck" in sys.modules:
            del sys.modules["healthcheck"]
        import healthcheck

        # Fresh heartbeats
        self._write_heartbeat("watchdog", age_seconds=10)
        self._write_heartbeat("monitor", age_seconds=20)

        # No alerts
        self.state_dir.joinpath("SECURITY-ALERTS.log").write_text("", encoding="utf-8")

        # Fresh orchestrator status
        self._write_orchestrator_status(activity="dispatching", phase="build", age_seconds=30)

        # Some tracker items
        self._write_tracker_items([
            {"id": "1", "title": "Item 1", "lane": "ranked", "priority": "P1"},
            {"id": "2", "title": "Item 2", "lane": "in-progress", "priority": "P1"},
        ])

        result = healthcheck.check_health()
        self.assertIn("🟢", result, f"Expected green ball, got: {result}")
        self.assertIn("HEALTH:", result)

    def test_green_json_output(self):
        """Green status outputs valid JSON in --json mode."""
        import sys
        if "healthcheck" in sys.modules:
            del sys.modules["healthcheck"]
        import healthcheck

        self._write_heartbeat("watchdog", age_seconds=10)
        self._write_heartbeat("monitor", age_seconds=20)
        self.state_dir.joinpath("SECURITY-ALERTS.log").write_text("", encoding="utf-8")
        self._write_orchestrator_status(activity="idle", phase="waiting", age_seconds=30)
        self._write_tracker_items([])

        output = healthcheck.check_health(json_mode=True)
        try:
            data = json.loads(output)
            self.assertEqual(data.get("ball"), "🟢")
            self.assertIn("health", data)
        except json.JSONDecodeError as e:
            self.fail(f"JSON output not valid: {e}\n{output}")


class TestHealthcheckYellow(HealthcheckTestCase):
    """Tests for yellow health status."""

    def test_yellow_stale_watchdog_heartbeat(self):
        """Yellow when watchdog heartbeat is stale (>300s)."""
        import sys
        if "healthcheck" in sys.modules:
            del sys.modules["healthcheck"]
        import healthcheck

        # Stale watchdog heartbeat
        self._write_heartbeat("watchdog", age_seconds=400)
        self._write_heartbeat("monitor", age_seconds=20)
        self.state_dir.joinpath("SECURITY-ALERTS.log").write_text("", encoding="utf-8")
        self._write_orchestrator_status(activity="idle", phase="waiting", age_seconds=30)
        self._write_tracker_items([])

        result = healthcheck.check_health()
        self.assertIn("🟡", result, f"Expected yellow ball, got: {result}")
        self.assertIn("stale", result.lower())

    def test_yellow_unreviewed_medium_alert(self):
        """Yellow when unreviewed MEDIUM severity alert present."""
        import sys
        if "healthcheck" in sys.modules:
            del sys.modules["healthcheck"]
        import healthcheck

        # Fresh heartbeats
        self._write_heartbeat("watchdog", age_seconds=10)
        self._write_heartbeat("monitor", age_seconds=20)

        # Unreviewed MED alert
        self._write_alerts([
            "[MED] Potential security issue in module X",
        ])

        self._write_orchestrator_status(activity="idle", phase="waiting", age_seconds=30)
        self._write_tracker_items([])

        result = healthcheck.check_health()
        self.assertIn("🟡", result, f"Expected yellow ball, got: {result}")

    def test_yellow_ignores_reviewed_alerts(self):
        """Yellow should be avoided if alerts are marked NOTE: or RESOLVED-FP."""
        import sys
        if "healthcheck" in sys.modules:
            del sys.modules["healthcheck"]
        import healthcheck

        self._write_heartbeat("watchdog", age_seconds=10)
        self._write_heartbeat("monitor", age_seconds=20)

        # Reviewed/FP alerts (should be ignored)
        self._write_alerts([
            "NOTE: [MED] Already reviewed",
            "RESOLVED-FP [MED] False positive",
        ])

        self._write_orchestrator_status(activity="idle", phase="waiting", age_seconds=30)
        self._write_tracker_items([])

        result = healthcheck.check_health()
        self.assertIn("🟢", result, f"Expected green (no unreviewed alerts), got: {result}")


class TestHealthcheckRed(HealthcheckTestCase):
    """Tests for red health status."""

    def test_red_high_severity_alert(self):
        """Red when HIGH severity alert is unreviewed."""
        import sys
        if "healthcheck" in sys.modules:
            del sys.modules["healthcheck"]
        import healthcheck

        # Fresh heartbeats
        self._write_heartbeat("watchdog", age_seconds=10)
        self._write_heartbeat("monitor", age_seconds=20)

        # Unreviewed HIGH alert
        self._write_alerts([
            "[HIGH] Critical security vulnerability detected",
        ])

        self._write_orchestrator_status(activity="idle", phase="waiting", age_seconds=30)
        self._write_tracker_items([])

        result = healthcheck.check_health()
        self.assertIn("🔴", result, f"Expected red ball, got: {result}")
        self.assertIn("HIGH", result.upper())

    def test_red_watchdog_dead_while_agents_running(self):
        """Red when watchdog dead (>300s) and agents appear active."""
        import sys
        if "healthcheck" in sys.modules:
            del sys.modules["healthcheck"]
        import healthcheck

        # Dead watchdog heartbeat
        self._write_heartbeat("watchdog", age_seconds=600)
        self._write_heartbeat("monitor", age_seconds=20)
        self.state_dir.joinpath("SECURITY-ALERTS.log").write_text("", encoding="utf-8")

        # Active orchestrator (agents likely running)
        self._write_orchestrator_status(activity="dispatching fleet", phase="execute", age_seconds=30)
        self._write_tracker_items([])

        result = healthcheck.check_health()
        # This should be red: dead watchdog while orchestrator actively dispatching
        self.assertIn("🔴", result, f"Expected red ball for dead watchdog + active dispatch, got: {result}")

    def test_red_json_output(self):
        """Red status outputs valid JSON in --json mode."""
        import sys
        if "healthcheck" in sys.modules:
            del sys.modules["healthcheck"]
        import healthcheck

        self._write_heartbeat("watchdog", age_seconds=10)
        self._write_heartbeat("monitor", age_seconds=20)
        self._write_alerts(["[HIGH] Severe issue"])
        self._write_orchestrator_status(activity="idle", phase="waiting", age_seconds=30)
        self._write_tracker_items([])

        output = healthcheck.check_health(json_mode=True)
        try:
            data = json.loads(output)
            self.assertEqual(data.get("ball"), "🔴")
            self.assertIn("health", data)
        except json.JSONDecodeError as e:
            self.fail(f"JSON output not valid: {e}\n{output}")


class TestHealthcheckMissingFiles(HealthcheckTestCase):
    """Tests for graceful handling of missing files."""

    def test_missing_heartbeats_still_reports(self):
        """Missing heartbeat files don't crash, just report as unknown/missing."""
        import sys
        if "healthcheck" in sys.modules:
            del sys.modules["healthcheck"]
        import healthcheck

        # No heartbeat files at all
        # No alerts file
        # No tracker
        self._write_orchestrator_status(activity="idle", phase="waiting", age_seconds=30)

        result = healthcheck.check_health()
        # Should not crash, should report some health status
        self.assertIn("HEALTH:", result)
        # Color ball must be present (green, yellow, or red)
        self.assertTrue(
            any(ball in result for ball in ["🟢", "🟡", "🔴"]),
            f"Expected a health ball in output: {result}"
        )

    def test_missing_all_files_defaults_gracefully(self):
        """When all state files missing, tool reports health as best it can."""
        import sys
        if "healthcheck" in sys.modules:
            del sys.modules["healthcheck"]
        import healthcheck

        # Empty state directory
        result = healthcheck.check_health()
        self.assertIn("HEALTH:", result)
        # Should report something reasonable (likely yellow for missing heartbeats)
        self.assertTrue(
            any(ball in result for ball in ["🟢", "🟡", "🔴"]),
            f"Expected a health ball in output: {result}"
        )


class TestHealthcheckTrackerCounts(HealthcheckTestCase):
    """Tests for tracker item lane counting."""

    def test_tracker_items_counted_by_lane(self):
        """Tracker items are aggregated/reported by lane."""
        import sys
        if "healthcheck" in sys.modules:
            del sys.modules["healthcheck"]
        import healthcheck

        self._write_heartbeat("watchdog", age_seconds=10)
        self._write_heartbeat("monitor", age_seconds=20)
        self.state_dir.joinpath("SECURITY-ALERTS.log").write_text("", encoding="utf-8")
        self._write_orchestrator_status(activity="idle", phase="waiting", age_seconds=30)

        # Multiple tracker items in different lanes
        self._write_tracker_items([
            {"id": "1", "title": "In progress", "lane": "in-progress", "priority": "P0"},
            {"id": "2", "title": "Ranked todo", "lane": "ranked", "priority": "P1"},
            {"id": "3", "title": "Proposed", "lane": "proposed", "priority": "P2"},
        ])

        result = healthcheck.check_health(json_mode=True)
        data = json.loads(result)
        # Should have tracker info in bullet list
        self.assertIn("tracker", data)


if __name__ == "__main__":
    unittest.main()
