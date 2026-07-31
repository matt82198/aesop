#!/usr/bin/env python3
"""Pre-merge test-suite-count drift gate.

verify_test_suite_count.py --check only compares tests/CLAUDE.md's documented
counts against the CURRENT WORKING TREE. That is a self-consistency check --
it always passes on a freshly-authored branch, because the branch's own docs
were written to match the branch's own files. It says nothing about whether
those counts will still be true once the branch lands on top of origin/main.

The gap it misses: a sibling PR merges to origin/main and bumps the true test
count (e.g. adds tests/test_new_thing.py) AFTER this branch's base was cut.
This branch never touched tests/ or tests/CLAUDE.md, so its own --check still
passes locally and at push time. But GitHub Actions' pull_request trigger
tests the MERGE ref (this branch merged into current main), so the actual
file count CI sees includes the sibling's new files while the documented
count in tests/CLAUDE.md (unchanged on this branch) still reflects the old
base. --check then fails in CI -- the classic "green branch, red PR" race.

This gate predicts that failure BEFORE merge, without needing a live GitHub
merge ref: it compares documented counts on HEAD against a PREDICTED
post-merge actual count, computed as:

    predicted = actual(base_ref)  +  [actual(HEAD) - actual(merge_base)]

i.e. "whatever base_ref (origin/main) has right now, plus whatever net
suites this branch itself is adding/removing relative to where it branched
from". If HEAD is up to date with base_ref, merge_base == base_ref and this
collapses to the same check verify_test_suite_count.py already performs
locally -- so this gate is a strict superset, not a replacement.

Usage:
    python tools/verify_premerge_count_drift.py --check [--repo ROOT]
                                                  [--base REF] [--head REF]
                                                  [--fetch] [--json]

Modes: --check is the only mode (default if omitted).
Exit codes:
    0 = clean (no predicted drift)
    1 = drift predicted (would fail verify_test_suite_count.py --check once merged)
    2 = could not evaluate (git error, missing ref, missing tests/CLAUDE.md, etc.)

Never wired into a hook or CI workflow by this change -- build/test only.
"""

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Same glob families verify_test_suite_count.py uses, so the two tools count
# identically. Duplicated (not imported) so this gate has no import-time
# dependency on tests/CLAUDE.md's sibling tool -- it must still be able to
# run and report COULD-NOT-EVALUATE even if that tool is broken or missing.
NODE_PATTERNS = ["tests/*.test.mjs"]
SHELL_PATTERNS = ["tests/*.test.sh", "tests/test_*.sh", "tests/test-*.sh"]
PYTHON_PATTERNS = ["tests/test_*.py"]

CATEGORY_PATTERNS = {
    "Node": NODE_PATTERNS,
    "Shell": SHELL_PATTERNS,
    "Python": PYTHON_PATTERNS,
}

COUNT_LINE_RE = {
    "Node": r"\*\*Node \((\d+) suites?\)\*\*:",
    "Shell": r"\*\*Shell \((\d+) suites?\)\*\*:",
    "Python": r"\*\*Python \((\d+) suites?\)\*\*:",
}


class GitError(Exception):
    """Raised when a git operation needed for evaluation fails."""


def _run_git(repo_root: Path, *args: str, timeout: int = 20) -> str:
    """Run a git command in repo_root; return stdout. Raises GitError on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        raise GitError(f"git {' '.join(args)} failed to run: {e}") from e
    if result.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} exited {result.returncode}: {result.stderr.strip()[:200]}"
        )
    return result.stdout


def resolve_ref(repo_root: Path, ref: str) -> str:
    """Resolve ref to a commit SHA. Raises GitError if ref does not exist."""
    out = _run_git(repo_root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    sha = out.strip()
    if not sha:
        raise GitError(f"ref {ref!r} did not resolve to a commit")
    return sha


def merge_base(repo_root: Path, ref_a: str, ref_b: str) -> str:
    out = _run_git(repo_root, "merge-base", ref_a, ref_b)
    sha = out.strip()
    if not sha:
        raise GitError(f"no merge-base between {ref_a!r} and {ref_b!r}")
    return sha


def list_tree_files(repo_root: Path, ref: str, pathspec: str = "tests") -> List[str]:
    """List all files under pathspec as they exist at ref (no working-tree dependency)."""
    out = _run_git(repo_root, "ls-tree", "-r", "--name-only", ref, "--", pathspec)
    return [line.strip() for line in out.splitlines() if line.strip()]


def count_at_ref(repo_root: Path, ref: str) -> Dict[str, int]:
    """Compute (Node, Shell, Python) actual suite counts as they exist at ref."""
    files = list_tree_files(repo_root, ref)
    counts = {}
    for category, patterns in CATEGORY_PATTERNS.items():
        n = 0
        for pattern in patterns:
            n += sum(1 for f in files if fnmatch.fnmatch(f, pattern))
        counts[category] = n
    return counts


def documented_at_ref(repo_root: Path, ref: str, claudemd_rel: str = "tests/CLAUDE.md") -> Dict[str, int]:
    """Read documented counts from tests/CLAUDE.md as it exists at ref."""
    import re

    try:
        content = _run_git(repo_root, "show", f"{ref}:{claudemd_rel}")
    except GitError as e:
        raise GitError(f"could not read {claudemd_rel} at {ref}: {e}") from e

    documented = {}
    missing = []
    for category, pattern in COUNT_LINE_RE.items():
        m = re.search(pattern, content)
        if not m:
            missing.append(category)
            continue
        documented[category] = int(m.group(1))

    if missing:
        raise GitError(
            f"could not find documented count line(s) for {', '.join(missing)} "
            f"in {claudemd_rel} at {ref}"
        )
    return documented


def maybe_fetch(repo_root: Path, base_ref: str) -> None:
    """Best-effort `git fetch` so a local origin/main isn't stale. Raises GitError on failure.

    Only called when --fetch is explicitly passed -- this gate never touches
    the network unless the caller opts in.
    """
    if "/" not in base_ref:
        return
    remote, _, branch = base_ref.partition("/")
    _run_git(repo_root, "fetch", remote, branch, timeout=60)


def check(repo_root: Path, base_ref: str, head_ref: str, do_fetch: bool) -> Tuple[int, dict]:
    """Run the pre-merge drift check.

    Returns (exit_code, report_dict). exit_code: 0 clean / 1 drift / 2 error.
    """
    report: dict = {
        "base_ref": base_ref,
        "head_ref": head_ref,
        "findings": [],
    }

    try:
        if do_fetch:
            maybe_fetch(repo_root, base_ref)

        base_sha = resolve_ref(repo_root, base_ref)
        head_sha = resolve_ref(repo_root, head_ref)
        base_point = merge_base(repo_root, head_sha, base_sha)

        actual_base = count_at_ref(repo_root, base_sha)
        actual_head = count_at_ref(repo_root, head_sha)
        actual_merge_base = count_at_ref(repo_root, base_point)
        documented_head = documented_at_ref(repo_root, head_sha)
    except GitError as e:
        report["error"] = str(e)
        return 2, report

    report["base_sha"] = base_sha
    report["head_sha"] = head_sha
    report["merge_base_sha"] = base_point
    report["predicted"] = {}
    report["documented"] = documented_head

    findings = []
    for category in CATEGORY_PATTERNS:
        own_delta = actual_head[category] - actual_merge_base[category]
        predicted = actual_base[category] + own_delta
        report["predicted"][category] = predicted
        if predicted != documented_head[category]:
            findings.append(
                {
                    "category": category,
                    "documented": documented_head[category],
                    "predicted_after_merge": predicted,
                    "detail": (
                        f"{category}: tests/CLAUDE.md documents {documented_head[category]}, "
                        f"but merging onto current {base_ref} would make the true count "
                        f"{predicted} ({base_ref} already has {actual_base[category]}, "
                        f"this branch itself adds/removes {own_delta:+d})."
                    ),
                }
            )

    report["findings"] = findings
    return (1 if findings else 0), report


def format_report(report: dict) -> str:
    lines = []
    if "error" in report:
        lines.append(f"[ERROR] {report['error']}")
        return "\n".join(lines)

    if not report["findings"]:
        lines.append(
            f"[OK] No pre-merge drift predicted: merging {report['head_ref']} onto "
            f"{report['base_ref']} would keep tests/CLAUDE.md counts accurate."
        )
        return "\n".join(lines)

    lines.append(
        f"[DRIFT] {report['head_ref']} would fail verify_test_suite_count.py --check "
        f"once merged onto {report['base_ref']}:"
    )
    for f in report["findings"]:
        lines.append(f"  {f['detail']}")
    lines.append("")
    lines.append(
        "Rebase onto the latest base and run `python tools/verify_test_suite_count.py --fix` "
        "immediately before merging (or have the merge queue do it as a pre-merge step)."
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Predict post-merge drift (default mode; exit 1 if drift predicted)",
    )
    parser.add_argument(
        "--base",
        default="origin/main",
        help="Base ref this branch will merge onto (default: origin/main)",
    )
    parser.add_argument(
        "--head",
        default="HEAD",
        help="Branch ref to evaluate (default: HEAD)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root (default: current directory)",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="git fetch the base ref's remote branch before evaluating (network; opt-in)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report instead of text",
    )
    args = parser.parse_args()

    repo_root = (args.repo or Path.cwd()).resolve()
    if not (repo_root / ".git").exists() and not _is_inside_git_repo(repo_root):
        print(f"[ERROR] {repo_root} is not a git repository", file=sys.stderr)
        return 2

    exit_code, report = check(repo_root, args.base, args.head, args.fetch)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        out = format_report(report)
        if exit_code == 2:
            print(out, file=sys.stderr)
        else:
            print(out)

    return exit_code


def _is_inside_git_repo(repo_root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


if __name__ == "__main__":
    sys.exit(main())
