#!/usr/bin/env python3
"""
Verify test suite counts via generated tests/SUITE-COUNTS.json (gateway to gen_suite_counts.py).
INDEX: Test suite count verification gateway. `--check` (default): READ-ONLY validation against generated tests/SUITE-COUNTS.json artifact, exit 1 on drift, 2 on eval error. `--regenerate` (alias `--fix`): delegates to gen_suite_counts.py --regenerate. This tool wraps gen_suite_counts.py to maintain backward compatibility with pre-push gates while counts live in a generated artifact. Counts are no longer hand-maintained in tests/CLAUDE.md (moved to tests/SUITE-COUNTS.json by gen_suite_counts.py). Exit 0=counts match (check)/regenerated, 1=drift, 2=cannot-evaluate. Runs as a pre-push gate via `hooks/pre-push-policy.sh` AND as a blocking CI step. Adding/removing a test suite requires running `gen_suite_counts.py --regenerate` and committing tests/SUITE-COUNTS.json

This tool wraps gen_suite_counts.py for backward compatibility with existing pre-push
gates and CI references. All actual work is delegated to gen_suite_counts.py:

- --check (default) / --strict: READ-ONLY validation. Delegates to
  gen_suite_counts.py --check. Verifies tests/SUITE-COUNTS.json matches actual
  files. Never writes anything. Exit 1 on drift, 2 if cannot evaluate.
- --regenerate (alias: --fix) [--dry-run]: the ONLY writing mode. Delegates to
  gen_suite_counts.py --regenerate. Rewrites tests/SUITE-COUNTS.json to match
  actual files. --dry-run shows what would change without writing.

The actual generation logic and hardened scanning are in gen_suite_counts.py.
This wrapper exists to maintain backward compatibility with existing hook/CI
references to verify_test_suite_count.py.

Exit codes:
    0  counts match (check) / regeneration succeeded
    1  drift or structural error
    2  cannot evaluate (file missing, target not a git repo, git failure,
       vacuous zero derivation -- fail-closed)

Usage:
    python tools/verify_test_suite_count.py --check [--repo ROOT]
    python tools/verify_test_suite_count.py --regenerate [--dry-run] [--repo ROOT]

If neither mode is specified, defaults to --check. Idempotent: running
--regenerate twice produces identical results, and --check never changes anything.
"""

import argparse
import subprocess
import sys
from pathlib import Path


def main():
    """Wrapper around gen_suite_counts.py for backward compatibility."""
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
        "--strict",
        action="store_true",
        help="Alias for --check",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Rewrite tests/SUITE-COUNTS.json to match actual files",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Deprecated alias for --regenerate",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --regenerate: show what would change but don't write",
    )
    parser.add_argument(
        "--claudemd",
        type=Path,
        default=None,
        help="Deprecated: no longer used (kept for CLI compatibility)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root (default: current directory)",
    )

    args = parser.parse_args()

    # Translate arguments to gen_suite_counts.py equivalents
    gen_args = ["python", "tools/gen_suite_counts.py"]

    read_only = args.check or args.strict
    write = args.regenerate or args.fix

    if read_only and write:
        print(
            "[ERROR] read-only mode (--check/--strict) and writing mode "
            "(--regenerate/--fix) are mutually exclusive",
            file=sys.stderr,
        )
        return 1

    # --dry-run implies --regenerate
    if args.dry_run:
        write = True

    # Default to --check if neither specified
    if not read_only and not write:
        read_only = True

    if write:
        gen_args.append("--regenerate")
        if args.dry_run:
            gen_args.append("--dry-run")
    else:
        gen_args.append("--check")

    if args.repo:
        gen_args.extend(["--repo", str(args.repo)])

    # Delegate to gen_suite_counts.py
    try:
        result = subprocess.run(gen_args, check=False)
        return result.returncode
    except FileNotFoundError:
        print("[ERROR] tools/gen_suite_counts.py not found", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[ERROR] Failed to run gen_suite_counts.py: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
