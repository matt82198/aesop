#!/usr/bin/env python3
"""
Shard-aware Python test runner for CI.

Distributes tracked test files across N shards using round-robin assignment.
Shard ID and total shard count come from environment variables or CLI args.

Solves the multiprocessing spawn-loop problem on Windows: inline heredocs set
__main__ = "<string>", and spawn-mode children recursively re-import and re-execute
the script. A guarded __main__ block prevents re-execution in child processes.

Usage:
  python tools/ci_shard_runner.py                   # Uses SHARD_ID, TOTAL_SHARDS env vars
  python tools/ci_shard_runner.py 0 4               # Or pass shard_id and total_shards as args

Exit codes:
  0: All tests passed
  1: Test failures, import failures, distribution errors, or no tests collected
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path


def distribute_shards(test_files, shard_id, total_shards):
    """Distribute test files across shards using round-robin.

    Args:
        test_files: sorted list of test module names (stems, e.g., 'test_foo')
        shard_id: integer 0..total_shards-1
        total_shards: total number of shards

    Returns:
        list of test module names assigned to this shard
    """
    return [test_files[i] for i in range(len(test_files)) if i % total_shards == shard_id]


def main():
    """Run Python tests for the assigned shard."""
    # Ensure repo root is in sys.path for test imports
    script_dir = Path(__file__).resolve().parent
    root = script_dir.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    # Parse shard ID and total shards from env or CLI args
    cli_args = sys.argv[1:]
    usage = "Usage: python tools/ci_shard_runner.py [shard_id total_shards]"

    if any(a in ("-h", "--help") for a in cli_args):
        print(usage)
        sys.exit(0)

    if len(cli_args) == 2:
        try:
            shard_id = int(cli_args[0])
            total_shards = int(cli_args[1])
        except ValueError:
            print(f"ERROR: {usage}")
            sys.exit(1)
    elif len(cli_args) == 0:
        try:
            shard_id = int(os.environ.get("SHARD_ID", os.environ.get("MATRIX_PYTHON_SHARD", "0")))
            total_shards = int(os.environ.get("TOTAL_SHARDS", "4"))
        except ValueError:
            print("ERROR: SHARD_ID and TOTAL_SHARDS must be integers")
            sys.exit(1)
    else:
        print(f"ERROR: Unknown argument(s): {' '.join(cli_args)}", file=sys.stderr)
        print(usage, file=sys.stderr)
        sys.exit(2)

    # Get tracked test files only (exclude WIP untracked files)
    try:
        result = subprocess.run(
            ["git", "ls-files", "tests/test_*.py"],
            capture_output=True,
            text=True,
            check=True,
        )
        tracked_files = result.stdout.strip().split("\n") if result.stdout.strip() else []
        # Extract just the stem for test discovery
        test_files = sorted(set(Path(f).stem for f in tracked_files if f))
    except Exception as e:
        print(f"ERROR: Failed to get tracked test files: {e}", file=sys.stderr)
        sys.exit(1)

    # Distribute files across shards (round-robin for balance)
    shard_files = distribute_shards(test_files, shard_id, total_shards)

    if not shard_files:
        print(
            f"ERROR: No tests assigned to shard {shard_id} (total test files: {len(test_files)})",
            file=sys.stderr,
        )
        print(f"This indicates a configuration error in the shard distribution.", file=sys.stderr)
        sys.exit(1)

    print(f"Shard {shard_id}: running {len(shard_files)} tests")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    failed_imports = []

    for test_name in shard_files:
        try:
            module = __import__(f"tests.{test_name}", fromlist=[test_name])
            suite.addTests(loader.loadTestsFromModule(module))
        except Exception as e:
            failed_imports.append((test_name, str(e)))
            print(f"ERROR: Failed to load {test_name}: {e}", file=sys.stderr)

    # If there were import failures, exit immediately
    if failed_imports:
        print(
            f"\n{len(failed_imports)} test module(s) failed to import:",
            file=sys.stderr,
        )
        for name, error in failed_imports:
            print(f"  - {name}: {error}", file=sys.stderr)
        sys.exit(1)

    # If no tests were collected, this is also an error
    if suite.countTestCases() == 0:
        print(
            f"ERROR: No tests were collected for shard {shard_id}",
            file=sys.stderr,
        )
        sys.exit(1)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
