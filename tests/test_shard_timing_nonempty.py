#!/usr/bin/env python3
"""Guard: .github/shard-timing.json must be populated and consumable by the runner.

An empty `{}` silently disables the timing-aware shard path in
tools/ci_shard_runner.py (main() falls back to round-robin when load_timing_data
returns a falsy value), so the CI shards stay unbalanced with no visible error.
"""
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from tools.ci_shard_runner import load_timing_data  # noqa: E402

TIMING_PATH = REPO_ROOT / ".github" / "shard-timing.json"


class TestShardTimingNonEmpty(unittest.TestCase):
    """The committed timing file must enable the timing-balanced shard path."""

    def setUp(self):
        self.raw = json.loads(TIMING_PATH.read_text(encoding="utf-8"))

    def test_file_is_non_empty_object(self):
        self.assertIsInstance(self.raw, dict)
        self.assertGreater(len(self.raw), 0, "shard-timing.json is {} -- timing path is inert")

    def test_runner_loader_accepts_file(self):
        loaded = load_timing_data(str(TIMING_PATH))
        self.assertTrue(loaded, "load_timing_data returned falsy; runner uses round-robin")

    def test_keys_are_existing_test_module_paths(self):
        for key in self.raw:
            self.assertTrue(key.startswith("tests/test_") and key.endswith(".py"), key)
            self.assertTrue((REPO_ROOT / key).is_file(), f"stale timing key: {key}")

    def test_values_are_non_negative_numbers(self):
        for key, value in self.raw.items():
            self.assertIsInstance(value, (int, float), key)
            self.assertNotIsInstance(value, bool, key)
            self.assertGreaterEqual(value, 0, key)


if __name__ == "__main__":
    unittest.main()
