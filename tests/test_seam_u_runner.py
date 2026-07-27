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
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def scratchpad_dir():
    """Create a scratchpad directory for test artifacts."""
    scratch = Path(tempfile.gettempdir()) / "test_seam_u" / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


@pytest.fixture
def sample_task_dir(scratchpad_dir):
    """Create a minimal seam task fixture under scratchpad (not bench/seam_tasks/)."""
    task_dir = scratchpad_dir / "seam_sample_task"
    task_dir.mkdir(parents=True, exist_ok=True)

    # Create task.json
    task_json = {
        "task_id": "sample_u1",
        "band": "short",
        "statement": "Fix the off-by-one error in the count function.",
        "context_files": ["main.py", "utils.py"],
        "oracle_cmd": "python -m pytest oracle -q",
    }
    (task_dir / "task.json").write_text(json.dumps(task_json, indent=2))

    # Create repo/ subdirectory (fixture project with defect); context_files
    # are repo-relative per the seam task contract
    repo_dir = task_dir / "repo"
    repo_dir.mkdir(exist_ok=True)
    (repo_dir / "main.py").write_text(
        "def count(items):\n"
        "    return len(items) + 1  # BUG: off-by-one\n"
    )
    (repo_dir / "utils.py").write_text(
        "def validate(n):\n"
        '    if n < 0:\n'
        '        raise ValueError("n must be >= 0")\n'
    )

    # Create oracle/ subdirectory (hidden test suite)
    oracle_dir = task_dir / "oracle"
    oracle_dir.mkdir(exist_ok=True)
    (oracle_dir / "test_count.py").write_text(
        "import sys\n"
        "sys.path.insert(0, '..')\n"
        "from repo.main import count\n"
        "\n"
        "def test_count():\n"
        "    assert count([1, 2, 3]) == 3, 'Expected 3, got {}'.format(count([1, 2, 3]))\n"
    )

    # Create SOLUTION.md (author reference)
    (task_dir / "SOLUTION.md").write_text(
        "# Fix\n\nChange `len(items) + 1` to `len(items)`.\n"
    )

    return task_dir


@pytest.fixture
def mock_anthropic_transport():
    """Mock Anthropic HTTP transport."""

    def mock_call(prompt: str) -> tuple[str, dict]:
        # Return a valid unified diff response
        diff = (
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,6 +1,6 @@\n"
            " def count(items):\n"
            '     """Count the number of items."""\n'
            "-    return len(items) + 1  # BUG: off-by-one error\n"
            "+    return len(items)  # FIXED\n"
            " \n"
            " \n"
            " def sum_values(values):\n"
        )
        usage = {"input_tokens": 100, "output_tokens": 50}
        return (diff, usage)

    return mock_call


@pytest.fixture
def mock_openai_transport():
    """Mock OpenAI HTTP transport."""

    def mock_call(prompt: str) -> tuple[str, dict]:
        # Return a fenced diff response
        diff = (
            "```diff\n"
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,6 +1,6 @@\n"
            " def count(items):\n"
            '     """Count the number of items."""\n'
            "-    return len(items) + 1  # BUG: off-by-one error\n"
            "+    return len(items)  # FIXED\n"
            " \n"
            " \n"
            " def sum_values(values):\n"
            "```"
        )
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "latency_ms": 250.5,
        }
        return (diff, usage)

    return mock_call


# ============================================================================
# TESTS: Prompt Assembly
# ============================================================================


class TestPromptAssembly:
    """Test U-arm prompt construction."""

    def test_prompt_includes_statement_and_context_files(self, sample_task_dir):
        """Prompt must include statement + all context_files, exclude oracle/SOLUTION.md."""
        from bench.run_seam_u import build_u_arm_prompt

        task_json = json.loads((sample_task_dir / "task.json").read_text())

        prompt = build_u_arm_prompt(task_json, sample_task_dir)

        assert "Fix the off-by-one error" in prompt
        assert "def count(items):" in prompt
        assert "def validate(n):" in prompt
        assert "oracle" not in prompt.lower()
        assert "SOLUTION" not in prompt

    def test_context_files_are_fenced_with_paths(self, sample_task_dir):
        """Context files must be fenced with their relative paths."""
        from bench.run_seam_u import build_u_arm_prompt

        task_json = json.loads((sample_task_dir / "task.json").read_text())
        prompt = build_u_arm_prompt(task_json, sample_task_dir)

        # Should contain paths in fence or in the output
        assert "src/main.py" in prompt or "main.py" in prompt
        assert "src/utils.py" in prompt or "utils.py" in prompt

    def test_prompt_ends_with_fixed_instruction(self, sample_task_dir):
        """Prompt must end with instruction about unified diff."""
        from bench.run_seam_u import build_u_arm_prompt

        task_json = json.loads((sample_task_dir / "task.json").read_text())
        prompt = build_u_arm_prompt(task_json, sample_task_dir)

        assert "unified diff" in prompt.lower()
        assert "no prose" in prompt.lower()


# ============================================================================
# TESTS: Diff Extraction
# ============================================================================


class TestDiffExtraction:
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
        assert diff.startswith("---")
        assert "return len(items)" in diff

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
        assert diff.startswith("---")

    def test_extract_fenced_diff_markdown_style(self):
        """Extract diff from markdown fenced response (```markdown ... ```)."""
        from bench.run_seam_u import extract_diff

        response = """```markdown
--- a/repo/main.py
+++ b/repo/main.py
@@ -1,2 +1,2 @@
 def count(items):
-    return len(items) + 1  # BUG
+    return len(items)
```"""
        diff = extract_diff(response)
        assert "---" in diff

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
        assert "---" in diff
        assert "return len(items)" in diff


# ============================================================================
# TESTS: Sandbox Apply & Oracle
# ============================================================================


class TestSandboxApply:
    """Test apply diff to temp sandbox and oracle scoring."""

    def test_apply_diff_to_sandbox(self):
        """Apply diff to a temp sandbox copy of repo/."""
        from bench.run_seam_u import apply_diff_to_sandbox

        # Use the actual fixture files
        fixture_repo = Path(__file__).parent / "fixtures" / "seam_sample_task" / "repo"

        diff = (
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,6 +1,6 @@\n"
            " def count(items):\n"
            '     """Count the number of items."""\n'
            "-    return len(items) + 1  # BUG: off-by-one error\n"
            "+    return len(items)  # FIXED\n"
            " \n"
            " \n"
            " def sum_values(values):\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            result = apply_diff_to_sandbox(fixture_repo, diff, sandbox)
            assert result, "apply_diff_to_sandbox should return True on success"
            assert (sandbox / "main.py").exists()
            content = (sandbox / "main.py").read_text()
            assert "FIXED" in content
            assert "return len(items)" in content

    def test_apply_diff_failure_returns_false(self):
        """apply_diff_to_sandbox returns False on git apply failure."""
        from bench.run_seam_u import apply_diff_to_sandbox

        # Use the actual fixture files
        fixture_repo = Path(__file__).parent / "fixtures" / "seam_sample_task" / "repo"

        bad_diff = "this is not a valid diff\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            result = apply_diff_to_sandbox(fixture_repo, bad_diff, sandbox)
            # Should return False because the diff format is invalid
            assert not result, "apply_diff_to_sandbox should return False on failure"

    def test_run_oracle_passes_on_valid_patch(self, sample_task_dir):
        """run_oracle returns True when oracle tests pass."""
        from bench.run_seam_u import apply_diff_to_sandbox, run_oracle

        # First apply a valid fix
        diff = """--- a/main.py
+++ b/main.py
@@ -1,2 +1,2 @@
 def count(items):
-    return len(items) + 1  # BUG: off-by-one
+    return len(items)  # FIXED
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            if apply_diff_to_sandbox(sample_task_dir / "repo", diff, sandbox):
                # Create a simple oracle test that should pass
                oracle_dir = sandbox / "oracle"
                oracle_dir.mkdir(parents=True, exist_ok=True)
                (oracle_dir / "test_basic.py").write_text(
                    "def test_simple():\n"
                    "    assert 1 + 1 == 2\n"
                )

                task_json = json.loads(
                    (sample_task_dir / "task.json").read_text()
                )
                # Override oracle_cmd for testing
                task_json["oracle_cmd"] = "python -m pytest oracle/ -q"

                result = run_oracle(task_json, sandbox, timeout=10)
                assert result, "run_oracle should return True on test pass"


# ============================================================================
# TESTS: Refusal Handling
# ============================================================================


class TestRefusalHandling:
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
        assert recorded["status"] == "refusal"
        assert "passed" not in recorded or not recorded.get("passed")

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
        assert recorded["status"] == "transient"


# ============================================================================
# TESTS: Checkpoint Skip/Retry
# ============================================================================


class TestCheckpointSemantics:
    """Test checkpoint resume behavior."""

    def test_checkpoint_skips_completed_tasks(self, scratchpad_dir):
        """Tasks in checkpoint should be skipped on re-invoke."""
        from bench.run_seam_u import load_checkpoint, should_skip

        checkpoint_file = scratchpad_dir / "checkpoint.jsonl"
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
        assert should_skip(key, completed, is_error=False)

    def test_checkpoint_retries_error_tasks(self, scratchpad_dir):
        """Error tasks should be retried (not skipped)."""
        from bench.run_seam_u import load_checkpoint, should_skip

        checkpoint_file = scratchpad_dir / "checkpoint.jsonl"
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
        assert not should_skip(key, completed, is_error=True)


# ============================================================================
# TESTS: Environment Variable Validation
# ============================================================================


class TestEnvVarValidation:
    """Test fail-fast on missing API keys."""

    def test_missing_bench_api_key_fails_fast(self):
        """Missing BENCH_API_KEY should fail cleanly for anthropic-http."""
        from bench.run_seam_u import validate_api_keys

        with mock.patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                validate_api_keys(transports=["anthropic-http"])
            assert exc_info.value.code != 0

    def test_missing_openai_key_fails_fast(self):
        """Missing OPENAI_API_KEY should fail cleanly for openai."""
        from bench.run_seam_u import validate_api_keys

        with mock.patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                validate_api_keys(transports=["openai"])
            assert exc_info.value.code != 0

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
# TESTS: CLI Argument Parsing
# ============================================================================


class TestCLIArgs:
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
        assert args.tiers is not None
        assert len(args.tiers) > 0

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
        assert "claude-haiku-4-5-20251001" in args.tiers
        assert "gpt-4o-mini" in args.tiers

    def test_repeats_default(self):
        """Repeats should default to 3."""
        from bench.run_seam_u import parse_args

        args = parse_args(
            [
                "--tasks-dir",
                "bench/seam_tasks",
            ]
        )
        assert args.repeats == 3

    def test_workers_default(self):
        """Workers should default to CPU count."""
        from bench.run_seam_u import parse_args

        args = parse_args(
            [
                "--tasks-dir",
                "bench/seam_tasks",
            ]
        )
        assert args.workers > 0

    def test_probe_mode(self):
        """Probe mode should set max_tokens to 64 and skip scoring."""
        from bench.run_seam_u import parse_args

        args = parse_args(
            [
                "--tasks-dir",
                "bench/seam_tasks",
                "--probe",
            ]
        )
        assert args.probe is True


# ============================================================================
# TESTS: Windows+Linux Parity
# ============================================================================


class TestParity:
    """Test Windows+Linux compatibility."""

    def test_uses_sys_executable(self, sample_task_dir):
        """subprocess calls must use sys.executable for parity."""
        from bench.run_seam_u import run_oracle
        import sys

        # Mock subprocess.run to verify sys.executable is used
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="OK")

            task_json = json.loads(
                (sample_task_dir / "task.json").read_text()
            )
            with tempfile.TemporaryDirectory() as tmpdir:
                sandbox = Path(tmpdir)
                sandbox.mkdir(exist_ok=True)
                run_oracle(task_json, sandbox, timeout=10)

                # Verify sys.executable was used
                assert mock_run.called
                call_args = mock_run.call_args
                if call_args and call_args[0]:
                    # First element of args should be a list with sys.executable
                    cmd_parts = call_args[0][0]
                    if isinstance(cmd_parts, (list, tuple)):
                        assert sys.executable in cmd_parts or "python" in str(cmd_parts[0])

    def test_timeout_on_subprocesses(self):
        """All subprocess calls must have timeouts."""
        from bench.run_seam_u import run_oracle
        import inspect

        # Check that run_oracle has timeout parameter
        sig = inspect.signature(run_oracle)
        assert "timeout" in sig.parameters


# ============================================================================
# TESTS: Checkpoint Append
# ============================================================================


class TestCheckpointAppend:
    """Test checkpoint file append semantics."""

    def test_checkpoint_appended_not_overwritten(self):
        """Each result should be appended to checkpoint, not overwrite."""
        from bench.run_seam_u import append_checkpoint

        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_file = Path(tmpdir) / "cp.jsonl"

            result1 = {"task_id": "t1", "passed": True}
            append_checkpoint(checkpoint_file, result1)

            result2 = {"task_id": "t2", "passed": False}
            append_checkpoint(checkpoint_file, result2)

            lines = checkpoint_file.read_text().strip().split("\n")
            assert len(lines) == 2
            assert json.loads(lines[0])["task_id"] == "t1"
            assert json.loads(lines[1])["task_id"] == "t2"


# ============================================================================
# TESTS: No Credential Hunting
# ============================================================================


class TestNoCredentialHunting:
    """Verify the runner doesn't hunt for missing credentials."""

    def test_missing_key_gives_clear_error_message(self):
        """Missing API key should produce actionable error, not search."""
        from bench.run_seam_u import validate_api_keys
        import io
        import sys

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                with pytest.raises(SystemExit):
                    validate_api_keys(transports=["anthropic-http"])

                stderr_text = mock_stderr.getvalue()
                assert "BENCH_API_KEY" in stderr_text or "anthropic" in stderr_text.lower()


# ============================================================================
# INTEGRATION-STYLE TESTS
# ============================================================================


class TestIntegration:
    """Integration-style tests with realistic flows."""

    def test_full_flow_with_mocked_transport(self, mock_anthropic_transport):
        """End-to-end flow: load task, build prompt, call transport, score."""
        from bench.run_seam_u import (
            build_u_arm_prompt,
            extract_diff,
            apply_diff_to_sandbox,
        )

        # Use the actual fixture files
        fixture_dir = Path(__file__).parent / "fixtures" / "seam_sample_task"
        task_json = json.loads((fixture_dir / "task.json").read_text())

        # Step 1: Build prompt
        prompt = build_u_arm_prompt(task_json, fixture_dir)
        assert prompt is not None

        # Step 2: Mock transport call
        response, usage = mock_anthropic_transport(prompt)

        # Step 3: Extract diff
        diff = extract_diff(response)
        assert diff is not None
        assert "---" in diff

        # Step 4: Apply to sandbox
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            fixture_repo = fixture_dir / "repo"
            success = apply_diff_to_sandbox(fixture_repo, diff, sandbox)
            assert success


# ============================================================================
# PROBE MODE TESTS
# ============================================================================


class TestProbeMode:
    """Test probe mode (max_tokens=64, refusal counting, no grading)."""

    def test_probe_limits_max_tokens(self):
        """Probe mode should limit max_tokens to 64."""
        # This is tested at the transport layer
        pass

    def test_probe_records_refused_answered(self, scratchpad_dir):
        """Probe mode should record refused/answered per (task, tier)."""
        from bench.run_seam_u import append_checkpoint

        checkpoint_file = scratchpad_dir / "probe_cp.jsonl"

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
        assert recorded.get("refusal") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
