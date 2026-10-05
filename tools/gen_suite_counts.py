#!/usr/bin/env python3
"""
Generate tests/SUITE-COUNTS.json with actual test suite counts.
INDEX: Generated suite-count artifact builder; walks git ls-files for test suites, emits JSON to tests/SUITE-COUNTS.json between GENERATED-BY markers; modes `--check` (byte-compare, exit 1 + regenerate hint) / `--regenerate` / `--json`; counts never derive to zero (fail-closed); deterministic + ASCII-safe; stdlib-only. Counts are derived per UNIQUE path, not per `git ls-files` line: an unmerged path is listed once per index stage (1=base/2=ours/3=theirs), and one file can match two shell globs at once, so a naive line-count over-counts (bit PRs #710/#711 under the old tests/CLAUDE.md-line gate); `list_git_files()` collects into a Python set to fix both. A merge in progress (MERGE_HEAD set) is a loud, non-fatal stderr `[WARN]` rather than a refusal, since conflict resolution is exactly when the sweep needs `--regenerate`.

This tool is the SOLE SOURCE OF TRUTH for suite-count values used by verify_test_suite_count.py
and pre-push/CI gates. It GENERATES tests/SUITE-COUNTS.json deterministically from git ls-files,
never from hand-maintained state in tests/CLAUDE.md.

Modes:
  --check (default): READ-ONLY validation. Compare tests/SUITE-COUNTS.json against derived counts.
    Exit 0 if counts match, 1 if drift, 2 if file missing or cannot evaluate.
  --regenerate: Rewrite tests/SUITE-COUNTS.json to match actual files.
    Exit 0 on success, 1 on structural error, 2 on evaluation failure.
  --json: Output counts as JSON (read-only, no file I/O).

The generated JSON is between GENERATED-BY markers for idempotent regeneration.
Count derivation always uses --repo (default: CWD) as the git work tree.

Exit codes:
    0  counts match (--check) / regeneration succeeded / --json output
    1  drift, file missing, or usage conflict
    2  cannot evaluate (target not a git repo, git failure, vacuous zero)

Usage:
    python tools/gen_suite_counts.py --check [--repo ROOT]
    python tools/gen_suite_counts.py --regenerate [--dry-run] [--repo ROOT]
    python tools/gen_suite_counts.py --json [--repo ROOT]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, Tuple


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
GENERATED_JSON_PATH = "tests/SUITE-COUNTS.json"

TEMPLATE = """\
<!-- GENERATED-BY: tools/gen_suite_counts.py -->
{json_content}
<!-- END-GENERATED -->
"""


class StructureError(Exception):
    """Evaluation or structural problem, carrying the process exit code."""

    def __init__(self, message: str, code: int = 1):
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

    Deliberately a WARNING and not a refusal. Conflict resolution is exactly when
    suite counts drift and exactly when the sweep needs --regenerate, so refusing
    would break the workflow that needs this tool most. What must never happen is
    a SILENT wrong number: list_git_files() makes the derivation correct (one count
    per unique path) and this makes the half-resolved tree visible to the operator.
    """
    if not merge_in_progress(repo_root):
        return
    print(
        f"[WARN] A merge is in progress in {repo_root} (MERGE_HEAD is set). Suite "
        "counts are derived per UNIQUE path, so unmerged index stages are not "
        "double counted -- but the tree is only half resolved, so files the merge "
        "has yet to add or delete are not reflected yet. Re-run this gate after "
        "the merge concludes.",
        file=sys.stderr,
    )


def list_git_files(repo_root: Path, *patterns: str) -> set:
    """Return the SET of tracked paths matching any pattern, inside repo_root.

    Deduplication is load-bearing, not cosmetic, for two reasons:

    1. `git ls-files <pattern>` lists an UNMERGED path once per index stage
       (1=base, 2=ours, 3=theirs). During an in-progress merge a single conflicted
       `tests/test_*.py` was counted two or three times, which is how a mid-merge
       --regenerate wrote an inflated suite count into the generated artifact.
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
    """Get actual test suite counts from the tree at repo_root.

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


def read_generated_json(json_path: Path) -> Dict[str, int]:
    """Read and parse the generated JSON file.

    Raises:
        StructureError: code 1 or 2 if file is missing or malformed.
    """
    if not json_path.exists():
        raise StructureError(
            f"[ERROR] {json_path} not found. "
            "Run: python tools/gen_suite_counts.py --regenerate",
            1,
        )

    try:
        content = json_path.read_text(encoding="utf-8")
        # Extract JSON content between markers
        if "<!-- GENERATED-BY:" in content:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                content = content[start:end]
        data = json.loads(content)
        return data
    except (json.JSONDecodeError, ValueError) as e:
        raise StructureError(
            f"[ERROR] {json_path} is not valid JSON: {e}",
            2,
        )


def check_mode(json_path: Path, repo_root: Path) -> int:
    """READ-ONLY validation of suite counts. Never writes.

    Returns:
        0 if counts match, 1 if drift / missing file, 2 if cannot evaluate.
    """
    try:
        documented = read_generated_json(json_path)
        actual = get_actual_counts(repo_root)
        assert_no_vacuous_zero(actual, repo_root)
    except StructureError as e:
        print(str(e), file=sys.stderr)
        return e.code

    if documented == actual:
        print("[OK] Suite counts match")
        return 0

    print("[DRIFT] Suite count mismatch in tests/SUITE-COUNTS.json:")
    for label in LABELS:
        if documented.get(label) != actual.get(label):
            print(f"  {label}: file says {documented.get(label)}, actual is {actual.get(label)}")
    print("")
    print("Run: python tools/gen_suite_counts.py --regenerate")
    print("Then commit the updated tests/SUITE-COUNTS.json with your change.")
    return 1


def regenerate_mode(json_path: Path, repo_root: Path, dry_run: bool = False) -> int:
    """Rewrite tests/SUITE-COUNTS.json to match actual files.

    Returns:
        0 if successful (or dry_run), 1 on error, 2 if cannot evaluate.
    """
    try:
        actual = get_actual_counts(repo_root)
        assert_no_vacuous_zero(actual, repo_root)
    except StructureError as e:
        print(str(e), file=sys.stderr)
        return e.code

    json_content = format_json(actual)
    full_content = TEMPLATE.format(json_content=json_content)

    # Check if already up-to-date
    if json_path.exists():
        current_content = json_path.read_text(encoding="utf-8")
        if current_content == full_content:
            print("[OK] Counts already match, no changes needed")
            return 0

    if dry_run:
        print("[DRY-RUN] Would update tests/SUITE-COUNTS.json:")
        for label in LABELS:
            print(f"  {label}: {actual[label]} suites")
        print("")
        print("Run without --dry-run to apply changes.")
        return 0

    json_path.write_text(full_content, encoding="utf-8")

    print("[REGENERATED] Updated tests/SUITE-COUNTS.json:")
    for label in LABELS:
        print(f"  {label}: {actual[label]} suites")
    return 0


def json_mode(repo_root: Path) -> int:
    """Output counts as JSON (read-only).

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
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only: verify counts match; exit 1 on drift. Default mode.",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Rewrite tests/SUITE-COUNTS.json to match actual files",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output counts as JSON to stdout (read-only, no file I/O)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --regenerate: show what would change but don't write",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root (default: current directory)",
    )

    args = parser.parse_args()

    modes = sum([args.check, args.regenerate, args.json])
    if modes > 1:
        print(
            "[ERROR] --check, --regenerate, and --json are mutually exclusive",
            file=sys.stderr,
        )
        return 1

    # --dry-run implies --regenerate
    if args.dry_run and not args.regenerate:
        args.regenerate = True

    # Default to --check if no mode specified
    if modes == 0:
        args.check = True

    repo_root = (args.repo or Path.cwd()).resolve()

    if not repo_root.is_dir():
        print(f"[ERROR] repo root {repo_root} is not a directory", file=sys.stderr)
        return 2

    json_path = repo_root / GENERATED_JSON_PATH

    if args.json:
        return json_mode(repo_root)
    elif args.regenerate:
        return regenerate_mode(json_path, repo_root, dry_run=args.dry_run)
    else:  # --check
        return check_mode(json_path, repo_root)


if __name__ == "__main__":
    sys.exit(main())
