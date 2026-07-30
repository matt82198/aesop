#!/usr/bin/env python3
"""
bench_panel.py — Benchmark API route handlers (wave-29 addition).

Provides GET /api/bench and GET /api/bench/compare endpoints for the dashboard
to display benchmark results. Uses BenchResultsCache to read from the append-only
journal (state/bench-runs.jsonl).

No external dependencies; stdlib only.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Add tools/ to path so we can import bench_results_cache
_tools_path = Path(__file__).resolve().parent.parent / "tools"
if str(_tools_path) not in sys.path:
    sys.path.insert(0, str(_tools_path))

import config  # noqa: E402
from bench_results_cache import BenchResultsCache  # noqa: E402


def _get_cache() -> BenchResultsCache:
    """Get the benchmark results cache with the standard journal path.

    The journal lives at state/bench-runs.jsonl (under AESOP_STATE_ROOT).
    Config is read at call time to honor reload().
    """
    journal_path = config.STATE_DIR / "bench-runs.jsonl"
    return BenchResultsCache(journal_path)


def get_bench_results(cache: Optional[BenchResultsCache] = None) -> Dict:
    """GET /api/bench — Return latest benchmark results as JSON.

    Args:
        cache: Optional BenchResultsCache instance (default: creates one).

    Returns:
        Dict with "results" list:
        {
            "results": [
                {
                    "timestamp": 1234567890,
                    "model": "haiku",
                    "accuracy": 0.917,
                    "total_tokens": 512,
                    "avg_latency_ms": 120.5,
                    "cost_estimate": 0.15,
                    ...
                },
                ...
            ]
        }
    """
    if cache is None:
        cache = _get_cache()

    results = cache.read_results(limit=50)

    return {
        "results": results,
    }


def get_bench_comparison(
    cache: Optional[BenchResultsCache] = None,
    models: Optional[List[str]] = None,
) -> Dict:
    """GET /api/bench/compare — Return model comparison data.

    Args:
        cache: Optional BenchResultsCache instance (default: creates one).
        models: Optional list of models to include.

    Returns:
        Dict with "comparison" map:
        {
            "comparison": {
                "haiku": {
                    "model": "haiku",
                    "accuracy": 0.917,
                    "total_tokens": 512,
                    "avg_latency_ms": 120.5,
                    "cost_estimate": 0.15,
                    "timestamp": 1234567890
                },
                ...
            }
        }
    """
    if cache is None:
        cache = _get_cache()

    comparison = cache.get_comparison(models=models)

    return {
        "comparison": comparison,
    }
