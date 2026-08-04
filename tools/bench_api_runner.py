#!/usr/bin/env python3
"""
bench_api_runner.py — Run bench v2+v3 via Anthropic API (not CLI).
INDEX: Bench v2+v3 via Anthropic API (BENCH_API_KEY, API-only per bench-no-cli-fallback rule); reuses bench_runner machinery; CLI: `bench_api_runner.py <v2|v3|all> <model...>`

Per user rule (bench-no-cli-fallback): benchmarks run API-only to avoid CLI
subscription burn. BENCH_API_KEY must be set in the user environment.

Usage:
    python tools/bench_api_runner.py v2 haiku
    python tools/bench_api_runner.py v3 opus
    python tools/bench_api_runner.py all haiku sonnet opus  # v2+v3, all models

Exit: 0 on success, 1 on error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import anthropic
except ImportError:
    anthropic = None  # checked at runtime in make_api_runner; import stays side-effect free

# Import the bench_runner machinery (direct run vs. tools.* package import)
try:
    from bench_runner import (
        load_tasks,
        load_ground_truth,
        run_bench,
        build_summary,
        print_table,
        print_comparison,
    )
except ImportError:
    from tools.bench_runner import (
        load_tasks,
        load_ground_truth,
        run_bench,
        build_summary,
        print_table,
        print_comparison,
    )

BENCH_DIR = Path(__file__).resolve().parent.parent / "bench"
RESULTS_DIR = BENCH_DIR / "results"

# Model IDs for the API
# As of 2026-07-29; claude-3-5-* models return 404
MODEL_IDS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5",
}

# Variant specs: (tasks_file, ground_truth_file, display_name)
VARIANTS = {
    "v2": (
        BENCH_DIR / "tasks_v2_judgment.jsonl",
        BENCH_DIR / "ground_truth_v2_judgment.jsonl",
        "v2 (11 judgment tasks)",
    ),
    "v3": (
        BENCH_DIR / "tasks_v3_judgment.jsonl",
        BENCH_DIR / "ground_truth_v3_judgment.jsonl",
        "v3 (28 judgment tasks)",
    ),
}


def get_api_key() -> str:
    """Fetch BENCH_API_KEY from environment. Fail if missing."""
    key = os.environ.get("BENCH_API_KEY")
    if not key:
        raise RuntimeError(
            "BENCH_API_KEY not set in environment. "
            "Set it via: setx BENCH_API_KEY <key>"
        )
    return key


def make_api_runner(model_id: str, api_key: str):
    """Create a runner that calls the Anthropic API directly."""
    if anthropic is None:
        print("ERROR: anthropic SDK not installed. Run: pip install anthropic")
        sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)

    def runner(prompt: str) -> Tuple[str, Dict]:
        """Call the API and return (text, usage).

        Handles both regular text blocks and thinking blocks (extended thinking).
        Retries on overloaded errors.
        """
        import time as time_module
        max_retries = 3
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                start = time_module.time()
                response = client.messages.create(
                    model=model_id,
                    max_tokens=256,
                    messages=[{"role": "user", "content": prompt}],
                )
                elapsed_ms = (time_module.time() - start) * 1000

                # Extract text from response, skipping thinking blocks
                text = None
                for block in response.content:
                    # Handle regular text blocks
                    if hasattr(block, 'text'):
                        text = block.text
                        break
                    # Handle thinking blocks (extended thinking) - skip and look for text
                    elif hasattr(block, 'type') and block.type == 'thinking':
                        continue

                if text is None:
                    # Fallback: try to get the first text content
                    text = response.content[0].text if response.content else ""

                usage = {
                    "tokens": response.usage.output_tokens,
                    "latency_ms": elapsed_ms,
                }
                return (text, usage)

            except anthropic.APIStatusError as e:
                if e.status_code == 529:  # Overloaded
                    if attempt < max_retries - 1:
                        print(f"  [API overloaded, retry {attempt + 1}/{max_retries - 1}]", file=sys.stderr)
                        time_module.sleep(retry_delay)
                        continue
                raise

        raise RuntimeError(f"Failed after {max_retries} retries")

    return runner


def run_variant(
    variant: str,  # "v2" or "v3"
    model_alias: str,  # "haiku", "sonnet", "opus"
    api_key: str,
) -> Tuple[List[dict], float, dict]:
    """Run one variant x model combo. Return (results, accuracy, cost_summary)."""
    tasks_file, gt_file, display_name = VARIANTS[variant]
    model_id = MODEL_IDS[model_alias]

    print(f"\n[{variant.upper()}] Running {model_alias} ({model_id})...")

    tasks = load_tasks(tasks_file)
    ground_truth = load_ground_truth(gt_file)
    runner = make_api_runner(model_id, api_key)

    results, accuracy = run_bench(tasks, ground_truth, runner)
    summary = build_summary(model_alias, results, accuracy)

    return results, accuracy, summary


def format_results_markdown(
    variant: str,
    models_summaries: Dict[str, dict],
    timestamp: str,
) -> str:
    """Format results as markdown for bench/results/."""
    tasks_file, gt_file, display_name = VARIANTS[variant]
    n_tasks = len(load_tasks(tasks_file))

    md = f"""# Benchmark {variant.upper()} run — {timestamp} — Haiku vs Sonnet vs Opus (API)

The {display_name}, run via Anthropic API (BENCH_API_KEY).

## Method

Each model answered all {n_tasks} tasks **blind** (no access to ground truth), scored by exact/regex match.
Runs via direct HTTP API, not CLI.

## Result

| Model  | Score | Accuracy | Avg Tokens | Total Tokens |
|--------|-------|----------|-----------|--------------|
"""

    for alias in ("haiku", "sonnet", "opus"):
        if alias not in models_summaries:
            continue
        s = models_summaries[alias]
        score_str = f"{s['n_correct']}/{s['n_tasks']}"
        acc_str = f"{s['accuracy']:.0%}"
        avg_tok = s.get("avg_tokens", 0)
        tot_tok = s.get("total_tokens", 0)
        md += f"| {alias.capitalize():<6} | {score_str:<5} | {acc_str:<8} | {avg_tok or '-':<9} | {tot_tok or '-':<12} |\n"

    md += f"\n## Cost axis\n\nTotal tokens across all {n_tasks} tasks:\n"
    for alias in ("haiku", "sonnet", "opus"):
        if alias not in models_summaries:
            continue
        s = models_summaries[alias]
        tot_tok = s.get("total_tokens", 0)
        md += f"- **{alias.capitalize()}**: {tot_tok} tokens\n"

    md += f"\n## Notes\n- Runs via Anthropic API (BENCH_API_KEY), not CLI\n- Transport: anthropic-http\n"

    return md


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variants",
        default="all",
        choices=["v2", "v3", "all"],
        help="Which benchmark variant(s) to run (v2, v3, or all; default: all)",
    )
    parser.add_argument(
        "--models",
        default="haiku,sonnet,opus",
        help="Which model(s) to run, comma-separated (default: haiku,sonnet,opus)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Estimate cost without running",
    )
    args = parser.parse_args(argv)

    # Expand 'all' to v2 and v3
    if args.variants == "all":
        variants = ["v2", "v3"]
    else:
        variants = [args.variants]

    # Parse models (comma-separated)
    models = [m.strip() for m in args.models.split(",")]

    api_key = get_api_key()

    timestamp = datetime.now().strftime("%Y-%m-%d")
    results_by_variant: Dict[str, Dict[str, dict]] = {}

    print(f"Bench API runner starting at {timestamp}")
    print(f"Variants: {', '.join(variants)}")
    print(f"Models: {', '.join(models)}")
    print(f"Dry run: {args.dry_run}")

    if args.dry_run:
        print("\n[DRY RUN] Estimating cost...")
        for variant in variants:
            tasks_file, _, _ = VARIANTS[variant]
            n_tasks = len(load_tasks(tasks_file))
            # Rough estimate: 300 input tokens + 30 output per task
            est_tokens = n_tasks * 330 * len(models)
            print(f"  {variant}: {n_tasks} tasks x {len(models)} models = {est_tokens} tokens")
        print("\nDry run complete; not running actual benchmarks.")
        return 0

    # Run each variant x model combo
    for variant in variants:
        results_by_variant[variant] = {}
        for model_alias in models:
            try:
                results, accuracy, summary = run_variant(
                    variant, model_alias, api_key
                )
                results_by_variant[variant][model_alias] = summary

                # Print per-model table
                print_table(model_alias, results, accuracy)

            except Exception as e:
                print(f"ERROR running {variant} x {model_alias}: {e}", file=sys.stderr)
                return 1

    # Write markdown results for each variant
    for variant in variants:
        if variant not in results_by_variant or not results_by_variant[variant]:
            print(f"WARN: No results for {variant}")
            continue

        md = format_results_markdown(variant, results_by_variant[variant], timestamp)
        output_file = RESULTS_DIR / f"{timestamp}-api-{variant}-haiku-sonnet-opus.md"
        output_file.write_text(md, encoding="utf-8")
        print(f"\nWrote results: {output_file}")

    # Print comparison table
    if len(results_by_variant) > 0:
        all_summaries = []
        for variant in variants:
            for model_alias in models:
                if variant in results_by_variant and model_alias in results_by_variant[variant]:
                    s = results_by_variant[variant][model_alias].copy()
                    s["model"] = f"{model_alias} ({variant})"
                    all_summaries.append(s)
        if all_summaries:
            print_comparison(all_summaries)

    print(f"\n✓ Benchmark run complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
