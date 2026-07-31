#!/usr/bin/env python3
"""Tests for tools/ci_shard_runner.py."""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.ci_shard_runner import (
    distribute_shards,
    distribute_shards_by_timing,
    load_timing_data,
    build_pytest_args,
)


class TestDistributeShards(unittest.TestCase):
    """Round-robin distribution tests."""

    def test_round_robin_distribution(self):
        files = ["test_a", "test_b", "test_c", "test_d"]
        self.assertEqual(distribute_shards(files, 0, 2), ["test_a", "test_c"])
        self.assertEqual(distribute_shards(files, 1, 2), ["test_b", "test_d"])

    def test_single_shard_gets_all(self):
        files = ["test_a", "test_b", "test_c"]
        self.assertEqual(distribute_shards(files, 0, 1), files)

    def test_more_shards_than_files(self):
        files = ["test_a", "test_b"]
        self.assertEqual(distribute_shards(files, 0, 4), ["test_a"])
        self.assertEqual(distribute_shards(files, 1, 4), ["test_b"])
        self.assertEqual(distribute_shards(files, 2, 4), [])
        self.assertEqual(distribute_shards(files, 3, 4), [])

    def test_empty_input(self):
        self.assertEqual(distribute_shards([], 0, 2), [])

    def test_empty_shard(self):
        files = ["test_a"]
        self.assertEqual(distribute_shards(files, 0, 4), ["test_a"])
        self.assertEqual(distribute_shards(files, 1, 4), [])

    def test_four_shard_assignment(self):
        files = [f"test_{i}" for i in range(8)]
        for shard in range(4):
            result = distribute_shards(files, shard, 4)
            self.assertEqual(len(result), 2)

    def test_four_way_split(self):
        files = [f"test_{i}" for i in range(8)]
        for shard_id in range(4):
            result = distribute_shards(files, shard_id, 4)
            self.assertEqual(len(result), 2)


class TestDistributeShardsByTiming(unittest.TestCase):
    """Greedy bin-packing distribution using timing data."""

    def test_balances_by_time(self):
        files = ["test_slow", "test_medium", "test_fast"]
        timing = {"test_slow": 30.0, "test_medium": 15.0, "test_fast": 5.0}
        result = distribute_shards_by_timing(files, 2, timing)
        self.assertEqual(len(result), 2)
        shard_0_time = sum(timing[f] for f in result[0])
        shard_1_time = sum(timing[f] for f in result[1])
        self.assertLessEqual(abs(shard_0_time - shard_1_time), 20.0)
        all_assigned = sorted(f for shard in result for f in shard)
        self.assertEqual(all_assigned, sorted(files))

    def test_single_shard_gets_all(self):
        files = ["test_a", "test_b"]
        timing = {"test_a": 10.0, "test_b": 5.0}
        result = distribute_shards_by_timing(files, 1, timing)
        self.assertEqual(len(result), 1)
        self.assertEqual(sorted(result[0]), sorted(files))

    def test_unknown_files_get_default_weight(self):
        files = ["test_known", "test_unknown"]
        timing = {"test_known": 20.0}
        result = distribute_shards_by_timing(files, 2, timing)
        all_assigned = sorted(f for shard in result for f in shard)
        self.assertEqual(all_assigned, sorted(files))

    def test_empty_timing_still_distributes(self):
        files = ["test_a", "test_b", "test_c"]
        result = distribute_shards_by_timing(files, 2, {})
        all_assigned = sorted(f for shard in result for f in shard)
        self.assertEqual(all_assigned, sorted(files))

    def test_greedy_assigns_heaviest_first(self):
        files = ["test_10", "test_20", "test_30", "test_40"]
        timing = {"test_10": 10, "test_20": 20, "test_30": 30, "test_40": 40}
        result = distribute_shards_by_timing(files, 2, timing)
        shard_times = []
        for shard in result:
            shard_times.append(sum(timing.get(f, 1.0) for f in shard))
        self.assertLessEqual(abs(shard_times[0] - shard_times[1]), 10.0)

    def test_returns_correct_shard_count(self):
        files = ["test_a", "test_b", "test_c", "test_d"]
        timing = {"test_a": 5, "test_b": 10, "test_c": 15, "test_d": 20}
        result = distribute_shards_by_timing(files, 4, timing)
        self.assertEqual(len(result), 4)


class TestTimingFileFallback(unittest.TestCase):
    """Loading timing data from a file with fallback to round-robin."""

    def test_valid_timing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"tests/test_foo.py": 12.5, "tests/test_bar.py": 8.3}, f)
            f.flush()
            path = f.name
        try:
            data = load_timing_data(path)
            self.assertEqual(data["test_foo"], 12.5)
            self.assertEqual(data["test_bar"], 8.3)
        finally:
            os.unlink(path)

    def test_empty_timing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({}, f)
            f.flush()
            path = f.name
        try:
            data = load_timing_data(path)
            self.assertEqual(data, {})
        finally:
            os.unlink(path)

    def test_missing_timing_file(self):
        data = load_timing_data("/nonexistent/path/timing.json")
        self.assertIsNone(data)

    def test_malformed_timing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json{{{")
            f.flush()
            path = f.name
        try:
            data = load_timing_data(path)
            self.assertIsNone(data)
        finally:
            os.unlink(path)


class TestBuildPytestArgs(unittest.TestCase):
    """Tests for pytest command-line argument building."""

    def test_without_timeout(self):
        shard_files = ["test_foo", "test_bar"]
        args = build_pytest_args(shard_files, timeout=None)
        self.assertEqual(args, [
            sys.executable, "-m", "pytest", "-v",
            "tests/test_foo.py", "tests/test_bar.py",
        ])

    def test_with_timeout(self):
        shard_files = ["test_foo", "test_bar"]
        args = build_pytest_args(shard_files, timeout=120)
        self.assertEqual(args, [
            sys.executable, "-m", "pytest", "-v",
            "--timeout=120",
            "tests/test_foo.py", "tests/test_bar.py",
        ])

    def test_single_file(self):
        args = build_pytest_args(["test_solo"], timeout=60)
        self.assertEqual(args, [
            sys.executable, "-m", "pytest", "-v",
            "--timeout=60",
            "tests/test_solo.py",
        ])

    def test_timeout_zero_omitted(self):
        """Timeout of 0 means disabled -- should not pass --timeout flag."""
        args = build_pytest_args(["test_x"], timeout=0)
        self.assertNotIn("--timeout=0", args)

    def test_timeout_none_omitted(self):
        args = build_pytest_args(["test_x"], timeout=None)
        for a in args:
            self.assertFalse(a.startswith("--timeout"))


class TestPytestTimeoutEnv(unittest.TestCase):
    """Tests for PYTEST_TIMEOUT environment variable handling."""

    def test_env_var_parsed_correctly(self):
        with unittest.mock.patch.dict(os.environ, {"PYTEST_TIMEOUT": "120"}):
            val = os.environ.get("PYTEST_TIMEOUT")
            self.assertEqual(int(val), 120)
            args = build_pytest_args(["test_a"], timeout=int(val))
            self.assertIn("--timeout=120", args)

    def test_env_var_absent_means_no_timeout(self):
        env = os.environ.copy()
        env.pop("PYTEST_TIMEOUT", None)
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            val = os.environ.get("PYTEST_TIMEOUT")
            self.assertIsNone(val)


if __name__ == "__main__":
    unittest.main()
