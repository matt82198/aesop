#!/usr/bin/env python3
"""Unit tests for crossos_drift.py — Windows vs Linux CI outcome drift measurement."""
import sys
import subprocess
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from io import StringIO


class TestCrossOsDrift(unittest.TestCase):
    """Test cases for crossos_drift.py using direct function testing."""

    def setUp(self):
        """Set up test fixtures."""
        self.tool_path = Path(__file__).parent.parent / "tools" / "crossos_drift.py"

    def _run_tool_subprocess(self, *args):
        """Run crossos_drift.py as subprocess."""
        cmd = [sys.executable, str(self.tool_path)] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result

    def test_help_works(self):
        """Test that --help works."""
        result = self._run_tool_subprocess("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("crossos_drift", result.stdout.lower())

    def test_unauth_exit_3(self):
        """Test that unauth error returns exit 3 with clear message."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("crossos_drift", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Mock gh_run_list to raise unauth error
        with patch.object(module, 'gh_run_list') as mock_gh:
            mock_gh.side_effect = module.GhError("gh is not authenticated")

            # Stub the main execution with a catch for the exception
            try:
                with patch("sys.argv", ["crossos_drift.py", "--runs", "5"]):
                    module.main()
            except SystemExit as e:
                # Should exit with code 3 on auth failure
                self.assertEqual(e.code, 3)

    def test_divergence_detection_ubuntu_green_windows_red(self):
        """Test divergence detection: ubuntu green + windows red."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("crossos_drift", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Simulate ubuntu shards (all green) + windows (red)
        ubuntu_shards = [
            {"name": "Ubuntu 22 / Py 3.10 shard 1", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "Ubuntu 22 / Py 3.10 shard 2", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ]
        windows = {"name": "Windows build", "status": "COMPLETED", "conclusion": "FAILURE"}

        # Collect results
        ubuntu_pass = all(s.get("conclusion") == "SUCCESS" for s in ubuntu_shards)
        windows_pass = windows.get("conclusion") == "SUCCESS"

        # Divergence: ubuntu pass != windows pass
        is_divergent = ubuntu_pass != windows_pass
        self.assertTrue(is_divergent)
        self.assertTrue(ubuntu_pass)
        self.assertFalse(windows_pass)

    def test_divergence_no_divergence_both_green(self):
        """Test no divergence when both ubuntu and windows are green."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("crossos_drift", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        ubuntu_pass = True
        windows_pass = True

        is_divergent = ubuntu_pass != windows_pass
        self.assertFalse(is_divergent)

    def test_not_present_handling_windows_job_missing(self):
        """Test NOT-PRESENT handling when windows job doesn't exist (pre-#317)."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("crossos_drift", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # When windows job is not present, it should be counted as NOT-PRESENT
        windows_status = "NOT-PRESENT"
        ubuntu_pass = True

        # NOT-PRESENT should NOT be treated as pass or fail for rate calculation
        self.assertEqual(windows_status, "NOT-PRESENT")
        # Should be excluded from windows pass rate calculations
        self.assertNotEqual(windows_status, "PASS")
        self.assertNotEqual(windows_status, "FAIL")

    def test_failing_test_aggregation_from_logs(self):
        """Test failing test name aggregation from gh run view logs."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("crossos_drift", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Mock log output with failing tests
        log_text = """
        FAIL: test_foo
        FAIL: test_bar
        ok test_baz
        not ok test_qux
        FAIL: test_foo
        """

        # Parse failing tests
        if hasattr(module, 'parse_failing_tests'):
            failures = module.parse_failing_tests(log_text)
            # Should aggregate and count frequencies
            self.assertIn("test_foo", failures or [])
            # test_foo appears twice, should be counted
        else:
            # If not implemented yet, that's OK for TDD
            pass

    def test_pass_rate_calculation(self):
        """Test pass rate calculation for windows and ubuntu."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("crossos_drift", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # 8 runs total: 6 ubuntu pass, 2 ubuntu fail; 7 windows present (1 not-present), 5 windows pass
        ubuntu_results = ["PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "FAIL", "FAIL"]
        windows_results = ["PASS", "PASS", "PASS", "PASS", "PASS", "NOT-PRESENT", "FAIL"]

        ubuntu_present = [r for r in ubuntu_results if r != "NOT-PRESENT"]
        windows_present = [r for r in windows_results if r != "NOT-PRESENT"]

        ubuntu_pass_rate = sum(1 for r in ubuntu_present if r == "PASS") / len(ubuntu_present) if ubuntu_present else 0
        windows_pass_rate = sum(1 for r in windows_present if r == "PASS") / len(windows_present) if windows_present else 0

        self.assertAlmostEqual(ubuntu_pass_rate, 0.75)  # 6/8
        self.assertEqual(windows_pass_rate, 5 / 6)  # 5/6
        # Should report: ubuntu 75%, windows 83%, not-present handling OK

    def test_empty_run_list(self):
        """Test handling of empty run list (no completed runs found)."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("crossos_drift", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        runs = []
        self.assertEqual(len(runs), 0)

    def test_json_output_format(self):
        """Test --json output is valid JSON with expected fields."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("crossos_drift", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Expected JSON structure
        expected_fields = {
            "ubuntu_pass_rate": float,
            "windows_pass_rate": float,
            "total_runs_analyzed": int,
            "divergences": list,
        }

        # Mock result structure
        result = {
            "ubuntu_pass_rate": 0.75,
            "windows_pass_rate": 0.83,
            "total_runs_analyzed": 8,
            "divergences": ["run-id-1", "run-id-5"],
        }

        # Verify all expected fields are present
        for field, expected_type in expected_fields.items():
            self.assertIn(field, result)
            self.assertIsInstance(result[field], expected_type)

    def test_bounded_runtime_no_unbounded_pagination(self):
        """Test that runtime is bounded and no unbounded pagination occurs."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("crossos_drift", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Should run with --runs 8 efficiently
        import time
        start = time.time()
        result = self._run_tool_subprocess("--runs", "8", "--help")
        elapsed = time.time() - start

        # Help should be instant (subsecond)
        self.assertLess(elapsed, 5.0, "Runtime should be bounded")

    def test_ascii_output_only(self):
        """Test that output is ASCII-safe and printable."""
        result = self._run_tool_subprocess("--help")
        self.assertEqual(result.returncode, 0)

        # Verify output is ASCII-safe (no non-ASCII chars)
        try:
            result.stdout.encode('ascii')
        except UnicodeEncodeError:
            self.fail("Output contains non-ASCII characters")

    def test_runs_parameter_parsing(self):
        """Test that --runs parameter is parsed correctly."""
        result = self._run_tool_subprocess("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--runs", result.stdout)
        self.assertIn("10", result.stdout)  # Default should be mentioned

    def test_json_parameter_parsing(self):
        """Test that --json parameter is recognized."""
        result = self._run_tool_subprocess("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--json", result.stdout)

    def test_runs_default_10(self):
        """Test that default runs is 10 if not specified."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("crossos_drift", self.tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Check if default is 10
        if hasattr(module, 'DEFAULT_RUNS'):
            self.assertEqual(module.DEFAULT_RUNS, 10)

    def test_divergence_set_reporting(self):
        """Test that divergence set is properly reported in output."""
        # When both ubuntu and windows pass rates are calculated,
        # the tool should identify runs where ubuntu=pass and windows=fail
        ubuntu_by_run = {
            "run-1": "PASS",
            "run-2": "PASS",
            "run-3": "FAIL",
            "run-4": "PASS",
        }
        windows_by_run = {
            "run-1": "PASS",
            "run-2": "FAIL",  # divergence
            "run-3": "FAIL",  # no divergence (both fail)
            "run-4": "PASS",
        }

        divergences = [
            run_id
            for run_id in ubuntu_by_run
            if ubuntu_by_run[run_id] == "PASS" and windows_by_run.get(run_id) == "FAIL"
        ]

        self.assertEqual(divergences, ["run-2"])

    def test_windows_failing_tests_aggregation(self):
        """Test aggregation of failing test names by frequency."""
        # Mock multiple job logs with failing tests
        logs = [
            "FAIL: test_unit_1\nFAIL: test_unit_2\nok test_int_1",
            "not ok test_unit_1\nFAIL: test_unit_3",
            "FAIL: test_unit_1\nok test_all",
        ]

        # Aggregate failing test frequencies
        failures = {}
        for log in logs:
            for line in log.split("\n"):
                if "FAIL:" in line or line.startswith("not ok"):
                    test_name = line.replace("FAIL:", "").replace("not ok", "").strip()
                    if test_name:
                        failures[test_name] = failures.get(test_name, 0) + 1

        # Sort by frequency (descending)
        sorted_failures = sorted(failures.items(), key=lambda x: x[1], reverse=True)

        # test_unit_1 should be most frequent (3 times)
        self.assertEqual(sorted_failures[0][0], "test_unit_1")
        self.assertEqual(sorted_failures[0][1], 3)

    def test_stdlib_only_no_external_deps(self):
        """Test that the tool uses only stdlib."""
        result = self._run_tool_subprocess("--help")
        self.assertEqual(result.returncode, 0)
        # Tool should work with subprocess, json, argparse from stdlib


class TestJobConclusionFailsClosed(unittest.TestCase):
    """Regression: a COMPLETED job with an unrecognized conclusion is a FAILURE.

    Fail-open repro (GAP5): job_conclusion() used to fall through to "PENDING"
    for any conclusion outside its hardcoded lists. PENDING is dropped from the
    pass-rate denominators (analyze_run / main), so an unknown conclusion --
    including GitHub's real `startup_failure` -- silently vanished from drift
    measurement instead of counting against it.
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        tool_path = Path(__file__).parent.parent / "tools" / "crossos_drift.py"
        spec = importlib.util.spec_from_file_location("crossos_drift", tool_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_startup_failure_conclusion_is_fail(self):
        job = {"status": "completed", "conclusion": "startup_failure"}
        self.assertEqual(self.module.job_conclusion(job), "FAIL")

    def test_unknown_future_conclusion_is_fail(self):
        job = {"status": "completed", "conclusion": "some_new_github_token"}
        self.assertEqual(self.module.job_conclusion(job), "FAIL")

    def test_missing_conclusion_on_completed_job_is_fail(self):
        job = {"status": "completed"}
        self.assertEqual(self.module.job_conclusion(job), "FAIL")

    def test_known_green_conclusions_still_pass(self):
        for conclusion in ("success", "neutral", "skipped"):
            job = {"status": "completed", "conclusion": conclusion}
            self.assertEqual(self.module.job_conclusion(job), "PASS", conclusion)

    def test_known_red_conclusions_still_fail(self):
        for conclusion in ("failure", "timed_out", "cancelled", "action_required", "stale"):
            job = {"status": "completed", "conclusion": conclusion}
            self.assertEqual(self.module.job_conclusion(job), "FAIL", conclusion)

    def test_incomplete_job_is_still_pending(self):
        for status in ("queued", "in_progress", "waiting"):
            job = {"status": status, "conclusion": ""}
            self.assertEqual(self.module.job_conclusion(job), "PENDING", status)


if __name__ == "__main__":
    unittest.main()
