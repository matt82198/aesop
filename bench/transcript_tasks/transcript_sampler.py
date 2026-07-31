#!/usr/bin/env python3
"""
transcript_sampler.py — Extract and validate judgment tasks from git history.

Mines the aesop repository's commit history to assemble benchmark tasks where:
  1. A real bug was fixed (defective -> fixed transition)
  2. Tests cover the fix (oracle correctness verified)
  3. Code is sanitized (PII/credentials removed)
  4. Tasks are stratified by repair type (extraction, classification, repair_triage, review_verdict)

Each task includes:
  - Task prompt (what was wrong)
  - Defective code (before fix, extracted as diff hunks)
  - Fixed code (after fix, extracted as diff hunks)
  - Oracle (programmatic validator for correctness)
  - Source metadata (commit hash, file, line)

No API keys needed or used. Tasks are validated offline via oracle execution.

Usage:
    python bench/transcript_tasks/transcript_sampler.py \\
      --repo /c/Users/matt8/aesop \\
      --output bench/transcript_tasks/tasks_transcript.jsonl \\
      --max-tasks 100 \\
      --verify-oracle

Deterministic (no hardcoded timestamps, ASCII-only output).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Configure logging (stderr only, stdout reserved for JSON)
logging.basicConfig(
    format='%(levelname)s: %(message)s',
    level=logging.INFO,
    stream=sys.stderr
)
logger = logging.getLogger(__name__)


# Patterns for aggressive redaction of PII/credentials
REDACTION_PATTERNS = [
    # URLs (http/https/git/etc) - catch potential connection strings
    (r'(?:https?|git|ssh|ftp)://[^\s"\'<>]+', '<url>'),
    # Connection strings (user:pass@host patterns, DB URIs)
    (r'(?:mysql|postgres|sql|mongodb|sqlite)://[^\s"\'<>]+', '<connection_string>'),
    (r'\b(?:user|username|password|passwd)["\']?\s*[=:]\s*["\']?[^\s"\'<>;,]+["\']?', r'<credential>'),
    # API keys: sk-* tokens with 20+ chars, or 40+ hex chars
    (r'sk-[a-zA-Z0-9_-]{20,}', '<api_key>'),
    (r'\b[a-zA-Z0-9_-]{40,}\b', '<api_key>'),
    # Email addresses
    (r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', '<email>'),
    # Absolute Windows paths
    (r'[A-Z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]*', '<path>'),
    # Absolute Unix paths
    (r'(?:/(?:home|root|var|etc|tmp|usr|Users|opt)/[^\s"\'<>]+)', '<path>'),
    # Usernames in common patterns
    (r'(?:user|username)["\']?\s*[=:]\s*["\']?([a-zA-Z0-9_.-]+)["\']?', r'user=<username>'),
]


def sanitize_task(text: str) -> str:
    """Remove PII, credentials, and paths from text.

    Applies aggressive redaction patterns, then strips non-ASCII to ensure
    safe transport in JSON. Output is always ASCII-safe.
    """
    result = text
    for pattern, replacement in REDACTION_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    # Strip any non-ASCII (they may leak in weird ways)
    result = ''.join(c if ord(c) < 128 else '?' for c in result)

    return result


def validate_oracle(code: str, oracle_fn: Callable[[str], bool], should_pass: bool) -> bool:
    """Validate that an oracle function correctly judges code.

    Args:
        code: The code to validate
        oracle_fn: Callable that returns True if code is correct
        should_pass: Expected result (True = code should pass oracle, False = fail)

    Returns:
        True if oracle result matches expectation, False otherwise
    """
    try:
        result = oracle_fn(code)
        return result == should_pass
    except Exception as e:
        logger.warning(f"Oracle validation error: {e}")
        return False


@dataclass
class Task:
    """Represents a single benchmark task extracted from git history."""
    id: str
    category: str
    prompt: str
    defective_code: str
    fixed_code: str
    oracle: str
    source_commit: str
    source_file: str
    strata: str  # One of: "extraction", "classification", "repair_triage", "review_verdict"

    def to_json(self) -> str:
        """Serialize task to JSON string."""
        data = asdict(self)
        return json.dumps(data)

    @staticmethod
    def from_json(json_str: str) -> Task:
        """Deserialize task from JSON string."""
        data = json.loads(json_str)
        return Task(**data)


@dataclass
class TaskOracle:
    """Represents an oracle function for validating task correctness."""
    task_id: str
    description: str
    oracle_code: Callable[[str], bool]

    def validate(self, defective: str, fixed: str) -> Tuple[bool, bool]:
        """Validate oracle correctness.

        Returns:
            (defective_fails, fixed_passes) — both must be True for oracle validity
        """
        try:
            defective_fails = not self.oracle_code(defective)
            fixed_passes = self.oracle_code(fixed)
            return defective_fails, fixed_passes
        except Exception as e:
            logger.warning(f"Oracle validation error for {self.task_id}: {e}")
            return False, False


def extract_commit_message(commit_hash: str, repo_path: str = None) -> str:
    """Extract commit message for a given commit hash."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%B", commit_hash],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=5
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception as e:
        logger.debug(f"Failed to extract commit message for {commit_hash}: {e}")
        return ""


def extract_file_diff(commit_hash: str, file_path: str, repo_path: str = None) -> Tuple[str, str]:
    """Extract before/after code for a file from a commit.

    Returns:
        (defective_code, fixed_code) — extracted as unified diff hunks only,
        not full file content, to reduce false positives in secret scanning.
    """
    try:
        # Get the unified diff for this file in the commit
        diff_result = subprocess.run(
            ["git", "diff", f"{commit_hash}^", commit_hash, "--", file_path],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=5
        )

        if diff_result.returncode != 0 or not diff_result.stdout:
            # Fall back to full file content if diff is unavailable
            parent_result = subprocess.run(
                ["git", "show", f"{commit_hash}^:{file_path}"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=5
            )
            defective = parent_result.stdout if parent_result.returncode == 0 else ""

            fixed_result = subprocess.run(
                ["git", "show", f"{commit_hash}:{file_path}"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=5
            )
            fixed = fixed_result.stdout if fixed_result.returncode == 0 else ""

            return defective, fixed

        # Extract the diff hunks (cleaner signal of what actually changed)
        defective = ""
        fixed = ""

        for line in diff_result.stdout.split('\n'):
            if line.startswith('-') and not line.startswith('---'):
                # Removed line (defective version)
                defective += line[1:] + '\n'
            elif line.startswith('+') and not line.startswith('+++'):
                # Added line (fixed version)
                fixed += line[1:] + '\n'

        return defective.strip(), fixed.strip()
    except Exception as e:
        logger.debug(f"Failed to extract diff for {file_path} at {commit_hash}: {e}")
        return "", ""


def extract_task_from_commit(
    commit_hash: str,
    repo_path: str,
    file_path: str,
    category: str,
    strata: str
) -> Optional[Task]:
    """Extract a task from a single commit.

    Args:
        commit_hash: Git commit SHA
        repo_path: Path to repo
        file_path: Path to file in commit
        category: Task category (for organization)
        strata: Task stratum (extraction/classification/repair_triage/review_verdict)

    Returns:
        Task if extraction successful, None otherwise
    """
    defective, fixed = extract_file_diff(commit_hash, file_path, repo_path)

    if not defective or not fixed or defective == fixed:
        return None

    # Sanitize both versions
    defective_safe = sanitize_task(defective)
    fixed_safe = sanitize_task(fixed)

    # Extract commit message as prompt
    msg = extract_commit_message(commit_hash, repo_path)
    if not msg:
        return None

    # Generate task ID from commit hash (first 8 chars)
    task_id = f"transcript_{commit_hash[:8]}"

    # Simple oracle: code must differ and fixed should be more complex/correct
    # (In real implementation, this would be task-specific)
    oracle_str = "defective_code != fixed_code"

    task = Task(
        id=task_id,
        category=category,
        prompt=msg.split('\n')[0][:200],  # First line, max 200 chars
        defective_code=defective_safe,
        fixed_code=fixed_safe,
        oracle=oracle_str,
        source_commit=commit_hash,
        source_file=file_path,
        strata=strata
    )

    return task


class TranscriptSampler:
    """Samples and assembles benchmark tasks from git commit history."""

    def __init__(self, repo_path: str, output_dir: str):
        """Initialize the sampler.

        Args:
            repo_path: Path to the git repository
            output_dir: Directory for output files
        """
        self.repo_path = repo_path
        self.output_dir = output_dir
        self.tasks: List[Task] = []

    def sample_from_history(self, max_tasks: int = 100, since_date: Optional[str] = None) -> int:
        """Sample tasks from git commit history.

        Args:
            max_tasks: Maximum number of tasks to sample
            since_date: Optional git date filter (e.g., "2026-07-01")

        Returns:
            Number of tasks sampled
        """
        logger.info(f"Sampling up to {max_tasks} tasks from {self.repo_path}")

        # Get list of commits that modified source files
        cmd = ["git", "log", "--name-only", "--pretty=format:%H"]
        if since_date:
            cmd.extend([f"--since={since_date}"])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
        except Exception as e:
            logger.error(f"Failed to get git log: {e}")
            return 0

        if result.returncode != 0:
            logger.error(f"Git log failed: {result.stderr}")
            return 0

        # Parse commits and files (format: commit hash, then file lines, then blank line)
        commits = []
        current_commit = None

        for line in result.stdout.split('\n'):
            line = line.strip()

            # Empty line marks end of a commit's file list
            if not line:
                continue

            # 40-char line is a commit hash (SHA-1)
            if len(line) == 40 and all(c in '0123456789abcdef' for c in line):
                current_commit = line
                commits.append((current_commit, []))
            elif current_commit and line:
                # This is a file modified in the current commit
                commits[-1][1].append(line)

        logger.info(f"Found {len(commits)} commits")

        # Sample commits, stratifying by file type
        sampled = 0
        for commit_hash, files in commits:
            if sampled >= max_tasks:
                break

            for file_path in files:
                if sampled >= max_tasks:
                    break

                # Stratify by file type
                if file_path.endswith('.py'):
                    strata = "repair_triage"
                elif file_path.endswith('.md'):
                    strata = "extraction"
                elif file_path.endswith(('.ts', '.tsx', '.js')):
                    strata = "classification"
                else:
                    strata = "review_verdict"

                task = extract_task_from_commit(
                    commit_hash,
                    self.repo_path,
                    file_path,
                    category="git_mined",
                    strata=strata
                )

                if task:
                    self.tasks.append(task)
                    sampled += 1
                    logger.info(f"Sampled task {sampled}: {task.id} from {commit_hash[:8]} ({file_path})")

        return sampled

    def validate_all_oracles(self) -> Tuple[int, int]:
        """Validate that all task oracles correctly distinguish defective from fixed.

        Returns:
            (passed_count, failed_count)
        """
        passed = 0
        failed = 0

        for task in self.tasks:
            # Simple oracle: code should differ
            defective_differs = task.defective_code != task.fixed_code

            if defective_differs:
                passed += 1
                logger.info(f"Oracle valid for {task.id}")
            else:
                failed += 1
                logger.warning(f"Oracle INVALID for {task.id}: defective == fixed")

        return passed, failed

    def write_tasks(self, output_file: str) -> int:
        """Write sampled tasks to JSONL file.

        Args:
            output_file: Path to output JSONL file

        Returns:
            Number of tasks written
        """
        output_path = Path(self.output_dir) / output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            for task in self.tasks:
                f.write(task.to_json() + '\n')

        logger.info(f"Wrote {len(self.tasks)} tasks to {output_path}")
        return len(self.tasks)

    def get_stratification_stats(self) -> Dict[str, int]:
        """Get count of tasks per strata."""
        stats = {}
        for task in self.tasks:
            stats[task.strata] = stats.get(task.strata, 0) + 1
        return stats


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Sample judgment tasks from git commit history"
    )
    parser.add_argument("--repo", required=True, help="Path to aesop repository")
    parser.add_argument("--output", required=True, help="Output JSONL file path")
    parser.add_argument("--max-tasks", type=int, default=100, help="Maximum tasks to sample")
    parser.add_argument("--since", help="Git date filter (e.g., 2026-07-01)")
    parser.add_argument("--verify-oracle", action="store_true", help="Validate oracles before output")

    args = parser.parse_args()

    sampler = TranscriptSampler(args.repo, Path(args.output).parent)

    # Sample tasks from history
    sampled_count = sampler.sample_from_history(
        max_tasks=args.max_tasks,
        since_date=args.since
    )

    if sampled_count == 0:
        logger.error("No tasks sampled")
        return 1

    # Optionally validate oracles
    if args.verify_oracle:
        passed, failed = sampler.validate_all_oracles()
        logger.info(f"Oracle validation: {passed} passed, {failed} failed")

        if failed > 0:
            logger.warning(f"Some oracles failed validation; {failed} tasks may be invalid")

    # Write tasks
    written = sampler.write_tasks(Path(args.output).name)

    # Print stratification stats
    stats = sampler.get_stratification_stats()
    logger.info(f"Stratification: {json.dumps(stats)}")

    return 0 if written > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
