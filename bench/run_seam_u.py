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

    # Fixed instruction
    instruction = (
        "\n\nReply with a single unified diff that fixes the defect. "
        "No prose outside the diff."
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


def apply_diff_to_sandbox(
    repo_dir: Path, diff: str, sandbox: Path
) -> bool:
    """
    Copy repo_dir to sandbox and apply diff.

    Tries multiple methods: git apply, patch, then fallback Python parser.

    Args:
        repo_dir: Source repo directory
        diff: Unified diff text
        sandbox: Destination sandbox directory

    Returns:
        True if successful, False otherwise
    """
    # Copy repo to sandbox
    try:
        sandbox.mkdir(parents=True, exist_ok=True)
        for item in repo_dir.iterdir():
            if item.is_dir():
                shutil.copytree(item, sandbox / item.name)
            else:
                shutil.copy2(item, sandbox / item.name)
    except Exception as e:
        print(f"Error copying repo to sandbox: {e}", file=sys.stderr)
        return False

    # Initialize git repo in sandbox (required for git apply)
    git_ok = False
    try:
        subprocess.run(
            ["git", "init"],
            cwd=sandbox,
            capture_output=True,
            timeout=10,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=sandbox,
            capture_output=True,
            timeout=10,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=sandbox,
            capture_output=True,
            timeout=10,
            text=True,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=sandbox,
            capture_output=True,
            timeout=10,
            text=True,
        )
        result = subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=sandbox,
            capture_output=True,
            timeout=10,
            text=True,
        )
        git_ok = result.returncode == 0
    except Exception:
        pass

    # Try git apply if git is ready
    if git_ok:
        try:
            result = subprocess.run(
                ["git", "apply"],
                input=diff,
                cwd=sandbox,
                capture_output=True,
                timeout=10,
                text=True,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    # Fall back to patch command
    try:
        result = subprocess.run(
            ["patch", "-p1"],
            input=diff,
            cwd=sandbox,
            capture_output=True,
            timeout=10,
            text=True,
        )
        if result.returncode == 0:
            return True
    except Exception:
        pass

    # Last resort: Python-based patch using difflib concepts
    try:
        # Parse the diff to find file changes
        lines = diff.split("\n")
        file_map = {}  # file -> list of changes
        current_file = None
        current_changes = []
        changes_made = False

        for i, line in enumerate(lines):
            if line.startswith("--- "):
                current_file = line[4:].split("\t")[0]
                if current_file.startswith("a/"):
                    current_file = current_file[2:]
            elif line.startswith("+++ "):
                pass  # Skip +++ lines
            elif line.startswith("@@") and current_file:
                if current_changes:
                    if current_file not in file_map:
                        file_map[current_file] = []
                    file_map[current_file].extend(current_changes)
                current_changes = []
            elif current_file and (line.startswith("-") or line.startswith("+")):
                if not line.startswith("---") and not line.startswith("+++"):
                    current_changes.append(line)

        if current_changes and current_file:
            if current_file not in file_map:
                file_map[current_file] = []
            file_map[current_file].extend(current_changes)

        # If no file map was built, the diff is invalid
        if not file_map:
            return False

        # Apply changes
        for filepath, changes in file_map.items():
            file_path = sandbox / filepath
            if not file_path.exists():
                continue

            original_content = file_path.read_text(encoding="utf-8")
            content = original_content

            # Process changes - look for - and + pairs
            i = 0
            while i < len(changes):
                if changes[i].startswith("-") and not changes[i].startswith("---"):
                    old_line = changes[i][1:]
                    if i + 1 < len(changes) and changes[i + 1].startswith("+") and not changes[i + 1].startswith("+++"):
                        new_line = changes[i + 1][1:]
                        # Try to replace the line
                        if old_line + "\n" in content:
                            content = content.replace(old_line + "\n", new_line + "\n", 1)
                            changes_made = True
                            i += 2
                            continue
                i += 1

            # Only write if changes were actually made
            if changes_made and content != original_content:
                file_path.write_text(content, encoding="utf-8")

        return changes_made
    except Exception as e:
        print(f"Python patch fallback failed: {e}", file=sys.stderr)

    return False


def run_oracle(
    task_json: Dict[str, Any], sandbox: Path, timeout: int = 120
) -> bool:
    """
    Run oracle tests in sandbox.

    Args:
        task_json: Task configuration
        sandbox: Sandbox directory with patched repo
        timeout: Timeout in seconds

    Returns:
        True if oracle tests pass (exit code 0), False otherwise
    """
    oracle_cmd = task_json.get("oracle_cmd", "")
    if not oracle_cmd:
        print("No oracle_cmd in task", file=sys.stderr)
        return False

    try:
        # Use sys.executable for parity
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
        help=f"Model tiers to test (default: {' '.join(DEFAULT_TIERS)})",
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
    Create a runner that calls Anthropic API via HTTP.

    Args:
        api_key: BENCH_API_KEY
        model: Model ID (e.g., claude-opus-5)
        probe: If True, use max_tokens=64

    Returns:
        Callable that takes prompt and returns (response, usage)
    """
    import json as json_lib

    max_tokens = 64 if probe else 1024

    def runner(prompt: str) -> Tuple[str, Dict[str, Any]]:
        import urllib.request
        import urllib.error

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
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

            # Check for refusal
            if data.get("stop_reason") == "end_turn":
                content = data.get("content", [{}])[0].get("text", "")
                if "i can't" in content.lower() or "i cannot" in content.lower():
                    raise RuntimeError("Model refused")

            text = data.get("content", [{}])[0].get("text", "")
            usage = {
                "input_tokens": data.get("usage", {}).get("input_tokens", 0),
                "output_tokens": data.get("usage", {}).get("output_tokens", 0),
                "latency_ms": elapsed_ms,
            }
            return (text, usage)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                # Client error (e.g., 403 Forbidden)
                raise RuntimeError(f"HTTP {e.code}: refusal or auth error")
            else:
                # Server error (5xx)
                raise RuntimeError(f"HTTP {e.code}: transient error")
        except Exception as e:
            if "refused" in str(e).lower():
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
    Create a runner that calls OpenAI API via HTTP.

    Args:
        api_key: OPENAI_API_KEY
        model: Model ID (e.g., gpt-4o-mini)
        probe: If True, use max_tokens=64

    Returns:
        Callable that takes prompt and returns (response, usage)
    """
    import json as json_lib

    max_tokens = 64 if probe else 1024

    def runner(prompt: str) -> Tuple[str, Dict[str, Any]]:
        import urllib.request
        import urllib.error

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
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

            # Check for refusal
            finish_reason = data.get("choices", [{}])[0].get("finish_reason")
            if finish_reason == "content_filter":
                raise RuntimeError("Model refused (content_filter)")

            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = {
                "input_tokens": data.get("usage", {}).get("prompt_tokens", 0),
                "output_tokens": data.get("usage", {}).get("completion_tokens", 0),
                "latency_ms": elapsed_ms,
            }
            return (text, usage)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise RuntimeError(f"HTTP {e.code}: refusal or auth error")
            else:
                raise RuntimeError(f"HTTP {e.code}: transient error")
        except Exception as e:
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

        # Extract diff and score
        diff = extract_diff(response)

        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            if not apply_diff_to_sandbox(task_dir / "repo", diff, sandbox):
                result["passed"] = False
                result["status"] = "apply_failed"
                return record_result(result)

            if run_oracle(task_json, sandbox, timeout=120):
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
