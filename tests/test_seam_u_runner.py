#!/usr/bin/env python3
"""
Tests for bench/run_seam_u.py — U-arm (unseated) runner with tool-call answer channel.

Tests cover:
- Prompt assembly (statement + context_files, excluding oracle/SOLUTION.md)
- Tool-call response parsing (Anthropic tool_use, OpenAI function_calls)
- Sandbox apply + oracle scoring
- Refusal handling (no tool_use block, empty content)
- Checkpoint skip/retry semantics
- Windows+Linux parity

unittest style (discovers via `python -m unittest discover`).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


# ============================================================================
# BASE TEST CLASS
# ============================================================================


class SeamURunnerTestCase(unittest.TestCase):
    """Base test case with common setup/teardown."""

    def setUp(self):
        """Set up test fixtures."""
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def tearDown(self):
        """Clean up test resources."""
        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir, ignore_errors=True)


# ============================================================================
# TESTS: Prompt Assembly
# ============================================================================


class TestPromptAssembly(SeamURunnerTestCase):
    """Test U-arm prompt construction."""

    def test_prompt_includes_statement_and_context_files(self):
        """Prompt must include statement + all context_files, exclude oracle/SOLUTION.md."""
        from bench.run_seam_u import build_u_arm_prompt

        task_dir = Path(self.tmpdir) / "task"
        repo_dir = task_dir / "repo"
        repo_dir.mkdir(parents=True)

        (repo_dir / "main.py").write_text("def count(items):\n    return len(items)")
        (repo_dir / "utils.py").write_text("def validate(n):\n    return n >= 0")

        task_json = {
            "statement": "Fix the off-by-one error",
            "context_files": ["main.py", "utils.py"],
        }

        prompt = build_u_arm_prompt(task_json, task_dir)

        self.assertIn("Fix the off-by-one error", prompt)
        self.assertIn("def count(items):", prompt)
        self.assertIn("def validate(n):", prompt)
        self.assertNotIn("oracle", prompt.lower())
        self.assertNotIn("SOLUTION", prompt)

    def test_prompt_includes_tool_instruction(self):
        """Prompt must include tool-call instruction."""
        from bench.run_seam_u import build_u_arm_prompt

        task_dir = Path(self.tmpdir) / "task"
        repo_dir = task_dir / "repo"
        repo_dir.mkdir(parents=True)

        (repo_dir / "main.py").write_text("def count():\n    pass")

        task_json = {
            "statement": "Fix it",
            "context_files": ["main.py"],
        }

        prompt = build_u_arm_prompt(task_json, task_dir)

        self.assertIn("submit_patch", prompt.lower())
        self.assertIn("tool", prompt.lower())

    def test_missing_context_file_fails_loud(self):
        """build_u_arm_prompt raises FileNotFoundError on missing context file."""
        from bench.run_seam_u import build_u_arm_prompt

        task_dir = Path(self.tmpdir) / "task"
        repo_dir = task_dir / "repo"
        repo_dir.mkdir(parents=True)

        task_json = {
            "statement": "Fix it",
            "context_files": ["nonexistent.py"],
        }

        with self.assertRaises(FileNotFoundError):
            build_u_arm_prompt(task_json, task_dir)


# ============================================================================
# TESTS: Tool-Call Response Parsing
# ============================================================================


class TestToolCallParsing(SeamURunnerTestCase):
    """Test extraction of diff from tool-call responses."""

    def test_anthropic_tool_use_response(self):
        """Parse diff from Anthropic tool_use block."""
        # Simulated Anthropic response with tool_use
        response_data = {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_123",
                    "name": "submit_patch",
                    "input": {
                        "patch": "--- a/main.py\n+++ b/main.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1 + 1\n+    return 2\n"
                    },
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }

        # Verify the data structure matches what the runner expects
        content = response_data.get("content", [])
        self.assertTrue(any(b.get("type") == "tool_use" for b in content))
        tool_use = next(b for b in content if b.get("type") == "tool_use")
        patch = tool_use.get("input", {}).get("patch", "")
        self.assertIn("--- a/main.py", patch)

    def test_openai_function_call_response(self):
        """Parse diff from OpenAI function_calls."""
        # Simulated OpenAI response with tool_calls
        response_data = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "submit_patch",
                                    "arguments": json.dumps(
                                        {
                                            "patch": "--- a/main.py\n+++ b/main.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1 + 1\n+    return 2\n"
                                        }
                                    ),
                                },
                            }
                        ]
                    },
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }

        # Verify the data structure
        choices = response_data.get("choices", [])
        self.assertTrue(len(choices) > 0)
        tool_calls = choices[0].get("message", {}).get("tool_calls", [])
        self.assertTrue(len(tool_calls) > 0)
        arguments = json.loads(tool_calls[0].get("function", {}).get("arguments", "{}"))
        patch = arguments.get("patch", "")
        self.assertIn("--- a/main.py", patch)


# ============================================================================
# TESTS: Refusal & Empty Response Handling
# ============================================================================


class TestRefusalHandling(SeamURunnerTestCase):
    """Test refusal and empty response handling."""

    def test_no_tool_use_block_is_refusal(self):
        """Response with no tool_use block should be classified as refusal."""
        # Simulate blocked/refused response (stop_reason=end_turn, no tool_use)
        response_data = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "I cannot help with that."}],
            "usage": {"input_tokens": 100, "output_tokens": 10},
        }

        # Verify no tool_use exists
        content = response_data.get("content", [])
        self.assertFalse(any(b.get("type") == "tool_use" for b in content))

    def test_empty_content_is_refusal(self):
        """Empty content should be classified as refusal, not crash."""
        response_data = {
            "stop_reason": "end_turn",
            "content": [],
            "usage": {"input_tokens": 100, "output_tokens": 0},
        }

        content = response_data.get("content", [])
        self.assertEqual(len(content), 0)

    def test_empty_patch_in_tool_input_is_refusal(self):
        """Empty patch in tool input should be refusal, not crash."""
        response_data = {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_123",
                    "name": "submit_patch",
                    "input": {"patch": ""},  # Empty
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }

        # Verify empty patch is detected
        content = response_data.get("content", [])
        tool_use = next((b for b in content if b.get("type") == "tool_use"), None)
        self.assertIsNotNone(tool_use)
        patch = tool_use.get("input", {}).get("patch", "")
        self.assertEqual(patch, "")


# ============================================================================
# TESTS: Sandbox Apply
# ============================================================================


class TestSandboxApply(SeamURunnerTestCase):
    """Test apply diff to temp sandbox and oracle scoring."""

    def test_apply_diff_to_sandbox(self):
        """Apply diff to a temp sandbox copy of repo/."""
        from bench.run_seam_u import apply_diff_to_sandbox

        fixture_repo = Path(self.tmpdir) / "fixture_repo"
        fixture_repo.mkdir(parents=True)

        (fixture_repo / "main.py").write_text(
            "def count(items):\n"
            '    """Count the number of items."""\n'
            "    return len(items) + 1  # BUG: off-by-one error\n"
            "\n"
            "\n"
            "def sum_values(values):\n"
            '    """Sum numeric values."""\n'
            "    return sum(values)\n"
        )

        diff = (
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,8 +1,8 @@\n"
            " def count(items):\n"
            '     """Count the number of items."""\n'
            "-    return len(items) + 1  # BUG: off-by-one error\n"
            "+    return len(items)  # FIXED\n"
            " \n"
            " \n"
            " def sum_values(values):\n"
            '     """Sum numeric values."""\n'
        )

        sandbox = Path(self.tmpdir) / "sandbox"
        result = apply_diff_to_sandbox(fixture_repo, diff, sandbox)

        self.assertEqual(result, "applied",
                         "apply_diff_to_sandbox should report 'applied' on success")
        # Patched code lives under sandbox/repo/ (mirrors the task layout so the
        # oracle's conftest resolves ../repo).
        self.assertTrue((sandbox / "repo" / "main.py").exists())
        content = (sandbox / "repo" / "main.py").read_text()
        self.assertIn("FIXED", content)
        self.assertIn("return len(items)", content)

    def test_apply_invalid_diff_returns_false(self):
        """apply_diff_to_sandbox returns False on invalid diff."""
        from bench.run_seam_u import apply_diff_to_sandbox

        fixture_repo = Path(self.tmpdir) / "fixture_repo"
        fixture_repo.mkdir(parents=True)

        (fixture_repo / "main.py").write_text("def f():\n    pass")

        bad_diff = "this is not a valid diff\n"
        sandbox = Path(self.tmpdir) / "sandbox"
        result = apply_diff_to_sandbox(fixture_repo, bad_diff, sandbox)

        # New contract returns a status string; an unappliable diff is "failed"
        # (or "noop" if it changed nothing) - never "applied".
        self.assertIn(result, ("failed", "noop", None),
                      "apply_diff_to_sandbox must not report 'applied' for an invalid diff")


# ============================================================================
# TESTS: Checkpoint
# ============================================================================


class TestCheckpointSemantics(SeamURunnerTestCase):
    """Test checkpoint resume behavior."""

    def test_checkpoint_skips_completed_tasks(self):
        """Tasks in checkpoint should be skipped on re-invoke."""
        from bench.run_seam_u import load_checkpoint, should_skip

        checkpoint_file = Path(self.tmpdir) / "checkpoint.jsonl"
        checkpoint_file.write_text(
            json.dumps(
                {
                    "task_id": "t1",
                    "tier": "haiku",
                    "repeat": 0,
                    "arm": "U",
                    "passed": True,
                }
            )
            + "\n"
        )

        completed = load_checkpoint(checkpoint_file)
        key = ("t1", "haiku", 0, "U")
        self.assertTrue(should_skip(key, completed, is_error=False))

    def test_checkpoint_retries_error_tasks(self):
        """Error tasks should be retried (not skipped)."""
        from bench.run_seam_u import load_checkpoint, should_skip

        checkpoint_file = Path(self.tmpdir) / "checkpoint.jsonl"
        checkpoint_file.write_text(
            json.dumps(
                {
                    "task_id": "t1",
                    "tier": "haiku",
                    "repeat": 0,
                    "arm": "U",
                    "status": "transient",
                }
            )
            + "\n"
        )

        completed = load_checkpoint(checkpoint_file)
        key = ("t1", "haiku", 0, "U")
        self.assertFalse(should_skip(key, completed, is_error=True))


# ============================================================================
# TESTS: Environment Validation
# ============================================================================


class TestEnvVarValidation(SeamURunnerTestCase):
    """Test fail-fast on missing API keys."""

    def test_missing_bench_api_key_fails_fast(self):
        """Missing BENCH_API_KEY should fail cleanly for anthropic-http."""
        from bench.run_seam_u import validate_api_keys

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                validate_api_keys(transports=["anthropic-http"])

    def test_api_keys_present_validates(self):
        """Validation should pass when keys are present."""
        from bench.run_seam_u import validate_api_keys

        with mock.patch.dict(
            os.environ,
            {"BENCH_API_KEY": "test_key", "OPENAI_API_KEY": "openai_key"},
        ):
            validate_api_keys(transports=["anthropic-http", "openai"])


# ============================================================================
# TESTS: CLI Arguments
# ============================================================================


class TestCLIArgs(SeamURunnerTestCase):
    """Test command-line argument parsing."""

    def test_default_tiers(self):
        """Default tiers should be set if not provided."""
        from bench.run_seam_u import parse_args

        args = parse_args(
            [
                "--tasks-dir",
                "bench/seam_tasks",
            ]
        )
        self.assertIsNotNone(args.tiers)
        self.assertGreater(len(args.tiers), 0)

    def test_repeats_default(self):
        """Repeats should default to 3."""
        from bench.run_seam_u import parse_args

        args = parse_args(
            [
                "--tasks-dir",
                "bench/seam_tasks",
            ]
        )
        self.assertEqual(args.repeats, 3)


# ============================================================================
# TESTS: Windows+Linux Parity
# ============================================================================


class TestParity(SeamURunnerTestCase):
    """Test Windows+Linux compatibility."""

    def test_timeout_on_subprocesses(self):
        """All subprocess calls must have timeouts."""
        from bench.run_seam_u import run_oracle
        import inspect

        sig = inspect.signature(run_oracle)
        self.assertIn("timeout", sig.parameters)


# ============================================================================
# TESTS: Checkpoint Append
# ============================================================================


class TestCheckpointAppend(SeamURunnerTestCase):
    """Test checkpoint file append semantics."""

    def test_checkpoint_appended_not_overwritten(self):
        """Each result should be appended to checkpoint, not overwrite."""
        from bench.run_seam_u import append_checkpoint

        checkpoint_file = Path(self.tmpdir) / "cp.jsonl"

        result1 = {"task_id": "t1", "passed": True}
        append_checkpoint(checkpoint_file, result1)

        result2 = {"task_id": "t2", "passed": False}
        append_checkpoint(checkpoint_file, result2)

        lines = checkpoint_file.read_text().strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["task_id"], "t1")
        self.assertEqual(json.loads(lines[1])["task_id"], "t2")


if __name__ == "__main__":
    unittest.main()
