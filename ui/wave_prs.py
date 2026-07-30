#!/usr/bin/env python3
"""Wave PR board collector — read-only snapshot of open PRs + feat/* branches.

Gathers the current wave's pull-request / branch status so the dashboard can
show CI, mergeability, age, and the top blocker at a glance without a trip to
GitHub. Read-only: only `gh pr list` and `git for-each-ref` are ever run, both
with a short timeout and cwd pinned to the repo root.

Robustness (wave-27 lesson): every subprocess text read passes
encoding='utf-8', errors='replace'. Missing `gh`, an un-authenticated `gh`, a
non-git cwd, or simply zero PRs all degrade to a well-formed empty payload
(available flag + human error), never an exception that 500s the endpoint.

A tiny module-level cache (default 5s) keeps repeated polls snappy — `gh pr
list` hits the network, so we do not want to run it on every dashboard tick.
"""
import json
import os
import subprocess
import time

import config

# gh fields we request. statusCheckRollup carries per-check conclusions; we
# roll them up into one of passing/failing/pending/none client-agnostic states.
_GH_FIELDS = (
    "number,title,headRefName,mergeable,isDraft,url,createdAt,"
    "reviewDecision,statusCheckRollup"
)

_CACHE_TTL_SECONDS = 5.0
_SUBPROCESS_TIMEOUT_SECONDS = 12

# Module-level cache: (expires_at_epoch, payload_dict). Guarded by the GIL for
# the simple read-modify-write here; the collector is I/O bound, not a hot loop.
_cache = {"expires": 0.0, "payload": None}


def _gh_bin():
    """Path to the GitHub CLI binary.

    Defaults to `gh` (resolved on PATH). Override with AESOP_GH_BIN to point at
    a specific install or a wrapper — read at call time so it stays live.
    """
    return os.environ.get("AESOP_GH_BIN", "gh")


def _run(cmd):
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


def _rollup_ci(status_check_rollup):
    """Reduce gh's statusCheckRollup list to one state.

    Returns one of: 'passing', 'failing', 'pending', 'none'. Color-independent
    label; the frontend pairs it with an icon + text so status never rides on
    color alone.
    """
    if not status_check_rollup:
        return "none"
    saw_pending = False
    for check in status_check_rollup:
        # CheckRun uses `status`/`conclusion`; StatusContext uses `state`.
        state = (check.get("state") or "").upper()
        status = (check.get("status") or "").upper()
        conclusion = (check.get("conclusion") or "").upper()

        if state in ("FAILURE", "ERROR") or conclusion in (
            "FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE",
        ):
            return "failing"
        if state == "PENDING" or status in ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING"):
            saw_pending = True
    return "pending" if saw_pending else "passing"


def _blocker(ci, mergeable, is_draft, review_decision):
    """Top single blocker for a PR, or None when it looks ready to merge."""
    if is_draft:
        return "Draft — not ready for review"
    if ci == "failing":
        return "CI failing"
    if (mergeable or "").upper() == "CONFLICTING":
        return "Merge conflict"
    if ci == "pending":
        return "CI pending"
    if (review_decision or "").upper() == "CHANGES_REQUESTED":
        return "Changes requested"
    if (review_decision or "").upper() == "REVIEW_REQUIRED":
        return "Review required"
    return None


def _collect_prs():
    """Return (available, error, prs). Never raises."""
    ok, out, err = _run([
        _gh_bin(), "pr", "list", "--state", "open", "--limit", "50", "--json", _GH_FIELDS,
    ])
    if not ok:
        low = (err or "").lower()
        if "command not found" in low:
            return False, "GitHub CLI (gh) is not installed.", []
        if "auth" in low or "not logged" in low or "gh auth login" in low:
            return False, "GitHub CLI is not authenticated (run: gh auth login).", []
        if "not a git" in low or "no git repository" in low:
            return False, "Not a GitHub repository.", []
        # Any other failure: surface a trimmed reason, degrade to empty.
        reason = (err or "gh pr list failed").strip().splitlines()
        return False, reason[0] if reason else "gh pr list failed", []

    try:
        raw = json.loads(out) if out.strip() else []
    except (ValueError, TypeError):
        return False, "Could not parse gh output.", []

    prs = []
    for item in raw:
        ci = _rollup_ci(item.get("statusCheckRollup"))
        mergeable = item.get("mergeable") or "UNKNOWN"
        is_draft = bool(item.get("isDraft"))
        review = item.get("reviewDecision") or ""
        prs.append({
            "number": item.get("number"),
            "title": item.get("title") or "(untitled)",
            "branch": item.get("headRefName") or "",
            "url": item.get("url") or "",
            "ci": ci,
            "mergeable": mergeable,
            "is_draft": is_draft,
            "review_decision": review,
            "created_at": item.get("createdAt") or "",
            "blocker": _blocker(ci, mergeable, is_draft, review),
            "has_pr": True,
        })
    return True, None, prs


def _collect_branch_only(pr_branches):
    """feat/* branches (local + remote) that have no open PR yet.

    Best-effort: a git failure just yields nothing extra (PRs still render).
    """
    ok, out, _err = _run([
        "git", "for-each-ref", "--format=%(refname:short)",
        "refs/heads/feat/", "refs/remotes/origin/feat/",
    ])
    if not ok:
        return []
    seen = set()
    rows = []
    for line in out.splitlines():
        name = line.strip()
        if not name:
            continue
        # Normalize origin/feat/x -> feat/x so local+remote of one branch dedupe.
        branch = name[len("origin/"):] if name.startswith("origin/") else name
        if branch in seen or branch in pr_branches:
            continue
        seen.add(branch)
        rows.append({
            "number": None,
            "title": branch,
            "branch": branch,
            "url": "",
            "ci": "none",
            "mergeable": "UNKNOWN",
            "is_draft": False,
            "review_decision": "",
            "created_at": "",
            "blocker": "No PR opened yet",
            "has_pr": False,
        })
    return rows


def get_wave_prs(force=False):
    """Snapshot of open PRs + PR-less feat/* branches for the PR board.

    Shape:
        {
          "available": bool,      # False when gh is missing/unauthed
          "error": str | None,    # human reason when available is False
          "generated_at": str,    # ISO 8601 UTC
          "prs": [ {number, title, branch, url, ci, mergeable, is_draft,
                    review_decision, created_at, blocker, has_pr}, ... ]
        }

    Cached for _CACHE_TTL_SECONDS so rapid polls don't re-run gh. Never raises.
    """
    # Zero-key demo mode: gh is never invoked; serve the fabricated PR board
    # (now-relative timestamps) so strangers see a populated board. No-op in
    # default mode.
    import demo
    if demo.enabled():
        return demo.get_demo_wave_prs()

    now = time.time()
    if not force and _cache["payload"] is not None and now < _cache["expires"]:
        return _cache["payload"]

    available, error, prs = _collect_prs()
    if available:
        pr_branches = {p["branch"] for p in prs if p.get("branch")}
        prs = prs + _collect_branch_only(pr_branches)
        # Sort: PRs first (by number desc), then branch-only rows by name.
        prs.sort(key=lambda p: (p["number"] is None, -(p["number"] or 0), p["branch"]))

    payload = {
        "available": available,
        "error": error,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "prs": prs,
    }
    _cache["payload"] = payload
    _cache["expires"] = now + _CACHE_TTL_SECONDS
    return payload
