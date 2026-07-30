#!/usr/bin/env python3
"""Tests for tools/ci_shard_runner.py."""
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.ci_shard_runner import distribute_shards, build_pytest_args


class TestDistributeShards(unittest.TestCase):
    def test_round_robin_distribution(self):
        files = ["test_a", "test_b", "test_c", "test_d"]
        self.assertEqual(distribute_shards(files, 0, 2), ["test_a", "test_c"])
        self.assertEqual(distribute_shards(files, 1, 2), ["test_b", "test_d"])

    def test_single_shard_gets_all(self):
        files = ["test_a", "test_b", "test_c"]
        self.assertEqual(distribute_shards(files, 0, 1), files)

    def test_empty_shard(self):
        files = ["test_a"]
        self.assertEqual(distribute_shards(files, 0, 4), ["test_a"])
        self.assertEqual(distribute_shards(files, 1, 4), [])

    def test_four_way_split(self):
        files = [f"test_{i}" for i in range(8)]
        for shard_id in range(4):
            result = distribute_shards(files, shard_id, 4)
            self.assertEqual(len(result), 2)


class TestBuildPytestArgs(unittest.TestCase):
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
