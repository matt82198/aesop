#!/usr/bin/env python3
"""Parallel frontier v2/v5 benchmark runner.

Supports two answer modes:
- regex (default, v2/v3/v4): prose format + regex grading
- tool (v5): structured tool calls + enum grading

Pre-registered protocols:
- v4 (regex mode): bench/EQUIVALENCE-MARGIN.md Amendment 3 (committed 2026-07-27)
- v5 (tool mode): bench/EQUIVALENCE-MARGIN.md Amendment 4 (committed 2026-07-27)

Transports: anthropic-http (Claude tiers via BENCH_API_KEY) + openai (gpt-4o-mini).

USAGE
-----
  # Regex mode (v2-v4): prose + regex grading (default)
  python bench/run_v2_parallel.py --max-runs 180
  python bench/run_v2_parallel.py --max-runs 180 --checkpoint bench/results/frontier-v4-checkpoint.jsonl

  # Tool mode (v5): tool calls + enum grading
  python bench/run_v2_parallel.py --answer-mode tool --max-runs 180
  python bench/run_v2_parallel.py --answer-mode tool --max-runs 180 --checkpoint bench/results/frontier-v5-checkpoint.jsonl

Checkpoint format (one JSON line per completed run):
  {
    "tier": "claude-opus-5",
    "task_id": "ft01_...",
    "repeat": 1,
    "response_hash": "sha256:abc...",
    "correct": true/false,
    "tokens_in": 1234,
    "tokens_out": 567,
    "cost_usd": 0.0234,
    "transport": "anthropic-http",
    "wall_s": 2.34,
    "answer_mode": "tool"  # NEW in v5: "tool" or "regex"
  }
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Import utilities
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import frontier infrastructure
sys.path.insert(0, str(Path(__file__).parent))
from frontier_slice import FrontierTask, GroundTruth, load_frontier_tasks, load_ground_truth, score_response
from frontier_eligibility import audit_tasks, parse_token_set, extract_correct_token, remove_format_instruction

# Import OpenAI transport
sys.path.insert(0, str(Path(__file__).parent.parent / "driver"))
from openai_transport import default_openai_transport


# ============================================================================
# Run Result
# ============================================================================


@dataclass
class FrontierV2Run:
    """One frontier v2/v5 run result."""
    tier: str
    task_id: str
    repeat: int
    response_hash: str
    correct: bool
    tokens_in: int
    tokens_out: int
    cost_usd: float
    transport: str
    wall_s: float
    answer_mode: str = "regex"  # NEW in v5: "regex" (v2-v4) or "tool" (v5)


# ============================================================================
# Checkpoint Management (thread-safe)
# ============================================================================


class CheckpointManager:
    """Thread-safe checkpoint reader/writer."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def load_completed(self) -> set:
        """Load set of (tier, task_id, repeat) tuples already completed."""
        completed = set()
        if self.path.exists():
            with open(self.path) as f:
                for line in f:
                    if line.strip():
                        obj = json.loads(line)
                        # error runs are retryable — only successful runs count as completed
                        if obj.get("transport") != "error" and obj.get("correct") is not None:
                            completed.add((obj["tier"], obj["task_id"], obj["repeat"]))
        return completed

    def append(self, run: FrontierV2Run) -> None:
        """Append completed run to checkpoint (thread-safe)."""
        with self.lock:
            with open(self.path, "a") as f:
                f.write(json.dumps(asdict(run)) + "\n")


# ============================================================================
# Model Invocation
# ============================================================================


# Billed $/MTok (input, output) — verified against platform.claude.com 2026-07-26.
# sonnet-5 uses introductory pricing (through 2026-08-31). The v2 ft01-ft60 run's
# embedded table understated fable-5 ~40x; disclosed in frontier-v2-2026-07-26.md.
PRICING_MTOK = {
    "claude-opus-5": (5.0, 25.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


# ============================================================================
# Tool-Mode Support (v5)
# ============================================================================


def extract_tool_answer(response_text: str) -> Optional[str]:
    """Extract the answer value from a tool-mode response.

    Parses the tool call from the response to get the submitted answer string.

    Args:
        response_text: Model's response (may contain tool calls)

    Returns:
        The submitted answer string, or None if no tool call found
    """
    try:
        # Look for "answer" field in JSON tool call
        answer_match = re.search(r'"answer"\s*:\s*"([^"]*)"', response_text)
        if answer_match:
            return answer_match.group(1)
    except Exception:
        pass
    return None


def grade_tool_mode_response(
    response_text: str,
    schema_type: str,
    correct_value: str,
    ground_truth_regex: Optional[str] = None,
) -> bool:
    """Grade a tool-mode response based on schema type.

    For enum schema: exact string equality.
    For string schema: run ground-truth regex against submitted answer.

    Args:
        response_text: Model's response (may contain tool calls)
        schema_type: "enum" (closed set) or "string" (free text)
        correct_value: For enum: the single correct token; for string: ignored
        ground_truth_regex: Ground-truth regex pattern (required for string schema)

    Returns:
        True if the submitted answer is correct, False otherwise
    """
    submitted_answer = extract_tool_answer(response_text)
    if submitted_answer is None:
        return False

    if schema_type == "enum":
        # Enum schema: exact equality
        return submitted_answer == correct_value
    elif schema_type == "string":
        # String schema: run ground-truth regex against submitted answer
        if ground_truth_regex is None:
            return False
        try:
            return bool(re.search(ground_truth_regex, submitted_answer, re.IGNORECASE | re.DOTALL))
        except re.error:
            return False
    return False


def invoke_claude_model(
    model: str,
    prompt: str,
    max_tokens: int = 8192,
    timeout_s: float = 180.0,
    tools: Optional[List[Dict]] = None,
    tool_choice: Optional[Dict] = None,
):
    """Invoke a Claude model via direct api.anthropic.com HTTP ("anthropic-http").

    API-only per the bench-no-cli-fallback directive (2026-07-26): the claude
    CLI bills subscription usage and must never be used for benchmark runs.
    The key is read ONLY from BENCH_API_KEY; a missing key is a hard error
    (caught at startup by main()), never a CLI fallback or a credential hunt.

    Args:
        model: Claude model ID
        prompt: User prompt
        max_tokens: Maximum tokens to generate (>= 256 for tool mode)
        timeout_s: Request timeout
        tools: Optional list of tool definitions (v5 tool mode)
        tool_choice: Optional tool choice constraint (v5 tool mode)

    Returns:
        (response_text, tokens_in, tokens_out, cost_usd, transport_label)
    """
    api_key = os.environ.get("BENCH_API_KEY")
    if not api_key:
        raise RuntimeError(
            "BENCH_API_KEY not set - bench runs are API-only (no CLI fallback)"
        )
    return _invoke_claude_http(model, prompt, api_key, max_tokens, timeout_s, tools, tool_choice)


def _invoke_claude_http(model, prompt, api_key, max_tokens, timeout_s, tools=None, tool_choice=None):
    import urllib.request, json as _json
    body_dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if tools:
        body_dict["tools"] = tools
    if tool_choice:
        body_dict["tool_choice"] = tool_choice
    body = _json.dumps(body_dict).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            out = _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"anthropic HTTP transport failed: {e}")
    # A safety-classifier decline is an unscored error run (retryable + disclosed),
    # never a scored-wrong answer.
    if out.get("stop_reason") == "refusal":
        raise RuntimeError("anthropic HTTP: stop_reason=refusal")

    # Extract text and tool calls from response content
    text_parts = []
    tool_call_parts = []
    for b in out.get("content", []):
        if b.get("type") == "text":
            text_parts.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            # Serialize tool use for grading (includes the answer parameter)
            tool_use = b.get("input", {})
            tool_call_parts.append(_json.dumps(tool_use))

    # Combine text and tool calls for grading
    text = "".join(text_parts)
    if tool_call_parts:
        text += "\n" + "\n".join(tool_call_parts)

    usage = out.get("usage", {})
    ti = usage.get("input_tokens", 0)
    to = usage.get("output_tokens", 0)
    inr, outr = PRICING_MTOK.get(model, (10.0, 50.0))
    cost = ti / 1_000_000 * inr + to / 1_000_000 * outr
    return text, ti, to, cost, "anthropic-http"


def invoke_openai_model(
    model: str,
    prompt: str,
    max_tokens: int = 512,
    timeout_s: float = 60.0,
    tools: Optional[List[Dict]] = None,
    tool_choice: Optional[Dict] = None,
) -> Tuple[str, int, int, float]:
    """Invoke OpenAI model via default_openai_transport.

    Supports both regular and tool-mode requests.

    Args:
        model: OpenAI model ID
        prompt: User prompt
        max_tokens: Maximum tokens to generate
        timeout_s: Request timeout
        tools: Optional list of tool definitions
        tool_choice: Optional tool choice constraint

    Returns:
        (response_text, tokens_in, tokens_out, cost_usd)
    """
    # Build OpenAI payload
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": max_tokens,
    }

    if tools:
        # Convert Anthropic-style tool def to OpenAI format (name, description, parameters)
        openai_tools = []
        for tool in tools:
            openai_tool = {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                }
            }
            openai_tools.append(openai_tool)
        payload["tools"] = openai_tools

    if tool_choice:
        # Convert Anthropic tool_choice to OpenAI format
        if tool_choice.get("type") == "tool":
            payload["tool_choice"] = {
                "type": "function",
                "function": {"name": tool_choice["name"]}
            }

    response = default_openai_transport(payload, timeout_s=timeout_s)

    if not response.get("choices"):
        raise RuntimeError(f"OpenAI API returned no choices: {response}")

    choice = response["choices"][0]
    response_text = ""

    # Extract text or tool calls from the response
    message = choice.get("message", {})
    if "content" in message and message["content"]:
        response_text = message["content"]

    # If tool was called, extract tool call info
    if "tool_calls" in message:
        for tool_call in message["tool_calls"]:
            # Serialize tool call for extraction
            import json as _json
            tool_args = tool_call.get("function", {}).get("arguments", "{}")
            if isinstance(tool_args, str):
                try:
                    args_dict = _json.loads(tool_args)
                except:
                    args_dict = {"answer": tool_args}
            else:
                args_dict = tool_args
            # Append in a format extract_tool_answer can parse
            response_text += "\n" + _json.dumps(args_dict)

    usage = response.get("usage", {})
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)

    # Pricing for gpt-4o-mini (2026-07-26 rates)
    rate_input = 0.15  # per million
    rate_output = 0.60  # per million
    cost = (tokens_in / 1_000_000) * rate_input + (tokens_out / 1_000_000) * rate_output

    return response_text, tokens_in, tokens_out, cost


# ============================================================================
# Run One Task
# ============================================================================


def run_single_task(
    tier: str,
    task: FrontierTask,
    repeat: int,
    ground_truth: Dict[str, GroundTruth],
    answer_mode: str = "regex",
    tool_tasks_info: Optional[Dict] = None,
    all_tasks: Optional[List[str]] = None,
) -> FrontierV2Run:
    """Run a single task on a single tier.

    v5 tool mode: applies to ALL tasks (both closed-set and free-string schemas).
    - Closed-set (39 tasks): enum schema, exact equality grading
    - Free-string (91 tasks): string schema, regex grading

    Args:
        tier: Model tier name
        task: FrontierTask to run
        repeat: Repeat index (1-3)
        ground_truth: Ground truth dictionary
        answer_mode: "regex" (default) or "tool" (v5 applies to all tasks)
        tool_tasks_info: Dict of {task_id: (token_set, correct_token)} for closed-set tasks
        all_tasks: List of all task IDs (for tool mode classification)

    Returns:
        FrontierV2Run with complete metadata and result.
    """
    wall_start = time.time()

    # In tool mode, apply to ALL tasks
    use_tool_mode = answer_mode == "tool"
    actual_answer_mode = "tool" if use_tool_mode else "regex"

    # Prepare prompt and request parameters
    request_prompt = task.prompt
    tools = None
    tool_choice = None
    schema_type = None  # "enum" or "string"
    correct_value = None

    if use_tool_mode:
        # Transform prompt: remove format instruction and add tool instruction
        request_prompt = remove_format_instruction(task.prompt)
        request_prompt += "\n\nCall the submit_answer tool with answer set to ONLY your final answer value - no explanation."

        # Determine schema type based on whether task has closed token set
        if tool_tasks_info and task.id in tool_tasks_info:
            # Closed-set task: enum schema
            schema_type = "enum"
            token_set, correct_value = tool_tasks_info[task.id]
            tools = [
                {
                    "name": "submit_answer",
                    "description": "Submit the final answer",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "answer": {
                                "type": "string",
                                "enum": token_set,
                                "description": "The final answer token (must be one of the allowed values)"
                            }
                        },
                        "required": ["answer"]
                    }
                }
            ]
        else:
            # Free-string task: string schema
            schema_type = "string"
            tools = [
                {
                    "name": "submit_answer",
                    "description": "Submit the final answer",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "answer": {
                                "type": "string",
                                "description": "The final answer (any string)"
                            }
                        },
                        "required": ["answer"]
                    }
                }
            ]

        tool_choice = {"type": "tool", "name": "submit_answer"}

    # Invoke model
    response_text = None
    tokens_in = tokens_out = 0
    cost = 0.0
    transport = "unknown"

    try:
        if tier.startswith("claude-"):
            response_text, tokens_in, tokens_out, cost, transport = invoke_claude_model(
                tier, request_prompt,
                max_tokens=256 if use_tool_mode else 8192,
                tools=tools,
                tool_choice=tool_choice
            )
        elif tier == "gpt-4o-mini":
            # v5: OpenAI now uses tool mode too (function calling)
            response_text, tokens_in, tokens_out, cost = invoke_openai_model(
                tier, request_prompt,
                max_tokens=256 if use_tool_mode else 512,
                tools=tools,
                tool_choice=tool_choice
            )
            transport = "openai"
        else:
            raise ValueError(f"Unknown tier: {tier}")
    except Exception as e:
        # Transport failure = an ERROR RUN, never a scored (wrong) answer
        transport = "error"
        response_text = f"[TRANSPORT-ERROR: {e}]"
        tokens_in = tokens_out = 0
        cost = 0.0

    # If we got no response, mark as error
    if not response_text:
        response_text = "[No response]"
        transport = "error"

    # Score response — error runs are NEVER scored (excluded from accuracy, retryable)
    if transport == "error":
        correct = None
    else:
        gt = ground_truth.get(task.id)
        if not gt:
            correct = False
        elif use_tool_mode and schema_type:
            # Tool mode grading: use appropriate schema grading
            expected_regex = gt.get("expected_regex")
            correct = grade_tool_mode_response(
                response_text,
                schema_type=schema_type,
                correct_value=correct_value,
                ground_truth_regex=expected_regex
            )
        else:
            # Regex mode grading (backward compatibility)
            score = score_response(task, response_text, gt)
            correct = score.correct

    # Compute response hash
    response_hash = "sha256:" + hashlib.sha256(response_text.encode()).hexdigest()[:16]

    wall_end = time.time()
    wall_s = wall_end - wall_start

    return FrontierV2Run(
        tier=tier,
        task_id=task.id,
        repeat=repeat,
        response_hash=response_hash,
        correct=correct,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        transport=transport,
        wall_s=wall_s,
        answer_mode=actual_answer_mode,
    )


# ============================================================================
# Main Runner
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Parallel frontier v2/v5 benchmark runner (N=130 tasks x 3 repeats x 5 tiers = 1950 runs)"
    )
    parser.add_argument(
        "--answer-mode",
        default="regex",
        choices=["regex", "tool"],
        help="Answer collection mode: 'regex' (v2-v4, default) or 'tool' (v5 tool calls)",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=180,
        help="Maximum runs per invocation (default 180, ~6min with 8 workers)",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,  # Will be set based on answer_mode
        help="Path to checkpoint file (default: frontier-v4-checkpoint.jsonl for regex, "
        "frontier-v5-checkpoint.jsonl for tool mode)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of parallel workers (default 8)",
    )
    parser.add_argument(
        "--tiers",
        default="claude-opus-5,claude-fable-5,claude-sonnet-5,claude-haiku-4-5-20251001,gpt-4o-mini",
        help="Comma-separated list of tiers to run",
    )

    args = parser.parse_args()

    # Set default checkpoint path based on answer_mode
    if args.checkpoint is None:
        args.checkpoint = (
            "bench/results/frontier-v5-checkpoint.jsonl" if args.answer_mode == "tool"
            else "bench/results/frontier-v4-checkpoint.jsonl"
        )

    # Load frontier data
    tasks = load_frontier_tasks("bench/tasks_frontier.jsonl")
    ground_truth = load_ground_truth("bench/ground_truth_frontier.jsonl")
    tiers = args.tiers.split(",")

    # Load tool-mode task info if needed
    tool_tasks_info = None
    if args.answer_mode == "tool":
        tool_tasks_info, regex_fallback = audit_tasks(
            "bench/tasks_frontier.jsonl",
            "bench/ground_truth_frontier.jsonl"
        )

    # Fail fast: Claude tiers are API-only (bench-no-cli-fallback directive).
    # Better one clear startup error than hundreds of error-run lines.
    if any(t.startswith("claude-") for t in tiers) and not os.environ.get("BENCH_API_KEY"):
        print(
            "ERROR: BENCH_API_KEY is not set. Bench runs are API-only (no CLI "
            "fallback). Set it from the Machine env scope and re-invoke; "
            "missing key = skip, never a credential hunt.",
            file=sys.stderr,
        )
        sys.exit(2)

    print(f"Frontier v2/v5 Parallel Runner")
    print(f"  Answer mode: {args.answer_mode}")
    print(f"  Tasks: {len(tasks)}")
    print(f"  Repeats: 3")
    print(f"  Tiers: {len(tiers)}")
    print(f"  Total runs: {len(tasks) * 3 * len(tiers)}")
    print(f"  Workers: {args.workers}")
    print(f"  Max runs this call: {args.max_runs}")
    if tool_tasks_info:
        print(f"  Tool-mode eligible: {len(tool_tasks_info)}")
        print(f"  Regex fallback: {len(tasks) - len(tool_tasks_info)}")
    print()

    # Initialize checkpoint
    checkpoint = CheckpointManager(args.checkpoint)
    completed = checkpoint.load_completed()

    print(f"Checkpoint: {args.checkpoint}")
    print(f"  Already completed: {len(completed)}")
    print()

    # Build work items (tier, task, repeat)
    work_items = []
    for tier in tiers:
        for task in tasks:
            for repeat in range(1, 4):  # 3 repeats
                if (tier, task.id, repeat) not in completed:
                    work_items.append((tier, task, repeat))

    print(f"Remaining work: {len(work_items)} runs")

    if not work_items:
        print("All runs completed!")
        return 0

    # Limit to max_runs
    work_items = work_items[:args.max_runs]
    print(f"Running: {len(work_items)} runs")
    print()

    # Run in parallel
    cost_per_tier = {}
    runs_per_tier = {}

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                run_single_task, tier, task, repeat, ground_truth,
                answer_mode=args.answer_mode,
                tool_tasks_info=tool_tasks_info
            ): (tier, task.id, repeat)
            for tier, task, repeat in work_items
        }

        completed_count = 0
        for future in as_completed(futures):
            tier, task_id, repeat = futures[future]
            try:
                run = future.result()
                checkpoint.append(run)

                completed_count += 1
                cost_per_tier[tier] = cost_per_tier.get(tier, 0.0) + run.cost_usd
                runs_per_tier[tier] = runs_per_tier.get(tier, 0) + 1

                status = "OK" if run.correct else "FAIL"
                print(f"[{completed_count:3d}/{len(work_items)}] {tier:30s} {task_id:40s} rep{run.repeat} {status} ({run.cost_usd:.4f} USD)")
            except Exception as e:
                print(f"[ERROR] {tier:30s} {task_id:40s} rep{repeat}: {e}")
                completed_count += 1

    print()
    print("Cost summary (this batch):")
    total_cost = 0.0
    for tier in tiers:
        tier_cost = cost_per_tier.get(tier, 0.0)
        tier_runs = runs_per_tier.get(tier, 0)
        total_cost += tier_cost
        print(f"  {tier:30s}: {tier_cost:8.4f} USD ({tier_runs:3d} runs)")
    print(f"  {'Total':30s}: {total_cost:8.4f} USD")

    print()
    print(f"Checkpoint saved to: {args.checkpoint}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
