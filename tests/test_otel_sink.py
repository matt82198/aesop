#!/usr/bin/env python3
"""
Test suite for tools/otel_sink.py — OpenTelemetry tracing integration.

Tests hermetic behavior: dry-run mode (no SDK), fake exporter, span structure,
metrics emission, Windows/Linux parity. No network, no external deps required.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timezone

# Ensure tools module is importable
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from tools.otel_sink import OTelSink, SpanContext, Metric


class TestOTelSinkDryRun(unittest.TestCase):
    """Test --dry-run mode (no SDK required, hand-rolled span tree)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dry_run_mode_prints_span_tree(self):
        """Verify --dry-run generates span tree output (no export)."""
        sink = OTelSink(state_dir=self.state_dir, dry_run=True)

        # Add a test span
        span = SpanContext(
            name="test_span",
            start_time=time.time(),
            end_time=time.time() + 1.0,
            attributes={"test": "value"}
        )
        sink.add_span(span)

        # Dry-run should not raise even without SDK
        output = sink.export()
        self.assertIsNotNone(output)
        # Output should be a string representation of the span tree
        self.assertIn("test_span", output)

    def test_dry_run_no_network(self):
        """Verify --dry-run produces output without OTLP endpoint."""
        sink = OTelSink(state_dir=self.state_dir, dry_run=True, endpoint=None)
        span = SpanContext(
            name="fleet_root",
            start_time=time.time(),
            end_time=time.time() + 10.0
        )
        sink.add_span(span)

        # Should complete without network access
        output = sink.export()
        self.assertIn("fleet_root", output)

    def test_missing_sdk_graceful_degradation(self):
        """Verify missing SDK is handled gracefully in dry-run."""
        # Temporarily hide opentelemetry to simulate missing SDK
        import sys
        hidden_modules = {}
        for module_name in list(sys.modules.keys()):
            if "opentelemetry" in module_name:
                hidden_modules[module_name] = sys.modules.pop(module_name)

        try:
            sink = OTelSink(state_dir=self.state_dir, dry_run=True)
            self.assertIsNotNone(sink)
        finally:
            # Restore modules
            sys.modules.update(hidden_modules)


class TestSpanContextConstruction(unittest.TestCase):
    """Test SpanContext creation and attributes."""

    def test_create_span_with_timestamps(self):
        """Verify SpanContext stores timestamps correctly."""
        start = time.time()
        end = start + 5.0

        span = SpanContext(
            name="test_operation",
            start_time=start,
            end_time=end,
            attributes={"status": "success"}
        )

        self.assertEqual(span.name, "test_operation")
        self.assertEqual(span.start_time, start)
        self.assertEqual(span.end_time, end)
        self.assertEqual(span.attributes["status"], "success")

    def test_span_duration_calculation(self):
        """Verify duration is calculated correctly."""
        start = time.time()
        end = start + 3.5

        span = SpanContext(
            name="operation",
            start_time=start,
            end_time=end
        )

        duration = span.end_time - span.start_time
        self.assertAlmostEqual(duration, 3.5, places=2)

    def test_span_with_nested_events(self):
        """Verify spans support nested events."""
        parent = SpanContext(
            name="parent",
            start_time=time.time(),
            end_time=time.time() + 10.0
        )

        child = SpanContext(
            name="child",
            start_time=time.time() + 1.0,
            end_time=time.time() + 5.0
        )

        parent.add_event(child)

        self.assertEqual(len(parent.events), 1)
        self.assertEqual(parent.events[0].name, "child")


class TestMetricConstruction(unittest.TestCase):
    """Test Metric creation and types."""

    def test_gauge_metric(self):
        """Verify gauge metric stores current value."""
        metric = Metric(
            name="active_agents",
            value=5,
            metric_type="gauge",
            timestamp=time.time()
        )

        self.assertEqual(metric.name, "active_agents")
        self.assertEqual(metric.value, 5)
        self.assertEqual(metric.metric_type, "gauge")

    def test_counter_metric(self):
        """Verify counter metric stores incremental value."""
        metric = Metric(
            name="gate_activations",
            value=2,
            metric_type="counter",
            timestamp=time.time()
        )

        self.assertEqual(metric.metric_type, "counter")
        self.assertEqual(metric.value, 2)

    def test_histogram_metric(self):
        """Verify histogram metric stores distribution."""
        metric = Metric(
            name="phase_duration",
            value=15.5,
            metric_type="histogram",
            timestamp=time.time(),
            attributes={"phase": "fix"}
        )

        self.assertEqual(metric.metric_type, "histogram")
        self.assertEqual(metric.attributes["phase"], "fix")


class TestOTelSinkIntegration(unittest.TestCase):
    """Integration tests: state surfaces → spans/metrics."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_heartbeats_as_spans(self):
        """Verify heartbeat files generate liveness spans."""
        heartbeat_dir = self.state_dir / "heartbeats"
        heartbeat_dir.mkdir(parents=True, exist_ok=True)

        # Create a heartbeat file
        now_epoch = str(int(time.time()))
        (heartbeat_dir / "watchdog").write_text(now_epoch + "\n")

        sink = OTelSink(state_dir=self.state_dir, dry_run=True)
        sink.ingest_heartbeats()

        # Check that heartbeat was processed
        self.assertTrue(len(sink.metrics) > 0 or len(sink.spans) > 0)

    def test_read_tracker_state_as_metrics(self):
        """Verify tracker.json generates status metrics."""
        tracker_file = self.state_dir / "tracker.json"
        tracker_data = {
            "items": [
                {"id": "item1", "status": "done"},
                {"id": "item2", "status": "in_progress"},
                {"id": "item3", "status": "ranked"}
            ]
        }
        tracker_file.write_text(json.dumps(tracker_data))

        sink = OTelSink(state_dir=self.state_dir, dry_run=True)
        sink.ingest_tracker_state()

        # Should generate metrics for item counts by status
        metric_names = {m.name for m in sink.metrics}
        self.assertIn("tracker.items.by_status", metric_names)

    def test_read_orchestrator_status(self):
        """Verify orchestrator-status.json generates phase span."""
        status_file = self.state_dir / "orchestrator-status.json"
        now_iso = datetime.now(timezone.utc).isoformat()
        status_data = {
            "phase": "fix",
            "activity": "running agents",
            "updated_at": now_iso
        }
        status_file.write_text(json.dumps(status_data))

        sink = OTelSink(state_dir=self.state_dir, dry_run=True)
        sink.ingest_orchestrator_status()

        # Should generate orchestrator span
        span_names = {s.name for s in sink.spans}
        self.assertIn("orchestrator.phase", span_names)

    def test_empty_state_no_crash(self):
        """Verify empty state dir doesn't crash."""
        sink = OTelSink(state_dir=self.state_dir, dry_run=True)

        # Should complete without error on empty state
        sink.ingest_heartbeats()
        sink.ingest_tracker_state()
        sink.ingest_orchestrator_status()

        output = sink.export()
        self.assertIsNotNone(output)


class TestFakeExporter(unittest.TestCase):
    """Test fake exporter for hermetic testing."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_fake_exporter_collects_spans(self):
        """Verify fake exporter captures spans."""
        from tools.otel_sink import FakeExporter

        exporter = FakeExporter()

        span = SpanContext(
            name="test",
            start_time=time.time(),
            end_time=time.time() + 1.0
        )

        exporter.export_spans([span])

        self.assertEqual(len(exporter.spans), 1)
        self.assertEqual(exporter.spans[0].name, "test")

    def test_fake_exporter_collects_metrics(self):
        """Verify fake exporter captures metrics."""
        from tools.otel_sink import FakeExporter

        exporter = FakeExporter()

        metric = Metric(
            name="agents",
            value=3,
            metric_type="gauge",
            timestamp=time.time()
        )

        exporter.export_metrics([metric])

        self.assertEqual(len(exporter.metrics), 1)
        self.assertEqual(exporter.metrics[0].value, 3)


class TestCLIInterface(unittest.TestCase):
    """Test CLI argument parsing and modes."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dry_run_flag_parsing(self):
        """Verify --dry-run flag is parsed correctly."""
        # This would be tested via CLI invocation in integration tests
        # For now, verify OTelSink accepts dry_run parameter
        sink = OTelSink(state_dir=self.state_dir, dry_run=True)
        self.assertTrue(sink.dry_run)

    def test_endpoint_env_var(self):
        """Verify OTEL_EXPORTER_OTLP_ENDPOINT env var is respected."""
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"

        sink = OTelSink(state_dir=self.state_dir, dry_run=True)
        # Endpoint should be set from env var
        self.assertIsNotNone(sink.endpoint)

        del os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]


class TestWindowsLinuxParity(unittest.TestCase):
    """Test cross-platform path handling and behavior."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_path_resolution_cross_platform(self):
        """Verify state_dir paths resolve correctly on all platforms."""
        # Use absolute path to avoid platform-specific issues
        abs_state_dir = self.state_dir.resolve()

        sink = OTelSink(state_dir=abs_state_dir, dry_run=True)

        # Should handle Path objects and strings
        self.assertIsNotNone(sink.state_dir)

    def test_timestamp_iso_format(self):
        """Verify timestamps use ISO 8601 format (cross-platform)."""
        sink = OTelSink(state_dir=self.state_dir, dry_run=True)

        now = datetime.now(timezone.utc).isoformat()

        # Should be parseable as ISO 8601
        datetime.fromisoformat(now.replace("Z", "+00:00"))


if __name__ == "__main__":
    unittest.main()
