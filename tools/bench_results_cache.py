#!/usr/bin/env python3
"""
bench_results_cache.py — Benchmark results journal (append-only).
INDEX: Append-only benchmark results journal (state/bench-runs.jsonl); idempotent dedup by model+timestamp; stdlib-only

Stores benchmark run results in an append-only JSONL file (state/bench-runs.jsonl)
for the dashboard to display. Each record is a complete run result (model, accuracy,
tokens, latency, cost). Idempotent deduplication by model+timestamp prevents
duplicate entries.

Usage:
    cache = BenchResultsCache(Path("state/bench-runs.jsonl"))
    cache.append_result("haiku", result_dict)
    results = cache.read_results(limit=50)
    comparison = cache.get_comparison(models=["haiku", "sonnet"])
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional


class BenchResultsCache:
    """Append-only benchmark results journal."""

    def __init__(self, journal_path: Path):
        """Initialize the cache with a journal file path.

        Args:
            journal_path: Path to the append-only JSONL journal file.
        """
        self.journal_path = Path(journal_path)

    def append_result(
        self,
        model: str,
        result: dict,
        timestamp: Optional[int] = None,
    ) -> None:
        """Append a benchmark result to the journal.

        Args:
            model: Model name (e.g., "haiku", "sonnet", "opus").
            result: Result dict with accuracy, tokens, latency, cost fields.
            timestamp: Unix timestamp (default: current time).

        Idempotent: if a result with the same model+timestamp already exists,
        it is not duplicated.
        """
        if timestamp is None:
            timestamp = int(time.time())

        # Check for existing entry with same model+timestamp (deduplication)
        existing = self._read_all_results()
        for entry in existing:
            if (
                entry.get("model") == model
                and entry.get("timestamp") == timestamp
            ):
                # Already exists, skip
                return

        # Build the full record
        record = {
            "timestamp": timestamp,
            "model": model,
        }
        record.update(result)

        # Append to journal
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def read_results(self, limit: int = 50) -> List[dict]:
        """Read recent benchmark results from the journal.

        Args:
            limit: Maximum number of recent results to return (default: 50).

        Returns:
            List of result records, most recent first.
        """
        if not self.journal_path.exists():
            return []

        all_results = self._read_all_results()

        # Return the last `limit` results in reverse order (most recent first)
        return list(reversed(all_results[-limit:]))

    def get_comparison(self, models: Optional[List[str]] = None) -> Dict[str, dict]:
        """Get a comparison summary across models.

        Args:
            models: Optional list of model names to include (default: all).

        Returns:
            Dict mapping model names to their latest stats:
            {
                "haiku": {
                    "accuracy": 0.917,
                    "total_tokens": 512,
                    "avg_latency_ms": 120.5,
                    "cost_estimate": 0.15,
                    "timestamp": 1234567890
                },
                ...
            }
        """
        all_results = self._read_all_results()

        # Group by model, keep only the most recent for each
        by_model: Dict[str, dict] = {}
        for entry in reversed(all_results):  # Process in reverse (newest first)
            model = entry.get("model")
            if model and model not in by_model:
                by_model[model] = entry

        # Filter by requested models if provided
        if models:
            by_model = {k: v for k, v in by_model.items() if k in models}

        return by_model

    def _read_all_results(self) -> List[dict]:
        """Read all results from the journal (oldest to newest).

        Returns:
            List of all result records in order (oldest first).
        """
        if not self.journal_path.exists():
            return []

        results: List[dict] = []
        try:
            with open(self.journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            record = json.loads(line)
                            results.append(record)
                        except json.JSONDecodeError:
                            # Skip malformed lines
                            pass
        except (IOError, OSError):
            # If file can't be read, return empty list
            return []

        return results
