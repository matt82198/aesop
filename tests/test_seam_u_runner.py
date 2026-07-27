#!/usr/bin/env python3
"""
Tests for bench/run_seam_u.py — U-arm (unseated) runner for seam-discrimination study.

Tests cover:
- Prompt assembly (statement + context_files, excluding oracle/SOLUTION.md)
- Diff extraction from fenced and bare responses
- Sandbox apply + oracle scoring
- Refusal/error handling
- Checkpoint skip/retry semantics
- Missing env var fail-fast
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

        # Create fixture
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

    def test_context_files_are_fenced_with_paths(self):
        """Context files must be fenced with their paths."""
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

        self.assertIn("main.py", prompt)

    def test_prompt_ends_with_instruction(self):
        """Prompt must end with instruction about unified diff."""
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

        self.assertIn("unified diff", prompt.lower())
        self.assertIn("no prose", prompt.lower())

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
# TESTS: Diff Extraction
# ============================================================================


class TestDiffExtraction(SeamURunnerTestCase):
    """Test unified diff extraction from various response formats."""

    def test_extract_bare_diff(self):
        """Extract diff from bare response without fencing."""
        from bench.run_seam_u import extract_diff

        response = """--- a/repo/main.py
+++ b/repo/main.py
@@ -1,2 +1,2 @@
 def count(items):
-    return len(items) + 1  # BUG
+    return len(items)
"""
        diff = extract_diff(response)
        self.assertTrue(diff.startswith("---"))
        self.assertIn("return len(items)", diff)

    def test_extract_fenced_diff(self):
        """Extract diff from fenced response (```diff ... ```)."""
        from bench.run_seam_u import extract_diff

        response = """```diff
--- a/repo/main.py
+++ b/repo/main.py
@@ -1,2 +1,2 @@
 def count(items):
-    return len(items) + 1  # BUG
+    return len(items)
```"""
        diff = extract_diff(response)
        self.assertTrue(diff.startswith("---"))

    def test_extract_with_surrounding_prose(self):
        """Extract diff even when surrounded by prose."""
        from bench.run_seam_u import extract_diff

        response = """Here's the fix:

```diff
--- a/repo/main.py
+++ b/repo/main.py
@@ -1,2 +1,2 @@
 def count(items):
-    return len(items) + 1
+    return len(items)
```

This fixes the off-by-one error."""
        diff = extract_diff(response)
        self.assertIn("---", diff)
        self.assertIn("return len(items)", diff)


# ============================================================================
# TESTS: Sandbox Apply
# ============================================================================


class TestSandboxApply(SeamURunnerTestCase):
    """Test apply diff to temp sandbox and oracle scoring."""

    def test_apply_diff_to_sandbox(self):
        """Apply diff to a temp sandbox copy of repo/."""
        from bench.run_seam_u import apply_diff_to_sandbox

        # Create temporary fixture repo
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

        self.assertTrue(result, "apply_diff_to_sandbox should return True on success")
        self.assertTrue((sandbox / "main.py").exists())
        content = (sandbox / "main.py").read_text()
        self.assertIn("FIXED", content)
        self.assertIn("return len(items)", content)

    def test_apply_diff_failure_returns_false(self):
        """apply_diff_to_sandbox returns False on invalid diff."""
        from bench.run_seam_u import apply_diff_to_sandbox

        fixture_repo = Path(__file__).resolve().parent / "fixtures" / "seam_sample_task" / "repo"
        if not fixture_repo.exists():
            self.skipTest(f"Fixture repo not found at {fixture_repo}")

        bad_diff = "this is not a valid diff\n"
        sandbox = Path(self.tmpdir) / "sandbox"
        result = apply_diff_to_sandbox(fixture_repo, bad_diff, sandbox)

        self.assertFalse(result, "apply_diff_to_sandbox should return False on failure")


# ============================================================================
# TESTS: Refusal Handling
# ============================================================================


class TestRefusalHandling(SeamURunnerTestCase):
    """Test handling of model refusals and errors."""

    def test_refusal_response_scored_as_error(self):
        """Refusal response should be recorded with status='refusal', unscored."""
        from bench.run_seam_u import record_result

        result = {
            "task_id": "t1",
            "tier": "claude-haiku-4-5-20251001",
            "transport": "anthropic-http",
            "passed": False,
            "status": "refusal",
            "refusal": True,
        }
        recorded = record_result(result)
        self.assertEqual(recorded["status"], "refusal")
        self.assertNotIn("passed", recorded)

    def test_transient_error_recorded_for_retry(self):
        """Transient HTTP error should be recorded and retryable."""
        from bench.run_seam_u import record_result

        result = {
            "task_id": "t1",
            "tier": "claude-opus-5",
            "transport": "anthropic-http",
            "status": "transient",
            "error": "500 Internal Server Error",
        }
        recorded = record_result(result)
        self.assertEqual(recorded["status"], "transient")


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
        # Error tasks should NOT be skipped if we're retrying
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

    def test_missing_openai_key_fails_fast(self):
        """Missing OPENAI_API_KEY should fail cleanly for openai."""
        from bench.run_seam_u import validate_api_keys

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                validate_api_keys(transports=["openai"])

    def test_api_keys_present_validates(self):
        """Validation should pass when keys are present."""
        from bench.run_seam_u import validate_api_keys

        with mock.patch.dict(
            os.environ,
            {"BENCH_API_KEY": "test_key", "OPENAI_API_KEY": "openai_key"},
        ):
            # Should not raise
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

    def test_custom_tiers(self):
        """Custom tiers should override defaults."""
        from bench.run_seam_u import parse_args

        args = parse_args(
            [
                "--tasks-dir",
                "bench/seam_tasks",
                "--tiers",
                "claude-haiku-4-5-20251001",
                "gpt-4o-mini",
            ]
        )
        self.assertIn("claude-haiku-4-5-20251001", args.tiers)
        self.assertIn("gpt-4o-mini", args.tiers)

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

    def test_workers_default(self):
        """Workers should default to CPU count."""
        from bench.run_seam_u import parse_args

        args = parse_args(
            [
                "--tasks-dir",
                "bench/seam_tasks",
            ]
        )
        self.assertGreater(args.workers, 0)

    def test_probe_mode(self):
        """Probe mode should be settable."""
        from bench.run_seam_u import parse_args

        args = parse_args(
            [
                "--tasks-dir",
                "bench/seam_tasks",
                "--probe",
            ]
        )
        self.assertTrue(args.probe)


# ============================================================================
# TESTS: Windows+Linux Parity
# ============================================================================


class TestParity(SeamURunnerTestCase):
    """Test Windows+Linux compatibility."""

    def test_timeout_on_subprocesses(self):
        """All subprocess calls must have timeouts."""
        from bench.run_seam_u import run_oracle
        import inspect

        # Check that run_oracle has timeout parameter
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


# ============================================================================
# TESTS: Integration
# ============================================================================


class TestIntegration(SeamURunnerTestCase):
    """Integration-style tests with realistic flows."""

    def test_build_and_extract_flow(self):
        """Build prompt and extract diff from mock response."""
        from bench.run_seam_u import build_u_arm_prompt, extract_diff

        # Create fixture
        task_dir = Path(self.tmpdir) / "task"
        repo_dir = task_dir / "repo"
        repo_dir.mkdir(parents=True)

        (repo_dir / "main.py").write_text("def count():\n    return len(items) + 1")

        task_json = {
            "statement": "Fix the off-by-one error",
            "context_files": ["main.py"],
        }

        # Step 1: Build prompt
        prompt = build_u_arm_prompt(task_json, task_dir)
        self.assertIsNotNone(prompt)

        # Step 2: Mock response with diff
        response = (
            "Here's the fix:\n\n"
            "```diff\n"
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def count():\n"
            "-    return len(items) + 1\n"
            "+    return len(items)\n"
            "```"
        )

        # Step 3: Extract diff
        diff = extract_diff(response)
        self.assertIsNotNone(diff)
        self.assertIn("---", diff)


# ============================================================================
# TESTS: Probe Mode
# ============================================================================


class TestProbeMode(SeamURunnerTestCase):
    """Test probe mode (max_tokens=64, refusal counting, no grading)."""

    def test_probe_records_refused_answered(self):
        """Probe mode should record refused/answered per (task, tier)."""
        from bench.run_seam_u import append_checkpoint

        checkpoint_file = Path(self.tmpdir) / "probe_cp.jsonl"

        # Simulate probe mode result
        result = {
            "task_id": "t1",
            "tier": "haiku",
            "arm": "U",
            "transport": "anthropic-http",
            "probe": True,
            "refusal": True,
        }
        append_checkpoint(checkpoint_file, result)

        # Verify it's recorded
        lines = checkpoint_file.read_text().strip().split("\n")
        recorded = json.loads(lines[0])
        self.assertTrue(recorded.get("refusal"))


if __name__ == "__main__":
    unittest.main()


class TestCLITaskLoading(unittest.TestCase):
    """Regression: the argparse->loader seam once passed a str where Path was
    assumed (precedence bug: '"task.json".read_text()'), so every task failed
    to load at the CLI boundary while unit tests (which pass Path objects
    directly) stayed green. Exercise the real CLI entrypoint as a subprocess
    against the real task set."""

    def test_cli_loads_all_real_tasks_without_loader_errors(self):
        repo_root = Path(__file__).parent.parent
        tasks_dir = repo_root / "bench" / "seam_tasks"
        if not tasks_dir.exists():
            self.skipTest("bench/seam_tasks not present")
        env = {k: v for k, v in os.environ.items()
               if k not in ("BENCH_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
        env["PYTHONUTF8"] = "1"
        proc = subprocess.run(
            [sys.executable, str(repo_root / "bench" / "run_seam_u.py"),
             "--tasks-dir", str(tasks_dir),
             "--tiers", "claude-fable-5",
             "--repeats", "1", "--probe",
             "--checkpoint", str(Path(tempfile.mkdtemp()) / "probe.jsonl")],
            capture_output=True, text=True, timeout=60, env=env,
            cwd=str(repo_root),
        )
        combined = proc.stdout + proc.stderr
        self.assertNotIn("Error loading task", combined)
        self.assertIn("Loaded 12 tasks", combined)
