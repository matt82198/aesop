#!/usr/bin/env python3
"""Wave PR failure drill-down collector — CI job logs and failure details.

Provides structured access to GitHub CI job logs and failure details for a given
PR number. When the Wave PR Board shows a red PR row, the UI can drill down via
this endpoint to see which checks failed, their logs, and stderr excerpts.

Backend:
  - Shells `gh run view --json jobs` to list jobs for the latest run on a PR branch
  - For failing jobs, shells `gh api repos/{owner}/{repo}/actions/jobs/{id}/logs`
    to fetch the full log, extracts ~100-line tail excerpt
  - 5-second cache per PR to avoid hammering GitHub API
  - Degrades to {available:false, error:...} when gh is missing/unauthed (never 500)
  - Honors AESOP_GH_BIN override; all text uses encoding='utf-8', errors='replace'

Response shape when available:
  {
    "available": true,
    "error": null,
    "pr_number": <int>,
    "branch": "<str>",
    "latest_run": {
      "id": "<str>",
      "name": "<str>",
      "status": "completed" | "in_progress" | "queued",
      "conclusion": "success" | "failure" | "cancelled" | "timed_out" | null,
      "url": "<str>"
    },
    "jobs": [
      {
        "id": <int>,
        "name": "<str>",
        "status": "completed" | "in_progress" | "queued",
        "conclusion": "success" | "failure" | "cancelled" | "timed_out" | null,
        "url": "<str>",
        "log_excerpt": "<str>" | null  # ~100 lines, tail-truncated; null if fetch fails
      },
      ...
    ]
  }

When gh is missing/unauthed:
  {
    "available": false,
    "error": "<human reason>",
    "pr_number": <int>,
    "branch": "",
    "latest_run": null,
    "jobs": []
  }
"""
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import config

_CACHE_TTL_SECONDS = 5.0
_SUBPROCESS_TIMEOUT_SECONDS = 12
_LOG_EXCERPT_LINES = 100

# Module-level cache: {pr_number: (expires_at_epoch, payload_dict)}
_cache: Dict[int, Tuple[float, Dict[str, Any]]] = {}


def _gh_bin() -> str:
    """Path to the GitHub CLI binary.

    Defaults to `gh` (resolved on PATH). Override with AESOP_GH_BIN to point at
    a specific install or a wrapper — read at call time so it stays live.
    """
    return os.environ.get("AESOP_GH_BIN", "gh")


def _run(cmd: List[str]) -> Tuple[bool, str, str]:
    """Run a subprocess, return (ok, stdout, stderr).

    ok is False on a missing binary, a non-zero exit, or a timeout. Text is
    decoded utf-8 with errors='replace' so undecodable bytes never raise.
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(config.AESOP_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return False, "", f"{cmd[0]}: command not found"
    except subprocess.TimeoutExpired:
        return False, "", f"{cmd[0]}: timed out"
    except OSError as e:
        return False, "", f"{cmd[0]}: {e}"
    return proc.returncode == 0, proc.stdout or "", proc.stderr or ""


def _get_branch_for_pr(pr_number: int) -> Optional[str]:
    """Get the branch name for a PR number via gh pr view.

    Returns the headRefName (branch) for the PR, or None if lookup fails.
    """
    ok, out, _err = _run([
        _gh_bin(), "pr", "view", str(pr_number),
        "--json", "headRefName"
    ])
    if not ok or not out.strip():
        return None
    try:
        data = json.loads(out)
        return data.get("headRefName")
    except (ValueError, TypeError):
        return None


def _get_latest_run(branch: str) -> Optional[Dict[str, Any]]:
    """Get the latest workflow run for a branch.

    Shells: gh run list --branch <branch> --limit 1 --json id,name,status,conclusion,url
    Returns a run object or None if lookup fails.
    """
    ok, out, _err = _run([
        _gh_bin(), "run", "list", "--branch", branch,
        "--limit", "1",
        "--json", "id,name,status,conclusion,url"
    ])
    if not ok or not out.strip():
        return None
    try:
        data = json.loads(out)
        return data[0] if data else None
    except (ValueError, TypeError, IndexError):
        return None


def _get_jobs_for_run(run_id: str) -> List[Dict[str, Any]]:
    """Get all jobs in a run.

    Shells: gh run view <run_id> --json jobs --json id,name,status,conclusion,url
    Returns a list of job objects, or empty list if lookup fails.
    """
    ok, out, _err = _run([
        _gh_bin(), "run", "view", run_id,
        "--json", "jobs"
    ])
    if not ok or not out.strip():
        return []
    try:
        data = json.loads(out)
        # data should be {"jobs": [{...}, ...]}
        return data.get("jobs", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _get_job_log_excerpt(job_id: int) -> Optional[str]:
    """Fetch the full log for a job and extract ~100-line tail excerpt.

    Shells: gh api repos/{owner}/{repo}/actions/jobs/{job_id}/logs
    (Note: gh api auto-fills {owner}/{repo} when run inside a repo.)
    Extracts the last _LOG_EXCERPT_LINES lines as a tail, decompresses if gzipped.

    Returns the tail excerpt (plain text, utf-8 with errors='replace'), or None if fetch fails.
    """
    # Determine owner/repo from the current repo (gh api can infer them)
    ok, repo_info, _err = _run([
        "git", "remote", "get-url", "origin"
    ])
    if not ok:
        return None

    # Try to extract owner/repo from the remote URL
    # Supports: https://github.com/owner/repo.git or git@github.com:owner/repo.git
    match = re.search(r'(?:github\.com[:/]|git@github\.com:)([^/]+)/([^/.]+)', repo_info)
    if not match:
        return None

    owner, repo = match.group(1), match.group(2)

    # Fetch log via gh api
    api_path = f"repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
    ok, out, _err = _run([
        _gh_bin(), "api", api_path
    ])
    if not ok or not out.strip():
        return None

    # Split into lines and take the tail
    lines = out.splitlines()
    tail_lines = lines[-_LOG_EXCERPT_LINES:] if lines else []
    return "\n".join(tail_lines) if tail_lines else None


def _get_failure_details(pr_number: int) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Collect failure details for a PR.

    Returns: (available, error, latest_run, jobs)
      - available: bool — gh is working and authenticated
      - error: str | None — human reason if available is False
      - latest_run: run object or None
      - jobs: list of job objects with log_excerpt fields
    """
    # Get the branch for the PR
    branch = _get_branch_for_pr(pr_number)
    if not branch:
        return False, f"Could not find branch for PR #{pr_number}", None, []

    # Get the latest run for the branch
    run = _get_latest_run(branch)
    if not run:
        # Could mean: no runs at all, or gh failure
        # Assume success (no failure data to show) rather than error
        return True, None, None, []

    # Get all jobs in the run
    jobs = _get_jobs_for_run(run["id"])

    # For each job, fetch log excerpt if it failed
    for job in jobs:
        job_id = job.get("id")
        if job_id and job.get("conclusion") == "failure":
            excerpt = _get_job_log_excerpt(job_id)
            job["log_excerpt"] = excerpt
        else:
            job["log_excerpt"] = None

    return True, None, run, jobs


def get_wave_failure(pr_number: int, force: bool = False) -> Dict[str, Any]:
    """Get consolidated failure drill-down data for a PR.

    Shape:
        {
          "available": bool,
          "error": str | None,
          "pr_number": int,
          "branch": str,
          "latest_run": {...} | null,
          "jobs": [{id, name, status, conclusion, url, log_excerpt}, ...]
        }

    Cached for _CACHE_TTL_SECONDS so rapid polls don't re-run gh. Never raises.
    """
    now = time.time()
    if not force and pr_number in _cache:
        expires, payload = _cache[pr_number]
        if now < expires:
            return payload

    try:
        available, error, run, jobs = _get_failure_details(pr_number)

        # Get branch name for the response
        branch = ""
        if run:
            # Try to infer branch from run or PR
            branch_name = _get_branch_for_pr(pr_number)
            branch = branch_name or ""

        payload = {
            "available": available,
            "error": error,
            "pr_number": pr_number,
            "branch": branch,
            "latest_run": run,
            "jobs": jobs,
        }
    except Exception as e:
        print(f"[wave_failure] Uncaught error: {e}", file=sys.stderr)
        payload = {
            "available": False,
            "error": f"Internal error: {e}",
            "pr_number": pr_number,
            "branch": "",
            "latest_run": None,
            "jobs": [],
        }

    _cache[pr_number] = (now + _CACHE_TTL_SECONDS, payload)
    return payload
