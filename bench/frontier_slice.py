#!/usr/bin/env python3
"""Frontier-discrimination benchmark slice for model tier separation.

Measures whether the benchmark can discriminate between Haiku and Opus model
tiers via a set of ~20 hard tasks designed to separate weaker from stronger models:
- Multi-step reasoning (SQL analysis, boolean logic, state machines)
- Subtle defect detection (race conditions, type coercion, security vulns)
- Long-context needle finding and semantic judgment
- Configuration/API contract correctness

Scoring: regex/exact match against ground truth.

USAGE
-----
Offline (no API key, no network, canned mock responses):
  python bench/frontier_slice.py --mode offline

Live (ANTHROPIC_API_KEY, real models, cost-gated):
  python bench/frontier_slice.py --mode live --model claude-3-5-haiku-20241022 --confirm-spend
  python bench/frontier_slice.py --mode live --model claude-3-5-opus-20241022 --confirm-spend

Cost estimate (before running live):
  python bench/frontier_slice.py --mode live --model claude-3-5-opus-20241022
  (prints estimate and exits 2 without --confirm-spend)
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Import bench utilities if needed
sys.path.insert(0, str(Path(__file__).parent.parent))

# Module-level error tracking for API runs
_error_tasks = []  # Populated by run_live_frontier_benchmark on API errors


# ============================================================================
# Task Loading
# ============================================================================


@dataclass
class FrontierTask:
    """One frontier discrimination task."""
    id: str
    category: str
    match: str  # "exact" or "regex"
    prompt: str
    discrimination_rationale: str = ""


@dataclass
class GroundTruth:
    """Ground truth for one task."""
    id: str
    expected: Optional[str] = None
    expected_regex: Optional[str] = None
    exemplar: Optional[str] = None  # Correct example that matches the regex/expected
    counter_example: Optional[str] = None  # Incorrect example that must NOT match


def load_frontier_tasks(path: str = "bench/tasks_frontier.jsonl") -> List[FrontierTask]:
    """Load frontier tasks from JSONL file."""
    tasks = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    obj = json.loads(line)
                    tasks.append(FrontierTask(**obj))
    except FileNotFoundError:
        print(f"ERROR: {path} not found", file=sys.stderr)
        sys.exit(1)
    return tasks


def load_ground_truth(path: str = "bench/ground_truth_frontier.jsonl") -> Dict[str, GroundTruth]:
    """Load ground truth from JSONL file."""
    gt = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    obj = json.loads(line)
                    gt[obj["id"]] = GroundTruth(**obj)
    except FileNotFoundError:
        print(f"ERROR: {path} not found", file=sys.stderr)
        sys.exit(1)
    return gt


# ============================================================================
# Scoring
# ============================================================================


@dataclass
class FrontierScore:
    """Score for one frontier task."""
    task_id: str
    category: str
    correct: bool
    reason: str
    raw_response_preview: str = ""


def score_response(
    task: FrontierTask,
    response_text: str,
    ground_truth: GroundTruth,
) -> FrontierScore:
    """Score a response against ground truth.

    Returns:
        FrontierScore with correct=True/False and reason
    """
    correct = False
    reason = "No match found"
    preview = response_text[:200] if response_text else "(empty)"

    if not response_text or not response_text.strip():
        return FrontierScore(
            task_id=task.id,
            category=task.category,
            correct=False,
            reason="Empty response",
            raw_response_preview=preview,
        )

    if task.match == "exact" and ground_truth.expected:
        # Exact match (case-insensitive)
        if response_text.strip().lower() == ground_truth.expected.lower():
            correct = True
            reason = "Exact match"
        else:
            reason = f"Expected: {ground_truth.expected[:50]}..."
    elif task.match == "regex" and ground_truth.expected_regex:
        # Regex match
        try:
            if re.search(ground_truth.expected_regex, response_text, re.IGNORECASE | re.DOTALL):
                correct = True
                reason = "Regex match"
            else:
                reason = f"Pattern not found: {ground_truth.expected_regex[:50]}..."
        except re.error as e:
            reason = f"Regex error: {e}"
    else:
        reason = "No ground truth defined"

    return FrontierScore(
        task_id=task.id,
        category=task.category,
        correct=correct,
        reason=reason,
        raw_response_preview=preview,
    )


# ============================================================================
# FakeTransport for Offline Testing
# ============================================================================


class FakeTransport:
    """Offline test transport with scripted responses for frontier tasks."""

    def __init__(self, task_id: str = "unknown"):
        self.call_count = 0
        self.task_id = task_id

    def __call__(self, prompt: str) -> str:
        """Return a canned response based on task ID."""
        self.call_count += 1
        task_id = self.task_id

        # Return scripted responses that are intentionally PARTIAL or WRONG
        # to simulate a weaker model's incomplete reasoning
        responses = {
            "ft01_multi_step_sql_refactor": "Problem 1: YEAR function is slow. The database query should use indexed comparison on the created_at column directly. Problem 2: The GROUP BY needs all non-aggregated columns. Final query: SELECT c.id, c.name, t.amount, COUNT(*) as cnt FROM customers c JOIN transactions t ON c.id = t.customer_id WHERE t.created_at >= '2024-01-01' AND t.created_at < '2025-01-01' GROUP BY c.id, c.name, t.amount HAVING COUNT(*) > 100 ORDER BY cnt DESC LIMIT 10",
            "ft02_code_defect_detection_concurrent": "Version B is broken. It doesn't have a lock, so multiple threads can read the same value and write back the same incremented value, losing updates. Version A is correct because it holds the lock during the entire read-modify-write sequence.",
            "ft03_instruction_conflict_precedence": "These requirements conflict. Requirement 1 says no numbers, but requirement 2 says count items, which requires numbers. Requirement 3 says use emoji. I'll interpret this as: Requirements contradict each other, so we must choose which takes precedence.",
            "ft04_long_context_needle_judgment": "The contradiction is in the requirement about duplicate handling. Section 3.2 says return cached result without re-charging, but later it says reject with HTTP 409 Conflict. These conflict directly.",
            "ft05_json_schema_validation_edge_case": "The sample is INVALID because it has an unknown_field property, and the schema specifies additionalProperties: false, which means no extra fields are allowed.",
            "ft06_git_history_blame_analysis": "Commit 55443210 by Alice at 2024-10-15 14:25 removed the permission validation check. The commit message probably said something like 'fix: remove redundant permission check' or 'refactor: simplify auth flow'.",
            "ft07_ambiguous_english_resolution": "Interpretation 1: Errors are recorded in logs before they are handled/resolved. Interpretation 2: The system requires logging to happen before error handling can begin. The first interpretation is more likely in technical context because logging is typically a prerequisite step.",
            "ft08_nested_boolean_logic_simplification": "Factor out A: A AND ((B AND NOT C) OR (NOT B AND C) OR (NOT B AND NOT C)). The inner expression simplifies to A AND (NOT B OR NOT C) which can also be written as A AND NOT(B AND C). This is minimal because we can't simplify further.",
            "ft09_refactoring_correctness_semantic": "EQUIVALENT. Both versions produce the same result: they filter for positive items and double them, returning a list in the same order.",
            "ft10_config_validation_corner_case": "For a production deployment: timeout=15 (from service.api override), retries=5 (from env.staging). The override precedence is: service-level overrides env-level, which overrides top-level defaults.",
            "ft11_regex_pattern_equivalence": "NOT_EQUIVALENT. Pattern 1 requires at least one letter followed by optional digits only. Pattern 2 allows mixing letters and digits after the first character. Example: 'a1b' matches Pattern 2 but not Pattern 1.",
            "ft12_api_contract_violation_detection": "The response has multiple violations: 1) id is a string '12345' but should be integer, 2) email format looks correct, 3) created_at is missing but is required by the schema, 4) unknown fields would violate additionalProperties, but this one doesn't have extra fields besides the ones shown.",
            "ft13_database_migration_safety": "Risk 1: Downtime if you rename the column directly while traffic is active. Mitigation: Use a dual-column approach with a shadow column. Risk 2: Data loss or inconsistency. Mitigation: Write triggers or application logic to keep both columns in sync during migration. Risk 3: Rollback difficulty. Mitigation: Version your schema with both columns present for some time before cleanup.",
            "ft14_performance_bottleneck_identification": "I agree the database is the bottleneck at 50ms. Total latency is roughly 10+50+5+15=80ms, so the database query takes 62.5% of the total time. To optimize, you could add a cache layer or use query result caching. First, I'd profile to confirm this is the issue.",
            "ft15_state_machine_correctness": "Yes, this breaks the workflow. If you skip PROCESSING, you miss side effects like audit logging, email notifications, or data validation that normally happens in PROCESSING. Fix: Instead of skipping to COMPLETED, add a new state like MANUAL_SKIP that still runs necessary cleanup before transitioning to COMPLETED.",
            "ft16_unicode_normalization_gotcha": "The issue is Unicode normalization. 'café' can be represented in NFC form (single composed character) or NFD form (base character + combining diacritic). When you hash them, they produce different hashes. Fix: Normalize both strings to the same form (typically NFC) before hashing.",
            "ft17_caching_invalidation_consistency": "The race condition is that the cache invalidation request is sent, but the async job is still running and might read the cache before it's invalidated, or the cache is invalidated but then a new request hits while the async job is still updating the database. Fix: Use versioning or ETags in the cached value, or use event-sourced cache invalidation where the cache is invalidated only after the async job completes.",
            "ft18_type_coercion_subtle_bug": "The code will print 'zero'. This is because JavaScript's loose equality operator (==) performs type coercion. The string '0' is coerced to the number 0, so '0' == 0 is true. Fix: Use strict equality (===) instead: if (userInput === 0) to avoid unexpected type coercion.",
            "ft19_format_string_vulnerability": "This is a format string vulnerability. The user input is passed directly as the format string to printf(). An attacker can craft input like '%x %x %x' to read from the stack, or '%n' to write to memory. Fix: Always use printf('%s', user_input) instead of printf(user_input).",
            "ft20_distributed_lock_correctness": "The problem is a safety issue. If Process A's lock expires while it's still in the critical section, Process B can acquire the lock and enter the critical section, creating a race condition. When Process A later tries to delete the lock, it might delete Process B's lock instead. Fix: Use a unique token for each lock holder and include the token in the delete operation (DEL key IF value == token). Discuss trade-offs: Redlock is more robust but requires 3+ Redis instances; single-instance token validation is simpler but still has edge cases under network partitions.",
        }

        return responses.get(task_id, "(scripted response for frontier task not found)")


# ============================================================================
# Claude API Runner
# ============================================================================


def run_live_frontier_benchmark(
    tasks: List[FrontierTask],
    model: str = "claude-3-5-opus-20241022",
    confirm_spend: bool = False,
) -> Tuple[List[FrontierScore], float]:
    """Run frontier tasks against Claude API.

    Args:
        tasks: List of frontier tasks
        model: Claude model ID
        confirm_spend: If False, print cost estimate and exit(2) without running

    Returns:
        Tuple of (scores, accuracy_percent)
    """
    # Estimate cost (input ~1000 tokens per task, output ~300)
    input_tokens_estimate = len(tasks) * 1000
    output_tokens_estimate = len(tasks) * 300

    # Anthropic Claude pricing (as of 2024)
    input_cost_per_mtok = {
        "claude-3-5-haiku-20241022": 0.80,
        "claude-3-5-sonnet-20241022": 3.00,
        "claude-3-5-opus-20241022": 15.00,
    }
    output_cost_per_mtok = {
        "claude-3-5-haiku-20241022": 4.00,
        "claude-3-5-sonnet-20241022": 15.00,
        "claude-3-5-opus-20241022": 75.00,
    }

    input_rate = input_cost_per_mtok.get(model)
    output_rate = output_cost_per_mtok.get(model)

    # Label fallback rates clearly when model not in pricing table
    input_rate_label = ""
    output_rate_label = ""
    if input_rate is None:
        input_rate = 10.0
        input_rate_label = " [FALLBACK - model not in pricing table]"
    if output_rate is None:
        output_rate = 30.0
        output_rate_label = " [FALLBACK - model not in pricing table]"

    estimated_cost_usd = (
        (input_tokens_estimate / 1_000_000) * input_rate +
        (output_tokens_estimate / 1_000_000) * output_rate
    )

    print(f"\n{'='*70}")
    print(f"COST ESTIMATE: {len(tasks)} frontier tasks")
    print(f"  Input:  ~{input_tokens_estimate:,} tokens @ ${input_rate:.2f}/MTok{input_rate_label}")
    print(f"  Output: ~{output_tokens_estimate:,} tokens @ ${output_rate:.2f}/MTok{output_rate_label}")
    print(f"  Total:  ~${estimated_cost_usd:.2f} USD")
    print(f"{'='*70}\n")

    if not confirm_spend:
        print("Use --confirm-spend to actually run (without it, exits 2).")
        if input_rate_label or output_rate_label:
            print("Note: FALLBACK pricing rates were used in the estimate above.")
        sys.exit(2)

    # Check API key up front (fail-closed) before creating client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("SKIP: ANTHROPIC_API_KEY not set — skipping live run", file=sys.stderr)
        return ([], 0.0)

    # Check HALT sentinel before starting API calls
    try:
        from tools import halt
    except ImportError:
        try:
            import halt
        except ImportError:
            halt = None

    if halt:
        if halt.is_halted():
            halt_info = halt.get_halt_info()
            reason = halt_info.get("reason") if halt_info else "(no reason recorded)"
            print(f"SKIP: HALT sentinel detected — {reason}", file=sys.stderr)
            return ([], 0.0)

    # Import Claude client
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic not installed. Install with: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    global _error_tasks
    scores = []
    ground_truth = load_ground_truth()
    _error_tasks = []  # Reset and track tasks that failed with API errors

    for i, task in enumerate(tasks):
        print(f"  [{i+1}/{len(tasks)}] {task.id} ({task.category})...", end=" ", flush=True)

        response_text = None
        try:
            response = client.messages.create(
                model=model,
                max_tokens=512,
                messages=[
                    {"role": "user", "content": task.prompt}
                ],
            )
            response_text = response.content[0].text if response.content else ""
            print("OK")
        except Exception as e:
            # API error: record as status "error", do NOT score
            error_msg = str(e)
            print(f"ERROR: {error_msg}")
            _error_tasks.append({
                "task_id": task.id,
                "category": task.category,
                "error": error_msg,
            })
            # Small delay to avoid rate limits even on error
            time.sleep(0.5)
            continue  # Skip scoring for error tasks

        gt = ground_truth.get(task.id)
        if gt and response_text is not None:
            score = score_response(task, response_text, gt)
            scores.append(score)

        # Small delay to avoid rate limits
        time.sleep(0.5)

    accuracy = sum(1 for s in scores if s.correct) / len(scores) if scores else 0.0
    return scores, accuracy * 100


def run_offline_frontier_benchmark(
    tasks: List[FrontierTask],
) -> Tuple[List[FrontierScore], float]:
    """Run frontier tasks with FakeTransport (no API calls)."""
    ground_truth = load_ground_truth()
    scores = []

    for task in tasks:
        transport = FakeTransport(task_id=task.id)
        response_text = transport(task.prompt)
        gt = ground_truth.get(task.id)
        if gt:
            score = score_response(task, response_text, gt)
            scores.append(score)

    accuracy = sum(1 for s in scores if s.correct) / len(scores) if scores else 0.0
    return scores, accuracy * 100


# ============================================================================
# Reporting
# ============================================================================


def print_results_table(scores: List[FrontierScore], accuracy: float, mode: str, model: str):
    """Print results table."""
    print("\n" + "=" * 90)
    print(f"Frontier Discrimination Slice — {mode.upper()} mode ({model})")
    print("=" * 90)
    print(f"{'ID':<20} {'Category':<30} {'Result':<10}")
    print("-" * 90)

    for score in scores:
        result = "PASS" if score.correct else "FAIL"
        print(f"{score.task_id:<20} {score.category:<30} {result:<10}")

    print("-" * 90)
    print(f"Accuracy: {sum(1 for s in scores if s.correct)}/{len(scores)} = {accuracy:.1f}%")
    print("=" * 90 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Frontier discrimination benchmark slice for model tier separation",
    )
    parser.add_argument(
        "--mode",
        choices=["offline", "live"],
        default="offline",
        help="Run mode: offline (FakeTransport, no API calls) or live (Claude API)",
    )
    parser.add_argument(
        "--model",
        default="claude-3-5-opus-20241022",
        help="Claude model ID for live mode",
    )
    parser.add_argument(
        "--confirm-spend",
        action="store_true",
        help="Confirm spending tokens on live run (required for --mode live)",
    )
    parser.add_argument(
        "--tasks",
        default="bench/tasks_frontier.jsonl",
        help="Path to tasks file",
    )
    parser.add_argument(
        "--ground-truth",
        default="bench/ground_truth_frontier.jsonl",
        help="Path to ground truth file",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON file (env: FRONTIER_SLICE_OUTPUT; default: state/bench/frontier_slice_results.json)",
    )

    args = parser.parse_args()

    # Resolve output path: env var > CLI arg > default (gitignored state dir)
    if args.output is None:
        args.output = os.environ.get(
            "FRONTIER_SLICE_OUTPUT",
            "state/bench/frontier_slice_results.json"
        )

    # Load tasks and ground truth
    tasks = load_frontier_tasks(args.tasks)
    print(f"Loaded {len(tasks)} frontier tasks")

    # Run benchmark
    if args.mode == "offline":
        print("Running OFFLINE frontier benchmark (FakeTransport)...\n")
        scores, accuracy = run_offline_frontier_benchmark(tasks)
    else:
        print(f"Running LIVE frontier benchmark against {args.model}...\n")
        scores, accuracy = run_live_frontier_benchmark(tasks, model=args.model, confirm_spend=args.confirm_spend)

    # Print results
    print_results_table(scores, accuracy, args.mode, args.model)

    # Report any API errors (live mode only)
    if args.mode == "live" and _error_tasks:
        print(f"\nWARNING: {len(_error_tasks)} task(s) had API errors and were excluded from accuracy:")
        for error_task in _error_tasks:
            print(f"  - {error_task['task_id']}: {error_task['error'][:80]}")

    # Save results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    results = {
        "mode": args.mode,
        "model": args.model if args.mode == "live" else "fake-transport",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "accuracy_percent": accuracy,
        "tasks_correct": sum(1 for s in scores if s.correct),
        "tasks_total": len(scores),
        "task_count": len(tasks),
        "tasks": [asdict(s) for s in scores],
    }

    # Include error tasks if present
    if _error_tasks:
        results["error_tasks"] = _error_tasks
        results["error_count"] = len(_error_tasks)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"Results saved to: {output_path}")

    # Exit 0 = run completed and every task was scored; accuracy is DATA, not
    # process health (a low FakeTransport score must not read as a crash).
    # If there were API errors (incomplete run), exit 1
    exit_code = 0 if len(scores) == len(tasks) else 1
    if _error_tasks:
        print(f"Exiting with code {exit_code} due to {len(_error_tasks)} error(s)", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
