#!/usr/bin/env python3
"""PR sweep: session-independent nudger for armed-but-stuck PRs.
INDEX: Session-independent PR sweep (closes STATE.md item 9 residual, PR #871 gap): per open non-draft PR, arms missing GitHub native auto-merge (`gh pr merge <N> --auto --squash`, never an admin-override merge), reports BEHIND branches capped at 2/run oldest-first (skips PRs touched in the last 20 min; never server-updated, lanes merge main locally), and signals stuck-red (required check failing, head older than 30 min) or DIRTY PRs as `pr.red`/`pr.dirty` events to the conductor signal-hub queue, deduped per (pr, head, type); retriggers CI (`gh workflow run ci.yml`) for armed PRs whose head has zero check runs 20+ min after push, and disarms + labels `needs-ci-trigger` if still zero 24 h later; `--dry-run` prints the plan only, `--json` adds a machine summary; exit 2 if `gh auth` is missing; never rebases, pushes code, or merges.

Gap this closes: a PR that goes RED (or falls BEHIND, or loses its auto-merge
arming) after auto-merge is armed previously had no session-independent actor --
only a live orchestrator session's Monitor tool noticed. This tool is the
actor: it is meant to be invoked on a schedule (daemons/run-watchdog.sh, see
daemons/CLAUDE.md) rather than from an interactive session, exactly like
tools/merge_queue.py is the scheduled actor for merging.

What it does, per open non-draft PR of the target repo:
  (a) autoMergeRequest is null  -> arm native auto-merge:
      `gh pr merge <N> --auto --squash` (literal PR number, never an admin-override merge).
  (b) mergeStateStatus == BEHIND -> REPORTED, never server-updated. A server-side
      branch update creates a merge head no box ran the gates on, so no receipt
      can exist for it; the lane merges origin/main locally instead. At most 2
      reported per run, oldest PR (by createdAt) first, skipping any PR whose
      `updatedAt` is under 20 minutes old.
  (c) a REQUIRED check (gh's own `--required` filter, i.e. whatever branch
      protection marks required) is failing on the current head AND that
      check's own startedAt/completedAt timestamp is older than 30 minutes
      (the closest cheap proxy this tool has for "how long has the head been
      red", without an extra per-PR commit-timestamp API call) -> append ONE
      `pr.red` event to the signal-hub queue, deduped on (pr, head).
  (d) mergeStateStatus == DIRTY -> append ONE `pr.dirty` event the same way,
      deduped on (pr, head).
  (e) ZERO-CHECK (verified class, PR #932/#934 2026-10-14): auto-merge is armed
      but the head has NO check rollup entries (`statusCheckRollup` empty) and
      the head commit is >= 20 minutes old. First sighting -> `gh workflow run
      ci.yml --ref <head-branch>` (ci.yml has workflow_dispatch) plus a
      marker comment carrying `sha=<head>`. Still zero check runs 24 h after
      that marker -> `gh pr merge <N> --disable-auto` (disarm, never merge)
      and label the PR `needs-ci-trigger` so it cannot merge silently. Both
      steps are idempotent on the marker comments, so a repeat sweep never
      re-dispatches or re-disarms the same head.

Never rebases, never pushes code, never merges -- purely observe + arm + nudge
+ signal + (e) retrigger/disarm. A human or a dispatched fix lane (never the
orchestrator's own main thread, per the merge-train dispatch rule) consumes
the signal-hub queue.

Fail closed: every gh read that decides an action runs BEFORE any write; a
failed read exits 3 having acted on nothing. A failed write is isolated to its
PR: it is logged with the PR number and reason, counted, and the sweep moves on
to the next PR; the sweep exits 1 at the END if any PR action failed. Every real action is appended to the sweep log
(`<conductor_root>/state/pr-sweep.log`, override --log-path or
AESOP_PR_SWEEP_LOG) with the PR number, reason, and outcome. --dry-run prints
the plan and touches neither GitHub nor the log.

The signal-hub queue path defaults to
`<conductor_root>/state/signal-hub-queue.jsonl` (tools/common.py::
get_conductor_root(), the same CONDUCTOR_ROOT resolution daemons/run-watchdog.sh
uses -- no hardcoded personal path); override with --queue-path or the
AESOP_SIGNAL_QUEUE env var.

Usage:
    python tools/pr_sweep.py [--dry-run] [--json] [--repo OWNER/NAME]
                              [--queue-path FILE] [--behind-cap N] [--log-path FILE]

Exit codes: 0 = swept (incl. no-op), 1 = one or more PR actions failed (all PRs
still attempted; failures listed on stderr), 2 = gh auth missing, PR listing failed,
or the listing hit the PR_LIST_LIMIT cap (partial sweep refused), 3 = a read that
decides an action failed (required-checks or zero-check state; nothing was acted on).
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
        # Flush for the repo this sweep runs from. The old hardcoded ~/aesop was a
        # stale non-git copy on the dev box, so the spool (which lives in the
        # pushing repo's git common dir) was never found and nothing was posted.
        aesop_root = Path(__file__).resolve().parent.parent
        if (aesop_root / "tools" / "receipt_flush.py").exists():
            rf.main(["--repo", str(aesop_root)])
    except Exception:
        # Flush errors are best-effort; do not block the sweep
        pass
BEHIND_THROTTLE_MIN = 20
RED_MIN_AGE_MIN = 30
GH_TIMEOUT = 60
# Zero-check retrigger (plan step e).
ZERO_CHECK_MIN_AGE_MIN = 20
ZERO_CHECK_DISARM_AFTER_MIN = 24 * 60
ZERO_CHECK_LABEL = "needs-ci-trigger"
CI_WORKFLOW_FILE = "ci.yml"  # has workflow_dispatch (ci.yml `on:`)
RETRY_MARK = "<!-- pr-sweep:zero-check-retry sha=%s -->"
DISARM_MARK = "<!-- pr-sweep:zero-check-disarm sha=%s -->"
REBASE_MARK = "<!-- pr-sweep:needs-rebase sha=%s -->"
LABEL_NEEDS_REBASE = "needs-rebase"


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
        # stdin=DEVNULL: the watchdog runs this hidden (CREATE_NO_WINDOW), where
        # the inherited STD_INPUT_HANDLE is invalid and subprocess.run() raises
        # OSError [WinError 6] before gh starts. gh never reads stdin here.
        result = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=GH_TIMEOUT,
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
    "createdAt,headRefOid,headRefName,url,mergeable"
)


# `gh pr list` returns at most --limit rows with no pagination, so the cap is
# set well above any real open-PR count. A listing that reaches the cap is
# refused (exit 2) rather than swept partially: the oldest PRs past the cap
# would otherwise be silently never armed or nudged.
PR_LIST_LIMIT = 1000
# gh's answer when a branch has no required checks configured. It is an
# empty set, not a read failure.
NO_REQUIRED_CHECKS_MARK = "no required checks"


def list_open_prs(repo=None):
    args = ["pr", "list", "--state", "open", "--limit", str(PR_LIST_LIMIT),
            "--json", PR_LIST_FIELDS]
    if repo:
        args += ["--repo", repo]
    result = gh(*args)
    if isinstance(result, dict) and "error" in result:
        raise GhError(result["error"])
    if isinstance(result, list):
        if len(result) >= PR_LIST_LIMIT:
            raise GhError("PR listing reached the %d-row cap; refusing a partial sweep"
                          % PR_LIST_LIMIT)
        return result
    return []


def get_required_checks(pr_number, repo=None):
    """Return the REQUIRED checks for pr_number. A branch with no required
    checks configured yields []. Any other gh error (timeout, auth, 5xx,
    unparseable output) raises GhError: an unreadable check state must never
    be read as "no failing check", because the caller would then skip the
    stuck-red signal on a guess. The caller turns GhError into exit 3 with
    nothing acted on.
    """
    args = ["pr", "checks", str(pr_number), "--required", "--json",
            "name,bucket,completedAt,startedAt"]
    if repo:
        args += ["--repo", repo]
    result = gh(*args)
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "error" in result:
        if NO_REQUIRED_CHECKS_MARK in str(result["error"]).lower():
            return []
        raise GhError("pr checks #%s failed: %s" % (pr_number, result["error"]))
    if result == "":
        return []
    if isinstance(result, str) and NO_REQUIRED_CHECKS_MARK in result.lower():
        return []
    raise GhError("pr checks #%s returned unexpected output: %r" % (pr_number, result))


def arm_auto_merge(pr_number, repo=None):
    args = ["pr", "merge", str(pr_number), "--auto", "--squash"]
    if repo:
        args += ["--repo", repo]
    result = gh(*args)
    if isinstance(result, dict) and "error" in result:
        return False, result["error"]
    return True, ""


BEHIND_REFUSAL = ("server-side branch update refused: server-authored merge heads are banned "
                  "(no box ran the gates); merge origin/main locally and push")


def behind_refusal(pr_number):
    """Refuse a BEHIND PR without any GitHub call. Returns (False, reason) so the caller
    records a refusal row; a server-side update is never issued by this tool."""
    return False, "#%s: %s" % (pr_number, BEHIND_REFUSAL)


def read_zero_check_state(pr_number, repo=None):
    """Fail-closed read for the zero-check decision. Any gh error, or a
    response without a statusCheckRollup list, raises GhError: an unreadable
    PR must never be treated as "zero checks" (that would dispatch or disarm
    on a guess).
    """
    args = ["pr", "view", str(pr_number), "--json", "statusCheckRollup,commits,comments"]
    if repo:
        args += ["--repo", repo]
    result = gh(*args)
    if not isinstance(result, dict):
        raise GhError("pr view #%s returned no JSON: %r" % (pr_number, result))
    if "error" in result:
        raise GhError("pr view #%s failed: %s" % (pr_number, result["error"]))
    if not isinstance(result.get("statusCheckRollup"), list):
        raise GhError("pr view #%s has no statusCheckRollup list" % pr_number)
    return result


def head_commit_time(state, head):
    for commit in state.get("commits") or []:
        if commit.get("oid") == head:
            return commit.get("committedDate")
    return None


def is_conflicted(pr):
    return pr.get("mergeStateStatus") == "DIRTY" or pr.get("mergeable") == "CONFLICTING"


def decide_zero_check(pr, state, now):
    """Pure decision for one armed PR. Returns ("retrigger", reason),
    ("disarm", reason), or None (leave the PR alone).
    """
    if state.get("statusCheckRollup"):
        return None
    head = pr.get("headRefOid") or ""
    if not head:
        return None
    age = minutes_since(head_commit_time(state, head), now)
    if age is None or age < ZERO_CHECK_MIN_AGE_MIN:
        return None
    # A conflicted PR gets no CI run because GitHub cannot build the merge
    # commit. Dispatching cannot fix that, and a 24h disarm would hide a
    # merge conflict as a CI problem, so it takes its own path: label
    # needs-rebase once, keep auto-merge armed (a rebase triggers runs).
    if is_conflicted(pr):
        for comment in state.get("comments") or []:
            if REBASE_MARK % head in (comment.get("body") or ""):
                return None
        return ("needs-rebase", "head %s conflicts with the base (merge state %s, mergeable %s); "
                "no CI run is expected until it is rebased"
                % (head[:12], pr.get("mergeStateStatus"), pr.get("mergeable")))
    retry_at = None
    for comment in state.get("comments") or []:
        body = comment.get("body") or ""
        if DISARM_MARK % head in body:
            return None
        if retry_at is None and RETRY_MARK % head in body:
            retry_at = comment.get("createdAt")
    if retry_at is None:
        return ("retrigger", "head %s has zero check runs %dm after push" % (head[:12], int(age)))
    since = minutes_since(retry_at, now)
    if since is None:
        return None
    if since >= ZERO_CHECK_DISARM_AFTER_MIN:
        return ("disarm", "still zero check runs %dm after CI dispatch on head %s"
                % (int(since), head[:12]))
    return None


def post_pr_comment(pr_number, repo, body):
    args = ["pr", "comment", str(pr_number), "--body", body]
    if repo:
        args += ["--repo", repo]
    result = gh(*args)
    if isinstance(result, dict) and "error" in result:
        raise GhError("pr comment #%s failed: %s" % (pr_number, result["error"]))


def gh_write(*args):
    result = gh(*args)
    if isinstance(result, dict) and "error" in result:
        raise GhError("gh %s failed: %s" % (" ".join(args[:2]), result["error"]))
    return result


def default_sweep_log_path():
    env = os.environ.get("AESOP_PR_SWEEP_LOG")
    if env:
        return Path(env)
    return get_conductor_root() / "state" / "pr-sweep.log"


def append_sweep_log(log_path, pr_number, action, reason, outcome):
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": iso_now(), "pr": pr_number, "action": action,
             "reason": reason, "outcome": outcome}
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=False) + "\n")


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
        actions.append({"kind": "behind", "pr": p["number"], "url": p.get("url", "")})

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

    # (e) zero-check armed PRs: reads only here, writes happen in execute_plan.
    for p in open_prs:
        if not p.get("autoMergeRequest"):
            continue
        state = read_zero_check_state(p["number"], repo)
        decision = decide_zero_check(p, state, now)
        if decision is None:
            continue
        kind, reason = decision
        actions.append({
            "kind": "zero-" + kind, "pr": p["number"], "url": p.get("url", ""),
            "head": p.get("headRefOid", "") or "", "branch": p.get("headRefName", "") or "",
            "reason": reason,
        })

    return actions


def record_pr_failure(results, lines, log_path, pr_number, kind, reason, err):
    """Per-PR write failure: logged with PR number + reason, counted, and the
    sweep continues with the next PR. main() exits non-zero after the summary."""
    message = "#%s %s failed: %s" % (pr_number, kind, err)
    results["errors"].append(message)
    lines.append("FAILED " + message)
    append_sweep_log(log_path, pr_number, kind, reason, "failed: %s" % err)


def execute_plan(actions, repo, dry_run, queue_path, log_path=None):
    lines = []
    results = {"armed": 0, "behind_refused": 0, "signaled": 0, "retriggered": 0,
               "disarmed": 0, "errors": []}
    for a in actions:
        if a["kind"] == "arm":
            lines.append("ARM #%s (auto-merge missing) %s" % (a["pr"], a.get("url", "")))
            if not dry_run:
                ok, err = arm_auto_merge(a["pr"], repo)
                if ok:
                    results["armed"] += 1
                else:
                    record_pr_failure(results, lines, log_path, a["pr"], "arm",
                                      "auto-merge missing", err)
        elif a["kind"] == "behind":
            lines.append("BEHIND #%s (refused: lane merges main locally) %s" % (a["pr"], a.get("url", "")))
            if not dry_run:
                _ok, reason = behind_refusal(a["pr"])
                results["behind_refused"] += 1
                append_sweep_log(log_path, a["pr"], "behind", "branch behind base", "refused: " + reason)
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
        elif a["kind"] in ("zero-retrigger", "zero-disarm", "zero-needs-rebase"):
            prefix_word = {"zero-retrigger": "ZERO-RETRIGGER", "zero-disarm": "ZERO-DISARM",
                           "zero-needs-rebase": "NEEDS-REBASE"}[a["kind"]]
            lines.append("%s #%s (%s) %s" % (prefix_word, a["pr"], a["reason"], a.get("url", "")))
            if dry_run:
                continue
            try:
                if a["kind"] == "zero-retrigger":
                    gh_write_args = ["workflow", "run", CI_WORKFLOW_FILE, "--ref", a["branch"]]
                    if repo:
                        gh_write_args += ["--repo", repo]
                    gh_write(*gh_write_args)
                    body = (RETRY_MARK % a["head"]) + "\n" + (
                        "pr-sweep: head `%s` had zero check runs after %d+ min; dispatched "
                        "`%s` on `%s`. If still no check runs 24h from now, auto-merge is "
                        "disarmed and this PR is labelled `%s`." % (
                            a["head"][:12], ZERO_CHECK_MIN_AGE_MIN, CI_WORKFLOW_FILE,
                            a["branch"], ZERO_CHECK_LABEL))
                    post_pr_comment(a["pr"], repo, body)
                    results["retriggered"] += 1
                    outcome = "dispatched %s on %s; marker comment posted" % (
                        CI_WORKFLOW_FILE, a["branch"])
                elif a["kind"] == "zero-needs-rebase":
                    label_args = ["label", "create", LABEL_NEEDS_REBASE, "--force",
                                  "--color", "D93F0B",
                                  "--description", "PR conflicts with main; rebase needed"]
                    if repo:
                        label_args += ["--repo", repo]
                    gh_write(*label_args)
                    edit_args = ["pr", "edit", str(a["pr"]), "--add-label", LABEL_NEEDS_REBASE]
                    if repo:
                        edit_args += ["--repo", repo]
                    gh_write(*edit_args)
                    body = (REBASE_MARK % a["head"]) + "\n" + (
                        "pr-sweep: head `%s` conflicts with the base branch, so GitHub creates no "
                        "CI run for it. Labelled `%s`; rebase onto main to resolve the conflict. "
                        "Auto-merge stays armed and will proceed once the rebase produces runs."
                        % (a["head"][:12], LABEL_NEEDS_REBASE))
                    post_pr_comment(a["pr"], repo, body)
                    results["rebase_flagged"] = results.get("rebase_flagged", 0) + 1
                    outcome = "labelled %s; conflict comment posted; auto-merge left armed" % (
                        LABEL_NEEDS_REBASE)
                else:
                    disarm_args = ["pr", "merge", str(a["pr"]), "--disable-auto"]
                    if repo:
                        disarm_args += ["--repo", repo]
                    gh_write(*disarm_args)
                    label_args = ["label", "create", ZERO_CHECK_LABEL, "--force",
                                  "--color", "FBCA04",
                                  "--description", "PR has zero CI runs; auto-merge disarmed"]
                    if repo:
                        label_args += ["--repo", repo]
                    gh_write(*label_args)
                    edit_args = ["pr", "edit", str(a["pr"]), "--add-label", ZERO_CHECK_LABEL]
                    if repo:
                        edit_args += ["--repo", repo]
                    gh_write(*edit_args)
                    body = (DISARM_MARK % a["head"]) + "\n" + (
                        "pr-sweep: no check runs for head `%s` 24h after CI dispatch; "
                        "auto-merge disarmed and labelled `%s`. Re-arm with "
                        "`gh pr merge <N> --auto --squash` once CI runs are visible." % (
                            a["head"][:12], ZERO_CHECK_LABEL))
                    post_pr_comment(a["pr"], repo, body)
                    results["disarmed"] += 1
                    outcome = "auto-merge disarmed; labelled %s" % ZERO_CHECK_LABEL
            except GhError as exc:
                record_pr_failure(results, lines, log_path, a["pr"], a["kind"],
                                  a["reason"], exc)
                continue
            append_sweep_log(log_path, a["pr"], a["kind"], a["reason"], outcome)
    return lines, results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan, act on nothing")
    parser.add_argument("--json", action="store_true", help="also print a JSON summary")
    parser.add_argument("--repo", help="owner/name (default: resolved via `gh repo view`)")
    parser.add_argument("--queue-path", help="override signal-hub queue path (testing)")
    parser.add_argument("--behind-cap", type=int, default=DEFAULT_BEHIND_CAP,
                         help="max BEHIND PRs reported per run (default 2; never server-updated)")
    parser.add_argument("--log-path", help="override sweep log path (testing)")
    args = parser.parse_args(argv)

    if not gh_auth_ok():
        print("ERROR: gh auth missing or invalid -- run `gh auth login` first", file=sys.stderr)
        return 2

    # Flush any spooled receipts from prior pushes. This is the
    # session-independent actor that completes receipt publication.
    # Errors are logged but do not block the sweep. A dry run posts
    # nothing, so it skips the flush (receipts post to GitHub commits).
    if not args.dry_run:
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

    log_path = Path(args.log_path) if args.log_path else default_sweep_log_path()

    # All gh reads happen inside build_plan; a failed read stops here, before
    # any write (fail closed: exit 3, nothing acted on).
    try:
        actions = build_plan(open_prs, repo, now, queue_path, behind_cap=args.behind_cap)
    except GhError as exc:
        print("ERROR: read failed, acted on nothing: %s" % exc, file=sys.stderr)
        return 3
    try:
        lines, results = execute_plan(actions, repo, args.dry_run, queue_path, log_path)
    except GhError as exc:
        print("ERROR: write failed, sweep stopped: %s" % exc, file=sys.stderr)
        return 1

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

    # Per-PR failures never stop the loop; the exit code reflects the whole
    # summary, so one refused PR cannot hide later PRs' outcomes and a failure
    # cannot pass silently.
    if results["errors"]:
        print("ERROR: %d PR action(s) failed (every other PR was still processed):"
              % len(results["errors"]), file=sys.stderr)
        for message in results["errors"]:
            print("ERROR: " + message, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
