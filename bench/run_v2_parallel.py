#!/usr/bin/env python3
"""Parallel frontier v2 benchmark runner — N=60 tasks x 3 repeats x 5 tiers = 900 runs.

Pre-registered protocol: bench/EQUIVALENCE-MARGIN.md (Amendment 1, committed 2026-07-26).
Grading: machine-checked ground-truth patterns only.
Transport: anthropic client for Claude tiers; OpenAI seam for gpt-4o-mini.
Cost cap: $30 USD. Spend tracking per tier.

USAGE
-----
  python bench/run_v2_parallel.py --max-runs 180
  python bench/run_v2_parallel.py --max-runs 180 --checkpoint bench/results/frontier-v2-checkpoint.jsonl
  (repeat until 900/900 or a tier is skipped)

Checkpoint format (frontier-v2-checkpoint.jsonl):
  One JSON line per completed run:
  {
    "tier": "claude-opus-5",
    "task_id": "ft01_...",
    "repeat": 1,
    "response_hash": "sha256:abc...",
    "correct": true/false,
    "tokens_in": 1234,
    "tokens_out": 567,
    "cost_usd": 0.0234,
    "transport": "anthropic",
    "wall_s": 2.34
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

# Import OpenAI transport
sys.path.insert(0, str(Path(__file__).parent.parent / "driver"))
from openai_transport import default_openai_transport


# ============================================================================
# Run Result
# ============================================================================


@dataclass
class FrontierV2Run:
    """One frontier v2 run result."""
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


def invoke_claude_model(
    model: str,
    prompt: str,
    max_tokens: int = 8192,
    timeout_s: float = 180.0,
):
    """Invoke a Claude model.

    Transport selection (per-run label recorded in the checkpoint):
    - BENCH_API_KEY set -> direct api.anthropic.com HTTP ("anthropic-http"):
      pay-per-use x-api-key billing, exact usage token counts.
      The key is read ONLY from this named env var; a missing key means the
      CLI fallback, never a credential hunt.
    - otherwise -> claude CLI ("anthropic"), the transport ft01-ft60 ran on.
    Returns (response_text, tokens_in, tokens_out, cost_usd, transport_label).
    """
    api_key = os.environ.get("BENCH_API_KEY")
    if api_key:
        return _invoke_claude_http(model, prompt, api_key, max_tokens, timeout_s)
    return _invoke_claude_cli(model, prompt, timeout_s)


def _invoke_claude_http(model, prompt, api_key, max_tokens, timeout_s):
    import urllib.request, json as _json
    body = _json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
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
    text = "".join(
        b.get("text", "") for b in out.get("content", []) if b.get("type") == "text"
    )
    usage = out.get("usage", {})
    ti = usage.get("input_tokens", 0)
    to = usage.get("output_tokens", 0)
    inr, outr = PRICING_MTOK.get(model, (10.0, 50.0))
    cost = ti / 1_000_000 * inr + to / 1_000_000 * outr
    return text, ti, to, cost, "anthropic-http"


def _invoke_claude_cli(model, prompt, timeout_s):
    import subprocess, json as _json
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", model, "--output-format", "json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude CLI rc={result.returncode}: {(result.stderr or '')[:200]}")
        out = _json.loads(result.stdout)
        text = out.get("result", "")
        ti = out.get("usage", {}).get("input_tokens", 0)
        to = out.get("usage", {}).get("output_tokens", 0)
        if ti == 0 or to == 0:
            for md in out.get("modelUsage", {}).values():
                ti = max(ti, md.get("inputTokens", 0))
                to = max(to, md.get("outputTokens", 0))
        inr, outr = PRICING_MTOK.get(model, (10.0, 50.0))
        cost = ti/1_000_000*inr + to/1_000_000*outr
        return text, ti, to, cost, "anthropic"
    except Exception as e:
        raise RuntimeError(f"claude CLI transport failed: {e}")

def invoke_openai_model(
    model: str,
    prompt: str,
    max_tokens: int = 512,
    timeout_s: float = 60.0,
) -> Tuple[str, int, int, float]:
    """Invoke OpenAI model via default_openai_transport.

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

    response = default_openai_transport(payload, timeout_s=timeout_s)

    if not response.get("choices"):
        raise RuntimeError(f"OpenAI API returned no choices: {response}")

    choice = response["choices"][0]
    response_text = choice.get("message", {}).get("content", "")

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
) -> FrontierV2Run:
    """Run a single task on a single tier.

    Returns:
        FrontierV2Run with complete metadata and result.
    """
    wall_start = time.time()

    # Invoke model
    response_text = None
    tokens_in = tokens_out = 0
    cost = 0.0
    transport = "unknown"

    try:
        if tier.startswith("claude-"):
            response_text, tokens_in, tokens_out, cost, transport = invoke_claude_model(tier, task.prompt)
        elif tier == "gpt-4o-mini":
            response_text, tokens_in, tokens_out, cost = invoke_openai_model(tier, task.prompt)
            transport = "openai"
        else:
            raise ValueError(f"Unknown tier: {tier}")
    except Exception as e:
        # Transport failure = an ERROR RUN, never a scored (wrong) answer.
        # Error runs carry error=<msg>, are excluded from accuracy, and are
        # retried on the next invocation (checkpoint skip is keyed on success).
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
        if gt:
            score = score_response(task, response_text, gt)
            correct = score.correct
        else:
            correct = False

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
    )


# ============================================================================
# Main Runner
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Parallel frontier v2 benchmark runner (N=60 tasks x 3 repeats x 5 tiers = 900 runs)"
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=180,
        help="Maximum runs per invocation (default 180, ~6min with 8 workers)",
    )
    parser.add_argument(
        "--checkpoint",
        default="bench/results/frontier-v2-checkpoint.jsonl",
        help="Path to checkpoint file (default bench/results/frontier-v2-checkpoint.jsonl)",
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

    # Load frontier data
    tasks = load_frontier_tasks("bench/tasks_frontier.jsonl")
    ground_truth = load_ground_truth("bench/ground_truth_frontier.jsonl")
    tiers = args.tiers.split(",")

    print(f"Frontier v2 Parallel Runner")
    print(f"  Tasks: {len(tasks)}")
    print(f"  Repeats: 3")
    print(f"  Tiers: {len(tiers)}")
    print(f"  Total runs: {len(tasks) * 3 * len(tiers)} (900)")
    print(f"  Workers: {args.workers}")
    print(f"  Max runs this call: {args.max_runs}")
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
            executor.submit(run_single_task, tier, task, repeat, ground_truth): (tier, task.id, repeat)
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
