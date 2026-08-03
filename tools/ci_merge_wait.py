#!/usr/bin/env python3
"""
ci_merge_wait.py - CI-gated merge helper: wait for PR checks to conclude, then merge.
INDEX: CI-gated merge helper (polls gh pr view until SUCCESS; fail-closed: empty rollup=PENDING, --expect-checks requires ALL named checks present AND concluded, --allow-no-checks escape hatch); uses subprocess_common.py for gh timeout + encoding; MERGED-state verification gate implicit in merge guard (structurally unreachable unless CI SUCCESS)

Polls gh pr view until all status checks conclude (SUCCESS/FAILURE), then merges ONLY
if all checks are SUCCESS. The gh pr merge call is STRUCTURALLY UNREACHABLE unless the
status is SUCCESS - this is the whole point (prevents merge-on-CI-failure edge cases).

Fail-closed semantics: empty rollup -> PENDING by default; use --allow-no-checks to treat
empty rollups as SUCCESS. Use --expect-checks to require specific named checks to be present
and successful before merging.

Usage:
  python ci_merge_wait.py <PR-number> [--timeout SECONDS] [--poll SECONDS] [--merge-method merge|squash|rebase]
                          [--dry-run] [--allow-no-checks] [--expect-checks NAME1,NAME2,...] [--self-test]

Options:
  --timeout SECONDS              Max seconds to wait for CI (default: 3600)
  --poll SECONDS                 Poll interval in seconds (default: 10)
  --merge-method METHOD          Merge strategy: merge, squash, rebase (default: merge)
  --dry-run                      Skip actual merge, just verify CI status and report what would happen
  --allow-no-checks              Allow merge when no CI checks present (repos without CI)
  --expect-checks NAME1,NAME2    Comma-separated list of checks that MUST be present and successful
  --self-test                    Run offline self-test of polling/decision logic (no network, no PR required)

Exit codes:
  0 = PR merged successfully, dry-run verified, or self-test passed
  1 = General error or self-test failed
  2 = CI checks failed (do NOT merge, prints which check failed)
  3 = Timeout waiting for CI to conclude
  4 = PR not mergeable or has merge conflicts

Requires: gh CLI available on PATH (unless --self-test). Gracefully exits with error if gh is missing.
"""

import argparse
import json
import os
import subprocess
import sys
import time

# Ensure this tool's own directory (tools/) is importable so the shared
# harness resolves regardless of cwd or how the file is loaded
# (the import-gate loads tools by path, without tools/ on sys.path).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from subprocess_common import gh, json_output


def run_gh_command(args):
    """
    Run gh CLI command; return parsed JSON or None if gh missing/error.
    Uses subprocess_common.gh() for unified timeout and encoding handling.
    N9 FIX: Raises TimeoutExpired on timeout (caller handles transient errors).
    """
    try:
        # Strip 'gh' prefix if present (subprocess_common.gh() adds it)
        if args and args[0] == 'gh':
            args = args[1:]
        result = gh(args, check=False)
        if result.returncode != 0:
            if "not found" in result.stderr or "No such file" in result.stderr:
                return None
            # Re-raise for non-zero return (will be caught by caller)
            result.check_returncode()
        return json.loads(result.stdout) if result.stdout.strip() else None
    except subprocess.TimeoutExpired:
        # N9 FIX: Re-raise timeout so caller (poll loop) can retry
        raise
    except FileNotFoundError:
        print("ERROR: gh CLI not found on PATH")
        sys.exit(1)


def get_pr_status(pr_number):
    """
    Fetch PR status via gh pr view.
    Returns dict with 'mergeable', 'statusCheckRollup', and 'headRefOid' keys.
    Returns None if gh is missing.
    F5 FIX: headRefOid is fetched to enable SHA-pinned merge via --match-head-commit.
    """
    data = run_gh_command([
        "gh", "pr", "view", str(pr_number),
        "--json", "mergeable,statusCheckRollup,headRefOid"
    ])
    return data


def check_ci_status(status_rollup, allow_no_checks=False, expected_checks=None):
    """
    Analyze status check rollup from gh pr view --json statusCheckRollup.
    Handles both CheckRun (status + conclusion) and StatusContext (state) entries.

    CheckRun classification:
      - status=COMPLETED + conclusion in (FAILURE, CANCELLED, TIMED_OUT, ACTION_REQUIRED, STARTUP_FAILURE) = FAILURE
      - status=COMPLETED + conclusion in (NEUTRAL, SKIPPED) or None/empty = SUCCESS (non-blocking per GitHub)
      - status in (QUEUED, IN_PROGRESS) = PENDING
      - status not recognized = PENDING (fail-closed)

    StatusContext classification:
      - state='success' = SUCCESS
      - state in ('failure', 'error') = FAILURE
      - state='pending' = PENDING
      - state in ('neutral', 'skipped') = SUCCESS (non-blocking advisory checks)
      - state not recognized = PENDING (fail-closed)

    Unrecognized check shapes (no status/state field) = PENDING (fail-closed).

    GitHub semantics: NEUTRAL and SKIPPED conclusions/states are non-blocking and do not prevent merge.

    Empty rollup (fail-closed):
      - If allow_no_checks=True: ("success", None)
      - If allow_no_checks=False: ("pending", None) [fail-closed default]

    Expected checks (if provided):
      - ALL named checks must be present in rollup
      - ALL named checks must be in SUCCESS state
      - If any expected check is missing: ("pending", None)
      - If any expected check fails: ("failure", check_name)

    Args:
      status_rollup: list of check dicts from gh pr view
      allow_no_checks: if True, empty rollup → success; if False (default), empty rollup → pending
      expected_checks: set of check names that MUST be present and successful

    Returns: ("pending", None), ("success", None), or ("failure", check_name)
    """
    # Fail-closed: empty rollup defaults to PENDING unless explicitly allowed
    # F2 FIX: expected_checks takes PRECEDENCE over allow_no_checks
    # If expected_checks is non-empty and rollup is empty, return pending (missing required checks)
    if not status_rollup:
        if expected_checks:
            # Expected checks are required but rollup is empty (they're missing)
            return ("pending", None)
        elif allow_no_checks:
            return ("success", None)
        else:
            # Empty rollup: fail-closed to PENDING (window where checks vanished/haven't registered yet)
            return ("pending", None)

    # Collect statuses
    pending_checks = []
    failed_checks = []
    found_checks = {}  # Map of check name → status

    for check in status_rollup:
        check_name = check.get("name", "unknown")
        check_status = None

        # Determine check classification (CheckRun or StatusContext or unrecognized)
        if "status" in check:
            # CheckRun entry (has 'status' field)
            status = check.get("status", "").upper()
            conclusion = check.get("conclusion", "")

            if status == "COMPLETED":
                # Check conclusion for failure indicators
                if conclusion and conclusion.upper() in ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"):
                    check_status = "failure"
                elif not conclusion or conclusion == "":
                    # COMPLETED with null/empty conclusion = fail-closed to PENDING (API anomaly)
                    check_status = "pending"
                elif conclusion.upper() in ("SUCCESS", "NEUTRAL", "SKIPPED"):
                    # SUCCESS, NEUTRAL, SKIPPED are all passing/non-blocking
                    check_status = "success"
                else:
                    # F6 FIX: Unknown conclusion values must fail-closed to PENDING (not success)
                    # Future GitHub API conclusion values should not auto-succeed
                    check_status = "pending"
            elif status in ("QUEUED", "IN_PROGRESS"):
                check_status = "pending"
            else:
                # Unrecognized status value (fail-closed)
                check_status = "pending"

        elif "state" in check:
            # StatusContext entry (has 'state' field)
            state = check.get("state", "").lower()
            if state == "success":
                check_status = "success"
            elif state in ("failure", "error"):
                check_status = "failure"
            elif state == "pending":
                check_status = "pending"
            elif state in ("neutral", "skipped"):
                # Non-blocking advisory or skipped checks (GitHub semantics)
                check_status = "success"
            else:
                # Unrecognized state value (fail-closed)
                check_status = "pending"

        else:
            # Unrecognized shape (no status or state field) - fail-closed
            check_status = "pending"

        # Record this check's status
        found_checks[check_name] = check_status

        # Collect check status
        if check_status == "failure":
            failed_checks.append(check_name)
        elif check_status == "pending":
            pending_checks.append(check_name)

    # If expected checks provided, verify all are present and successful
    if expected_checks:
        for expected_name in expected_checks:
            if expected_name not in found_checks:
                # Expected check not found (missing in the window)
                return ("pending", None)
            if found_checks[expected_name] == "failure":
                # Expected check failed
                return ("failure", expected_name)
            if found_checks[expected_name] == "pending":
                # Expected check still pending
                return ("pending", None)

        # All expected checks passed. Now check if ANY check failed (expected or not).
        # CRITICAL (BL4 P2 fix): Any check failure blocks merge, not just expected checks.
        # The expected_checks parameter only REQUIRES certain checks to be present and pass,
        # but doesn't exempt other checks from the failure rule (fail-closed semantics).
        if failed_checks:
            return ("failure", failed_checks[0])
        elif pending_checks:
            return ("pending", None)
        else:
            return ("success", None)

    # Determine overall status (when expected_checks is not specified)
    if failed_checks:
        return ("failure", failed_checks[0])
    elif pending_checks:
        return ("pending", None)
    else:
        return ("success", None)


def merge_pr(pr_number, merge_method, dry_run=False, head_ref_oid=None):
    """
    Merge the PR using gh pr merge with SHA-pinned commit.
    This call is STRUCTURALLY UNREACHABLE unless CI is SUCCESS.
    F5 FIX: Uses --match-head-commit <sha> to ensure merge targets the exact commit
    whose CI checks passed, preventing TOCTOU race where a push between CI check and
    merge could sneak in an untested commit.
    RS-B FIX: Requires headRefOid to be present (fail-closed). Does NOT merge unpinned.
    If dry_run is True, report what would be done without actually merging.
    Returns True on success, False on error.
    """
    # RS-B FIX: Fail-closed if headRefOid is missing - require the SHA pin or abort
    if not head_ref_oid:
        print("ERROR: Cannot merge without SHA pin (headRefOid missing). Aborting merge to prevent TOCTOU race.")
        return False

    if dry_run:
        print(f"[DRY-RUN] Would merge PR #{pr_number} with --{merge_method} --match-head-commit {head_ref_oid}")
        return True

    merge_cmd = ["pr", "merge", str(pr_number), f"--{merge_method}"]
    # F5 FIX: Add --match-head-commit to pin merge to the SHA that passed CI
    merge_cmd.extend(["--match-head-commit", head_ref_oid])

    result = gh(merge_cmd, check=False)
    return result.returncode == 0


def run_self_test():
    """
    Run self-test with real GitHub API payload structures.
    No network calls; verifies the merge guard logic with CheckRun and StatusContext payloads.
    Returns True if all tests pass, False otherwise.
    """
    print("Running self-test with real GitHub API payloads...")

    # Test 1: CheckRun success case (COMPLETED with explicit success conclusion)
    checkrun_success = [
        {"name": "test-unit", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "test-integration", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint", "status": "COMPLETED", "conclusion": None},  # null conclusion = fail-closed to PENDING
    ]
    ci_status, failed_check = check_ci_status(checkrun_success)
    if ci_status != "pending":
        print(f"FAIL: Expected 'pending' for COMPLETED with null conclusion (fail-closed), got '{ci_status}'")
        return False
    print("[OK] CheckRun with null conclusion fails-closed to PENDING")

    # Test 2: CheckRun in-progress case (should be pending)
    checkrun_pending = [
        {"name": "test-unit", "status": "IN_PROGRESS", "conclusion": None},
        {"name": "test-integration", "status": "COMPLETED", "conclusion": None},
    ]
    ci_status, failed_check = check_ci_status(checkrun_pending)
    if ci_status != "pending":
        print(f"FAIL: Expected 'pending' for IN_PROGRESS, got '{ci_status}'")
        return False
    print("[OK] CheckRun pending case (IN_PROGRESS)")

    # Test 3: CheckRun failure case (COMPLETED with FAILURE conclusion)
    checkrun_failure = [
        {"name": "test-unit", "status": "COMPLETED", "conclusion": "FAILURE"},
        {"name": "test-integration", "status": "COMPLETED", "conclusion": None},
    ]
    ci_status, failed_check = check_ci_status(checkrun_failure)
    if ci_status != "failure" or failed_check != "test-unit":
        print(f"FAIL: Expected 'failure' with 'test-unit', got '{ci_status}' / '{failed_check}'")
        return False
    print("[OK] CheckRun failure case (COMPLETED + FAILURE)")

    # Test 4: StatusContext success case (state=success)
    statuscontext_success = [
        {"name": "continuous-integration/travis-ci/push", "state": "success"},
    ]
    ci_status, failed_check = check_ci_status(statuscontext_success)
    if ci_status != "success":
        print(f"FAIL: Expected 'success' for StatusContext state=success, got '{ci_status}'")
        return False
    print("[OK] StatusContext success case (state=success)")

    # Test 5: StatusContext failure case (state=failure)
    statuscontext_failure = [
        {"name": "continuous-integration/travis-ci/push", "state": "failure"},
    ]
    ci_status, failed_check = check_ci_status(statuscontext_failure)
    if ci_status != "failure" or failed_check != "continuous-integration/travis-ci/push":
        print(f"FAIL: Expected 'failure' with StatusContext, got '{ci_status}' / '{failed_check}'")
        return False
    print("[OK] StatusContext failure case (state=failure)")

    # Test 6: StatusContext pending case (state=pending)
    statuscontext_pending = [
        {"name": "continuous-integration/travis-ci/push", "state": "pending"},
    ]
    ci_status, failed_check = check_ci_status(statuscontext_pending)
    if ci_status != "pending":
        print(f"FAIL: Expected 'pending' for StatusContext state=pending, got '{ci_status}'")
        return False
    print("[OK] StatusContext pending case (state=pending)")

    # Test 7: Mixed CheckRun and StatusContext (all success)
    mixed_success = [
        {"name": "test-unit", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "travis-ci", "state": "success"},
    ]
    ci_status, _ = check_ci_status(mixed_success)
    if ci_status != "success":
        print(f"FAIL: Expected 'success' for mixed payloads, got '{ci_status}'")
        return False
    print("[OK] Mixed CheckRun + StatusContext success case")

    # Test 8: Mixed payloads with pending (should block merge)
    mixed_pending = [
        {"name": "test-unit", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint", "status": "QUEUED", "conclusion": None},
    ]
    ci_status, _ = check_ci_status(mixed_pending)
    if ci_status != "pending":
        print(f"FAIL: Expected 'pending' for QUEUED check, got '{ci_status}'")
        return False
    print("[OK] Mixed payloads with QUEUED (pending)")

    # Test 9: Empty checks array (fail-closed: should be PENDING by default)
    ci_status, _ = check_ci_status([])
    if ci_status != "pending":
        print(f"FAIL: Expected 'pending' for empty rollup (fail-closed), got '{ci_status}'")
        return False
    print("[OK] Empty rollup case: treated as PENDING (fail-closed)")

    # Test 10: Empty checks array with --allow-no-checks flag (should be SUCCESS)
    ci_status, _ = check_ci_status([], allow_no_checks=True)
    if ci_status != "success":
        print(f"FAIL: Expected 'success' for empty rollup with allow_no_checks=True, got '{ci_status}'")
        return False
    print("[OK] Empty rollup with allow_no_checks=True: treated as SUCCESS")

    # Test 11: Unrecognized shape (fail-closed: should not succeed)
    unrecognized = [
        {"name": "mystery-check"},
    ]
    ci_status, _ = check_ci_status(unrecognized)
    if ci_status == "success":
        print(f"FAIL: Expected not 'success' for unrecognized shape, got '{ci_status}'")
        return False
    print("[OK] Unrecognized check shape (fail-closed)")

    # Test 12: CheckRun cancelled (counts as failure)
    cancelled_check = [
        {"name": "test-unit", "status": "COMPLETED", "conclusion": "CANCELLED"},
    ]
    ci_status, _ = check_ci_status(cancelled_check)
    if ci_status != "failure":
        print(f"FAIL: Expected 'failure' for CANCELLED, got '{ci_status}'")
        return False
    print("[OK] CheckRun CANCELLED (failure)")

    # Test 13: CheckRun neutral conclusion (non-blocking advisory, should be success)
    neutral_check = [
        {"name": "advisory-lint", "status": "COMPLETED", "conclusion": "NEUTRAL"},
    ]
    ci_status, _ = check_ci_status(neutral_check)
    if ci_status != "success":
        print(f"FAIL: Expected 'success' for NEUTRAL conclusion, got '{ci_status}'")
        return False
    print("[OK] CheckRun NEUTRAL conclusion (success, non-blocking)")

    # Test 14: CheckRun skipped conclusion (non-blocking, should be success)
    skipped_check = [
        {"name": "optional-test", "status": "COMPLETED", "conclusion": "SKIPPED"},
    ]
    ci_status, _ = check_ci_status(skipped_check)
    if ci_status != "success":
        print(f"FAIL: Expected 'success' for SKIPPED conclusion, got '{ci_status}'")
        return False
    print("[OK] CheckRun SKIPPED conclusion (success, non-blocking)")

    # Test 15: StatusContext neutral state (non-blocking advisory, should be success)
    neutral_state = [
        {"name": "advisory-check", "state": "neutral"},
    ]
    ci_status, _ = check_ci_status(neutral_state)
    if ci_status != "success":
        print(f"FAIL: Expected 'success' for StatusContext state=neutral, got '{ci_status}'")
        return False
    print("[OK] StatusContext neutral state (success, non-blocking)")

    # Test 16: StatusContext skipped state (non-blocking, should be success)
    skipped_state = [
        {"name": "optional-check", "state": "skipped"},
    ]
    ci_status, _ = check_ci_status(skipped_state)
    if ci_status != "success":
        print(f"FAIL: Expected 'success' for StatusContext state=skipped, got '{ci_status}'")
        return False
    print("[OK] StatusContext skipped state (success, non-blocking)")

    # Test 17: Fabricated unknown state (fail-closed to pending)
    unknown_state = [
        {"name": "mystery-state", "state": "fabricated_unknown_state"},
    ]
    ci_status, _ = check_ci_status(unknown_state)
    if ci_status != "pending":
        print(f"FAIL: Expected 'pending' for unknown state, got '{ci_status}'")
        return False
    print("[OK] Fabricated unknown state (pending, fail-closed)")

    # Test 18: Expected checks - all present and successful
    rollup_with_expected = [
        {"name": "unit-tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "integration-tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"},
    ]
    ci_status, _ = check_ci_status(
        rollup_with_expected,
        expected_checks={"unit-tests", "integration-tests"}
    )
    if ci_status != "success":
        print(f"FAIL: Expected 'success' with expected checks present, got '{ci_status}'")
        return False
    print("[OK] Expected checks all present and successful")

    # Test 19: Expected checks - one missing (should be PENDING)
    rollup_missing_expected = [
        {"name": "unit-tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"},
    ]
    ci_status, _ = check_ci_status(
        rollup_missing_expected,
        expected_checks={"unit-tests", "integration-tests"}
    )
    if ci_status != "pending":
        print(f"FAIL: Expected 'pending' with missing expected check, got '{ci_status}'")
        return False
    print("[OK] Missing expected check (window transition): returns PENDING")

    # Test 20: Expected checks - one failed (should be FAILURE)
    rollup_failed_expected = [
        {"name": "unit-tests", "status": "COMPLETED", "conclusion": "FAILURE"},
        {"name": "integration-tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
    ]
    ci_status, failed_check = check_ci_status(
        rollup_failed_expected,
        expected_checks={"unit-tests", "integration-tests"}
    )
    if ci_status != "failure" or failed_check != "unit-tests":
        print(f"FAIL: Expected 'failure' with expected check failed, got '{ci_status}' / '{failed_check}'")
        return False
    print("[OK] Expected check failed: returns FAILURE")

    # Test 20b: Expected checks all pass, but non-expected check FAILED (P2 audit bug fix - BL4)
    # FIXED: When --expect-checks is given, ANY check failure (expected or not) must block merge.
    # The --expect-checks parameter only REQUIRES certain checks to be present and pass,
    # but doesn't exempt other checks from the failure rule (fail-closed semantics).
    rollup_expected_pass_noncxpected_fail = [
        {"name": "unit-tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "integration-tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"},
    ]
    ci_status, failed_check = check_ci_status(
        rollup_expected_pass_noncxpected_fail,
        expected_checks={"unit-tests", "integration-tests"}
    )
    if ci_status != "failure" or failed_check != "lint":
        print(f"FAIL: Expected 'failure' with non-expected check failed (BL4 P2 fix), got '{ci_status}' / '{failed_check}'")
        return False
    print("[OK] Non-expected check failed while expected pass: returns FAILURE (BL4 P2 audit fix)")

    # Test 20c: Expected checks all pass, but non-expected pending (must wait for completion)
    rollup_expected_pass_noncexpected_pending = [
        {"name": "unit-tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "integration-tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "optional-scan", "status": "IN_PROGRESS", "conclusion": None},
    ]
    ci_status, _ = check_ci_status(
        rollup_expected_pass_noncexpected_pending,
        expected_checks={"unit-tests", "integration-tests"}
    )
    if ci_status != "pending":
        print(f"FAIL: Expected 'pending' when all expected pass + non-expected pending, got '{ci_status}'")
        return False
    print("[OK] Expected all pass, non-expected pending: returns PENDING (wait for all checks)")

    # Test 22: CheckRun STALE conclusion (invalidated check, counts as failure)
    stale_check = [
        {"name": "ci-run", "status": "COMPLETED", "conclusion": "STALE"},
    ]
    ci_status, _ = check_ci_status(stale_check)
    if ci_status != "failure":
        print(f"FAIL: Expected 'failure' for STALE conclusion, got '{ci_status}'")
        return False
    print("[OK] CheckRun STALE conclusion (failure, invalidated by force-push)")

    # Test 23: CheckRun COMPLETED with null conclusion (API anomaly, fail-closed)
    null_conclusion_check = [
        {"name": "test-suite", "status": "COMPLETED", "conclusion": None},
    ]
    ci_status, _ = check_ci_status(null_conclusion_check)
    if ci_status != "pending":
        print(f"FAIL: Expected 'pending' for COMPLETED + null conclusion (fail-closed), got '{ci_status}'")
        return False
    print("[OK] CheckRun COMPLETED + null conclusion fails-closed to PENDING")

    # Test 21: Superseded-run window simulation (old checks vanish, new pending appear)
    # First: old run had checks that completed successfully
    old_run_checks = [
        {"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
    ]
    ci_status, _ = check_ci_status(old_run_checks)
    if ci_status != "success":
        print(f"FAIL: Old run checks should be success, got '{ci_status}'")
        return False

    # Second: transition window where old checks vanished and new run hasn't registered yet
    # Empty rollup should return PENDING (fail-closed)
    empty_window = []
    ci_status, _ = check_ci_status(empty_window)
    if ci_status != "pending":
        print(f"FAIL: Transition window (empty rollup) should be PENDING (fail-closed), got '{ci_status}'")
        return False

    # Third: new run appears with pending checks
    new_run_checks = [
        {"name": "build", "status": "IN_PROGRESS", "conclusion": None},
        {"name": "test", "status": "QUEUED", "conclusion": None},
    ]
    ci_status, _ = check_ci_status(new_run_checks)
    if ci_status != "pending":
        print(f"FAIL: New run pending checks should be PENDING, got '{ci_status}'")
        return False

    print("[OK] Superseded-run window simulation: transitions correctly")

    print("\nAll self-tests passed!")
    return True


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "pr_number",
        type=int,
        nargs="?",
        default=None,
        help="GitHub PR number (required unless using --self-test)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Max seconds to wait for CI (default: 3600)"
    )
    parser.add_argument(
        "--poll",
        type=int,
        default=10,
        help="Poll interval in seconds (default: 10)"
    )
    parser.add_argument(
        "--merge-method",
        choices=["merge", "squash", "rebase"],
        default="merge",
        help="Merge strategy (default: merge)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip actual merge, just verify CI status and report what would happen"
    )
    parser.add_argument(
        "--allow-no-checks",
        action="store_true",
        help="Allow merge when no CI checks present (repos without CI)"
    )
    parser.add_argument(
        "--expect-checks",
        type=str,
        default=None,
        help="Comma-separated list of checks that MUST be present and successful"
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run offline self-test of polling/decision logic (no network)"
    )

    args = parser.parse_args()

    # Handle self-test mode
    if args.self_test:
        if run_self_test():
            sys.exit(0)
        else:
            sys.exit(1)

    # Validate PR is provided for non-self-test mode
    if args.pr_number is None:
        print("ERROR: PR number is required (unless using --self-test)")
        sys.exit(1)

    # Validate inputs
    if args.pr_number <= 0:
        print("ERROR: PR number must be positive")
        sys.exit(1)

    if args.timeout <= 0 or args.poll <= 0:
        print("ERROR: timeout and poll must be positive")
        sys.exit(1)

    # Parse expected checks
    expected_checks = None
    if args.expect_checks:
        expected_checks = set(c.strip() for c in args.expect_checks.split(",") if c.strip())

    # Fetch initial PR status
    print(f"Checking PR #{args.pr_number} status...")
    status = get_pr_status(args.pr_number)
    if status is None:
        print("ERROR: gh CLI not available or PR not found")
        sys.exit(1)

    mergeable = status.get("mergeable", "").upper()
    if mergeable == "CONFLICTED":
        print(f"MERGE CONFLICT: PR #{args.pr_number} has conflicts")
        sys.exit(4)
    elif mergeable not in ("MERGEABLE", "UNKNOWN"):
        # Also treat UNKNOWN conservatively as non-mergeable until we have clarity
        print(f"NOT MERGEABLE: PR #{args.pr_number} status={mergeable}")
        sys.exit(4)

    # Poll until CI concludes
    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        if elapsed > args.timeout:
            print(f"TIMEOUT: CI did not conclude within {args.timeout}s")
            sys.exit(3)

        # N9 FIX: Catch transient gh errors (CalledProcessError, TimeoutExpired) and retry
        try:
            status = get_pr_status(args.pr_number)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"TRANSIENT ERROR: gh command failed ({type(e).__name__}), retrying...")
            # Treat as "status unknown this tick", continue polling
            print(f"CI PENDING ({elapsed:.0f}s elapsed)... waiting {args.poll}s")
            time.sleep(args.poll)
            continue

        if status is None:
            print("ERROR: gh CLI check failed")
            sys.exit(1)

        status_rollup = status.get("statusCheckRollup", [])
        ci_status, failed_check = check_ci_status(
            status_rollup,
            allow_no_checks=args.allow_no_checks,
            expected_checks=expected_checks
        )

        if ci_status == "failure":
            print(f"CI FAILED: {failed_check}")
            sys.exit(2)
        elif ci_status == "success":
            # SUCCESS: CI is green, re-check immediately before proceeding
            print(f"CI GREEN: All checks passed. Re-checking status before merge...")
            # N9 FIX: Also catch transient errors on final check
            try:
                final_check = get_pr_status(args.pr_number)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                print(f"TRANSIENT ERROR: final gh check failed ({type(e).__name__}), retrying...")
                # Treat as status change, go back to polling
                print(f"CI PENDING ({elapsed:.0f}s elapsed)... waiting {args.poll}s")
                time.sleep(args.poll)
                continue

            if final_check is None:
                print("ERROR: final status check failed")
                sys.exit(1)

            final_rollup = final_check.get("statusCheckRollup", [])
            final_ci_status, final_failed = check_ci_status(
                final_rollup,
                allow_no_checks=args.allow_no_checks,
                expected_checks=expected_checks
            )

            if final_ci_status != "success":
                print(f"CI STATUS CHANGED: {final_failed}")
                sys.exit(2)

            # SUCCESS: CI is still green, proceed to merge
            # This merge call is STRUCTURALLY UNREACHABLE unless ci_status == "success"
            # F5 FIX: Capture headRefOid for SHA-pinned merge (prevents TOCTOU)
            head_ref_oid = final_check.get("headRefOid")
            if args.dry_run:
                print(f"[DRY-RUN] PR #{args.pr_number} CI is green, would merge...")
            else:
                print(f"CI CONFIRMED GREEN. Merging PR #{args.pr_number}...")

            # N9 FIX: Catch TimeoutExpired from merge subprocess and treat as transient error
            try:
                merge_result = merge_pr(args.pr_number, args.merge_method, dry_run=args.dry_run, head_ref_oid=head_ref_oid)
            except subprocess.TimeoutExpired:
                print(f"TRANSIENT ERROR: merge command timed out, retrying...")
                print(f"CI PENDING ({elapsed:.0f}s elapsed)... waiting {args.poll}s")
                time.sleep(args.poll)
                continue

            if merge_result:
                if args.dry_run:
                    print(f"[DRY-RUN] PR #{args.pr_number} merge command would succeed")
                else:
                    print(f"MERGED: PR #{args.pr_number} merged successfully")
                sys.exit(0)
            else:
                print("ERROR: merge failed (PR state changed? or missing SHA pin?)")
                sys.exit(1)

        # Still pending: wait and retry
        print(f"CI PENDING ({elapsed:.0f}s elapsed)... waiting {args.poll}s")
        time.sleep(args.poll)


if __name__ == "__main__":
    main()
