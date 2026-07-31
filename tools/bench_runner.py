#!/usr/bin/env python3
"""
bench_runner.py — Held-out benchmark scorer for fleet subagent capability claims.

Wave-26 context: an external critique observed that the claim "Haiku is sufficient
for fleet subagent work" was unmeasured and, where it *was* measured at all, was
self-graded by the fleet (agents grading agents) with results parked in a private
MEMORY.md. That is not evidence. This module is the MEASUREMENT APPARATUS, not a
verdict: it loads a fixed set of held-out tasks (bench/tasks.jsonl), scores a
model runner's outputs against externally-fixed ground truth (bench/ground_truth.jsonl)
by exact string match or regex match, and prints a per-model accuracy table.

It ships with a MOCK runner (`mock_runner`) so the SCORING LOGIC can be tested and
demonstrated completely offline, without calling any model or spending any tokens.
The mock runner is a small deterministic heuristic (regex/string-parsing stand-in).
Its accuracy says NOTHING about how Haiku, Sonnet, or Opus would actually score —
see bench/README.md for how to wire a real model runner.

Usage:
    python tools/bench_runner.py                  # run the mock runner, print table
    python tools/bench_runner.py --runner mock
    python tools/bench_runner.py --tasks path/to/tasks.jsonl --ground-truth path/to/gt.jsonl

Cost axis (wave-32): accuracy alone can't settle "is Haiku good enough" — the
whole cost thesis is "equal quality at ~1/3 the cost," which requires reporting
cost *alongside* accuracy. A runner may therefore return either a bare string
(text only, backward-compatible) or a `(text, usage)` pair, where usage carries
a token count and/or a latency figure. run_bench() records per-task usage,
summarize_cost() aggregates it, and the tables print accuracy and cost together.
This module still never calls a real model or spends a token; usage is supplied
by whatever runner the caller wires in.

Exit codes: 0 always on a completed run (this is a measurement tool, not a gate).
Programmatic API: load_tasks(), load_ground_truth(), score_output(), run_bench(),
normalize_runner_output(), summarize_cost(), build_summary(), print_comparison().
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

BENCH_DIR = Path(__file__).resolve().parent.parent / "bench"
DEFAULT_TASKS_PATH = BENCH_DIR / "tasks.jsonl"
DEFAULT_GROUND_TRUTH_PATH = BENCH_DIR / "ground_truth.jsonl"

REQUIRED_TASK_FIELDS = ("id", "category", "match", "prompt")
VALID_MATCH_TYPES = ("exact", "regex")

# A model runner is any callable that takes a prompt string and returns either:
#   * the model's raw text response (a str), OR
#   * a (text, usage) pair, where `usage` is a token/latency figure — a dict
#     like {"tokens": 812, "latency_ms": 430.0}, or a bare int/float taken as a
#     token count.
# The bare-str form is the original contract and stays fully supported; runners
# that don't measure cost simply keep returning a string. Real runners
# (Haiku/Sonnet/Opus/etc.) are wired by the caller; this module never imports a
# model SDK itself.
ModelRunner = Callable[[str], object]

# Normalized per-task usage record: either field may be None when the runner did
# not report it. A task where the runner returned a bare string has usage=None.
USAGE_KEYS = ("tokens", "latency_ms")


def normalize_runner_output(output: object) -> Tuple[Optional[str], Optional[dict]]:
    """Normalize a runner's return value into (text, usage).

    Accepts, in order:
      * str (or None)           -> (output, None)          [original contract]
      * (text, usage) 2-tuple   -> (text, normalized_usage)
        where usage may be:
          - a dict with any of USAGE_KEYS -> those fields (others None)
          - an int/float                  -> {"tokens": int(usage), "latency_ms": None}
          - None                          -> None

    Any other shape is a runner bug and raises ValueError, so a malformed runner
    fails loudly rather than silently scoring blank.
    """
    if output is None or isinstance(output, str):
        return output, None
    if isinstance(output, tuple):
        if len(output) != 2:
            raise ValueError(
                f"runner returned a {len(output)}-tuple; expected a bare str or a "
                f"(text, usage) 2-tuple"
            )
        text, usage = output
        if text is not None and not isinstance(text, str):
            raise ValueError(f"runner (text, usage) tuple has non-str text: {type(text).__name__}")
        return text, _normalize_usage(usage)
    raise ValueError(
        f"runner returned unsupported type {type(output).__name__}; expected a bare "
        f"str or a (text, usage) 2-tuple"
    )


def _normalize_usage(usage: object) -> Optional[dict]:
    if usage is None:
        return None
    if isinstance(usage, bool):
        # bool is an int subclass; a bool token count is almost certainly a bug.
        raise ValueError("usage token count must be a number, not a bool")
    if isinstance(usage, (int, float)):
        return {"tokens": int(usage), "latency_ms": None}
    if isinstance(usage, dict):
        return {
            "tokens": usage.get("tokens"),
            "latency_ms": usage.get("latency_ms"),
        }
    raise ValueError(
        f"usage must be a dict, an int/float token count, or None; got {type(usage).__name__}"
    )


def load_jsonl(path: Path) -> List[dict]:
    """Load a JSON-Lines file into a list of dicts. Blank lines are skipped."""
    items: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return items


def load_tasks(tasks_path: Optional[Path] = None) -> List[dict]:
    """Load the task list and validate required fields are present."""
    path = Path(tasks_path) if tasks_path else DEFAULT_TASKS_PATH
    tasks = load_jsonl(path)
    for task in tasks:
        missing = [f for f in REQUIRED_TASK_FIELDS if f not in task]
        if missing:
            raise ValueError(f"task {task.get('id', '<unknown>')} missing fields: {missing}")
        if task["match"] not in VALID_MATCH_TYPES:
            raise ValueError(
                f"task {task['id']} has invalid match type {task['match']!r}; "
                f"expected one of {VALID_MATCH_TYPES}"
            )
    return tasks


def load_ground_truth(ground_truth_path: Optional[Path] = None) -> Dict[str, dict]:
    """Load ground truth entries keyed by task id."""
    path = Path(ground_truth_path) if ground_truth_path else DEFAULT_GROUND_TRUTH_PATH
    entries = load_jsonl(path)
    by_id: Dict[str, dict] = {}
    for entry in entries:
        if "id" not in entry:
            raise ValueError(f"ground truth entry missing 'id': {entry}")
        by_id[entry["id"]] = entry
    return by_id


def score_output(output: Optional[str], ground_truth_entry: dict, match_type: str) -> bool:
    """Score a single model output against its ground truth entry.

    exact: case-insensitive, whitespace-trimmed string equality against
           ground_truth_entry["expected"].
    regex: re.search of ground_truth_entry["expected_regex"] against the
           trimmed output (search, not fullmatch — callers anchor with ^/$
           in the pattern when they need a full-string match).
    """
    if output is None:
        return False
    trimmed = output.strip()
    if match_type == "regex":
        pattern = ground_truth_entry.get("expected_regex")
        if pattern is None:
            raise ValueError("ground truth entry for a regex task is missing 'expected_regex'")
        return re.search(pattern, trimmed) is not None
    if match_type == "exact":
        expected = ground_truth_entry.get("expected")
        if expected is None:
            raise ValueError("ground truth entry for an exact task is missing 'expected'")
        return trimmed.lower() == str(expected).strip().lower()
    raise ValueError(f"unknown match type: {match_type!r}")


def run_bench(
    tasks: List[dict],
    ground_truth: Dict[str, dict],
    runner: ModelRunner,
) -> Tuple[List[dict], float]:
    """Run every task through `runner`, score against `ground_truth`.

    Returns (per_task_results, accuracy) where accuracy is correct/total
    (0.0 for an empty task list, never a division error).
    """
    results: List[dict] = []
    correct = 0
    for task in tasks:
        tid = task["id"]
        if tid not in ground_truth:
            raise KeyError(f"no ground truth entry for task id {tid!r}")
        gt_entry = ground_truth[tid]
        text, usage = normalize_runner_output(runner(task["prompt"]))
        ok = score_output(text, gt_entry, task["match"])
        if ok:
            correct += 1
        results.append(
            {
                "id": tid,
                "category": task.get("category", ""),
                "output": text,
                "correct": ok,
                # Cost axis: None when the runner returned a bare string.
                "usage": usage,
                "tokens": usage.get("tokens") if usage else None,
                "latency_ms": usage.get("latency_ms") if usage else None,
            }
        )
    accuracy = correct / len(tasks) if tasks else 0.0
    return results, accuracy


def summarize_cost(results: List[dict]) -> dict:
    """Aggregate the per-task cost axis recorded by run_bench.

    Returns a dict with total/average tokens and latency across the tasks that
    reported them. Fields are None when no task reported that figure, so a
    string-only (cost-free) run summarizes cleanly to all-None rather than 0.
    """
    token_vals = [r["tokens"] for r in results if r.get("tokens") is not None]
    latency_vals = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
    n = len(results)

    def _avg(vals):
        return (sum(vals) / len(vals)) if vals else None

    return {
        "n_tasks": n,
        "n_with_tokens": len(token_vals),
        "n_with_latency": len(latency_vals),
        "total_tokens": sum(token_vals) if token_vals else None,
        "avg_tokens": _avg(token_vals),
        "total_latency_ms": sum(latency_vals) if latency_vals else None,
        "avg_latency_ms": _avg(latency_vals),
        "has_cost": bool(token_vals or latency_vals),
    }


def build_summary(model_name: str, results: List[dict], accuracy: float) -> dict:
    """Combine accuracy and cost into one per-model row for a comparison table."""
    n = len(results)
    n_correct = sum(1 for r in results if r["correct"])
    row = {
        "model": model_name,
        "n_tasks": n,
        "n_correct": n_correct,
        "accuracy": accuracy,
    }
    row.update(summarize_cost(results))
    return row


# ---------------------------------------------------------------------------
# Mock runner — zero-cost, deterministic, offline. Exists ONLY to prove the
# scoring pipeline works end to end without a network call or an API key.
# Do not read its accuracy as evidence about any real model's capability.
# ---------------------------------------------------------------------------

_FILE_RE = re.compile(r"File:\s*(\S+)")
_FAILED_TEST_RE = re.compile(r"FAILED\s+\S+::(\w+)")
_ISSUE_NUM_RE = re.compile(r"#(\d+)")
_PR_TITLE_RE = re.compile(r"Title:\s*(\w+):")
_EXC_TYPE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):")
_VERSION_RE = re.compile(r"\[(\d+\.\d+\.\d+)\]")
_IDENTIFIER_RE = re.compile(r"Identifier:\s*(\S+)")
_FILE_LINE_RE = re.compile(r"([A-Za-z0-9_\-./]+\.py:\d+)")


def _classify_file_path(path: str) -> str:
    filename = path.rsplit("/", 1)[-1]
    if path.startswith("tests/") or "/tests/" in path or filename.startswith("test_"):
        return "test"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in {"md", "rst", "txt"}:
        return "docs"
    if ext in {"json", "yaml", "yml", "toml", "ini", "cfg"}:
        return "config"
    return "code"


def _snake_to_camel(identifier: str) -> str:
    parts = identifier.split("_")
    if not parts:
        return identifier
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def mock_runner(prompt: str) -> str:
    """Deterministic offline stand-in for a real model runner.

    Handles extraction/classification/transform tasks with plain regex and
    string logic (no judgment required). Deliberately does NOT attempt the
    semantic "is this a real bug" judgment task and always answers "no" for
    it — a cheap default that is sometimes wrong, so the scorer has at least
    one guaranteed miss to prove it distinguishes correct from incorrect
    (a mock that always scored 100% would not exercise the failure path).
    """
    m = _FILE_RE.search(prompt)
    if m and "Classify this changed file path" in prompt:
        return _classify_file_path(m.group(1))

    m = _FAILED_TEST_RE.search(prompt)
    if m and "failing test function" in prompt:
        return m.group(1)

    if "issue/PR number" in prompt:
        m = _ISSUE_NUM_RE.search(prompt)
        if m:
            return m.group(1)

    m = _PR_TITLE_RE.search(prompt)
    if m and "PR title" in prompt:
        return m.group(1)

    if "exception class name" in prompt:
        matches = _EXC_TYPE_RE.findall(prompt)
        if matches:
            return matches[-1]

    if "is the finding a real bug" in prompt:
        return "no"

    m = _VERSION_RE.search(prompt)
    if m and "semantic version" in prompt:
        return m.group(1)

    m = _IDENTIFIER_RE.search(prompt)
    if m and "camelCase" in prompt:
        return _snake_to_camel(m.group(1))

    m = _FILE_LINE_RE.search(prompt)
    if m and "path:line" in prompt:
        return m.group(1)

    return ""


# ---------------------------------------------------------------------------
# Claude CLI runner — shell the local `claude` command for real model calls.
# Gracefully unavailable if `claude` is not on PATH.
# ---------------------------------------------------------------------------

def _make_claude_runner(model_alias: str) -> ModelRunner:
    """Create a runner that shells the local `claude` CLI.

    Args:
        model_alias: A model alias (e.g., "haiku", "sonnet", "opus") or full
                     model id (e.g., "claude-haiku-4-5-20251001"). Passed to
                     claude --model <alias>.

    Returns:
        A ModelRunner that shells `claude -p <prompt> --model <alias>
        --output-format json` and returns (text, usage) with latency_ms.
        Raises RuntimeError if claude is not available.
    """
    def runner(prompt: str) -> tuple:
        if not shutil.which("claude"):
            raise RuntimeError(
                "claude CLI not found on PATH: install it or check your PATH. "
                "See https://github.com/anthropic-ai/claude-code for setup."
            )
        try:
            start = time.time()
            result = subprocess.run(
                ["claude", "-p", prompt, "--model", model_alias, "--output-format", "json"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=300,  # 5 minutes per task
            )
            elapsed_ms = (time.time() - start) * 1000
            if result.returncode != 0:
                raise RuntimeError(
                    f"claude CLI exited {result.returncode}: {result.stderr}"
                )
            # Parse JSON output
            output_json = json.loads(result.stdout)
            # Extract text and tokens from the JSON response
            # The claude CLI returns a structure like:
            # {"type": "result", "subtype": "success", "result": "...", "usage": {...}}
            text = None
            tokens = None
            if isinstance(output_json, dict):
                # Try new format first (--output-format json with --print)
                if "result" in output_json:
                    text = output_json.get("result")
                # Fallback to old message format if needed
                elif "content" in output_json:
                    content = output_json.get("content")
                    if isinstance(content, list) and content:
                        first = content[0]
                        if isinstance(first, dict):
                            text = first.get("text")
                # Extract token count if available
                usage_obj = output_json.get("usage", {})
                if isinstance(usage_obj, dict):
                    tokens = usage_obj.get("output_tokens")
            if not text:
                raise RuntimeError(
                    f"unable to extract text from claude CLI JSON response: {output_json}"
                )
            usage = {
                "tokens": tokens,
                "latency_ms": elapsed_ms,
            }
            return (text, usage)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"claude CLI timed out after 300s")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"claude CLI output is not valid JSON: {e}")
        except Exception as e:
            raise RuntimeError(f"claude CLI error: {e}")

    return runner


RUNNERS: Dict[str, ModelRunner] = {
    "mock": mock_runner,
}

# Register claude runners if claude CLI is available
_CLAUDE_AVAILABLE = bool(shutil.which("claude"))
if _CLAUDE_AVAILABLE:
    for alias in ("haiku", "sonnet", "opus"):
        try:
            # Pre-flight check: make sure the runner can be created
            # (we don't call it until later, but we want to register it)
            runner = _make_claude_runner(alias)
            RUNNERS[alias] = runner
        except Exception:
            # If something fails during registration, skip this model
            pass


def _fmt(value, suffix: str = "") -> str:
    return "-" if value is None else f"{value:g}{suffix}"


def print_table(model_name: str, results: List[dict], accuracy: float, stream=None) -> None:
    stream = stream or sys.stdout
    cost = summarize_cost(results)
    show_cost = cost["has_cost"]
    print(f"\nBenchmark results -- runner: {model_name}", file=stream)
    print("-" * 72, file=stream)
    if show_cost:
        print(f"{'id':<6}{'category':<28}{'result':<8}{'tokens':<10}{'latency_ms':<12}", file=stream)
        for r in results:
            mark = "PASS" if r["correct"] else "FAIL"
            print(
                f"{r['id']:<6}{r['category']:<28}{mark:<8}"
                f"{_fmt(r.get('tokens')):<10}{_fmt(r.get('latency_ms')):<12}",
                file=stream,
            )
    else:
        print(f"{'id':<6}{'category':<28}{'result':<8}", file=stream)
        for r in results:
            mark = "PASS" if r["correct"] else "FAIL"
            print(f"{r['id']:<6}{r['category']:<28}{mark:<8}", file=stream)
    print("-" * 72, file=stream)
    n = len(results)
    n_correct = sum(1 for r in results if r["correct"])
    print(f"Accuracy: {n_correct}/{n} = {accuracy:.1%}", file=stream)
    if show_cost:
        # Cost axis printed right under accuracy so the two are read together.
        print(
            f"Cost:     total_tokens={_fmt(cost['total_tokens'])} "
            f"avg_tokens/task={_fmt(round(cost['avg_tokens'], 1) if cost['avg_tokens'] is not None else None)} "
            f"total_latency_ms={_fmt(cost['total_latency_ms'])} "
            f"avg_latency_ms/task={_fmt(round(cost['avg_latency_ms'], 1) if cost['avg_latency_ms'] is not None else None)}",
            file=stream,
        )


def print_comparison(summaries: List[dict], stream=None) -> None:
    """Print accuracy AND cost side by side across models.

    Each element of `summaries` is a build_summary() row. This is the table that
    lets "equal accuracy at a fraction of the cost" be shown rather than
    asserted: models line up in rows, accuracy and token/latency cost in columns.
    """
    stream = stream or sys.stdout
    print("\nModel comparison -- accuracy vs cost", file=stream)
    print("-" * 72, file=stream)
    print(
        f"{'model':<12}{'accuracy':<12}{'avg_tokens':<12}{'total_tokens':<14}{'avg_latency_ms':<16}",
        file=stream,
    )
    for s in summaries:
        acc = f"{s['n_correct']}/{s['n_tasks']}={s['accuracy']:.0%}"
        avg_tok = _fmt(round(s["avg_tokens"], 1) if s.get("avg_tokens") is not None else None)
        tot_tok = _fmt(s.get("total_tokens"))
        avg_lat = _fmt(round(s["avg_latency_ms"], 1) if s.get("avg_latency_ms") is not None else None)
        print(f"{s['model']:<12}{acc:<12}{avg_tok:<12}{tot_tok:<14}{avg_lat:<16}", file=stream)
    print("-" * 72, file=stream)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    available_runners = sorted(RUNNERS.keys())
    default_runner = "mock"
    parser.add_argument(
        "--runner",
        default=default_runner,
        choices=available_runners,
        help=f"Which registered model runner to score (default: {default_runner}, offline/zero-cost). "
             f"Available: {', '.join(available_runners)}",
    )
    parser.add_argument("--tasks", default=None, help="Override path to tasks.jsonl")
    parser.add_argument("--ground-truth", default=None, help="Override path to ground_truth.jsonl")
    args = parser.parse_args(argv)

    tasks = load_tasks(args.tasks)
    ground_truth = load_ground_truth(args.ground_truth)
    runner = RUNNERS[args.runner]

    results, accuracy = run_bench(tasks, ground_truth, runner)
    print_table(args.runner, results, accuracy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
