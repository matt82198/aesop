#!/usr/bin/env python3
"""Measure agreement between locally-emitted receipts and hosted CI check-runs.

INDEX: Receipt-gate measurement tool (increment 4 precondition): for a window of recent merged/open PRs, find each head sha's verified receipt (via verify_receipt library), fetch the hosted CI conclusion per matrix part (check-run results from `gh api repos/owner/repo/commits/sha/check-runs`), and report agreement: parts compared, agreements, disagreements (listing sha + part + local vs CI), agreement %, verdict against configurable threshold. Default matrix: py-shard-0..3. Parts mapping: receipt py-shard-N -> ci (N) check-run. `--json` for structured output; `--require N` exits 1 if agreement < threshold (default exits 0 always). TDD: fixtures only, no network.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))

import verify_receipt as vr  # noqa: E402


# Mapping from receipt part names to hosted check-run names
PART_TO_CHECKRUN = {
    "py-shard-0": "ci (0)",
    "py-shard-1": "ci (1)",
    "py-shard-2": "ci (2)",
    "py-shard-3": "ci (3)",
}

# Only these parts are compared (hosted checks exist for them)
COMPARABLE_PARTS = set(PART_TO_CHECKRUN.keys())


def _exit_code_to_status(exit_code):
    """Convert exit code to status string."""
    return "success" if exit_code == 0 else "failed" if exit_code != 0 else "unknown"


def _conclusion_to_status(conclusion):
    """Convert GitHub check-run conclusion to status string."""
    conclusion = conclusion or "unknown"
    if conclusion in ("success", "neutral"):
        return "success"
    elif conclusion in ("failure", "cancelled", "timed_out", "action_required"):
        return "failed"
    else:
        return conclusion


def _gh_api(path):
    """Call gh api and return parsed JSON, or None on error."""
    try:
        result = subprocess.run(
            ["gh", "api", path],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (ValueError, subprocess.TimeoutExpired):
        return None


def fetch_check_runs_for_sha(sha, repo_slug):
    """Fetch check-run results for a sha. Returns {check_run_name: {conclusion: ...}}."""
    payload = _gh_api(f"repos/{repo_slug}/commits/{sha}/check-runs?per_page=100")
    if payload is None:
        return {}

    results = {}
    for run in payload.get("check_runs", []):
        name = run.get("name")
        if name:
            results[name] = {
                "conclusion": run.get("conclusion"),
                "status": run.get("status"),
            }
    return results


def measure_agreement(pr_list, fetch_receipt_func=None, fetch_check_runs_func=None, repo_slug="matt82198/aesop"):
    """Measure agreement between receipts and hosted checks.

    Returns (agreements, disagreements, missing_receipts).

    agreements: list of (sha, part_name) tuples that agree
    disagreements: list of {"sha": sha, "part": name, "local": status, "hosted": status}
    missing_receipts: list of shas with no receipt
    """
    if fetch_receipt_func is None:
        fetch_receipt_func = vr.fetch_receipt_for_head
    if fetch_check_runs_func is None:
        fetch_check_runs_func = fetch_check_runs_for_sha

    agreements = []
    disagreements = []
    missing = []

    for pr in pr_list:
        sha = pr.get("sha") or pr.get("headRefOid")
        if not sha:
            continue

        # Fetch receipt
        receipt_env, _ = fetch_receipt_func(sha, repo_slug)
        if receipt_env is None:
            missing.append(sha)
            continue

        receipt = receipt_env.get("receipt", {})

        # Fetch hosted checks
        check_runs = fetch_check_runs_func(sha, repo_slug)

        # Build part name -> local status map
        local_status = {}
        for part in receipt.get("parts", []):
            part_name = part.get("name")
            if part_name in COMPARABLE_PARTS:
                exit_code = part.get("exit_code")
                local_status[part_name] = _exit_code_to_status(exit_code)

        # Compare each comparable part
        for part_name in COMPARABLE_PARTS:
            checkrun_name = PART_TO_CHECKRUN[part_name]
            local = local_status.get(part_name)

            if local is None:
                # Receipt missing this part entirely
                disagreements.append({
                    "sha": sha,
                    "part": part_name,
                    "local": "missing",
                    "hosted": _conclusion_to_status(check_runs.get(checkrun_name, {}).get("conclusion"))
                })
                continue

            # Compare with hosted
            hosted_conclusion = check_runs.get(checkrun_name, {}).get("conclusion")
            hosted = _conclusion_to_status(hosted_conclusion)

            if local == hosted:
                agreements.append((sha, part_name))
            else:
                disagreements.append({
                    "sha": sha,
                    "part": part_name,
                    "local": local,
                    "hosted": hosted
                })

    return agreements, disagreements, missing


def build_parser():
    p = argparse.ArgumentParser(
        prog="receipt_agreement.py",
        description="Measure agreement between local receipts and hosted CI checks."
    )
    p.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Number of recent PRs to sample (default: 30)"
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )
    p.add_argument(
        "--require",
        type=int,
        default=None,
        help="Fail (exit 1) if agreement < threshold %% (e.g. 90)"
    )
    p.add_argument(
        "--repo",
        default="matt82198/aesop",
        help="GitHub repo slug (owner/repo)"
    )
    return p


def main():
    args = build_parser().parse_args()

    # Fetch recent PRs
    try:
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--state", "all",
                "--limit", str(args.limit),
                "--json", "number,headRefOid,mergeCommit"
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60
        )
        if result.returncode != 0:
            print(f"Error fetching PRs: {result.stderr}", file=sys.stderr)
            return 1

        pr_list = json.loads(result.stdout)
    except (ValueError, subprocess.TimeoutExpired) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Convert to our format (use headRefOid as sha)
    pr_list = [{"sha": pr.get("headRefOid")} for pr in pr_list if pr.get("headRefOid")]

    # Measure agreement
    agreements, disagreements, missing = measure_agreement(
        pr_list,
        repo_slug=args.repo
    )

    # Calculate statistics
    comparable_count = len(agreements) + len(disagreements)
    agreement_count = len(agreements)
    if comparable_count > 0:
        agreement_pct = 100 * agreement_count // comparable_count
    else:
        agreement_pct = 0

    # Build output
    summary = {
        "parts_compared": comparable_count,
        "agreements": agreement_count,
        "disagreements": len(disagreements),
        "missing_receipts": len(missing),
        "agreement_percentage": agreement_pct,
        "threshold_pct": args.require or 90,
        "verdict": "PASS" if agreement_pct >= (args.require or 90) else "FAIL",
    }

    if args.json:
        # JSON output
        output = {
            "summary": summary,
            "disagreements": disagreements,
            "missing_receipts": missing[:10],  # Show first 10
        }
        print(json.dumps(output, indent=2))
    else:
        # Human-readable output
        print("\n=== Receipt Agreement Measurement ===")
        print(f"Parts compared: {comparable_count}")
        print(f"Agreements: {agreement_count}")
        print(f"Disagreements: {len(disagreements)}")
        print(f"Missing receipts: {len(missing)}")
        print(f"Agreement: {agreement_pct}% (threshold: {args.require or 90}%)")
        print(f"Verdict: {summary['verdict']}")

        if disagreements:
            print("\n--- Disagreements ---")
            for d in disagreements[:20]:  # Show first 20
                print(f"  {d['sha'][:12]} {d['part']}: local={d['local']}, hosted={d['hosted']}")

        if missing:
            print("\n--- Missing receipts (showing first 10) ---")
            for sha in missing[:10]:
                print(f"  {sha[:12]}")

    # Exit code
    if args.require is not None and agreement_pct < args.require:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
