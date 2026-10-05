#!/usr/bin/env python3
"""
Live test-suite-count library: derives Node/Shell/Python suite counts from git ls-files.
INDEX: Live suite-count library (no stored artifact): derives Node/Shell/Python test-suite counts straight from `git ls-files` on every call; stdlib-only, deterministic, ASCII-safe; counts never derive to zero (fail-closed). `--json` (default, read-only) prints the live counts; there is no `--check`/`--regenerate` mode and nothing is written anywhere, because as of structural fix #830 there is no committed tests/SUITE-COUNTS.json left to drift or regenerate -- counts are a pure function of the tree, computed fresh on every call, so nothing can go stale. Counts are derived per UNIQUE path, not per `git ls-files` line: an unmerged path is listed once per index stage (1=base/2=ours/3=theirs), and one file can match two shell globs at once, so a naive line-count over-counts (bit PRs #710/#711 under the old tests/CLAUDE.md-line gate); `list_git_files()` collects into a Python set to fix both. A merge in progress (MERGE_HEAD set) is a loud, non-fatal stderr `[WARN]` rather than a refusal, since conflict resolution is exactly when counts legitimately move. Consumed by `tools/verify_test_suite_count.py` (CI-shard-coverage gate) and `tests/test_list_test_suites.py` / `tests/test_tests_claudemd_drift.py` as the live ground truth.

Why there is no artifact any more (PR #830, "guard: compute suite counts live"):
tests/SUITE-COUNTS.json was a committed snapshot of this module's own output, rewritten by
`--regenerate` and compared against by `--check`. Two clean merges (PR #828 postmortem)
drifted it anyway: each side's own branch had the file correct in isolation, but the UNION
of two merges changed the live count without either side's CI ever re-running --check against
the merged tree, so main went red both times over a file that carried no information a human
put there -- it was always a pure function of `git ls-files`. Nothing else in the repo ever
read the committed VALUE (only the generator/gate/registry triangle that produced and checked
it did), so the fix is to stop storing it: callers that need the count call get_actual_counts()
or run `--json` and get the live number directly, and there is nothing left to drift.

Usage:
    python tools/gen_suite_counts.py [--json] [--repo ROOT]

Exit codes:
    0  counts printed successfully
    2  cannot evaluate (target not a git repo, git failure, vacuous zero)
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict


# Force UTF-8 output on all platforms (especially Windows where stdout defaults to cp1252)
if sys.stdout.encoding and 'utf' not in sys.stdout.encoding.lower():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, TypeError):
        pass
if sys.stderr.encoding and 'utf' not in sys.stderr.encoding.lower():
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, TypeError):
        pass


LABELS = ("Node", "Shell", "Python")


class StructureError(Exception):
    """Evaluation or structural problem, carrying the process exit code."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def ensure_git_repo(repo_root: Path) -> None:
    """Fail closed unless repo_root is inside a git work tree.

    Raises:
        StructureError: code 2 when git is unavailable or repo_root is not a repo.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError) as exc:
        raise StructureError(
            f"[ERROR] Cannot derive test suite counts: {repo_root} is not a git "
            f"repository (or git is unavailable): {type(exc).__name__}",
            2,
        )

    if result.stdout.strip() != "true":
        raise StructureError(
            f"[ERROR] Cannot derive test suite counts: {repo_root} is not a git "
            "repository work tree",
            2,
        )


def merge_in_progress(repo_root: Path) -> bool:
    """True when repo_root has an in-progress merge (MERGE_HEAD resolvable).

    Advisory only: a merge is the state in which the index carries multiple stages
    per conflicted path, so it is the state that used to inflate the counts. It is
    NOT a refusal condition -- see warn_if_merge_in_progress().
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # Never let an advisory probe break the gate; ensure_git_repo() already
        # fails closed on a genuinely unusable git.
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def warn_if_merge_in_progress(repo_root: Path) -> None:
    """Emit a loud, non-fatal warning when counts are derived mid-merge.

    Deliberately a WARNING and not a refusal: counts are computed live on every
    call, so there is no stale artifact to protect and no reason to refuse. What
    must never happen is a SILENT wrong number: list_git_files() makes the
    derivation correct (one count per unique path) and this makes the
    half-resolved tree visible to the operator.
    """
    if not merge_in_progress(repo_root):
        return
    print(
        f"[WARN] A merge is in progress in {repo_root} (MERGE_HEAD is set). Suite "
        "counts are derived per UNIQUE path, so unmerged index stages are not "
        "double counted -- but the tree is only half resolved, so files the merge "
        "has yet to add or delete are not reflected yet. Re-run this after "
        "the merge concludes for a fully-resolved count.",
        file=sys.stderr,
    )


def list_git_files(repo_root: Path, *patterns: str) -> set:
    """Return the SET of tracked paths matching any pattern, inside repo_root.

    Deduplication is load-bearing, not cosmetic, for two reasons:

    1. `git ls-files <pattern>` lists an UNMERGED path once per index stage
       (1=base, 2=ours, 3=theirs). During an in-progress merge a single conflicted
       `tests/test_*.py` was counted two or three times, which is how a mid-merge
       read once inflated a now-removed generated count.
    2. The shell family is derived from three globs, and one path can match two of
       them (`tests/test_x.test.sh` matches `tests/*.test.sh` AND `tests/test_*.sh`),
       which double counted it across patterns.

    Deduplicating in Python rather than relying on `git ls-files --deduplicate`
    (git >= 2.31) keeps this correct on every git the project might be built with,
    and is the only form that also fixes the cross-pattern case above.

    Omits untracked files; uses git to ensure we count only tracked files. The
    `cwd` is threaded explicitly so `--repo` actually selects the tree being
    graded instead of silently grading the process CWD.

    Raises:
        StructureError: code 2 if git cannot be run for a pattern (a swallowed
            failure here reads as a count of zero, i.e. fake-green).
    """
    paths = set()
    for pattern in patterns:
        try:
            result = subprocess.run(
                ["git", "ls-files", pattern],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError, OSError) as exc:
            raise StructureError(
                f"[ERROR] Cannot derive test suite counts: 'git ls-files {pattern}' "
                f"failed in {repo_root}: {type(exc).__name__}",
                2,
            )
        # splitlines(), not strip().split("\n"): a path is only ever mangled by
        # stripping, and git quotes any path that could contain a newline.
        for line in result.stdout.splitlines():
            if line:
                paths.add(line)
    return paths


def count_git_files(repo_root: Path, *patterns: str) -> int:
    """Count UNIQUE tracked files matching patterns using git ls-files."""
    return len(list_git_files(repo_root, *patterns))


def get_actual_counts(repo_root: Path) -> Dict[str, int]:
    """Get live test suite counts from the tree at repo_root.

    Returns: {"Node": int, "Shell": int, "Python": int}
    """
    ensure_git_repo(repo_root)
    warn_if_merge_in_progress(repo_root)

    node_count = count_git_files(repo_root, "tests/*.test.mjs")
    shell_count = count_git_files(
        repo_root, "tests/*.test.sh", "tests/test_*.sh", "tests/test-*.sh"
    )
    python_count = count_git_files(repo_root, "tests/test_*.py")

    return {
        "Node": node_count,
        "Shell": shell_count,
        "Python": python_count,
    }


def assert_no_vacuous_zero(counts: Dict[str, int], repo_root: Path) -> None:
    """Fail-closed when any suite family derives to zero.

    Raises:
        StructureError: code 2 when a family has zero files (broken derivation).
    """
    for label, count in counts.items():
        if count == 0:
            raise StructureError(
                f"[ERROR] Cannot evaluate: git ls-files found ZERO {label} test files "
                f"in {repo_root}. An entire suite family collapsing to zero is "
                "indistinguishable from a broken derivation (wrong --repo, bad checkout, "
                "git failure), so this fails closed.",
                2,
            )


def format_json(counts: Dict[str, int]) -> str:
    """Format counts as JSON with consistent ordering and formatting."""
    ordered = {label: counts[label] for label in LABELS}
    return json.dumps(ordered, indent=2, sort_keys=False)


def json_mode(repo_root: Path) -> int:
    """Print live counts as JSON (read-only, no file I/O anywhere).

    Returns:
        0 on success, 2 if cannot evaluate.
    """
    try:
        actual = get_actual_counts(repo_root)
        assert_no_vacuous_zero(actual, repo_root)
    except StructureError as e:
        print(str(e), file=sys.stderr)
        return e.code

    print(format_json(actual))
    return 0


def main():
    """Main entry point. Always read-only: there is nothing left to write."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output live counts as JSON to stdout (default; read-only, no file I/O)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root (default: current directory)",
    )

    args = parser.parse_args()

    repo_root = (args.repo or Path.cwd()).resolve()

    if not repo_root.is_dir():
        print(f"[ERROR] repo root {repo_root} is not a directory", file=sys.stderr)
        return 2

    return json_mode(repo_root)


if __name__ == "__main__":
    sys.exit(main())
