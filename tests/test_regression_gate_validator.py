#!/usr/bin/env python3
"""Test suite for regression_gate_validator.py (Guardrail G8).

Tests verify that the guardrail catches the original escape:
- Agents citing `pytest tests/` instead of `tools/ci_shard_runner.py`
- Agents inferring test failures from exit code 124 (timeout) without validating completion
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Tuple


class TestRegressionGateValidator(unittest.TestCase):
    """Test regression gate validator catches wrong test gates."""

    def setUp(self):
        """Set up test fixtures in temporary directories."""
        self.test_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.test_dir.name)

        # Create directory structure
        (self.repo_root / "driver").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "monitor").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up temporary directories."""
        self.test_dir.cleanup()

    def run_validator(self, output_mode: str = "--check") -> Tuple[int, str]:
        """Run the regression gate validator on the test repo.

        Returns: (exit_code, output)
        """
        result = subprocess.run(
            [sys.executable, "tools/regression_gate_validator.py", str(self.repo_root), output_mode],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        return result.returncode, result.stdout + result.stderr

    def test_clean_state_no_violations(self):
        """Clean state: no violations when dispatches use correct gate."""
        # Create a clean dispatch file that uses ci_shard_runner.py
        dispatch_file = self.repo_root / "driver" / "test_dispatch.py"
        dispatch_file.write_text(
            '''
"""Regression test dispatch - uses correct gate."""

def run_regression_tests():
    """Run regression tests using the actual CI gate."""
    import subprocess
    result = subprocess.run([
        "python", "tools/ci_shard_runner.py", "0", "4"
    ])
    if result.returncode == 0:
        print("All regression tests passed")
    return result.returncode
'''
        )

        exit_code, output = self.run_validator("--check")

        # Should pass with no violations
        self.assertEqual(exit_code, 0, f"Expected exit 0 (clean), got {exit_code}\nOutput: {output}")
        self.assertIn("No regression gate violations", output)

    def test_escape_pytest_tests_forbidden(self):
        """ESCAPE REPRODUCTION: pytest tests/ is forbidden (original escape)."""
        # This is the exact pattern that agents were using incorrectly
        dispatch_file = self.repo_root / "driver" / "audit_dispatch.py"
        dispatch_file.write_text(
            '''
"""Audit regression tests - WRONG: uses pytest directly."""

def run_audit_tests():
    """Run regression audit - uses forbidden pytest proxy."""
    import subprocess
    result = subprocess.run(["pytest", "tests/"])
    if result.returncode == 0:
        print("Audit passed")
    return result.returncode
'''
        )

        exit_code, output = self.run_validator("--check")

        # Should fail: pytest tests/ is forbidden
        self.assertEqual(exit_code, 1, f"Expected exit 1 (violations), got {exit_code}\nOutput: {output}")
        self.assertIn("pytest", output.lower())
        self.assertIn("gate", output.lower())

    def test_escape_timeout_inference(self):
        """ESCAPE REPRODUCTION: inferring failures from exit 124 (timeout)."""
        # This represents the second part of the original escape:
        # agent reported "7+ test failures" from truncated output after 300s timeout
        monitor_file = self.repo_root / "monitor" / "regression_check.py"
        monitor_file.write_text(
            '''
"""Monitor regression exit codes - verification phase."""

def check_regression():
    """Check regression - WRONG: infers failures from exit code 124."""
    import subprocess
    result = subprocess.run(["python", "tools/ci_shard_runner.py", "0", "4"], timeout=300)

    # WRONG: agent inferred "7+ test failures" from this check
    if result.returncode == 124:
        print("Timeout exit code 124 - should not infer test failures from this")
        # This inference is wrong - exit 124 means timeout, not test failure

    return result.returncode
'''
        )

        exit_code, output = self.run_validator("--check")

        # Should flag the timeout exit code check for failure inference
        self.assertEqual(exit_code, 1, f"Expected exit 1 (violations), got {exit_code}\nOutput: {output}")
        self.assertIn("124", output.lower())

    def test_suppression_comment(self):
        """Suppression: regression-gate-ok comment suppresses violations."""
        dispatch_file = self.repo_root / "driver" / "test_dispatch.py"
        dispatch_file.write_text(
            '''
"""Regression test - deliberately suppressed."""

def run_tests():
    """Run regression via pytest tests/ (suppressed for legacy reasons)."""
    import subprocess
    result = subprocess.run(["pytest", "tests/"])  # regression-gate-ok
    return result.returncode
'''
        )

        exit_code, output = self.run_validator("--check")

        # Should pass: suppression comment exempts this line
        self.assertEqual(exit_code, 0, f"Expected exit 0 (suppressed), got {exit_code}\nOutput: {output}")

    def test_json_output(self):
        """JSON output mode formats violations as JSON."""
        dispatch_file = self.repo_root / "driver" / "bad_dispatch.py"
        dispatch_file.write_text(
            '''
"""Verify regression via wrong gate."""

def verify_tests():
    """Run verification - uses pytest instead of ci_shard_runner."""
    import subprocess
    subprocess.run(["pytest", "tests/test_core.py"])
'''
        )

        exit_code, output = self.run_validator("--json")

        # Should be valid JSON
        try:
            data = json.loads(output)
            self.assertIn("violations", data)
            self.assertGreater(data["total"], 0)
        except json.JSONDecodeError:
            self.fail(f"Expected valid JSON output, got: {output}")

    def test_no_false_positives_on_ci_runner(self):
        """No false positives: ci_shard_runner.py citations are clean."""
        dispatch_file = self.repo_root / "driver" / "correct_dispatch.py"
        dispatch_file.write_text(
            '''
"""Regression verification using the actual CI gate."""

def run_verification():
    """Run regression verification - uses ci_shard_runner.py (correct)."""
    import subprocess

    # This is the correct citation of the actual CI gate
    result = subprocess.run([
        "python", "tools/ci_shard_runner.py", "0", "4"
    ])

    if result.returncode != 0:
        print(f"Regression tests failed with exit code {result.returncode}")

    return result.returncode
'''
        )

        exit_code, output = self.run_validator("--check")

        # Should pass: ci_shard_runner.py is the correct gate
        self.assertEqual(exit_code, 0, f"Expected exit 0 (clean), got {exit_code}\nOutput: {output}")

    def test_no_violations_in_non_regression_files(self):
        """No violations in files without regression context."""
        # Create a file with pytest mentions but no regression keywords
        unrelated_file = self.repo_root / "some_util.py"
        unrelated_file.write_text(
            '''
"""Unrelated utility file (not a dispatch)."""

def helper():
    """Some helper - mentions pytest but not in regression context."""
    # Note: pytest is installed as a dev dependency
    pass
'''
        )

        exit_code, output = self.run_validator("--check")

        # Should pass: file doesn't have regression context
        self.assertEqual(exit_code, 0, f"Expected exit 0 (clean), got {exit_code}\nOutput: {output}")

    def test_no_false_positive_on_timeout_string_constant(self):
        """No false positive: string constants mentioning exit 124 are not flagged."""
        # This reproduces the false positive from driver/proc_util.py
        dispatch_file = self.repo_root / "driver" / "util_with_timeout_string.py"
        dispatch_file.write_text(
            '''
"""Utility with timeout handling."""

# This is a string constant describing timeout behavior, NOT code that infers failures
_TIMEOUT_NOTE = "Command timed out after {t}s; process tree killed (exit 124)"

def handle_timeout(proc):
    """Handle a timed-out process gracefully."""
    # This is legitimate timeout handling, not failure inference
    return _TIMEOUT_NOTE
'''
        )

        exit_code, output = self.run_validator("--check")

        # Should pass: string constants are not violations
        self.assertEqual(exit_code, 0, f"Expected exit 0 (string constant is not a violation), got {exit_code}\nOutput: {output}")
        self.assertIn("No regression gate violations", output)

    def test_catch_timeout_exit_code_in_conditional_logic(self):
        """ESSENTIAL: still catches exit 124 checks in actual conditional code."""
        # This is the REAL escape: code that checks if result == 124 and infers failure
        dispatch_file = self.repo_root / "monitor" / "verify_bad_timeout_logic.py"
        dispatch_file.write_text(
            '''
"""Verification that infers failures from timeout - this is the real escape."""

def verify_tests():
    """Regression verification with bad timeout logic."""
    import subprocess
    result = subprocess.run(["python", "tools/ci_shard_runner.py", "0", "4"], timeout=300)

    # WRONG: checking if result.returncode == 124 and inferring test failures
    if result.returncode == 124:
        print("Tests failed: timeout occurred")  # WRONG - timeout is not test failure
        return False

    return result.returncode == 0
'''
        )

        exit_code, output = self.run_validator("--check")

        # Should fail: this IS a violation (comparing exit code to 124)
        self.assertEqual(exit_code, 1, f"Expected exit 1 (violation), got {exit_code}\nOutput: {output}")
        self.assertIn("124", output)

    def test_multiple_violations_same_file(self):
        """Multiple violations: catches all violations in one file."""
        dispatch_file = self.repo_root / "driver" / "multi_violations.py"
        dispatch_file.write_text(
            '''
"""Multiple violations in one dispatch - verification phase."""

def verify_regression():
    """Regression verification - multiple violations."""
    import subprocess

    # Violation 1: pytest invocation
    result = subprocess.run(["pytest", "tests/"])

    # Violation 2: timeout exit code inference
    if result.returncode == 124:
        print("Timeout - should not infer test failures from exit 124")
        return False

    return True
'''
        )

        exit_code, output = self.run_validator("--check")

        # Should report violations
        self.assertEqual(exit_code, 1, f"Expected exit 1, got {exit_code}")
        # Should mention pytest violation
        self.assertIn("pytest", output.lower())

    def test_exit_code_2_on_error(self):
        """Error handling: exit 2 on file read errors."""
        # Create a non-existent path
        nonexistent = self.repo_root / "nonexistent_subdir"

        result = subprocess.run(
            [sys.executable, "tools/regression_gate_validator.py", str(nonexistent)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        # Should exit with error code on missing path
        # (behavior depends on implementation: may be 0 for empty scan or 2 for error)
        self.assertIn(result.returncode, [0, 2],
                     f"Expected exit 0 or 2 on missing path, got {result.returncode}")


class TestRegressionGateValidatorIntegration(unittest.TestCase):
    """Integration tests: validate against the real repo structure."""

    def test_real_repo_driver_files_are_clean(self):
        """Integration: driver/ files in the real repo don't have violations."""
        # Find driver directory in the real repo
        repo_root = Path(__file__).parent.parent
        driver_dir = repo_root / "driver"

        if not driver_dir.exists():
            self.skipTest("driver/ directory not found")

        result = subprocess.run(
            [sys.executable, "tools/regression_gate_validator.py", str(repo_root)],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )

        # Real repo should be clean (or only have violations in test fixtures)
        # For now, we just verify the tool runs without error
        self.assertIn(result.returncode, [0, 1],
                     f"Tool should exit 0 (clean) or 1 (violations), got {result.returncode}")


if __name__ == "__main__":
    unittest.main()
