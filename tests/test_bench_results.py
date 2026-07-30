#!/usr/bin/env python3
"""Unit tests for tools/bench_results_cache.py — benchmark results journal."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import bench_results_cache  # noqa: E402


class TestAppendAndRead(unittest.TestCase):
    """Test append_result and read_results."""

    def setUp(self):
        """Create a temporary journal file for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.journal_path = Path(self.temp_dir.name) / "bench-runs.jsonl"

    def tearDown(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def test_append_and_read_single_result(self):
        """Append a result and read it back."""
        cache = bench_results_cache.BenchResultsCache(self.journal_path)

        result = {
            "model": "haiku",
            "tasks": 12,
            "passed": 11,
            "accuracy": 0.917,
            "total_tokens": 512,
            "avg_latency_ms": 120.5,
            "cost_estimate": 0.15,
        }

        cache.append_result("haiku", result)

        # Read results (should contain the one we just appended)
        results = cache.read_results(limit=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["model"], "haiku")
        self.assertEqual(results[0]["accuracy"], 0.917)

    def test_append_multiple_results(self):
        """Append multiple results and read them back."""
        cache = bench_results_cache.BenchResultsCache(self.journal_path)

        models = ["haiku", "sonnet", "opus"]
        for model in models:
            result = {
                "model": model,
                "tasks": 12,
                "passed": 12,
                "accuracy": 1.0,
                "total_tokens": 600,
                "avg_latency_ms": 150.0,
                "cost_estimate": 0.20,
            }
            cache.append_result(model, result)

        # Read all results (most recent first)
        results = cache.read_results(limit=10)
        self.assertEqual(len(results), 3)
        # Results are returned in reverse order (most recent first)
        self.assertEqual([r["model"] for r in results], list(reversed(models)))

    def test_read_with_limit(self):
        """Verify limit parameter works."""
        cache = bench_results_cache.BenchResultsCache(self.journal_path)

        # Append 5 results
        for i in range(5):
            result = {
                "model": f"test_{i}",
                "tasks": 12,
                "passed": 12,
                "accuracy": 1.0,
                "total_tokens": 600,
                "avg_latency_ms": 150.0,
                "cost_estimate": 0.20,
            }
            cache.append_result(f"test_{i}", result)

        # Read with limit=3
        results = cache.read_results(limit=3)
        self.assertEqual(len(results), 3)

        # Should be the most recent 3
        self.assertEqual(results[0]["model"], "test_4")
        self.assertEqual(results[1]["model"], "test_3")
        self.assertEqual(results[2]["model"], "test_2")

    def test_deduplication_same_model_same_timestamp(self):
        """Same model+timestamp should not duplicate."""
        cache = bench_results_cache.BenchResultsCache(self.journal_path)

        result = {
            "model": "haiku",
            "tasks": 12,
            "passed": 11,
            "accuracy": 0.917,
            "total_tokens": 512,
            "avg_latency_ms": 120.5,
            "cost_estimate": 0.15,
        }

        # Append the same result twice (same model, same timestamp)
        import time
        timestamp = int(time.time())
        cache.append_result("haiku", result, timestamp=timestamp)
        cache.append_result("haiku", result, timestamp=timestamp)

        # Should only have one entry
        results = cache.read_results(limit=10)
        self.assertEqual(len(results), 1)

    def test_empty_journal_graceful_handling(self):
        """Gracefully handle missing/empty journal file."""
        cache = bench_results_cache.BenchResultsCache(self.journal_path)

        # Journal doesn't exist yet
        self.assertFalse(self.journal_path.exists())

        # read_results should return empty list
        results = cache.read_results(limit=10)
        self.assertEqual(results, [])

    def test_result_has_required_fields(self):
        """Each result should have required fields."""
        cache = bench_results_cache.BenchResultsCache(self.journal_path)

        result = {
            "model": "haiku",
            "tasks": 12,
            "passed": 11,
            "accuracy": 0.917,
            "total_tokens": 512,
            "avg_latency_ms": 120.5,
            "cost_estimate": 0.15,
        }

        cache.append_result("haiku", result)

        results = cache.read_results(limit=10)
        self.assertEqual(len(results), 1)
        record = results[0]

        # Should have timestamp and the result data
        self.assertIn("timestamp", record)
        self.assertEqual(record["model"], "haiku")
        self.assertEqual(record["accuracy"], 0.917)


class TestComparison(unittest.TestCase):
    """Test get_comparison method."""

    def setUp(self):
        """Create a temporary journal file for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.journal_path = Path(self.temp_dir.name) / "bench-runs.jsonl"

    def tearDown(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def test_comparison_single_model(self):
        """Get comparison data for a single model."""
        cache = bench_results_cache.BenchResultsCache(self.journal_path)

        result = {
            "model": "haiku",
            "tasks": 12,
            "passed": 11,
            "accuracy": 0.917,
            "total_tokens": 512,
            "avg_latency_ms": 120.5,
            "cost_estimate": 0.15,
        }

        cache.append_result("haiku", result)

        comparison = cache.get_comparison()
        self.assertIn("haiku", comparison)
        self.assertEqual(comparison["haiku"]["accuracy"], 0.917)

    def test_comparison_multiple_models(self):
        """Get comparison data for multiple models."""
        cache = bench_results_cache.BenchResultsCache(self.journal_path)

        models_data = {
            "haiku": {"accuracy": 0.917, "avg_tokens": 42, "avg_latency_ms": 120.0},
            "sonnet": {"accuracy": 1.0, "avg_tokens": 60, "avg_latency_ms": 150.0},
            "opus": {"accuracy": 1.0, "avg_tokens": 70, "avg_latency_ms": 180.0},
        }

        for model, data in models_data.items():
            result = {
                "model": model,
                "tasks": 12,
                "passed": int(data["accuracy"] * 12),
                "accuracy": data["accuracy"],
                "total_tokens": int(data["avg_tokens"] * 12),
                "avg_latency_ms": data["avg_latency_ms"],
                "cost_estimate": 0.20,
            }
            cache.append_result(model, result)

        comparison = cache.get_comparison()
        self.assertEqual(len(comparison), 3)
        self.assertIn("haiku", comparison)
        self.assertIn("sonnet", comparison)
        self.assertIn("opus", comparison)

    def test_comparison_with_model_filter(self):
        """Get comparison data filtered by specific models."""
        cache = bench_results_cache.BenchResultsCache(self.journal_path)

        for model in ["haiku", "sonnet", "opus"]:
            result = {
                "model": model,
                "tasks": 12,
                "passed": 12,
                "accuracy": 1.0,
                "total_tokens": 600,
                "avg_latency_ms": 150.0,
                "cost_estimate": 0.20,
            }
            cache.append_result(model, result)

        # Get comparison for only haiku and sonnet
        comparison = cache.get_comparison(models=["haiku", "sonnet"])
        self.assertEqual(len(comparison), 2)
        self.assertIn("haiku", comparison)
        self.assertIn("sonnet", comparison)
        self.assertNotIn("opus", comparison)


if __name__ == "__main__":
    unittest.main()
