#!/usr/bin/env python3
"""Token-set extraction helper for frontier v5 tool-use mode.

Factors out the token parsing logic used by:
- tests/test_frontier_grader_audit.py (gate validation)
- bench/run_v2_parallel.py (tool schema generation and enum grading)
- bench/probe_refusals.py (refusal probing)
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


TOKEN_LINE = re.compile(
    r"(?:First line(?: of your response)?:\s*exactly\s+(.+)"
    r"|Answer with\s+(.+?)\s+on the first line)",
    re.IGNORECASE,
)
SCORE_FLAGS = re.IGNORECASE | re.DOTALL  # keep identical to score_response


def parse_token_set(prompt: str) -> Optional[List[str]]:
    """Extract the pinned closed token set from a prompt, or None.

    Looks for format instructions like:
    - "First line of your response: exactly A or B ..."
    - "Answer with A or B ... on the first line"

    Args:
        prompt: Task prompt text

    Returns:
        List of token strings if found and valid, None otherwise.
        Tokens are UPPERCASE_WITH_UNDERSCORES by authoring rule.
    """
    m = TOKEN_LINE.search(prompt)
    if not m:
        return None
    spec = (m.group(1) or m.group(2)).split("\n")[0].strip().rstrip(".")
    # Accept "A or B", "A, B, or C", "A or B or C", "A / B / C"
    parts = re.split(r"\s*,\s*|\s+or\s+|\s*/\s*", spec)
    parts = [re.sub(r"^or\s+", "", p.strip()) for p in parts]
    tokens = [p.strip().strip("`'\"") for p in parts if p.strip()]
    # Tokens are uppercase-with-underscores by authoring rule; drop empties/stragglers
    tokens = [t for t in tokens if re.fullmatch(r"[A-Z0-9_]+", t)]
    return tokens if len(tokens) >= 2 else None


def extract_correct_token(
    tokens: List[str],
    expected_regex: str,
) -> Optional[str]:
    """Identify the single correct token from a token set via ground-truth regex.

    Args:
        tokens: List of candidate tokens
        expected_regex: Ground-truth regex pattern

    Returns:
        The single token that matches the regex, or None if none/multiple match.
    """
    accepted = [t for t in tokens if re.search(expected_regex, t, SCORE_FLAGS)]
    return accepted[0] if len(accepted) == 1 else None


def remove_format_instruction(prompt: str) -> str:
    """Remove format instruction sentence from prompt, preserving task content.

    Removes these patterns (and ONLY these):
    - "First line of your response: exactly ..."
    - "First line: exactly ..."
    - "Answer with ... on the first line" (including trailing clauses like ", then explain")

    All variants found in ft01-ft130 are covered. The regex is conservative to
    avoid accidentally removing task content.

    Args:
        prompt: Original prompt with format instruction

    Returns:
        Prompt with instruction sentence removed, stripped of leading/trailing space
    """
    # Pattern matches all observed format instruction variants:
    # 1. "First line of your response: exactly ..." (and "First line: exactly ...")
    # 2. "Answer with ... on the first line ..." (handles tokens, "/" separators, trailing clauses)
    #    Stops at the first sentence-ending punctuation (period, newline, or "then")
    instruction_pattern = re.compile(
        r"(?:First line(?:\s+of\s+your\s+response)?:\s*exactly\s+.+?(?:\.|$))"
        r"|(?:Answer\s+with\s+.+?\s+on\s+the\s+first\s+line[^.]*(?:\.|$))",
        re.IGNORECASE | re.DOTALL,
    )
    transformed = instruction_pattern.sub("", prompt).strip()
    return transformed


def audit_tasks(
    tasks_path: str = "bench/tasks_frontier.jsonl",
    ground_truth_path: str = "bench/ground_truth_frontier.jsonl",
) -> Tuple[Dict[str, Tuple[List[str], str]], List[str]]:
    """Audit all frontier tasks for tool-mode eligibility.

    Args:
        tasks_path: Path to tasks JSONL file
        ground_truth_path: Path to ground truth JSONL file

    Returns:
        Tuple of:
        - Dict[task_id] = (token_set, correct_token) for tasks with closed sets
        - List of task_ids without closable token sets (fallback to regex mode)

    Example:
        >>> tool_tasks, regex_fallback = audit_tasks()
        >>> print(f"{len(tool_tasks)} tasks use tool mode")
        >>> print(f"{len(regex_fallback)} tasks use regex fallback")
        >>> for tid, (tokens, correct) in list(tool_tasks.items())[:1]:
        ...     print(f"{tid}: tokens={tokens}, correct={correct}")
    """
    # Load tasks and ground truth
    tasks = {}
    with open(tasks_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                tasks[obj["id"]] = obj

    gt = {}
    with open(ground_truth_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                gt[obj["id"]] = obj

    tool_mode = {}
    regex_fallback = []

    for task_id, task in tasks.items():
        # Try to parse token set from prompt
        token_set = parse_token_set(task["prompt"])
        if not token_set:
            regex_fallback.append(task_id)
            continue

        # Verify exactly one correct token via ground truth
        ground_truth_entry = gt.get(task_id)
        if not ground_truth_entry:
            regex_fallback.append(task_id)
            continue

        expected_regex = ground_truth_entry.get("expected_regex")
        if not expected_regex:
            regex_fallback.append(task_id)
            continue

        correct_token = extract_correct_token(token_set, expected_regex)
        if not correct_token:
            regex_fallback.append(task_id)
            continue

        # Task is eligible for tool mode
        tool_mode[task_id] = (token_set, correct_token)

    return tool_mode, regex_fallback


if __name__ == "__main__":
    # Quick audit report
    tool_tasks, regex_fallback = audit_tasks()
    print(f"Tool-mode eligible: {len(tool_tasks)} tasks")
    print(f"Regex-fallback: {len(regex_fallback)} tasks")
    print()
    print("Regex fallback tasks:")
    for tid in sorted(regex_fallback):
        print(f"  {tid}")
    print()
    print("First 3 tool-mode tasks:")
    for tid, (tokens, correct) in sorted(tool_tasks.items())[:3]:
        print(f"  {tid}: tokens={tokens}, correct={correct}")
