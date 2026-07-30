#!/usr/bin/env python3
"""Unit tests for ui/bench_panel.py — benchmark API route handler."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ui"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import bench_panel  # noqa: E402
import bench_results_cache  # noqa: E402


class TestBenchPanel(unittest.TestCase):
    """Test bench_panel API route handlers."""

    def setUp(self):
        """Create a temporary journal file for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.journal_path = Path(self.temp_dir.name) / "bench-runs.jsonl"
        self.cache = bench_results_cache.BenchResultsCache(self.journal_path)

        # Populate cache with test data
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
            self.cache.append_result(model, result)

    def tearDown(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def test_get_bench_results_valid_format(self):
        """GET /api/bench returns valid JSON with expected structure."""
        results = bench_panel.get_bench_results(self.cache)

        self.assertIsInstance(results, dict)
        self.assertIn("results", results)
        self.assertIsInstance(results["results"], list)

        # Should have at least one result
        self.assertGreater(len(results["results"]), 0)

        # Each result should have required fields
        for result in results["results"]:
            self.assertIn("model", result)
            self.assertIn("accuracy", result)
            self.assertIn("timestamp", result)

    def test_get_bench_results_contains_all_models(self):
        """Benchmark results include all appended models."""
        results = bench_panel.get_bench_results(self.cache)

        models = [r["model"] for r in results["results"]]
        self.assertIn("haiku", models)
        self.assertIn("sonnet", models)
        self.assertIn("opus", models)

    def test_get_bench_comparison_valid_format(self):
        """GET /api/bench/compare returns comparison data."""
        comparison = bench_panel.get_bench_comparison(self.cache)

        self.assertIsInstance(comparison, dict)
        self.assertIn("comparison", comparison)
        self.assertIsInstance(comparison["comparison"], dict)

    def test_get_bench_comparison_contains_models(self):
        """Comparison includes all models from cache."""
        comparison = bench_panel.get_bench_comparison(self.cache)

        models = comparison["comparison"]
        self.assertIn("haiku", models)
        self.assertIn("sonnet", models)
        self.assertIn("opus", models)

    def test_get_bench_comparison_model_stats(self):
        """Comparison includes accuracy and token stats per model."""
        comparison = bench_panel.get_bench_comparison(self.cache)

        models = comparison["comparison"]
        for model_name, model_data in models.items():
            self.assertIn("accuracy", model_data)
            # May have tokens if runner reported them
            if "total_tokens" in model_data:
                self.assertIsInstance(model_data["total_tokens"], (int, float))

    def test_get_bench_results_empty_cache(self):
        """Gracefully handle empty cache."""
        empty_cache = bench_results_cache.BenchResultsCache(
            Path(self.temp_dir.name) / "empty.jsonl"
        )

        results = bench_panel.get_bench_results(empty_cache)
        self.assertIsInstance(results, dict)
        self.assertEqual(len(results["results"]), 0)

    def test_get_bench_comparison_empty_cache(self):
        """Gracefully handle empty cache in comparison."""
        empty_cache = bench_results_cache.BenchResultsCache(
            Path(self.temp_dir.name) / "empty.jsonl"
        )

        comparison = bench_panel.get_bench_comparison(empty_cache)
        self.assertIsInstance(comparison, dict)
        self.assertEqual(len(comparison["comparison"]), 0)

    def test_results_json_serializable(self):
        """Results are JSON-serializable without errors."""
        results = bench_panel.get_bench_results(self.cache)

        try:
            json_str = json.dumps(results)
            self.assertIsInstance(json_str, str)
        except TypeError as e:
            self.fail(f"Results not JSON-serializable: {e}")

    def test_comparison_json_serializable(self):
        """Comparison data is JSON-serializable without errors."""
        comparison = bench_panel.get_bench_comparison(self.cache)

        try:
            json_str = json.dumps(comparison)
            self.assertIsInstance(json_str, str)
        except TypeError as e:
            self.fail(f"Comparison not JSON-serializable: {e}")


if __name__ == "__main__":
    unittest.main()
