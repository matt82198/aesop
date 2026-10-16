#!/usr/bin/env python3
"""PR trigger check: read-only detector for open PRs with no pull_request run on their head.
INDEX: Read-only single-shot detector (no polling, no remediation) for open non-draft PRs whose CURRENT head sha has no pull_request workflow run; one `gh pr list`/`gh pr view` plus one `gh run list --event pull_request --branch <head>` per PR; prints one line per PR (number, head sha, pull_request_run=yes|no, run status); exit 1 if any open non-draft PR lacks a run on its exact head, 0 otherwise, 2 on a gh error (fail closed)

Observed on aesop #1072 (head 8d925be6): the PR's head had no pull_request
workflow run, so auto-merge could never see a required check on that head.
The cause is unresolved; this tool only DETECTS it. It never updates a branch,
closes or reopens a PR, reruns a workflow or merges. Any remediation is a user
decision.

Usage:
    python tools/pr_trigger_check.py                 # all open PRs of the repo
    python tools/pr_trigger_check.py 1072 1074       # only the named PRs
    python tools/pr_trigger_check.py --repo owner/name

Exit codes: 0 no open non-draft PR is missing a run on its head; 1 at least one
is missing; 2 a gh call failed (nothing is reported as missing on a gh error).
"""
import argparse
import json
import subprocess
import sys

DEFAULT_REPO = "matt82198/aesop"
GH_TIMEOUT = 60
PR_FIELDS = "number,state,isDraft,headRefName,headRefOid"
RUN_FIELDS = "headSha,status,conclusion"
RUN_LIMIT = "50"
PR_LIST_LIMIT = "200"


class GhError(Exception):
    """A gh read failed; the tool maps this to exit 2."""


def gh(*args):
    """Run one read-only `gh` call. Module global so tests can monkeypatch it.

    Returns the parsed JSON on success, or a dict {"error": ..., "rc": ...} on
    any failure (non-zero exit, timeout, missing executable, unparseable output).
    """
    cmd = ["gh"] + list(args)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=GH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"error": "gh call timed out: %s" % " ".join(args), "rc": -1}
    except FileNotFoundError:
        return {"error": "gh executable not found", "rc": -1}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return {"error": detail or "gh exited %d" % result.returncode, "rc": result.returncode}
    try:
        return json.loads(result.stdout or "null")
    except (json.JSONDecodeError, ValueError):
        return {"error": "gh returned non-JSON output for: %s" % " ".join(args), "rc": 0}


def gh_read(*args):
    """Call gh and raise GhError on any failure, so no failure reads as 'no run'."""
    result = gh(*args)
    if isinstance(result, dict) and "error" in result:
        raise GhError("gh %s failed: %s" % (" ".join(args[:2]), result["error"]))
    return result


def evaluate_pr(pr, runs):
    """Pure check: does `runs` contain a pull_request run on the PR's exact head sha?

    `runs` is the `gh run list --json headSha,status,conclusion` answer, newest
    first. Only the first run whose headSha equals headRefOid is reported.
    A PR counts (can fail the check) only when it is OPEN and not a draft.
    """
    head = (pr.get("headRefOid") or "").lower()
    match = None
    for run in runs or []:
        if (run.get("headSha") or "").lower() == head and head:
            match = run
            break
    is_open = pr.get("state", "OPEN") == "OPEN"
    is_draft = bool(pr.get("isDraft"))
    return {
        "number": pr["number"],
        "head": pr.get("headRefOid") or "",
        "state": pr.get("state", "OPEN"),
        "draft": is_draft,
        "has_run": match is not None,
        "run_status": match.get("status") if match else None,
        "counts": is_open and not is_draft,
    }


def format_line(row):
    """One ASCII line per PR."""
    run_part = ("pull_request_run=yes status=%s" % row["run_status"]
                if row["has_run"] else "pull_request_run=no status=none")
    return "#%d head=%s state=%s draft=%s %s" % (
        row["number"], row["head"], row["state"],
        "yes" if row["draft"] else "no", run_part)


def fetch_prs(repo, pr_numbers):
    """Explicit numbers: one `gh pr view` each. Default: one `gh pr list`."""
    if pr_numbers:
        return [gh_read("pr", "view", str(n), "--repo", repo, "--json", PR_FIELDS)
                for n in pr_numbers]
    return gh_read("pr", "list", "--repo", repo, "--state", "open",
                   "--limit", PR_LIST_LIMIT, "--json", PR_FIELDS)


def fetch_runs(repo, branch):
    """One `gh run list` per PR, filtered on the PR head branch."""
    return gh_read("run", "list", "--repo", repo, "--event", "pull_request",
                   "--branch", branch, "--limit", RUN_LIMIT, "--json", RUN_FIELDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pr_numbers", nargs="*", type=int,
                        help="PR numbers to check (default: every open PR)")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name (default %s)" % DEFAULT_REPO)
    args = parser.parse_args(argv)

    try:
        prs = fetch_prs(args.repo, args.pr_numbers)
        rows = []
        for pr in prs or []:
            runs = fetch_runs(args.repo, pr["headRefName"])
            rows.append(evaluate_pr(pr, runs))
    except GhError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    for row in rows:
        print(format_line(row))
    missing = [row for row in rows if row["counts"] and not row["has_run"]]
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
