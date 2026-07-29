#!/usr/bin/env python3
"""Tests for latency_report.py wave timing analyzer."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from latency_report import (
    parse_bench_results,
    parse_wave_journal,
    estimate_orchestrator_overhead,
    compute_latency_breakdown,
    format_latency_table,
)


class TestParseBenchResults(unittest.TestCase):
    """Test bench results parsing."""

    def test_parse_single_result_file(self):
        """Parse a single benchmark result file."""
        result_data = {
            "mode": "offline",
            "model": "test-model",
            "timestamp": "2026-07-22T14:21:06Z",
            "overall_accuracy": 0.86,
            "task_count": 2,
            "tasks": [
                {
                    "task_id": "t01",
                    "category": "cat1",
                    "composite_accuracy": 1.0,
                },
                {
                    "task_id": "t02",
                    "category": "cat2",
                    "composite_accuracy": 0.5,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            result_file = Path(tmpdir) / "results.json"
            result_file.write_text(json.dumps(result_data))

            results = parse_bench_results(result_file)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["model"], "test-model")
            self.assertEqual(results[0]["task_count"], 2)

    def test_parse_multiple_result_files(self):
        """Parse multiple benchmark result files from a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create two result files
            for i in range(2):
                result_data = {
                    "mode": "offline",
                    "model": f"model-{i}",
                    "timestamp": "2026-07-22T14:21:06Z",
                    "task_count": i + 1,
                    "tasks": [],
                }
                (tmpdir_path / f"results-{i}.json").write_text(json.dumps(result_data))

            results = parse_bench_results(tmpdir_path)

            self.assertEqual(len(results), 2)
            models = [r["model"] for r in results]
            self.assertIn("model-0", models)
            self.assertIn("model-1", models)

    def test_parse_nonexistent_file(self):
        """Handle nonexistent file gracefully."""
        results = parse_bench_results(Path("/nonexistent/path"))
        self.assertEqual(len(results), 0)


class TestEstimateOrchestratorOverhead(unittest.TestCase):
    """Test orchestrator overhead estimation."""

    def test_single_item_overhead(self):
        """Estimate overhead for single item wave."""
        items = [{"duration_s": 30.0}]
        wave_duration_s = 35.0

        overhead = estimate_orchestrator_overhead(
            wave_duration_s=wave_duration_s,
            items=items,
            method="wall_clock_minus_parallel"
        )

        # Overhead should be ~5s (35s total - 30s agent work)
        self.assertAlmostEqual(overhead, 5.0, delta=1.0)

    def test_parallel_items_overhead(self):
        """Estimate overhead for parallel items (wall-clock doesn't add)."""
        items = [
            {"duration_s": 30.0},
            {"duration_s": 25.0},
            {"duration_s": 20.0},
        ]
        wave_duration_s = 35.0  # All items run in parallel

        overhead = estimate_orchestrator_overhead(
            wave_duration_s=wave_duration_s,
            items=items,
            method="wall_clock_minus_parallel"
        )

        # With full parallelism, longest item is 30s, overhead is ~5s
        self.assertGreater(overhead, 0)
        self.assertLess(overhead, 10)


class TestComputeLatencyBreakdown(unittest.TestCase):
    """Test full latency breakdown computation."""

    def test_simple_wave_breakdown(self):
        """Compute breakdown for a simple wave."""
        items_data = [
            {"slug": "item-1", "duration_s": 25.0, "repairs": 0},
            {"slug": "item-2", "duration_s": 30.0, "repairs": 1},
        ]

        breakdown = compute_latency_breakdown(
            items=items_data,
            wave_duration_s=45.0,
            wave_name="test-wave"
        )

        self.assertIn("wave_name", breakdown)
        self.assertIn("wall_clock_s", breakdown)
        self.assertIn("item_durations", breakdown)
        self.assertEqual(breakdown["wave_name"], "test-wave")
        self.assertAlmostEqual(breakdown["wall_clock_s"], 45.0)

    def test_percentile_calculations(self):
        """Verify p50/p95 calculations."""
        items_data = [
            {"slug": f"item-{i}", "duration_s": float(10 + i * 5)}
            for i in range(10)
        ]

        breakdown = compute_latency_breakdown(
            items=items_data,
            wave_duration_s=50.0
        )

        # Should compute percentiles
        self.assertIn("p50", breakdown["item_durations"])
        self.assertIn("p95", breakdown["item_durations"])


class TestFormatLatencyTable(unittest.TestCase):
    """Test markdown table formatting."""

    def test_format_simple_table(self):
        """Format latency breakdown to markdown table."""
        breakdown = {
            "wave_name": "wave-1",
            "wall_clock_s": 45.0,
            "item_durations": {
                "min": 20.0,
                "max": 35.0,
                "p50": 27.5,
                "p95": 33.0,
                "mean": 28.0,
            },
            "orchestrator_overhead_s": 10.0,
            "method": "wall_clock_minus_parallel",
        }

        table = format_latency_table([breakdown])

        self.assertIn("wave-1", table)
        self.assertIn("45.0", table)
        self.assertIn("|", table)  # Markdown table structure
        self.assertIn("---", table)


if __name__ == "__main__":
    unittest.main()
