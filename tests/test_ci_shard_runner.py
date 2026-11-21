#!/usr/bin/env python3
"""Tests for tools/ci_shard_runner.py."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.ci_shard_runner import (
    _ISOLATION_ARMED_ENV,
    distribute_shards,
    distribute_shards_by_timing,
    load_timing_data,
    build_pytest_args,
    main,
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


_FAKE_STEMS = [f"test_{c}" for c in "abcdefgh"]


def _fake_ls_files(*_args, **_kwargs):
    """Stand-in for `git ls-files tests/test_*.py` returning eight fixed files."""
    stdout = "\n".join(f"tests/{s}.py" for s in _FAKE_STEMS) + "\n"
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _placeholder_module(stem):
    """A fake tests.<stem> module holding one trivial TestCase named Case_<stem>."""
    case = type(f"Case_{stem}", (unittest.TestCase,), {"test_placeholder": lambda self: None})
    module = types.ModuleType(f"tests.{stem}")
    setattr(module, case.__name__, case)
    return module


def _leaf_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _leaf_tests(item)
        else:
            yield item


def _run_main(argv, faulthandler_mock, runner_mock, timing_file=None):
    """Drive main() in-process. Returns (exit_code, stdout, stderr).

    Stubs: the isolation tripwire (env marked armed), the network-isolation env
    hook, git ls-files, the faulthandler module, the unittest text runner, and
    the tests.<stem> modules. Non-SystemExit exceptions propagate to the caller.
    """
    args = list(argv)
    if timing_file is not None:
        args += ["--timing-file", timing_file]
    out, err = io.StringIO(), io.StringIO()
    code = None
    with unittest.mock.patch.dict(os.environ, {_ISOLATION_ARMED_ENV: "1"}), \
            unittest.mock.patch.object(sys, "argv", ["ci_shard_runner.py", *args]), \
            unittest.mock.patch("test_network_isolation.apply_test_isolation_env"), \
            unittest.mock.patch("tools.ci_shard_runner.subprocess.run", side_effect=_fake_ls_files), \
            unittest.mock.patch("tools.ci_shard_runner.faulthandler", faulthandler_mock), \
            unittest.mock.patch("tools.ci_shard_runner.unittest.TextTestRunner",
                                return_value=runner_mock), \
            unittest.mock.patch.dict(sys.modules, {
                f"tests.{s}": _placeholder_module(s) for s in _FAKE_STEMS
            }), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            main()
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def _passing_runner():
    runner = unittest.mock.Mock()
    runner.run.return_value.wasSuccessful.return_value = True
    return runner


class TestHangDumpArming(unittest.TestCase):
    """The faulthandler hang diagnostic is armed, disabled and cancelled as specified."""

    def test_default_arms_540_with_exit_false(self):
        fh = unittest.mock.Mock()
        code, _out, _err = _run_main(["0", "4"], fh, _passing_runner())
        self.assertEqual(code, 0)
        fh.dump_traceback_later.assert_called_once()
        args, kwargs = fh.dump_traceback_later.call_args
        self.assertEqual(args, (540,))
        self.assertIs(kwargs["exit"], False)
        self.assertIn("file", kwargs)

    def test_hang_dump_seconds_option_arms_given_value(self):
        fh = unittest.mock.Mock()
        _run_main(["0", "4", "--hang-dump-seconds", "7"], fh, _passing_runner())
        args, kwargs = fh.dump_traceback_later.call_args
        self.assertEqual(args, (7,))
        self.assertIs(kwargs["exit"], False)

    def test_zero_disables_arming(self):
        fh = unittest.mock.Mock()
        _run_main(["0", "4", "--hang-dump-seconds", "0"], fh, _passing_runner())
        fh.dump_traceback_later.assert_not_called()

    def test_negative_value_is_argparse_error(self):
        fh = unittest.mock.Mock()
        runner = _passing_runner()
        code, _out, err = _run_main(["0", "4", "--hang-dump-seconds", "-1"], fh, runner)
        self.assertEqual(code, 2)
        self.assertIn("--hang-dump-seconds", err)
        self.assertIn("error:", err)
        fh.dump_traceback_later.assert_not_called()
        runner.run.assert_not_called()

    def test_non_integer_value_is_argparse_error(self):
        for bad in ("abc", "1.5"):
            with self.subTest(value=bad):
                fh = unittest.mock.Mock()
                runner = _passing_runner()
                code, _out, err = _run_main(["0", "4", "--hang-dump-seconds", bad], fh, runner)
                self.assertEqual(code, 2)
                self.assertIn("error:", err)
                runner.run.assert_not_called()

    def test_cancel_called_when_run_succeeds(self):
        fh = unittest.mock.Mock()
        code, _out, _err = _run_main(["0", "4"], fh, _passing_runner())
        self.assertEqual(code, 0)
        fh.cancel_dump_traceback_later.assert_called_once_with()

    def test_cancel_called_when_run_fails(self):
        fh = unittest.mock.Mock()
        runner = unittest.mock.Mock()
        runner.run.return_value.wasSuccessful.return_value = False
        code, _out, _err = _run_main(["0", "4"], fh, runner)
        self.assertEqual(code, 1)
        fh.cancel_dump_traceback_later.assert_called_once_with()

    def test_cancel_called_when_run_raises(self):
        fh = unittest.mock.Mock()
        runner = unittest.mock.Mock()
        runner.run.side_effect = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            _run_main(["0", "4"], fh, runner)
        fh.cancel_dump_traceback_later.assert_called_once_with()


class TestShardMembershipUnchanged(unittest.TestCase):
    """Shard membership and its printed output are byte-identical to the pre-change runner."""

    def test_round_robin_membership_and_output(self):
        fh = unittest.mock.Mock()
        runner = _passing_runner()
        code, out, _err = _run_main(["1", "4", "--hang-dump-seconds", "0"], fh, runner)
        self.assertEqual(code, 0)
        self.assertEqual(out, "Shard 1: round-robin distribution\nShard 1: running 2 tests\n")
        suite = runner.run.call_args.args[0]
        names = [type(t).__name__ for t in _leaf_tests(suite)]
        self.assertEqual(names, ["Case_test_b", "Case_test_f"])

    def test_timing_membership_and_output(self):
        timing = {f"tests/{s}.py": w for s, w in zip(
            _FAKE_STEMS, [50, 40, 30, 20, 10, 5, 4, 3])}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "timing.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(timing, f)
            fh = unittest.mock.Mock()
            runner = _passing_runner()
            code, out, _err = _run_main(["0", "2", "--hang-dump-seconds", "0"], fh, runner,
                                        timing_file=path)
        self.assertEqual(code, 0)
        self.assertEqual(out, "Shard 0: timing-aware distribution\nShard 0: running 3 tests\n")
        suite = runner.run.call_args.args[0]
        names = [type(t).__name__ for t in _leaf_tests(suite)]
        self.assertEqual(names, ["Case_test_a", "Case_test_d", "Case_test_e"])


if __name__ == "__main__":
    unittest.main()
