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
import re
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

# Unconditional top-level insert is the repo's sanctioned sibling-import guard
# (same form as auto_merge.py / tracker_autoclose.py / merge_queue.py) and the
# only form tools/sibling_import_check.py recognizes -- its AST walk only
# looks for a bare sys.path.insert(...) directly in the module body, applied
# file-wide, so main()'s own `from test_network_isolation import ...` (below)
# needs exactly this, not a guard nested inside main() itself. A module body
# executes once, so an unconditional insert cannot accumulate duplicate
# sys.path entries.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_TOOLS_DIR))


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


_ISOLATION_ARMED_ENV = "AESOP_CI_SHARD_TRIPWIRE_ARMED"
_EXPECTED_RE = re.compile(r"^Shard \d+: expected (\d+) test\(s\)", re.MULTILINE)
_CLASS_SKIPPED_RE = re.compile(r"^Shard \d+: class_skipped=(\d+)", re.MULTILINE)
_UNITTEST_RAN_RE = re.compile(r"^Ran (\d+) tests?\b", re.MULTILINE)
_TEST_LINE_RE = re.compile(r"^([A-Za-z_]\w*\s+\([^()\r\n]+\))", re.MULTILINE)


def last_started_test(output):
    """Return the last verbosity=2 unittest test header seen in output, or None."""
    matches = _TEST_LINE_RE.findall(output or "")
    return matches[-1] if matches else None


def judge_shard_output(output, exit_code, expected_count=None):
    """Fail closed when a shard exits 0 without a complete 'Ran N tests' summary.

    Returns (judged_exit_code, failure_reason_or_None).
    """
    if exit_code != 0:
        return exit_code, None
    text = output or ""
    if expected_count is None:
        exp_m = _EXPECTED_RE.search(text)
        expected_count = int(exp_m.group(1)) if exp_m else None
    skip_m = _CLASS_SKIPPED_RE.search(text)
    class_skipped = int(skip_m.group(1)) if skip_m else 0
    last_test = last_started_test(text) or "unknown"
    m = _UNITTEST_RAN_RE.search(text)
    if m is None:
        return 1, f"shard exited 0 without 'Ran N tests' summary (last test started: {last_test})"
    ran_count = int(m.group(1))
    if expected_count is not None and (ran_count + class_skipped) < expected_count:
        return 1, (
            f"shard exited 0 after running {ran_count} (+{class_skipped} class-skipped) "
            f"of {expected_count} expected tests (last test started: {last_test})"
        )
    if ran_count == 0 and class_skipped == 0:
        return 1, f"shard exited 0 with 0 tests run (last test started: {last_test})"
    return 0, None


def _count_tests_by_scope(suite):
    """Return (class_counts, module_counts) for tests in a unittest.TestSuite."""
    class_counts = {}
    module_counts = {}
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            sub_cls, sub_mod = _count_tests_by_scope(item)
            for k, v in sub_cls.items():
                class_counts[k] = class_counts.get(k, 0) + v
            for k, v in sub_mod.items():
                module_counts[k] = module_counts.get(k, 0) + v
        else:
            cls = item.__class__
            cls_key = f"{cls.__module__}.{cls.__qualname__}"
            class_counts[cls_key] = class_counts.get(cls_key, 0) + 1
            module_counts[cls.__module__] = module_counts.get(cls.__module__, 0) + 1
    return class_counts, module_counts


def _pump_stream(pipe, dest, chunks):
    """Stream lines from pipe to dest while recording them in chunks."""
    try:
        for line in iter(pipe.readline, ""):
            chunks.append(line)
            dest.write(line)
            dest.flush()
    finally:
        pipe.close()


def _arm_isolation_tripwire(target_argv):
    """Re-run target_argv under tools/test_isolation_tripwire.py (fail closed).

    Without this, a Python test writing to the live fleet-state heartbeats or
    the real home profile passed every CI shard silently (the tripwire only
    wrapped the Node and shell suites). The child is marked armed via env so it
    never re-wraps itself. Also enforces complete unittest summary output so a
    silent mid-shard exit 0 never reads as green.
    """
    if os.environ.get(_ISOLATION_ARMED_ENV):
        return
    env = dict(os.environ)
    env[_ISOLATION_ARMED_ENV] = "1"
    tripwire = _TOOLS_DIR / "test_isolation_tripwire.py"
    proc = subprocess.Popen(
        [sys.executable, str(tripwire), "--", *target_argv],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    out_chunks = []
    err_chunks = []
    t_out = threading.Thread(target=_pump_stream, args=(proc.stdout, sys.stdout, out_chunks), daemon=True)
    t_err = threading.Thread(target=_pump_stream, args=(proc.stderr, sys.stderr, err_chunks), daemon=True)
    t_out.start()
    t_err.start()
    rc = proc.wait()
    t_out.join()
    t_err.join()
    skip_judge = (
        "-h" in target_argv
        or "--help" in target_argv
        or "--emit-timing" in target_argv
        or os.environ.get("PYTEST_TIMEOUT") is not None
    )
    if not skip_judge:
        combined = "".join(out_chunks) + "\n" + "".join(err_chunks)
        judged_rc, reason = judge_shard_output(combined, rc)
        if reason:
            print(f"ERROR: {reason}", file=sys.stderr, flush=True)
            sys.exit(judged_rc)
    sys.exit(rc)


def main():
    """Run Python tests for the assigned shard."""
    # sys.path already carries _REPO_ROOT/_TOOLS_DIR from module import time
    # (see the module-level guard above).
    _arm_isolation_tripwire([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])

    # Defense-in-depth: tests/__init__.py already applies this on first import of
    # the `tests` package (the universal hook, reached by every path below), but
    # setting it explicitly here too documents the intent at this entry point and
    # means it is active even before any test module is imported. Idempotent
    # per-process. See tools/test_network_isolation.py (incident 2026-10-05: an
    # unmocked merge_train/merge_queue subprocess test pushed real branches).
    from test_network_isolation import apply_test_isolation_env
    apply_test_isolation_env()

    shard_id, total_shards, timing_file, emit_timing = _parse_args(sys.argv[1:])

    try:
        result = subprocess.run(
            ["git", "ls-files", "tests/test_*.py"],
            stdin=subprocess.DEVNULL,
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
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL)
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

    expected_count = suite.countTestCases()
    if expected_count == 0:
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

    class_counts, module_counts = _count_tests_by_scope(suite)
    state = {"last_started": None}

    class _TrackingResult(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.class_skipped = 0

        def startTest(self, test):
            state["last_started"] = str(test)
            super().startTest(test)
            # Flush the test header immediately so a hard exit (e.g. os._exit)
            # still leaves the last started test visible in parent output.
            self.stream.flush()

        def addSkip(self, test, reason):
            desc = str(test)
            if desc.startswith("setUpClass (") and desc.endswith(")"):
                self.class_skipped += class_counts.get(desc[len("setUpClass ("):-1], 0)
            elif desc.startswith("setUpModule (") and desc.endswith(")"):
                self.class_skipped += module_counts.get(desc[len("setUpModule ("):-1], 0)
            super().addSkip(test, reason)

    print(f"Shard {shard_id}: expected {expected_count} test(s)", flush=True)
    runner = unittest.TextTestRunner(verbosity=2, resultclass=_TrackingResult)
    try:
        result = runner.run(suite)
    except SystemExit as exc:
        print(
            f"ERROR: shard {shard_id} aborted via SystemExit({exc.code}) before test summary "
            f"(last test started: {state['last_started'] or 'unknown'})",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
    if result.class_skipped:
        print(f"Shard {shard_id}: class_skipped={result.class_skipped}", flush=True)
    if not result.wasSuccessful():
        sys.exit(1)
    if (result.testsRun + result.class_skipped) < expected_count or (
        result.testsRun == 0 and result.class_skipped == 0
    ):
        print(
            f"ERROR: shard {shard_id} ran {result.testsRun} (+{result.class_skipped} class-skipped) "
            f"of {expected_count} expected tests (last test started: {state['last_started'] or 'unknown'})",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
