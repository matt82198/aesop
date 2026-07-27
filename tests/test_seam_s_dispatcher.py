#!/usr/bin/env python3
"""Tests for bench/run_seam_s.py — S-arm dispatcher with REAL REPAIR LOOP (TDD).

Tests verify:
  1. Checkpoint keys include arm (no S/U collision)
  2. Bounded repair loop: failures trigger next attempt
  3. Repair loop appends failure output to next prompt
  4. Retries_used counts actual repair attempts
  5. Oracle never visible until grading
  6. Verification policy drives repair_cap (not hardcoded)

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

# Add driver to path.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

import bench.run_seam_s as seam_s  # noqa: E402


class TestCheckpointKeyIncludesArm(unittest.TestCase):
    """Test that checkpoint keys include arm to avoid S/U collisions."""

    def test_checkpoint_key_format(self):
        """Checkpoint key is (task_id, tier, repeat, arm)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_path = Path(tmpdir) / "checkpoint.jsonl"

            # Save one result with arm="S".
            result = seam_s.Result(
                task_id="task1",
                band="starter",
                tier="claude-haiku",
                repeat=1,
                arm="S",
                backend="anthropic",
                passed=True,
                worker_verdict="Fixed",
                retries_used=0,
                tokens_spent=100,
                duration_s=5.0,
                status="scored",
            )
            seam_s.save_result(checkpoint_path, result)

            # Load checkpoint.
            completed = seam_s.load_checkpoint(checkpoint_path)

            # Key MUST include arm.
            key = ("task1", "claude-haiku", 1, "S")
            self.assertIn(key, completed)

            # Verify arm is in the loaded result.
            loaded = completed[key]
            self.assertEqual(loaded.arm, "S")

    def test_s_and_u_arms_no_collision(self):
        """S-arm and U-arm with same (task, tier, repeat) stored separately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_path = Path(tmpdir) / "checkpoint.jsonl"

            # Save S-arm result.
            result_s = seam_s.Result(
                task_id="task1",
                band="starter",
                tier="claude-haiku",
                repeat=1,
                arm="S",
                backend="anthropic",
                passed=True,
                worker_verdict="Fixed",
                retries_used=1,
                tokens_spent=100,
                duration_s=5.0,
                status="scored",
            )
            seam_s.save_result(checkpoint_path, result_s)

            # Save U-arm with same (task, tier, repeat) but different arm.
            result_u = seam_s.Result(
                task_id="task1",
                band="starter",
                tier="claude-haiku",
                repeat=1,
                arm="U",
                backend="anthropic",
                passed=False,
                worker_verdict="Incomplete",
                retries_used=0,
                tokens_spent=150,
                duration_s=6.0,
                status="scored",
            )
            seam_s.save_result(checkpoint_path, result_u)

            # Load checkpoint.
            completed = seam_s.load_checkpoint(checkpoint_path)

            # Both must be present with different keys.
            self.assertEqual(len(completed), 2)
            s_key = ("task1", "claude-haiku", 1, "S")
            u_key = ("task1", "claude-haiku", 1, "U")
            self.assertIn(s_key, completed)
            self.assertIn(u_key, completed)

            # Verify they have different data.
            s_result = completed[s_key]
            u_result = completed[u_key]
            self.assertTrue(s_result.passed)
            self.assertFalse(u_result.passed)
            self.assertEqual(s_result.retries_used, 1)
            self.assertEqual(u_result.retries_used, 0)


class TestBoundedRepairLoop(unittest.TestCase):
    """Test bounded repair loop with failure appending."""

    def test_first_attempt_succeeds(self):
        """First dispatch_item succeeds -> retries_used=0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "task001"
            task_dir.mkdir()

            task_json = {
                "task_id": "task001",
                "band": "starter",
                "statement": "Fix the function",
                "context_files": ["main.py"],
                "oracle_cmd": "python -m pytest oracle -q",
            }
            (task_dir / "task.json").write_text(json.dumps(task_json))

            repo_dir = task_dir / "repo"
            repo_dir.mkdir()
            (repo_dir / "main.py").write_text("x = 1")

            (task_dir / "oracle").mkdir()

            task = seam_s.load_task(task_dir)

            # Mock driver.
            mock_driver = Mock()
            mock_driver.resolve_model.return_value = "claude-haiku"
            mock_driver.get_tokens_spent.return_value = 100

            with patch("bench.run_seam_s.dispatch_item") as mock_dispatch:
                mock_dispatch.return_value = {
                    "ok": True,
                    "testExit": 0,
                    "filesWritten": ["main.py"],
                    "error": None,
                }

                with patch("bench.run_seam_s.build_manifest_item") as mock_build:
                    mock_build.return_value = {
                        "slug": "task001",
                        "prompt": "Fix the function",
                        "ownsFiles": ["main.py"],
                        "testCmd": "pytest",
                        "model": "claude-haiku",
                    }

                    sandbox = Path(tmpdir) / "sandbox"
                    sandbox.mkdir()

                    ok, verdict, retries, tokens = seam_s.run_bounded_repair(
                        mock_driver, task, sandbox, repair_cap=2
                    )

                    self.assertTrue(ok)
                    self.assertEqual(retries, 0)  # No repairs needed
                    self.assertEqual(tokens, 100)

    def test_failure_appends_to_next_prompt(self):
        """First attempt fails, second attempt gets failure output in prompt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "task001"
            task_dir.mkdir()

            task_json = {
                "task_id": "task001",
                "band": "starter",
                "statement": "Fix it",
                "context_files": ["main.py"],
                "oracle_cmd": "python -m pytest oracle -q",
            }
            (task_dir / "task.json").write_text(json.dumps(task_json))

            repo_dir = task_dir / "repo"
            repo_dir.mkdir()
            (repo_dir / "main.py").write_text("x = 1")

            (task_dir / "oracle").mkdir()

            task = seam_s.load_task(task_dir)

            mock_driver = Mock()
            mock_driver.resolve_model.return_value = "claude-haiku"
            mock_driver.get_tokens_spent.return_value = 100

            with patch("bench.run_seam_s.dispatch_item") as mock_dispatch:
                # First: fail. Second: succeed.
                mock_dispatch.side_effect = [
                    {
                        "ok": False,
                        "testExit": 1,
                        "filesWritten": [],
                        "error": "AssertionError: x != 2",
                    },
                    {
                        "ok": True,
                        "testExit": 0,
                        "filesWritten": ["main.py"],
                        "error": None,
                    },
                ]

                with patch("bench.run_seam_s.build_manifest_item") as mock_build:
                    manifest = {
                        "slug": "task001",
                        "prompt": "Fix it",
                        "ownsFiles": ["main.py"],
                        "testCmd": "pytest",
                        "model": "claude-haiku",
                    }
                    mock_build.return_value = manifest

                    sandbox = Path(tmpdir) / "sandbox"
                    sandbox.mkdir()

                    ok, verdict, retries, tokens = seam_s.run_bounded_repair(
                        mock_driver, task, sandbox, repair_cap=2
                    )

                    self.assertTrue(ok)
                    self.assertEqual(retries, 1)  # One repair

                    # Verify second dispatch_item received updated prompt.
                    second_call_item = mock_dispatch.call_args_list[1][0][1]
                    second_prompt = second_call_item["prompt"]

                    # CRITICAL: second prompt must contain failure output.
                    self.assertIn("AssertionError: x != 2", second_prompt)
                    self.assertIn("Test failed with exit code 1", second_prompt)

    def test_retries_used_increments(self):
        """retries_used counts actual repair attempts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "task001"
            task_dir.mkdir()

            task_json = {
                "task_id": "task001",
                "band": "starter",
                "statement": "Fix it",
                "context_files": ["main.py"],
                "oracle_cmd": "python -m pytest oracle -q",
            }
            (task_dir / "task.json").write_text(json.dumps(task_json))

            repo_dir = task_dir / "repo"
            repo_dir.mkdir()
            (repo_dir / "main.py").write_text("x = 1")

            (task_dir / "oracle").mkdir()

            task = seam_s.load_task(task_dir)

            mock_driver = Mock()
            mock_driver.resolve_model.return_value = "claude-haiku"
            mock_driver.get_tokens_spent.return_value = 100

            with patch("bench.run_seam_s.dispatch_item") as mock_dispatch:
                # Fail, fail, succeed.
                mock_dispatch.side_effect = [
                    {"ok": False, "testExit": 1, "filesWritten": [], "error": "Err1"},
                    {"ok": False, "testExit": 1, "filesWritten": [], "error": "Err2"},
                    {"ok": True, "testExit": 0, "filesWritten": ["main.py"], "error": None},
                ]

                with patch("bench.run_seam_s.build_manifest_item") as mock_build:
                    manifest = {
                        "slug": "task001",
                        "prompt": "Fix it",
                        "ownsFiles": ["main.py"],
                        "testCmd": "pytest",
                        "model": "claude-haiku",
                    }
                    mock_build.return_value = manifest

                    sandbox = Path(tmpdir) / "sandbox"
                    sandbox.mkdir()

                    ok, verdict, retries, tokens = seam_s.run_bounded_repair(
                        mock_driver, task, sandbox, repair_cap=3
                    )

                    self.assertTrue(ok)
                    self.assertEqual(retries, 2)  # Two repairs before success


class TestOracleNeverVisibleBeforeGrading(unittest.TestCase):
    """Test oracle is injected only at grading time, not during repair."""

    def test_oracle_not_in_sandbox_during_repair(self):
        """Oracle directory only exists after run_oracle is called."""
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "task001"
            task_dir.mkdir()

            task_json = {
                "task_id": "task001",
                "band": "starter",
                "statement": "Fix it",
                "context_files": ["main.py"],
                "oracle_cmd": "python -m pytest oracle -q",
            }
            (task_dir / "task.json").write_text(json.dumps(task_json))

            repo_dir = task_dir / "repo"
            repo_dir.mkdir()
            (repo_dir / "main.py").write_text("x = 1")

            oracle_dir = task_dir / "oracle"
            oracle_dir.mkdir()
            (oracle_dir / "test_oracle.py").write_text("def test(): pass")

            task = seam_s.load_task(task_dir)

            with tempfile.TemporaryDirectory() as sandbox_tmpdir:
                sandbox = Path(sandbox_tmpdir)

                # Copy repo (simulating execute_task_run).
                for item in repo_dir.iterdir():
                    if item.is_file():
                        shutil.copy2(item, sandbox / item.name)

                # Oracle should NOT be here before grading.
                self.assertFalse((sandbox / "oracle").exists())

                # run_oracle copies it for grading.
                run_oracle_result = seam_s.run_oracle(oracle_dir, sandbox)

                # NOW oracle is present (after grading).
                self.assertTrue((sandbox / "oracle").exists())


class TestVerificationPolicyDrivesRepairCap(unittest.TestCase):
    """Test repair_cap comes from verification_policy."""

    def test_repair_cap_from_policy_tier_1(self):
        """Tier 1 (Claude) has repair_cap=1 from policy."""
        from verification_policy import verification_policy
        from agent_driver import DriverCapabilities

        caps_tier1 = DriverCapabilities(
            name="test-tier1",
            parallel_dispatch=True,
            worker_filesystem_access=True,
            worker_shell_access=True,
            structured_output=True,
            worktree_isolation=True,
            recommended_verification_tier=1,
            tool_use_accuracy=0.99,
        )

        policy_tier1 = verification_policy(caps_tier1)
        self.assertEqual(policy_tier1["repair_cap"], 1)

    def test_repair_cap_from_policy_tier_2(self):
        """Tier 2 (Codex) has repair_cap=2 from policy."""
        from verification_policy import verification_policy
        from agent_driver import DriverCapabilities

        caps_tier2 = DriverCapabilities(
            name="test-tier2",
            parallel_dispatch=False,
            worker_filesystem_access=False,
            worker_shell_access=False,
            structured_output=True,
            worktree_isolation=False,
            recommended_verification_tier=2,
            tool_use_accuracy=0.92,
        )

        policy_tier2 = verification_policy(caps_tier2)
        self.assertEqual(policy_tier2["repair_cap"], 2)


if __name__ == "__main__":
    unittest.main()
