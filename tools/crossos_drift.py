#!/usr/bin/env python3
"""Cross-OS drift measurement tool.
INDEX: Cross-OS CI drift measurement (Windows vs Linux outcome drift from GitHub Actions history; CLI: `--runs N=10 [--json]`; reports pass rates, divergence set, failing test aggregation; exit 3 on auth failure); `job_conclusion()` fails CLOSED — a COMPLETED job whose conclusion is outside (SUCCESS, NEUTRAL, SKIPPED) is FAIL, never PENDING, because PENDING is dropped from the pass-rate denominators (GAP5 fix; pre-fix, GitHub's `startup_failure` silently vanished from drift measurement)

Quantifies Windows-vs-Linux CI outcome drift from GitHub Actions history via gh CLI.

For the last N completed ci.yml runs on main, collects per-job conclusions (ubuntu ci shards vs
windows job) and reports:
  - Windows pass rate vs ubuntu pass rate
  - Set of runs where they diverged (ubuntu green + windows red)
  - Failing windows test names aggregated by frequency (from gh run view --job <id> --log-failed)

Usage:
  python crossos_drift.py [--runs N=10] [--json]

Exit codes:
  0 - Success
  1 - Error (e.g., parsing failure, logic error)
  2 - Execution error (e.g., gh call failed)
  3 - Authentication error (gh is not authenticated)

Honesty:
  - If windows job doesn't exist (pre-#317), counts as NOT-PRESENT in per-run analysis
  - If gh is unauthenticated, exits 3 with a clear message
  - Parsing bounds test names; full logs never dumped
"""
import sys
import subprocess
import json
import argparse
from collections import defaultdict
from typing import Dict, List, Tuple, Optional

DEFAULT_RUNS = 10

# GitHub Actions workflow context
WORKFLOW_FILE = "ci.yml"
GH_REPO = None  # Auto-detect current repo


class GhError(Exception):
    """gh CLI error."""
    pass


def gh_run_list(limit: int = 10, branch: str = "main") -> List[Dict]:
    """Fetch the last N completed workflow runs for ci.yml on main.

    Returns list of run objects with: databaseId, number, name, status, conclusion, createdAt.
    Raises GhError if gh is not authenticated.
    """
    try:
        cmd = [
            "gh", "run", "list",
            "--workflow", WORKFLOW_FILE,
            "--branch", branch,
            "--limit", str(limit),
            "--json", "databaseId,number,name,status,conclusion,createdAt",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30,
        )

        if result.returncode != 0:
            stderr = result.stderr.lower()
            if "authentication" in stderr or "not authenticated" in stderr:
                raise GhError("gh is not authenticated")
            raise GhError(f"gh run list failed: {result.stderr}")

        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise GhError("gh run list timed out")
    except json.JSONDecodeError as e:
        raise GhError(f"Failed to parse gh response: {e}")


def gh_jobs_for_run(run_id: str) -> List[Dict]:
    """Fetch all jobs for a given run.

    Returns list of job objects with: id, name, status, conclusion.
    """
    try:
        cmd = [
            "gh", "run", "view", str(run_id),
            "--json", "jobs",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30,
        )

        if result.returncode != 0:
            raise GhError(f"gh run view failed: {result.stderr}")

        data = json.loads(result.stdout)
        return data.get("jobs", [])
    except subprocess.TimeoutExpired:
        raise GhError(f"gh run view timed out for run {run_id}")
    except json.JSONDecodeError as e:
        raise GhError(f"Failed to parse gh jobs response: {e}")


def gh_job_logs(job_id: str) -> str:
    """Fetch logs for a job. Return stdout only (not stderr).

    Returns the full log text as a string.
    """
    try:
        cmd = [
            "gh", "run", "view", "--job", str(job_id), "--log",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=60,
        )

        if result.returncode != 0:
            # Log fetch failure is not fatal; continue with divergence analysis
            return ""

        return result.stdout
    except subprocess.TimeoutExpired:
        return ""


def parse_failing_tests(log_text: str) -> Dict[str, int]:
    """Parse failing test names from job log output.

    Searches for lines matching "FAIL:" or "not ok" patterns.
    Returns dict of {test_name: frequency} sorted by frequency descending.
    """
    failures = defaultdict(int)

    for line in log_text.split("\n"):
        line = line.strip()
        test_name = None

        if line.startswith("FAIL:"):
            test_name = line.replace("FAIL:", "", 1).strip()
        elif line.startswith("not ok"):
            # TAP format: "not ok N - test_name" or "not ok test_name"
            test_name = line.replace("not ok", "", 1).strip()
            # Remove test number prefix if present
            if test_name and test_name[0].isdigit():
                test_name = " ".join(test_name.split()[1:])

        if test_name:
            failures[test_name] += 1

    return dict(sorted(failures.items(), key=lambda x: x[1], reverse=True))


def classify_job(job: Dict) -> Tuple[str, Optional[str]]:
    """Classify a job as ubuntu/windows and return (category, specific_name).

    Returns: ("ubuntu", "shard-X") | ("windows", None) | (None, None)
    """
    name = job.get("name", "").lower()

    # Windows job: named "windows"
    if name == "windows":
        return ("windows", job.get("name"))

    # Ubuntu jobs: named "ci (N)" or contain "ubuntu"/"linux"
    if name.startswith("ci (") or "ubuntu" in name or "linux" in name:
        return ("ubuntu", job.get("name"))

    return (None, None)


def job_conclusion(job: Dict) -> str:
    """Extract job conclusion as PASS or FAIL or PENDING.

    Return "PENDING" only while the job has not COMPLETED.
    Return "PASS" for the non-blocking conclusions (SUCCESS/NEUTRAL/SKIPPED).
    Return "FAIL" for every other conclusion of a COMPLETED job.

    Fails CLOSED on unknown conclusions. A COMPLETED job whose conclusion is
    outside the green list -- FAILURE, TIMED_OUT, CANCELLED, ACTION_REQUIRED,
    STALE, GitHub's STARTUP_FAILURE, an empty conclusion, or any token GitHub
    adds later -- counts as a failure. It must never fall through to PENDING:
    PENDING is excluded from the pass-rate denominators in main(), so an
    unrecognized outcome would silently vanish from drift measurement instead
    of counting against it.
    """
    status = job.get("status", "").upper()
    conclusion = job.get("conclusion", "").upper()

    if status != "COMPLETED":
        return "PENDING"

    if conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED"):
        return "PASS"

    return "FAIL"


def analyze_run(run_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Analyze a single run.

    Returns (ubuntu_status, windows_status) where each is:
      "PASS" | "FAIL" | "PENDING" | "NOT-PRESENT"
    """
    try:
        jobs = gh_jobs_for_run(run_id)

        ubuntu_results = []
        windows_result = None

        for job in jobs:
            category, _ = classify_job(job)
            conclusion = job_conclusion(job)

            if category == "ubuntu":
                ubuntu_results.append(conclusion)
            elif category == "windows":
                windows_result = conclusion

        # Ubuntu: all shards must pass for "PASS"
        ubuntu_status = "PASS" if ubuntu_results and all(r == "PASS" for r in ubuntu_results) else (
            "FAIL" if ubuntu_results and any(r == "FAIL" for r in ubuntu_results) else "PENDING"
        )

        # Windows: not present if no windows job found
        windows_status = windows_result if windows_result else "NOT-PRESENT"

        return (ubuntu_status, windows_status)
    except GhError:
        # Run analysis failed; skip this run
        return (None, None)


def analyze_windows_failures(run_id: str) -> Dict[str, int]:
    """Fetch logs for windows job in this run and parse failing tests.

    Returns dict of {test_name: frequency}.
    """
    try:
        jobs = gh_jobs_for_run(run_id)

        for job in jobs:
            category, _ = classify_job(job)
            if category == "windows":
                job_id = job.get("id")
                if job_id:
                    log_text = gh_job_logs(job_id)
                    if log_text:
                        return parse_failing_tests(log_text)
    except GhError:
        pass

    return {}


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Measure Windows-vs-Linux CI outcome drift from GitHub Actions history.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python crossos_drift.py
    Analyze last 10 completed ci.yml runs on main (default).

  python crossos_drift.py --runs 8
    Analyze last 8 runs.

  python crossos_drift.py --runs 10 --json
    Output results as JSON.

Exit codes:
  0 - Success
  1 - Error (parsing, logic, etc.)
  2 - Execution error (gh call failed)
  3 - Authentication error (gh not authenticated)
""",
    )

    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Number of completed runs to analyze (default: {DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    if args.runs <= 0:
        print("ERROR: --runs must be > 0", file=sys.stderr)
        sys.exit(1)

    # Fetch run list
    try:
        runs = gh_run_list(limit=args.runs)
    except GhError as e:
        if "not authenticated" in str(e).lower():
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(3)
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    if not runs:
        print("ERROR: No completed runs found", file=sys.stderr)
        sys.exit(1)

    # Analyze each run
    ubuntu_results = []
    windows_results = []
    divergences = []
    all_windows_failures = defaultdict(int)

    for run in runs:
        run_id = run.get("databaseId") or run.get("number")
        ubuntu_status, windows_status = analyze_run(str(run_id))

        if ubuntu_status is None or windows_status is None:
            # Run analysis failed
            continue

        ubuntu_results.append(ubuntu_status)
        windows_results.append(windows_status)

        # Detect divergence: ubuntu PASS + windows FAIL (or NOT-PRESENT doesn't count as divergence)
        if ubuntu_status == "PASS" and windows_status == "FAIL":
            divergences.append(run_id)

        # Collect windows failures
        if windows_status == "FAIL":
            failures = analyze_windows_failures(run_id)
            for test_name, freq in failures.items():
                all_windows_failures[test_name] += freq

    # Calculate pass rates
    ubuntu_completed = [r for r in ubuntu_results if r != "PENDING"]
    windows_completed = [r for r in windows_results if r not in ("PENDING", "NOT-PRESENT")]

    ubuntu_pass_rate = (sum(1 for r in ubuntu_completed if r == "PASS") / len(ubuntu_completed)) if ubuntu_completed else 0.0
    windows_pass_rate = (sum(1 for r in windows_completed if r == "PASS") / len(windows_completed)) if windows_completed else 0.0

    # Top windows failures (bound output)
    top_failures = dict(sorted(all_windows_failures.items(), key=lambda x: x[1], reverse=True)[:20])

    # Output
    if args.json:
        output = {
            "ubuntu_pass_rate": round(ubuntu_pass_rate, 4),
            "windows_pass_rate": round(windows_pass_rate, 4),
            "total_runs_analyzed": len(ubuntu_results),
            "ubuntu_total": len(ubuntu_results),
            "windows_total": len(windows_results),
            "windows_not_present": sum(1 for r in windows_results if r == "NOT-PRESENT"),
            "divergences": divergences,
            "top_windows_failures": top_failures,
        }
        print(json.dumps(output, indent=2))
    else:
        print("Cross-OS CI Drift Report")
        print("=" * 60)
        print(f"Runs analyzed: {len(ubuntu_results)}")
        print()
        print("Pass Rates:")
        print(f"  Ubuntu:  {ubuntu_pass_rate*100:6.2f}% ({sum(1 for r in ubuntu_completed if r == 'PASS')}/{len(ubuntu_completed)} runs)")
        print(f"  Windows: {windows_pass_rate*100:6.2f}% ({sum(1 for r in windows_completed if r == 'PASS')}/{len(windows_completed)} runs)")
        print()

        windows_not_present = sum(1 for r in windows_results if r == "NOT-PRESENT")
        if windows_not_present > 0:
            print(f"Windows job not present in {windows_not_present} runs (pre-#317)")
            print()

        if divergences:
            print(f"Divergences (ubuntu PASS + windows FAIL): {len(divergences)}")
            for run_id in divergences[:10]:  # Show first 10
                print(f"  {run_id}")
            if len(divergences) > 10:
                print(f"  ... and {len(divergences) - 10} more")
            print()

        if top_failures:
            print("Top Windows Failures:")
            for test_name, freq in sorted(top_failures.items(), key=lambda x: x[1], reverse=True)[:10]:
                print(f"  {freq:2d}x {test_name}")
            print()

    sys.exit(0)


if __name__ == "__main__":
    main()
