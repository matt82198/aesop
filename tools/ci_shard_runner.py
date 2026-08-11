#!/usr/bin/env python3
"""
Shard-aware Python test runner for CI.
INDEX: Shard-aware Python test runner (distributes tracked test files across N shards round-robin; spawn-safe with __main__ guard; used by ci and windows-shard jobs); added 60s timeout to git ls-files call (critical fix: no prior timeout)

Distributes tracked test files across N shards using round-robin or
timing-aware greedy bin-packing (when --timing-file is supplied).

Solves the multiprocessing spawn-loop problem on Windows: inline heredocs set
__main__ = "<string>", and spawn-mode children recursively re-import and re-execute
the script. A guarded __main__ block prevents re-execution in child processes.

Usage:
  python tools/ci_shard_runner.py 0 4
  python tools/ci_shard_runner.py 0 4 --timing-file .github/shard-timing.json
  python tools/ci_shard_runner.py 0 4 --emit-timing

Exit codes:
  0: All tests passed
  1: Test failures, import failures, distribution errors, or no tests collected
"""
import json
import os
import subprocess
import sys
import time
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


def distribute_shards_by_timing(test_files, total_shards, timing):
    """Distribute test files using greedy bin-packing by execution time.

    Assigns each test (heaviest first) to the shard with the smallest
    accumulated runtime, producing near-optimal balance.

    Args:
        test_files: list of test module names (stems)
        total_shards: number of shards
        timing: dict mapping stem -> seconds (missing entries get 1.0s default)

    Returns:
        list of lists, one per shard, containing assigned test stems
    """
    default_weight = 1.0
    weighted = [(timing.get(f, default_weight), f) for f in test_files]
    weighted.sort(reverse=True)

    buckets = [[] for _ in range(total_shards)]
    totals = [0.0] * total_shards

    for weight, name in weighted:
        lightest = totals.index(min(totals))
        buckets[lightest].append(name)
        totals[lightest] += weight

    return buckets


def load_timing_data(path):
    """Load timing data from a JSON file.

    The file maps full test paths (e.g. "tests/test_foo.py") to seconds.
    Returns a dict keyed by stem (e.g. "test_foo") or None on missing/malformed.
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(raw, dict):
        return None

    return {Path(k).stem: v for k, v in raw.items() if isinstance(v, (int, float))}


def build_pytest_args(shard_files, timeout=None):
    """Build pytest command-line args for shard files.

    Args:
        shard_files: list of test module stems (e.g., ['test_foo', 'test_bar'])
        timeout: per-test timeout in seconds (int) or None/0 to disable

    Returns:
        list of command-line args suitable for subprocess.run
    """
    cmd = [sys.executable, "-m", "pytest", "-v"]
    if timeout:
        cmd.append(f"--timeout={timeout}")
    cmd.extend(f"tests/{name}.py" for name in shard_files)
    return cmd


def _parse_args(argv):
    """Parse CLI arguments into (shard_id, total_shards, timing_file, emit_timing)."""
    positional = []
    timing_file = None
    emit_timing = False

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print("Usage: python tools/ci_shard_runner.py [shard_id total_shards] "
                  "[--timing-file PATH] [--emit-timing]")
            sys.exit(0)
        elif arg == "--timing-file":
            if i + 1 >= len(argv):
                print("ERROR: --timing-file requires a path argument", file=sys.stderr)
                sys.exit(2)
            timing_file = argv[i + 1]
            i += 2
            continue
        elif arg == "--emit-timing":
            emit_timing = True
        elif arg.startswith("-") and arg != "-":
            # Unrecognised flags previously fell through into `positional`, so
            # `--bogus` reported "expected 0 or 2 positional args" -- an error naming
            # the wrong problem, sending the reader to check shard numbers instead of
            # their typo.
            print(f"ERROR: unknown argument: {arg}", file=sys.stderr)
            sys.exit(2)
        else:
            positional.append(arg)
        i += 1

    if len(positional) == 2:
        try:
            shard_id = int(positional[0])
            total_shards = int(positional[1])
        except ValueError:
            print("ERROR: shard_id and total_shards must be integers", file=sys.stderr)
            sys.exit(1)
    elif len(positional) == 0:
        try:
            shard_id = int(os.environ.get("SHARD_ID", os.environ.get("MATRIX_PYTHON_SHARD", "0")))
            total_shards = int(os.environ.get("TOTAL_SHARDS", "4"))
        except ValueError:
            print("ERROR: SHARD_ID and TOTAL_SHARDS must be integers", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"ERROR: expected 0 or 2 positional args, got {len(positional)}", file=sys.stderr)
        sys.exit(2)

    return shard_id, total_shards, timing_file, emit_timing


def main():
    """Run Python tests for the assigned shard."""
    script_dir = Path(__file__).resolve().parent
    root = script_dir.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    shard_id, total_shards, timing_file, emit_timing = _parse_args(sys.argv[1:])

    try:
        result = subprocess.run(
            ["git", "ls-files", "tests/test_*.py"],
            capture_output=True,
            text=True,
            encoding='utf-8', errors='replace',
            timeout=60,
            check=True,
        )
        tracked_files = result.stdout.strip().split("\n") if result.stdout.strip() else []
        test_files = sorted(set(Path(f).stem for f in tracked_files if f))
    except Exception as e:
        print(f"ERROR: Failed to get tracked test files: {e}", file=sys.stderr)
        sys.exit(1)

    timing = None
    if timing_file:
        timing = load_timing_data(timing_file)

    if timing:
        buckets = distribute_shards_by_timing(test_files, total_shards, timing)
        shard_files = buckets[shard_id] if shard_id < len(buckets) else []
        print(f"Shard {shard_id}: timing-aware distribution")
    else:
        shard_files = distribute_shards(test_files, shard_id, total_shards)
        print(f"Shard {shard_id}: round-robin distribution")

    if not shard_files:
        print(
            f"ERROR: No tests assigned to shard {shard_id} (total test files: {len(test_files)})",
            file=sys.stderr,
        )
        print("This indicates a configuration error in the shard distribution.", file=sys.stderr)
        sys.exit(1)

    print(f"Shard {shard_id}: running {len(shard_files)} tests")

    pytest_timeout = os.environ.get("PYTEST_TIMEOUT")
    if pytest_timeout is not None:
        timeout = int(pytest_timeout) if pytest_timeout else 0
        cmd = build_pytest_args(shard_files, timeout=timeout or None)
        print(f"  pytest mode: {' '.join(cmd)}")
        proc = subprocess.run(cmd)
        sys.exit(proc.returncode)

    test_timings = {}
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    failed_imports = []

    skipped_modules = []

    for test_name in shard_files:
        try:
            module = __import__(f"tests.{test_name}", fromlist=[test_name])
            suite.addTests(loader.loadTestsFromModule(module))
        except unittest.SkipTest as e:
            # A module-level raise unittest.SkipTest is a deliberate, self-documenting
            # skip -- pytest and `unittest discover` both honor it. It subclasses
            # Exception, so the generic handler below used to record it as an import
            # FAILURE and fail the shard. Report it as skipped and keep going.
            skipped_modules.append((test_name, str(e)))
            print(f"SKIP: {test_name}: {e}", file=sys.stderr)
        except Exception as e:
            failed_imports.append((test_name, str(e)))
            print(f"ERROR: Failed to load {test_name}: {e}", file=sys.stderr)

    if skipped_modules:
        print(
            f"\n{len(skipped_modules)} test module(s) skipped at import:",
            file=sys.stderr,
        )
        for name, reason in skipped_modules:
            print(f"  - {name}: {reason}", file=sys.stderr)

    if failed_imports:
        print(
            f"\n{len(failed_imports)} test module(s) failed to import:",
            file=sys.stderr,
        )
        for name, error in failed_imports:
            print(f"  - {name}: {error}", file=sys.stderr)
        sys.exit(1)

    if suite.countTestCases() == 0:
        print(
            f"ERROR: No tests were collected for shard {shard_id}",
            file=sys.stderr,
        )
        sys.exit(1)

    if emit_timing:
        for test_name in shard_files:
            t0 = time.monotonic()
            per_module_suite = unittest.TestSuite()
            try:
                module = __import__(f"tests.{test_name}", fromlist=[test_name])
                per_module_suite.addTests(loader.loadTestsFromModule(module))
            except Exception:
                pass
            runner = unittest.TextTestRunner(verbosity=2)
            runner.run(per_module_suite)
            elapsed = time.monotonic() - t0
            test_timings[f"tests/{test_name}.py"] = round(elapsed, 3)

        timing_out = f"shard-{shard_id}-timing.json"
        with open(timing_out, "w", encoding="utf-8") as f:
            json.dump(test_timings, f, indent=2)
        print(f"Timing written to {timing_out}")
        sys.exit(0)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
