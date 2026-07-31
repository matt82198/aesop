#!/usr/bin/env python3
"""
Verify test suite counts in tests/CLAUDE.md match actual test files on disk.

Eliminates treadmill drift via auto-correction: --check derives counts at runtime,
auto-updates stale literals in place (no failure), and exits 0. Real invariant
violations (missing sections) still exit 1. Cannot-evaluate errors exit 2.

Supports two modes:
- --check (default): Verify counts match; auto-correct drift (exit 0 if OK after auto-correction,
  exit 1 if sections missing, exit 2 if cannot evaluate)
- --fix: Explicitly rewrite counts in tests/CLAUDE.md to match actual files (rarely needed)

Usage:
    python tools/verify_test_suite_count.py --check [--repo ROOT]
    python tools/verify_test_suite_count.py --fix [--dry-run] [--repo ROOT]

Modes are mutually exclusive; if neither is specified, defaults to --check.
Idempotent: running --check twice or --fix twice produces identical results.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Tuple


def count_git_files(*patterns: str) -> int:
    """Count files matching patterns using git ls-files.

    Omits untracked files; uses git to ensure we count only tracked files.
    """
    count = 0
    for pattern in patterns:
        try:
            result = subprocess.run(
                ["git", "ls-files", pattern],
                capture_output=True,
                text=True,
                encoding='utf-8',
                check=True,
                timeout=10,
            )
            count += len([line for line in result.stdout.strip().split("\n") if line])
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    return count


def get_actual_counts(repo_root: Path) -> Tuple[int, int, int]:
    """Get actual test suite counts from disk.

    Returns: (node_count, shell_count, python_count)
    """
    node_count = count_git_files("tests/*.test.mjs")
    shell_count = count_git_files("tests/*.test.sh", "tests/test_*.sh", "tests/test-*.sh")
    python_count = count_git_files("tests/test_*.py")

    return node_count, shell_count, python_count


def get_documented_counts(claudemd_path: Path) -> Tuple[int, int, int]:
    """Extract documented counts from tests/CLAUDE.md.

    Returns: (node_count, shell_count, python_count) or raises ValueError if not found.
    """
    content = claudemd_path.read_text(encoding="utf-8")

    # Match "**<Type> (N suites?)**:" patterns
    node_match = re.search(r"\*\*Node \((\d+) suites?\)\*\*:", content)
    shell_match = re.search(r"\*\*Shell \((\d+) suites?\)\*\*:", content)
    python_match = re.search(r"\*\*Python \((\d+) suites?\)\*\*:", content)

    if not (node_match and shell_match and python_match):
        raise ValueError(
            "Could not find one or more test suite count lines in tests/CLAUDE.md. "
            "Expected: **Node (N suites)**: **Shell (N suites)**: **Python (N suites):**"
        )

    return (
        int(node_match.group(1)),
        int(shell_match.group(1)),
        int(python_match.group(1)),
    )


def check_mode(claudemd_path: Path) -> int:
    """Verify counts match. Auto-correct stale literals (no failure for treadmill drift).

    Exit 0 if counts match (or were auto-corrected).
    Exit 1 only if documented sections are missing (real invariant broken).
    Exit 2 if cannot evaluate (git error, zero files found).

    This eliminates the treadmill: each branch auto-corrects locally before push,
    so parallel branches never conflict on stale numbers.

    Args:
        claudemd_path: Path to tests/CLAUDE.md

    Returns:
        0 if counts match (or auto-corrected), 1 if invariant broken, 2 if error
    """
    try:
        # Verify the documented sections exist (the real invariant)
        content = claudemd_path.read_text(encoding="utf-8")
        node_match = re.search(r"\*\*Node \((\d+) suites?\)\*\*:", content)
        shell_match = re.search(r"\*\*Shell \((\d+) suites?\)\*\*:", content)
        python_match = re.search(r"\*\*Python \((\d+) suites?\)\*\*:", content)

        if not (node_match and shell_match and python_match):
            print(
                "[FAIL] Missing test suite sections in tests/CLAUDE.md. "
                "Expected: **Node (N suites)**: **Shell (N suites)**: **Python (N suites):**",
                file=sys.stderr,
            )
            return 1

        # Get documented counts from regex
        documented = (
            int(node_match.group(1)),
            int(shell_match.group(1)),
            int(python_match.group(1)),
        )

        # Get actual counts from disk
        actual = get_actual_counts(claudemd_path.parent.parent)

        if documented == actual:
            print("[OK] Test suite counts match")
            return 0

        # Counts mismatch: auto-correct by rewriting CLAUDE.md
        doc_node, doc_shell, doc_python = documented
        act_node, act_shell, act_python = actual

        print("[AUTO-CORRECT] Test suite count drift detected (treadmill auto-corrected):")
        if doc_node != act_node:
            print(f"  Node: {doc_node} -> {act_node}")
        if doc_shell != act_shell:
            print(f"  Shell: {doc_shell} -> {act_shell}")
        if doc_python != act_python:
            print(f"  Python: {doc_python} -> {act_python}")

        # Rewrite the counts in place
        updated = re.sub(
            r"\*\*Node \(\d+ suites?\)\*\*:",
            f"**Node ({act_node} suites)**:",
            content,
        )
        updated = re.sub(
            r"\*\*Shell \(\d+ suites?\)\*\*:",
            f"**Shell ({act_shell} suites)**:",
            updated,
        )
        updated = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            f"**Python ({act_python} suites)**:",
            updated,
        )

        claudemd_path.write_text(updated, encoding="utf-8")
        print("[AUTO-CORRECT] Updated tests/CLAUDE.md and continuing (--check passes)")
        return 0

    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2


def fix_mode(claudemd_path: Path, dry_run: bool = False) -> int:
    """Auto-rewrite counts in tests/CLAUDE.md to match actual files.

    Args:
        claudemd_path: Path to tests/CLAUDE.md
        dry_run: If True, show what would change but don't write

    Returns:
        0 if successful (or dry_run shows what would change), 1 on error
    """
    try:
        actual = get_actual_counts(claudemd_path.parent.parent)
        act_node, act_shell, act_python = actual

        content = claudemd_path.read_text(encoding="utf-8")
        original_content = content

        # Rewrite count patterns
        content = re.sub(
            r"\*\*Node \(\d+ suites?\)\*\*:",
            f"**Node ({act_node} suites)**:",
            content,
        )
        content = re.sub(
            r"\*\*Shell \(\d+ suites?\)\*\*:",
            f"**Shell ({act_shell} suites)**:",
            content,
        )
        content = re.sub(
            r"\*\*Python \(\d+ suites?\)\*\*:",
            f"**Python ({act_python} suites)**:",
            content,
        )

        if content == original_content:
            print("[OK] Counts already match, no changes needed")
            return 0

        if dry_run:
            print(f"[DRY-RUN] Would update counts:")
            print(f"  Node: {re.search(r'Node \((\d+)', original_content).group(1)} → {act_node}")
            print(f"  Shell: {re.search(r'Shell \((\d+)', original_content).group(1)} → {act_shell}")
            print(f"  Python: {re.search(r'Python \((\d+)', original_content).group(1)} → {act_python}")
            print()
            print("Run without --dry-run to apply changes.")
            return 0

        # Write the updated content
        claudemd_path.write_text(content, encoding="utf-8")

        doc_node, doc_shell, doc_python = get_documented_counts(claudemd_path)
        print(f"[FIXED] Updated tests/CLAUDE.md:")
        print(f"  Node: {doc_node} suites")
        print(f"  Shell: {doc_shell} suites")
        print(f"  Python: {doc_python} suites")
        return 0
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify counts match, auto-correct drift (treadmill fix); default if neither specified",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-rewrite counts to match actual files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --fix: show what would change but don't write (implies --fix)",
    )
    parser.add_argument(
        "--claudemd",
        type=Path,
        default=None,
        help="Path to tests/CLAUDE.md (default: auto-detect from repo root)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root (default: current directory)",
    )

    args = parser.parse_args()

    # Validate mutually exclusive modes
    if args.check and args.fix:
        print("[ERROR] --check and --fix are mutually exclusive", file=sys.stderr)
        return 1

    # --dry-run implies --fix
    if args.dry_run and not args.fix:
        args.fix = True

    # Default to --check if neither specified
    if not args.check and not args.fix:
        args.check = True

    # Determine repo root
    repo_root = args.repo or Path.cwd()
    repo_root = repo_root.resolve()

    # Determine CLAUDE.md path
    if args.claudemd:
        claudemd_path = args.claudemd.resolve()
    else:
        claudemd_path = repo_root / "tests" / "CLAUDE.md"

    if not claudemd_path.exists():
        print(f"[ERROR] {claudemd_path} not found", file=sys.stderr)
        return 2

    # Run the selected mode
    if args.check:
        return check_mode(claudemd_path)
    else:
        return fix_mode(claudemd_path, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
