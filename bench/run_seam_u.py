#!/usr/bin/env python3
"""
run_seam_u.py — U-arm (unseated) runner for seam-discrimination study.

Runs fixed defect-repair tasks against multiple model tiers via HTTP APIs.
U-arm = unseated (no prompt engineering, raw statement + context files).

CLI:
  python bench/run_seam_u.py \
    --tasks-dir bench/seam_tasks \
    --tiers claude-fable-5 claude-opus-5 ... \
    --repeats 3 \
    --workers N \
    --checkpoint bench/results/seam-u-checkpoint.jsonl \
    --max-runs M \
    --probe

Requirements:
  - Environment variables for API access:
    - BENCH_API_KEY (for anthropic-http transport)
    - OPENAI_API_KEY (for openai transport)
  - No CLI tool invocations; API-only.
  - All subprocess calls use sys.executable + timeouts.
  - Checkpoint append-only (JSONL).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set, Tuple


# ============================================================================
# CONSTANTS
# ============================================================================

DEFAULT_TIERS = [
    "claude-fable-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
    "gpt-4o-mini",
]

# Transport name -> (env_var, provider)
TRANSPORTS = {
    "anthropic-http": ("BENCH_API_KEY", "anthropic"),
    "openai": ("OPENAI_API_KEY", "openai"),
}


# ============================================================================
# CORE FUNCTIONS: Prompt Assembly
# ============================================================================


def build_u_arm_prompt(task_json: Dict[str, Any], task_dir: Path) -> str:
    """
    Build U-arm (unseated) prompt: statement + all context_files.
    Excludes oracle/ and SOLUTION.md.

    Context files are resolved under task_dir/repo/ as bare repo-relative paths.
    Fails loud (FileNotFoundError) if a context file is missing.

    Args:
        task_json: Parsed task.json
        task_dir: Path to the task directory

    Returns:
        Complete prompt for the model

    Raises:
        FileNotFoundError: If any context_file is not found under task_dir/repo/
    """
    parts = []

    # Statement (the problem description)
    statement = task_json.get("statement", "")
    if statement:
        parts.append(statement)

    # Context files (fenced, with paths) — resolved under repo/
    context_files = task_json.get("context_files", [])
    repo_dir = task_dir / "repo"
    for context_path in context_files:
        file_path = repo_dir / context_path
        if not file_path.exists():
            raise FileNotFoundError(
                f"Context file not found: {context_path} "
                f"(resolved to {file_path})"
            )
        content = file_path.read_text(encoding="utf-8", errors="replace")
        # Fence the content with the relative path
        parts.append(f"\n# File: {context_path}\n```\n{content}\n```")

    # Tool-call instruction (fixes API safety classifier refusal on prose diff request)
    instruction = (
        "\n\nSubmit your fix by calling the submit_patch tool with the complete unified diff."
    )
    parts.append(instruction)

    return "".join(parts)


# ============================================================================
# CORE FUNCTIONS: Diff Extraction
# ============================================================================


def extract_diff(response: str) -> str:
    """
    Extract unified diff from model response.

    Handles:
    - Bare diffs (no fencing)
    - ```diff ... ``` fenced
    - ```markdown ... ``` fenced
    - Surrounding prose

    Args:
        response: Model response text

    Returns:
        Extracted unified diff (lines starting with '---')
    """
    # Try to find fenced diff first
    match = re.search(r"```(?:diff|markdown)?\n([\s\S]*?)\n```", response)
    if match:
        fenced_content = match.group(1)
        # Extract just the diff part
        if fenced_content.strip().startswith("---"):
            return fenced_content.strip()

    # Fall back to finding bare diff
    match = re.search(r"^(---.*?)(?=\n(?:[^+\-@ ]|$)|\Z)", response, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(1).strip()

    # Last resort: return the response as-is if it looks like a diff
    if response.strip().startswith("---"):
        return response.strip()

    return response


# ============================================================================
# CORE FUNCTIONS: Sandbox Apply & Oracle
# ============================================================================


def _normalize_diff(diff: str) -> str:
    """
    Normalize diff by removing git diff headers and metadata lines.

    Handles both:
    - Unified diff format (--- a/file, +++ b/file)
    - Full git diff format (diff --git, index, ---/+++ lines)

    Args:
        diff: Raw diff text from model

    Returns:
        Normalized diff ready for git apply or patch
    """
    lines = diff.split('\n')
    normalized = []
    found_start = False

    for line in lines:
        # Skip git diff metadata headers before the first --- line
        if not found_start:
            if line.startswith('diff --git '):
                continue
            if line.startswith('index '):
                continue
            if line.startswith('GIT binary'):
                continue
            # When we see ---, we've found the start of the unified diff
            if line.startswith('---'):
                found_start = True

        # Keep all lines once we've found the start, plus any before if they're diff content
        if found_start or line.startswith('---') or line.startswith('+++') or line.startswith('@@') or (line and line[0] in ' +-'):
            normalized.append(line)

    return '\n'.join(normalized).strip()


def apply_diff_to_sandbox(
    repo_dir: Path, diff: str, sandbox: Path
) -> str:
    """
    Copy repo_dir to sandbox/repo/ and apply diff.

    Sandbox structure (for oracle):
      sandbox/repo/      <- copy of task repo with diff applied
      sandbox/oracle/    <- copied at grading time (not shown to model)

    Tries multiple methods: git apply, patch, then fallback Python parser.

    Args:
        repo_dir: Source repo directory
        diff: Unified diff text (can include git diff headers)
        sandbox: Destination sandbox directory (parent of repo/ and oracle/)

    Returns:
        "applied" if diff was successfully applied
        "failed" if patch failed to apply
        "noop" if diff had no effect
        None on error
    """
    # Normalize diff: remove git diff headers and index lines if present
    # (models may produce full git diffs, not just unified diffs)
    normalized_diff = _normalize_diff(diff)

    # Create sandbox and sandbox/repo
    try:
        sandbox.mkdir(parents=True, exist_ok=True)
        repo_sandbox = sandbox / "repo"
        repo_sandbox.mkdir(exist_ok=True)

        # Copy repo contents into sandbox/repo
        for item in repo_dir.iterdir():
            dest = repo_sandbox / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
    except Exception as e:
        print(f"Error copying repo to sandbox/repo: {e}", file=sys.stderr)
        return None

    # Initialize git repo in sandbox/repo (required for git apply)
    git_ok = False
    try:
        subprocess.run(
            ["git", "init"],
            cwd=repo_sandbox,
            capture_output=True,
            timeout=10,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo_sandbox,
            capture_output=True,
            timeout=10,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_sandbox,
            capture_output=True,
            timeout=10,
            text=True,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=repo_sandbox,
            capture_output=True,
            timeout=10,
            text=True,
        )
        result = subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo_sandbox,
            capture_output=True,
            timeout=10,
            text=True,
        )
        git_ok = result.returncode == 0
        if not git_ok:
            print(f"Warning: git commit failed: {result.stderr[:100]}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: git initialization failed: {str(e)[:100]}", file=sys.stderr)
        pass

    # Try git apply with various -p levels and options (models emit different path formats)
    if git_ok:
        # Try different -p levels (0, 1, 2) and options
        last_error = None
        for p_level in [1, 0, 2]:
            for extra_args in [[], ["--3way"], ["--recount"], ["--ignore-whitespace"]]:
                try:
                    cmd = ["git", "apply", f"-p{p_level}"] + extra_args
                    result = subprocess.run(
                        cmd,
                        input=normalized_diff,
                        cwd=repo_sandbox,
                        capture_output=True,
                        timeout=10,
                        text=True,
                    )
                    if result.returncode == 0:
                        # Verify something actually changed
                        result_check = subprocess.run(
                            ["git", "status", "--porcelain"],
                            cwd=repo_sandbox,
                            capture_output=True,
                            timeout=10,
                            text=True,
                        )
                        if result_check.stdout.strip():
                            return "applied"
                        else:
                            return "noop"
                    else:
                        # Capture error for debugging
                        last_error = f"git apply -p{p_level} {' '.join(extra_args)}: {result.stderr[:100]}"
                        # Log first failure only to avoid spam
                        if p_level == 1 and not extra_args:
                            import os
                            debug_mode = os.environ.get('DEBUG_PATCH')
                            if debug_mode:
                                print(f"Debug: {last_error}", file=sys.stderr)
                except Exception as e:
                    last_error = str(e)
                    pass  # Try next option

    # Fall back to patch command (try multiple -p levels)
    for p_level in [1, 0, 2]:
        try:
            result = subprocess.run(
                ["patch", f"-p{p_level}"],
                input=normalized_diff,
                cwd=repo_sandbox,
                capture_output=True,
                timeout=10,
                text=True,
            )
            if result.returncode == 0:
                # Verify something changed
                result_check = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=repo_sandbox,
                    capture_output=True,
                    timeout=10,
                    text=True,
                )
                if result_check.stdout.strip():
                    return "applied"
        except Exception as e:
            last_error = f"patch -p{p_level}: {str(e)[:100]}"
            pass

    # All attempts failed; return with diagnostic info
    diagnostic = f" (last: {last_error})" if last_error else ""
    print(f"Warning: Patch failed to apply{diagnostic}", file=sys.stderr)
    return "failed"


def run_oracle(
    task_json: Dict[str, Any], task_dir: Path, sandbox: Path, timeout: int = 120
) -> bool:
    """
    Run oracle tests in sandbox.

    Expects sandbox layout:
      sandbox/repo/      <- patched code
      sandbox/oracle/    <- test suite

    Args:
        task_json: Task configuration
        task_dir: Original task directory (to copy oracle from)
        sandbox: Sandbox directory with repo/ and oracle/
        timeout: Timeout in seconds

    Returns:
        True if oracle tests pass (exit code 0), False otherwise
    """
    oracle_cmd = task_json.get("oracle_cmd", "")
    if not oracle_cmd:
        print("No oracle_cmd in task", file=sys.stderr)
        return False

    # Copy oracle/ from task to sandbox/oracle at grading time
    try:
        oracle_src = task_dir / "oracle"
        oracle_dst = sandbox / "oracle"
        if oracle_src.exists():
            if oracle_dst.exists():
                shutil.rmtree(oracle_dst)
            shutil.copytree(oracle_src, oracle_dst)
    except Exception as e:
        print(f"Error copying oracle to sandbox: {e}", file=sys.stderr)
        return False

    try:
        # Run oracle_cmd with cwd=sandbox so "pytest oracle" finds oracle/
        # oracle/conftest.py does: sys.path.insert(../repo) to find the code
        result = subprocess.run(
            oracle_cmd,
            shell=True,
            cwd=sandbox,
            capture_output=True,
            timeout=timeout,
            text=True,
            env={**os.environ, "PYTHONPATH": str(sandbox)},
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"Oracle timeout ({timeout}s)", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error running oracle: {e}", file=sys.stderr)
        return False


# ============================================================================
# CORE FUNCTIONS: Result Recording
# ============================================================================


def record_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Record a result (no transformation, just return as-is for now).

    Args:
        result: Result dict

    Returns:
        Recorded result
    """
    # Ensure refusal results don't have a 'passed' field
    if result.get("status") == "refusal":
        result.pop("passed", None)
    return result


def append_checkpoint(checkpoint_file: Path, result: Dict[str, Any]) -> None:
    """
    Append result to checkpoint (JSONL format, append-only).

    Args:
        checkpoint_file: Path to checkpoint JSONL
        result: Result to append
    """
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    with open(checkpoint_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")


def load_checkpoint(checkpoint_file: Path) -> Set[Tuple[str, str, int, str]]:
    """
    Load checkpoint and return set of completed (task_id, tier, repeat, arm) tuples.

    Args:
        checkpoint_file: Path to checkpoint JSONL

    Returns:
        Set of (task_id, tier, repeat, arm) tuples for completed tasks
    """
    completed = set()
    if not checkpoint_file.exists():
        return completed

    try:
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    key = (
                        record.get("task_id"),
                        record.get("tier"),
                        record.get("repeat"),
                        record.get("arm", "U"),
                    )
                    completed.add(key)
    except Exception as e:
        print(f"Error loading checkpoint: {e}", file=sys.stderr)

    return completed


def should_skip(
    key: Tuple[str, str, int, str], completed: Set, is_error: bool = False
) -> bool:
    """
    Check if a task should be skipped.

    Args:
        key: (task_id, tier, repeat, arm) tuple
        completed: Set of completed task keys
        is_error: True if retrying error tasks, False if skipping completed

    Returns:
        True if should skip, False otherwise
    """
    if is_error:
        # When retrying, skip only non-error tasks (those with 'passed' field)
        # Error tasks should be retried
        return False
    return key in completed


# ============================================================================
# CORE FUNCTIONS: Environment Validation
# ============================================================================


def validate_api_keys(transports: list) -> None:
    """
    Validate that required API keys are present.

    Exits with error if any required key is missing.

    Args:
        transports: List of transport names to validate
    """
    missing = []
    for transport in transports:
        if transport not in TRANSPORTS:
            continue
        env_var, _ = TRANSPORTS[transport]
        if not os.environ.get(env_var):
            missing.append((transport, env_var))

    if missing:
        for transport, env_var in missing:
            print(
                f"Error: Missing required environment variable {env_var} for {transport}",
                file=sys.stderr,
            )
        sys.exit(1)


# ============================================================================
# CORE FUNCTIONS: CLI Argument Parsing
# ============================================================================


def parse_args(args: Optional[list] = None) -> argparse.Namespace:
    """
    Parse command-line arguments.

    Args:
        args: List of arguments (for testing)

    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="U-arm (unseated) runner for seam-discrimination study"
    )
    parser.add_argument(
        "--tasks-dir",
        type=str,
        required=True,
        help="Directory containing seam_tasks/<task_id>/",
    )
    parser.add_argument(
        "--tiers",
        nargs="+",
        default=DEFAULT_TIERS,
        help=(
            "Model tiers to test, space- or comma-separated "
            f"(default: {' '.join(DEFAULT_TIERS)})"
        ),
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Number of repeats per (task, tier) (default: 3)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of parallel workers (default: CPU count)",
    )
    parser.add_argument(
        "--checkpoint",
        type=str,
        default="bench/results/seam-u-checkpoint.jsonl",
        help="Checkpoint file for resume (default: bench/results/seam-u-checkpoint.jsonl)",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="Maximum number of runs (default: no limit)",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Probe mode: max_tokens=64, record refusals/answered, no grading",
    )

    parsed = parser.parse_args(args)

    # Normalize tiers: accept space- AND comma-separated forms, then validate
    # every name so a typo aborts before any API call instead of erroring one
    # run at a time with an invalid model id.
    tiers = []
    for item in parsed.tiers:
        tiers.extend(t.strip() for t in item.split(",") if t.strip())
    unknown = [t for t in tiers if t not in DEFAULT_TIERS]
    if unknown:
        parser.error(
            f"unknown tier(s): {', '.join(unknown)} "
            f"(known: {', '.join(DEFAULT_TIERS)})"
        )
    parsed.tiers = tiers

    # Default workers to CPU count
    if parsed.workers is None:
        import multiprocessing
        parsed.workers = multiprocessing.cpu_count()

    return parsed


# ============================================================================
# TRANSPORT: Mock Runners (for testing)
# ============================================================================


def create_mock_runner(tier: str) -> Callable[[str], Tuple[str, Dict[str, Any]]]:
    """
    Create a mock runner for testing (always returns a valid diff).

    Args:
        tier: Model tier

    Returns:
        Callable that returns (response, usage)
    """

    def mock_runner(prompt: str) -> Tuple[str, Dict[str, Any]]:
        # Return a valid diff that fixes the off-by-one error
        response = """--- a/main.py
+++ b/main.py
@@ -2,2 +2,2 @@
 def count(items):
-    return len(items) + 1  # BUG: off-by-one error
+    return len(items)  # FIXED
"""
        usage = {"input_tokens": 100, "output_tokens": 50}
        return (response, usage)

    return mock_runner


# ============================================================================
# TRANSPORT: Anthropic HTTP
# ============================================================================


def create_anthropic_http_runner(
    api_key: str, model: str, probe: bool = False
) -> Callable[[str], Tuple[str, Dict[str, Any]]]:
    """
    Create a runner that calls Anthropic API via HTTP with forced tool call.

    Uses tool_choice to force submit_patch tool call (refusal-safe answer channel).

    Args:
        api_key: BENCH_API_KEY
        model: Model ID (e.g., claude-opus-5)
        probe: If True, use max_tokens=64

    Returns:
        Callable that takes prompt and returns (response, usage)

    Raises:
        RuntimeError: on refusal, transient error, or empty/blocked response
    """
    import json as json_lib

    max_tokens = 64 if probe else 1500  # Generous for full diff

    def runner(prompt: str) -> Tuple[str, Dict[str, Any]]:
        import urllib.request
        import urllib.error

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        # Define submit_patch tool
        tools = [
            {
                "name": "submit_patch",
                "description": "Submit a unified diff patch that fixes the defect",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "patch": {
                            "type": "string",
                            "description": "The complete unified diff patch",
                        }
                    },
                    "required": ["patch"],
                },
            }
        ]

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": tools,
            "tool_choice": {"type": "tool", "name": "submit_patch"},
        }

        try:
            req = urllib.request.Request(
                url,
                data=json_lib.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            start = time.time()
            with urllib.request.urlopen(req, timeout=60) as response:
                elapsed_ms = (time.time() - start) * 1000
                data = json_lib.loads(response.read().decode("utf-8"))

            # Check for refusal or blocked response
            if data.get("stop_reason") == "end_turn":
                # Empty or blocked (no tool_use block returned)
                raise RuntimeError("Model refused or blocked")

            # Extract tool_use block
            content = data.get("content", [])
            if not content:
                raise RuntimeError("Empty response")

            tool_use = None
            for block in content:
                if block.get("type") == "tool_use":
                    tool_use = block
                    break

            if not tool_use:
                raise RuntimeError("No tool_use block in response")

            # Extract patch from tool input
            patch = tool_use.get("input", {}).get("patch", "")
            if not patch:
                raise RuntimeError("Empty patch in tool input")

            usage = {
                "input_tokens": data.get("usage", {}).get("input_tokens", 0),
                "output_tokens": data.get("usage", {}).get("output_tokens", 0),
                "latency_ms": elapsed_ms,
            }
            return (patch, usage)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                # Client error (e.g., 403 Forbidden)
                raise RuntimeError(f"HTTP {e.code}: refusal or auth error")
            else:
                # Server error (5xx)
                raise RuntimeError(f"HTTP {e.code}: transient error")
        except RuntimeError:
            raise  # Re-raise our custom messages
        except Exception as e:
            if "refused" in str(e).lower() or "blocked" in str(e).lower():
                raise RuntimeError("Model refused")
            raise RuntimeError(f"HTTP error: {e}")

    return runner


# ============================================================================
# TRANSPORT: OpenAI HTTP
# ============================================================================


def create_openai_runner(
    api_key: str, model: str, probe: bool = False
) -> Callable[[str], Tuple[str, Dict[str, Any]]]:
    """
    Create a runner that calls OpenAI API via HTTP with forced function call.

    Uses tool_choice to force submit_patch function call (refusal-safe answer channel).

    Args:
        api_key: OPENAI_API_KEY
        model: Model ID (e.g., gpt-4o-mini)
        probe: If True, use max_tokens=64

    Returns:
        Callable that takes prompt and returns (response, usage)

    Raises:
        RuntimeError: on refusal, transient error, or empty/blocked response
    """
    import json as json_lib

    max_tokens = 64 if probe else 1500  # Generous for full diff

    def runner(prompt: str) -> Tuple[str, Dict[str, Any]]:
        import urllib.request
        import urllib.error

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Define submit_patch function
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "submit_patch",
                    "description": "Submit a unified diff patch that fixes the defect",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "patch": {
                                "type": "string",
                                "description": "The complete unified diff patch",
                            }
                        },
                        "required": ["patch"],
                    },
                },
            }
        ]

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": tools,
            "tool_choice": {"type": "function", "function": {"name": "submit_patch"}},
        }

        try:
            req = urllib.request.Request(
                url,
                data=json_lib.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            start = time.time()
            with urllib.request.urlopen(req, timeout=60) as response:
                elapsed_ms = (time.time() - start) * 1000
                data = json_lib.loads(response.read().decode("utf-8"))

            # Check for refusal or blocked response
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError("Empty choices")

            choice = choices[0]
            finish_reason = choice.get("finish_reason")

            if finish_reason == "content_filter":
                raise RuntimeError("Model refused (content_filter)")

            message = choice.get("message", {})
            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                # No tool call returned (empty or blocked)
                raise RuntimeError("No tool_calls in response")

            # Extract patch from function arguments
            tool_call = tool_calls[0]
            if tool_call.get("type") != "function":
                raise RuntimeError("Expected function tool_call")

            try:
                arguments = json_lib.loads(tool_call.get("function", {}).get("arguments", "{}"))
            except json_lib.JSONDecodeError as e:
                raise RuntimeError(f"Failed to parse function arguments: {e}")

            patch = arguments.get("patch", "")
            if not patch:
                raise RuntimeError("Empty patch in function arguments")

            usage = {
                "input_tokens": data.get("usage", {}).get("prompt_tokens", 0),
                "output_tokens": data.get("usage", {}).get("completion_tokens", 0),
                "latency_ms": elapsed_ms,
            }
            return (patch, usage)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise RuntimeError(f"HTTP {e.code}: refusal or auth error")
            else:
                raise RuntimeError(f"HTTP {e.code}: transient error")
        except RuntimeError:
            raise  # Re-raise our custom messages
        except Exception as e:
            if "refused" in str(e).lower() or "blocked" in str(e).lower():
                raise RuntimeError("Model refused")
            raise RuntimeError(f"HTTP error: {e}")

    return runner


# ============================================================================
# MAIN EXECUTION
# ============================================================================


def main():
    """Main entry point."""
    args = parse_args()

    # Load tasks first (offline, harmless) so loader defects surface even
    # without credentials; API keys are validated below, before any transport
    # call can happen.
    tasks_dir = Path(args.tasks_dir)
    if not tasks_dir.exists():
        print(f"Error: Tasks directory not found: {tasks_dir}", file=sys.stderr)
        sys.exit(1)

    tasks = []
    for task_json_path in sorted(tasks_dir.glob("*/task.json")):
        try:
            task_json = json.loads(task_json_path.read_text(encoding="utf-8"))
            tasks.append((task_json, task_json_path.parent))
        except Exception as e:
            print(f"Error loading task {task_json_path}: {e}", file=sys.stderr)

    if not tasks:
        print(f"No tasks found in {tasks_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(tasks)} tasks")

    # Validate API keys for all tiers (before any transport call)
    transports_needed = set()
    for tier in args.tiers:
        if "gpt" in tier.lower() or "openai" in tier.lower():
            transports_needed.add("openai")
        else:
            transports_needed.add("anthropic-http")

    validate_api_keys(list(transports_needed))

    # Load checkpoint
    checkpoint_file = Path(args.checkpoint)
    completed = load_checkpoint(checkpoint_file)
    print(f"Loaded {len(completed)} completed runs from checkpoint")

    # Create work queue
    work_items = []
    for task_json, task_dir in tasks:
        task_id = task_json.get("task_id")
        for tier in args.tiers:
            for repeat in range(args.repeats):
                key = (task_id, tier, repeat, "U")
                if key not in completed:
                    work_items.append((task_json, task_dir, tier, repeat))

    if args.max_runs:
        work_items = work_items[: args.max_runs]

    print(f"Running {len(work_items)} tasks ({len(completed)} already completed)")

    # Process in parallel
    run_count = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for task_json, task_dir, tier, repeat in work_items:
            future = executor.submit(
                run_single_task,
                task_json,
                task_dir,
                tier,
                repeat,
                args.probe,
            )
            futures[future] = (task_json, tier, repeat)

        for future in as_completed(futures):
            task_json, tier, repeat = futures[future]
            try:
                result = future.result()
                append_checkpoint(checkpoint_file, result)
                run_count += 1
                if run_count % 10 == 0:
                    print(f"Completed {run_count} runs...")
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)

    print(f"Complete: {run_count} runs, checkpoint: {checkpoint_file}")


def run_single_task(
    task_json: Dict[str, Any],
    task_dir: Path,
    tier: str,
    repeat: int,
    probe: bool,
) -> Dict[str, Any]:
    """
    Run a single task against a tier.

    Args:
        task_json: Task configuration
        task_dir: Task directory
        tier: Model tier
        repeat: Repeat number
        probe: Probe mode flag

    Returns:
        Result dict
    """
    task_id = task_json.get("task_id")
    result = {
        "task_id": task_id,
        "band": task_json.get("band"),
        "tier": tier,
        "repeat": repeat,
        "arm": "U",
        "transport": "anthropic-http" if "gpt" not in tier else "openai",
    }

    try:
        # Determine transport and create runner
        if "gpt" in tier.lower():
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                result["status"] = "error"
                result["error"] = "OPENAI_API_KEY not set"
                return result
            runner = create_openai_runner(api_key, tier, probe=probe)
        else:
            api_key = os.environ.get("BENCH_API_KEY")
            if not api_key:
                result["status"] = "error"
                result["error"] = "BENCH_API_KEY not set"
                return result
            runner = create_anthropic_http_runner(api_key, tier, probe=probe)

        # Build prompt
        prompt = build_u_arm_prompt(task_json, task_dir)

        # Call model
        try:
            response, usage = runner(prompt)
        except RuntimeError as e:
            error_msg = str(e)
            if "refused" in error_msg.lower():
                result["status"] = "refusal"
                result["refusal"] = True
            elif "transient" in error_msg.lower():
                result["status"] = "transient"
                result["error"] = error_msg
            else:
                result["status"] = "error"
                result["error"] = error_msg
            return record_result(result)

        result["tokens_in"] = usage.get("input_tokens", 0)
        result["tokens_out"] = usage.get("output_tokens", 0)
        result["cost_usd"] = calculate_cost(tier, usage)
        result["latency_ms"] = usage.get("latency_ms", 0)

        # Probe mode: just record response, don't score
        if probe:
            result["probe"] = True
            result["refusal"] = "refused" in response.lower() or "cannot" in response.lower()
            return record_result(result)

        # Response is already the diff (extracted from tool call)
        diff = response

        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            apply_status = apply_diff_to_sandbox(task_dir / "repo", diff, sandbox)
            result["patch_apply_status"] = apply_status

            if apply_status is None:
                result["passed"] = False
                result["status"] = "apply_error"
                return record_result(result)

            if apply_status == "noop":
                result["passed"] = False
                result["status"] = "apply_noop"
                return record_result(result)

            if apply_status == "failed":
                result["passed"] = False
                result["status"] = "apply_failed"
                return record_result(result)

            # apply_status == "applied"
            if run_oracle(task_json, task_dir, sandbox, timeout=120):
                result["passed"] = True
                result["status"] = "pass"
            else:
                result["passed"] = False
                result["status"] = "fail"

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return record_result(result)


def calculate_cost(tier: str, usage: Dict[str, Any]) -> float:
    """
    Calculate cost in USD for a single call.

    Pricing (approximate, as of 2025):
    - Claude Haiku: $0.80/$4 per 1M in/out tokens
    - Claude Sonnet: $3/$15 per 1M in/out tokens
    - Claude Opus: $15/$75 per 1M in/out tokens
    - Claude Fable: $0.30/$1.20 per 1M in/out tokens
    - GPT-4o Mini: $0.15/$0.60 per 1M in/out tokens

    Args:
        tier: Model tier
        usage: Usage dict with input_tokens, output_tokens

    Returns:
        Cost in USD
    """
    pricing = {
        "claude-fable-5": (0.30, 1.20),
        "claude-haiku-4-5-20251001": (0.80, 4.0),
        "claude-sonnet-5": (3.0, 15.0),
        "claude-opus-5": (15.0, 75.0),
        "gpt-4o-mini": (0.15, 0.60),
    }

    in_price, out_price = pricing.get(tier, (0.0, 0.0))
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    cost = (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    return round(cost, 6)


if __name__ == "__main__":
    main()
