#!/usr/bin/env python3
"""PR sweep: session-independent nudger for armed-but-stuck PRs.
INDEX: Session-independent PR sweep (closes STATE.md item 9 residual, PR #871 gap): per open non-draft PR, arms missing GitHub native auto-merge (`gh pr merge <N> --auto --squash`, never an admin-override merge), updates BEHIND branches capped at 2/run oldest-first (skips PRs touched in the last 20 min), and signals stuck-red (required check failing, head older than 30 min) or DIRTY PRs as `pr.red`/`pr.dirty` events to the conductor signal-hub queue, deduped per (pr, head, type); `--dry-run` prints the plan only, `--json` adds a machine summary; exit 2 if `gh auth` is missing; never rebases, pushes code, or merges.

Gap this closes: a PR that goes RED (or falls BEHIND, or loses its auto-merge
arming) after auto-merge is armed previously had no session-independent actor --
only a live orchestrator session's Monitor tool noticed. This tool is the
actor: it is meant to be invoked on a schedule (daemons/run-watchdog.sh, see
daemons/CLAUDE.md) rather than from an interactive session, exactly like
tools/merge_queue.py is the scheduled actor for merging.

What it does, per open non-draft PR of the target repo:
  (a) autoMergeRequest is null  -> arm native auto-merge:
      `gh pr merge <N> --auto --squash` (literal PR number, never an admin-override merge).
  (b) mergeStateStatus == BEHIND -> `gh api repos/<repo>/pulls/<N>/update-branch
      -X PUT`. At most 2 per run (hosted-runner capacity), oldest PR
      (by createdAt) first, skipping any PR whose `updatedAt` is under 20
      minutes old (avoids hammering a PR an update-branch call just touched).
  (c) a REQUIRED check (gh's own `--required` filter, i.e. whatever branch
      protection marks required) is failing on the current head AND that
      check's own startedAt/completedAt timestamp is older than 30 minutes
      (the closest cheap proxy this tool has for "how long has the head been
      red", without an extra per-PR commit-timestamp API call) -> append ONE
      `pr.red` event to the signal-hub queue, deduped on (pr, head).
  (d) mergeStateStatus == DIRTY -> append ONE `pr.dirty` event the same way,
      deduped on (pr, head).

Never rebases, never pushes code, never merges -- purely observe + arm + nudge
+ signal. A human or a dispatched fix lane (never the orchestrator's own main
thread, per the merge-train dispatch rule) consumes the signal-hub queue.

The signal-hub queue path defaults to
`<conductor_root>/state/signal-hub-queue.jsonl` (tools/common.py::
get_conductor_root(), the same CONDUCTOR_ROOT resolution daemons/run-watchdog.sh
uses -- no hardcoded personal path); override with --queue-path or the
AESOP_SIGNAL_QUEUE env var.

Usage:
    python tools/pr_sweep.py [--dry-run] [--json] [--repo OWNER/NAME]
                              [--queue-path FILE] [--behind-cap N]

Exit codes: 0 = swept (incl. no-op), 2 = gh auth missing or PR listing failed.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
# Sanctioned sibling-import guard (same unconditional top-level form as
# auto_merge.py / merge_queue.py); tools/sibling_import_check.py recognizes
# only this form, not a conditional "if ... not in sys.path" variant.
sys.path.insert(0, str(_TOOLS_DIR))

from common import get_conductor_root  # noqa: E402

DEFAULT_BEHIND_CAP = 2


def _flush_receipts_if_possible():
    """Try to flush spooled receipts. This is best-effort; errors are logged but
    do not block the sweep."""
    try:
        import receipt_flush as rf  # noqa: F401, E402
        # Flush receipts using the aesop repo (origin)
        aesop_root = Path.home() / "aesop"
        if aesop_root.exists() and (aesop_root / "tools" / "receipt_flush.py").exists():
            rf.main(["--repo", str(aesop_root)])
    except Exception:
        # Flush errors are best-effort; do not block the sweep
        pass
BEHIND_THROTTLE_MIN = 20
RED_MIN_AGE_MIN = 30
GH_TIMEOUT = 60


class GhError(Exception):
    """A gh call failed in a way the caller must not silently swallow."""


def gh(*args):
    """Run one `gh` call. Module global so tests can monkeypatch it directly
    (same pattern as merge_train.gh / merge_queue's imported gh).

    Tries to parse stdout as JSON regardless of exit code first: `gh pr
    checks --json` uses non-zero exit codes (1 = some failed, 8 = some
    pending) as STATUS, not failure, so gating on returncode alone would
    misreport a successful, well-formed query as an error. Only when stdout
    is not valid JSON and the process also exited non-zero is this reported
    as an error.
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
    out = (result.stdout or "").strip()
    if out:
        try:
            return json.loads(out)
        except (json.JSONDecodeError, ValueError):
            pass
    if result.returncode != 0:
        return {"error": (result.stderr or out or "").strip(), "rc": result.returncode}
    return out


def gh_auth_ok():
    result = gh("auth", "status")
    return not (isinstance(result, dict) and "error" in result)


def resolve_repo(repo_override):
    if repo_override:
        return repo_override
    result = gh("repo", "view", "--json", "nameWithOwner")
    if isinstance(result, dict) and "nameWithOwner" in result:
        return result["nameWithOwner"]
    return None


def default_queue_path():
    env = os.environ.get("AESOP_SIGNAL_QUEUE")
    if env:
        return Path(env)
    return get_conductor_root() / "state" / "signal-hub-queue.jsonl"


PR_LIST_FIELDS = (
    "number,title,isDraft,autoMergeRequest,mergeStateStatus,updatedAt,"
    "createdAt,headRefOid,headRefName,url"
)


def list_open_prs(repo=None):
    args = ["pr", "list", "--state", "open", "--limit", "100", "--json", PR_LIST_FIELDS]
    if repo:
        args += ["--repo", repo]
    result = gh(*args)
    if isinstance(result, dict) and "error" in result:
        raise GhError(result["error"])
    if isinstance(result, list):
        return result
    return []


def get_required_checks(pr_number, repo=None):
    """Return the REQUIRED checks for pr_number, or [] if gh can't answer
    (no branch protection, transient error, etc.) -- treated as "no evidence
    of a stuck red", which is the fail-safe-against-false-alarms direction
    for a tool that only ever appends advisory signal rows.
    """
    args = ["pr", "checks", str(pr_number), "--required", "--json",
            "name,bucket,completedAt,startedAt"]
    if repo:
        args += ["--repo", repo]
    result = gh(*args)
    if isinstance(result, list):
        return result
    return []


def arm_auto_merge(pr_number, repo=None):
    args = ["pr", "merge", str(pr_number), "--auto", "--squash"]
    if repo:
        args += ["--repo", repo]
    result = gh(*args)
    if isinstance(result, dict) and "error" in result:
        return False, result["error"]
    return True, ""


def update_branch(pr_number, repo):
    endpoint = "repos/%s/pulls/%s/update-branch" % (repo, pr_number)
    result = gh("api", endpoint, "-X", "PUT")
    if isinstance(result, dict) and "error" in result:
        return False, result["error"]
    return True, ""


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def minutes_since(iso_ts, now):
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 60.0


def read_existing_keys(queue_path, event_type):
    """(pr, head) pairs already recorded for event_type -- the dedupe key."""
    keys = set()
    if not queue_path.exists():
        return keys
    try:
        text = queue_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return keys
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if obj.get("type") == event_type:
            keys.add((obj.get("pr"), obj.get("head")))
    return keys


def append_signal_event(queue_path, event):
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with open(queue_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=False) + "\n")


def build_plan(open_prs, repo, now, queue_path, behind_cap=DEFAULT_BEHIND_CAP):
    """Pure(ish) planning pass: one gh call per PR for required checks, no
    mutation. Returns a list of action dicts consumed by execute_plan().
    """
    actions = []

    # (a) arm missing auto-merge
    for p in open_prs:
        if not p.get("autoMergeRequest"):
            actions.append({"kind": "arm", "pr": p["number"], "url": p.get("url", "")})

    # (b) BEHIND, cap behind_cap, oldest (by createdAt) first, 20-min throttle
    behind_eligible = []
    for p in open_prs:
        if p.get("mergeStateStatus") != "BEHIND":
            continue
        age = minutes_since(p.get("updatedAt"), now)
        if age is not None and age < BEHIND_THROTTLE_MIN:
            continue
        behind_eligible.append(p)
    behind_eligible.sort(key=lambda p: p.get("createdAt") or "")
    for p in behind_eligible[:behind_cap]:
        actions.append({"kind": "update-branch", "pr": p["number"], "url": p.get("url", "")})

    # (c) stuck red, (d) DIRTY
    red_keys = read_existing_keys(queue_path, "pr.red")
    dirty_keys = read_existing_keys(queue_path, "pr.dirty")
    for p in open_prs:
        num = p["number"]
        head = p.get("headRefOid", "") or ""

        if p.get("mergeStateStatus") == "DIRTY" and (num, head) not in dirty_keys:
            actions.append({"kind": "signal", "type": "pr.dirty", "pr": num,
                             "head": head, "url": p.get("url", ""), "checks": []})

        checks = get_required_checks(num, repo)
        failing = [c for c in checks if c.get("bucket") == "fail"]
        if not failing:
            continue
        ages = [a for a in (minutes_since(c.get("startedAt") or c.get("completedAt"), now)
                             for c in failing) if a is not None]
        if not ages or min(ages) < RED_MIN_AGE_MIN:
            continue
        if (num, head) in red_keys:
            continue
        actions.append({"kind": "signal", "type": "pr.red", "pr": num, "head": head,
                         "url": p.get("url", ""), "checks": [c.get("name", "") for c in failing]})

    return actions


def execute_plan(actions, repo, dry_run, queue_path):
    lines = []
    results = {"armed": 0, "updated": 0, "signaled": 0, "errors": []}
    for a in actions:
        if a["kind"] == "arm":
            lines.append("ARM #%s (auto-merge missing) %s" % (a["pr"], a.get("url", "")))
            if not dry_run:
                ok, err = arm_auto_merge(a["pr"], repo)
                if ok:
                    results["armed"] += 1
                else:
                    results["errors"].append("#%s arm failed: %s" % (a["pr"], err))
        elif a["kind"] == "update-branch":
            lines.append("UPDATE-BRANCH #%s (BEHIND) %s" % (a["pr"], a.get("url", "")))
            if not dry_run:
                ok, err = update_branch(a["pr"], repo)
                if ok:
                    results["updated"] += 1
                else:
                    results["errors"].append("#%s update-branch failed: %s" % (a["pr"], err))
        elif a["kind"] == "signal":
            checks_str = ",".join(a.get("checks") or [])
            lines.append("SIGNAL %s #%s head=%s checks=[%s] %s" % (
                a["type"], a["pr"], (a.get("head") or "")[:12], checks_str, a.get("url", "")))
            if not dry_run:
                event = {
                    "ts": iso_now(), "type": a["type"], "repo": repo, "pr": a["pr"],
                    "head": a.get("head", ""), "checks": a.get("checks") or [],
                    "url": a.get("url", ""),
                }
                append_signal_event(queue_path, event)
                results["signaled"] += 1
    return lines, results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan, act on nothing")
    parser.add_argument("--json", action="store_true", help="also print a JSON summary")
    parser.add_argument("--repo", help="owner/name (default: resolved via `gh repo view`)")
    parser.add_argument("--queue-path", help="override signal-hub queue path (testing)")
    parser.add_argument("--behind-cap", type=int, default=DEFAULT_BEHIND_CAP,
                         help="max update-branch calls per run (default 2)")
    args = parser.parse_args(argv)

    if not gh_auth_ok():
        print("ERROR: gh auth missing or invalid -- run `gh auth login` first", file=sys.stderr)
        return 2

    # Flush any spooled receipts from prior pushes. This is the
    # session-independent actor that completes receipt publication.
    # Errors are logged but do not block the sweep.
    _flush_receipts_if_possible()

    repo = resolve_repo(args.repo)
    queue_path = Path(args.queue_path) if args.queue_path else default_queue_path()

    try:
        prs = list_open_prs(repo)
    except GhError as exc:
        print("ERROR: failed to list PRs: %s" % exc, file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    draft_count = sum(1 for p in prs if p.get("isDraft"))
    open_prs = [p for p in prs if not p.get("isDraft")]

    actions = build_plan(open_prs, repo, now, queue_path, behind_cap=args.behind_cap)
    lines, results = execute_plan(actions, repo, args.dry_run, queue_path)

    prefix = "[DRY-RUN] " if args.dry_run else ""
    for line in lines:
        print(prefix + line)
    if not lines:
        print(prefix + "nothing to do (%d open PR(s) checked)" % len(open_prs))
    if draft_count:
        print("%sSKIP %d draft PR(s)" % (prefix, draft_count))

    if args.json:
        print(json.dumps({
            "dry_run": args.dry_run, "repo": repo, "open_prs": len(open_prs),
            "draft_skipped": draft_count, "plan": lines, "results": results,
        }, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
