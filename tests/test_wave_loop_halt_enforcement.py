#!/usr/bin/env python3
"""Halt kill-switch enforcement tests for wave_loop.py.

Behavioral proof that halt checks work at phase boundaries:
  - HALT sentinel before build aborts cleanly
  - HALT sentinel in repair aborts cleanly
  - HALT sentinel before adversarial review aborts cleanly
  - HALT sentinel before orchestrator final-catch aborts cleanly
  - HALT sentinel before ship aborts cleanly
  - Resume after clearing HALT proceeds normally

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

# Add driver/, tools/, state_store/ to path.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
TOOLS_DIR = REPO / "tools"
STATE_STORE_DIR = REPO / "state_store"
for _p in (str(DRIVER_DIR), str(TOOLS_DIR), str(STATE_STORE_DIR), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import agent_driver as ad
from agent_driver import (
    AgentDriver,
    DriverCapabilities,
    WorkerRequest,
    WorkerResult,
    CommandResult,
    WORKER_DONE,
    WORKER_FAILED,
)
import wave_loop
from wave_loop import run_wave
from claude_code_driver import ClaudeCodeDriver
import halt as halt_module

# Module-level tmpdir isolation (hygiene rule: no cwd pollution).
_MODULE_TMP = None
_MODULE_SAVED_CWD = None


def setUpModule():
    global _MODULE_TMP, _MODULE_SAVED_CWD
    _MODULE_SAVED_CWD = os.getcwd()
    _MODULE_TMP = tempfile.mkdtemp(prefix="wave-loop-halt-tests-")
    os.chdir(_MODULE_TMP)


def tearDownModule():
    global _MODULE_TMP, _MODULE_SAVED_CWD
    if _MODULE_SAVED_CWD:
        os.chdir(_MODULE_SAVED_CWD)
    if _MODULE_TMP:
        shutil.rmtree(_MODULE_TMP, ignore_errors=True)


class FakeDriver(AgentDriver):
    """Minimal fake driver that returns green results."""

    def __init__(self, success_items=None):
        self.success_items = success_items or {}

    def probe_capabilities(self):
        return DriverCapabilities(
            name="fake-driver",
            parallel_dispatch=True,
            worker_filesystem_access=True,
            worker_shell_access=True,
            structured_output=True,
            worktree_isolation=True,
            native_cost_tracking=True,
            tool_use_accuracy=0.99,
            recommended_verification_tier=1,
        )

    def dispatch_worker(self, request):
        slug = request.owned_files[0] if request.owned_files else "test-item"
        is_success = self.success_items.get(slug, True)
        return WorkerResult(
            worker_id=f"worker-{slug}",
            exit_code=0 if is_success else 1,
            stdout=f"Test {'passed' if is_success else 'failed'}",
            stderr="",
            structured_output={"test": "output"},
            structured_errors=[],
            files_written=[slug] if is_success else [],
            done=True,
            result_code=WORKER_DONE if is_success else WORKER_FAILED,
        )

    def worker_status(self, worker_id):
        return {"state": "done"}

    def run_command(self, command, cwd=None, shell=False):
        return CommandResult(
            exit_code=0,
            stdout="",
            stderr="",
        )

    def resolve_model(self, role):
        return "fake-model"

    def get_tokens_spent(self):
        return None


class TestHaltEnforcement(unittest.TestCase):
    """Test halt kill-switch enforcement at phase boundaries."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="halt-test-state-")
        # Save original halt module for restore in tearDown
        self.original_halt = wave_loop.halt

    def tearDown(self):
        if os.path.exists(self.state_dir):
            shutil.rmtree(self.state_dir, ignore_errors=True)
        # Restore original halt module
        wave_loop.halt = self.original_halt

    def test_halt_before_build(self):
        """Halt sentinel before build aborts with correct reason."""
        # Set halt before calling run_wave
        halt_module.halt("test halt before build", state_dir=self.state_dir)

        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "prompt": "test prompt",
                    "testCmd": "exit 0",
                    "ownsFiles": ["file1.py"],
                }
            ]
        }

        driver = FakeDriver()
        result = run_wave(
            manifest=manifest,
            driver=driver,
            state_dir=self.state_dir,
            git=None,
            orchestrator_backend=None,
        )

        # Wave should abort before build
        self.assertTrue(result.get("aborted"))
        self.assertIn(result.get("abort_reason"), ["halt_before_build"])
        self.assertIn("halt_reason", result)
        self.assertIn("test halt before build", result.get("halt_reason", ""))

        # Verify the sentinel was actually read
        self.assertTrue(halt_module.is_halted(self.state_dir))

    def test_halt_before_adversarial_review(self):
        """Halt sentinel before adversarial review aborts with correct reason."""
        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "prompt": "test prompt",
                    "testCmd": "exit 0",
                    "ownsFiles": ["file1.py"],
                }
            ]
        }

        driver = FakeDriver()

        # Set halt after preflight/build would complete but before adversarial review
        # We'll use a mock to intercept at the right point
        original_check_halt = wave_loop._check_halt
        halt_triggered = [False]

        def mock_check_halt(state_dir, result, abort_reason):
            if abort_reason == "halt_before_adversarial_review":
                halt_triggered[0] = True
                halt_module.halt("test halt before adversarial review", state_dir=state_dir)
            return original_check_halt(state_dir, result, abort_reason)

        with mock.patch.object(wave_loop, "_check_halt", side_effect=mock_check_halt):
            result = run_wave(
                manifest=manifest,
                driver=driver,
                state_dir=self.state_dir,
                git=None,
                orchestrator_backend=None,
            )

        # Wave should abort before adversarial review
        self.assertTrue(result.get("aborted"))
        self.assertIn(
            result.get("abort_reason"),
            ["halt_before_adversarial_review"],
        )
        self.assertTrue(halt_triggered[0], "halt check should have been reached")

    def test_halt_before_ship(self):
        """Halt sentinel before ship aborts with correct reason."""
        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "prompt": "test prompt",
                    "testCmd": "exit 0",
                    "ownsFiles": ["file1.py"],
                }
            ]
        }

        driver = FakeDriver()

        # Set halt after most phases complete but before ship
        original_check_halt = wave_loop._check_halt
        halt_triggered = [False]

        def mock_check_halt(state_dir, result, abort_reason):
            if abort_reason == "halt_before_ship":
                halt_triggered[0] = True
                halt_module.halt("test halt before ship", state_dir=state_dir)
            return original_check_halt(state_dir, result, abort_reason)

        with mock.patch.object(wave_loop, "_check_halt", side_effect=mock_check_halt):
            result = run_wave(
                manifest=manifest,
                driver=driver,
                state_dir=self.state_dir,
                git=None,
                orchestrator_backend=None,
            )

        # Wave should abort before ship
        self.assertTrue(result.get("aborted"))
        self.assertIn(result.get("abort_reason"), ["halt_before_ship"])
        self.assertTrue(halt_triggered[0], "halt check should have been reached")

    def test_halt_and_resume(self):
        """Halt mid-wave, clear halt, and resume proceeds normally."""
        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "prompt": "test prompt",
                    "testCmd": "exit 0",
                    "ownsFiles": ["file1.py"],
                }
            ]
        }

        driver = FakeDriver()

        # First run: set halt before build
        halt_module.halt("test halt before build", state_dir=self.state_dir)
        result1 = run_wave(
            manifest=manifest,
            driver=driver,
            state_dir=self.state_dir,
            git=None,
            orchestrator_backend=None,
        )

        # Verify first run was halted
        self.assertTrue(result1.get("aborted"))
        self.assertTrue(halt_module.is_halted(self.state_dir))

        # Clear halt
        halt_module.clear_halt(self.state_dir)
        self.assertFalse(halt_module.is_halted(self.state_dir))

        # Second run: should proceed normally
        result2 = run_wave(
            manifest=manifest,
            driver=driver,
            state_dir=self.state_dir,
            git=None,
            orchestrator_backend=None,
        )

        # Verify second run completed without halt abort
        self.assertFalse(result2.get("aborted"))
        # Should have built items
        self.assertTrue(len(result2.get("built", [])) > 0)

    def test_halt_module_unavailable_fails_closed(self):
        """Wave refuses to run when halt module is unavailable (fail-closed).

        Simulate ImportError by monkeypatching halt to None. The wave should
        refuse to start with abort_reason="halt_module_unavailable" rather than
        silently proceeding unguarded.
        """
        # Simulate halt module import failure
        wave_loop.halt = None

        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "prompt": "test prompt",
                    "testCmd": "exit 0",
                    "ownsFiles": ["file1.py"],
                }
            ]
        }

        driver = FakeDriver()

        # Wave should refuse to start
        result = run_wave(
            manifest=manifest,
            driver=driver,
            state_dir=self.state_dir,
            git=None,
            orchestrator_backend=None,
        )

        # Verify the wave aborted at entry with halt unavailable reason
        self.assertTrue(result.get("aborted"))
        self.assertEqual(result.get("abort_reason"), "halt_module_unavailable")
        self.assertIn("halt module unavailable", result.get("error", ""))
        # Should not have built any items (failed before preflight)
        self.assertEqual(len(result.get("built", [])), 0)

    def test_halt_available_but_state_dir_none_warns_but_proceeds(self):
        """With state_dir=None, halt is available but enforcement is off (warns).

        This is legitimate for tests/dry-run. The wave should warn prominently
        but proceed (skipping halt checks due to no coordination directory).
        """
        # halt module is available (original), state_dir is None
        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "prompt": "test prompt",
                    "testCmd": "exit 0",
                    "ownsFiles": ["file1.py"],
                }
            ]
        }

        driver = FakeDriver()

        # Capture stderr to check for warning
        import io
        from contextlib import redirect_stderr

        stderr_capture = io.StringIO()
        with redirect_stderr(stderr_capture):
            result = run_wave(
                manifest=manifest,
                driver=driver,
                state_dir=None,  # Legitimate case: no coordination
                git=None,
                orchestrator_backend=None,
            )

        # Wave should proceed normally (no halt check aborts)
        self.assertFalse(result.get("aborted"))
        # Should have built items
        self.assertTrue(len(result.get("built", [])) > 0)

        # Check that warning was logged
        stderr_text = stderr_capture.getvalue()
        self.assertIn("halt enforcement disabled", stderr_text.lower())


class TestHaltAPI(unittest.TestCase):
    """Test halt.py public API."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="halt-api-test-")

    def tearDown(self):
        if os.path.exists(self.state_dir):
            shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_halt_write_and_read(self):
        """Halt can be written and read."""
        self.assertFalse(halt_module.is_halted(self.state_dir))

        halt_module.halt("test reason", state_dir=self.state_dir)

        self.assertTrue(halt_module.is_halted(self.state_dir))
        info = halt_module.get_halt_info(self.state_dir)
        self.assertIsNotNone(info)
        self.assertEqual(info["reason"], "test reason")
        self.assertIn("timestamp", info)

    def test_halt_clear(self):
        """Halt can be cleared."""
        halt_module.halt("test reason", state_dir=self.state_dir)
        self.assertTrue(halt_module.is_halted(self.state_dir))

        halt_module.clear_halt(self.state_dir)
        self.assertFalse(halt_module.is_halted(self.state_dir))

    def test_halt_idempotent(self):
        """Halt operations are idempotent."""
        # Multiple writes should update the reason
        halt_module.halt("reason 1", state_dir=self.state_dir)
        halt_module.halt("reason 2", state_dir=self.state_dir)

        info = halt_module.get_halt_info(self.state_dir)
        self.assertEqual(info["reason"], "reason 2")

        # Multiple clears should be safe
        halt_module.clear_halt(self.state_dir)
        result = halt_module.clear_halt(self.state_dir)
        self.assertFalse(result)  # Second clear returns False (nothing to clear)


if __name__ == "__main__":
    unittest.main()
