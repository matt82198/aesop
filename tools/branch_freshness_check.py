#!/usr/bin/env python3
"""Pre-merge branch freshness gate.

Root cause this closes: aesop main has 'strict / up-to-date with base
branch' DISABLED (user-approved 2026-07-30, see serial-merge-train-pattern
history) so GitHub's own `mergeStateStatus` field on a PR stops reliably
reporting BEHIND for a stale branch -- tools/merge_train.py's existing
`info["merge"] == "BEHIND"` handling (which calls `gh pr update-branch`)
never fires for those PRs. A branch can then sit in the merge queue for
hours with a stale copy of a file main already fixed, and its CI keeps
re-running the STALE code against a test suite that has already moved on
(observed: 0dc79da fixed tools/commit_lint.py:130 `if args.message:` ->
`if args.message is not None:`; branches forked between c1000e9 (added the
buggy line) and 0dc79da (fixed it) that never merged/rebased main since
still fail tests/test_commit_lint.py::test_cli_empty_message_explicit).

This gate re-derives branch-vs-main staleness directly from local git
history (merge-base + rev-list), independent of GitHub's mergeStateStatus,
so it works whether or not strict/up-to-date is enabled on the remote.
Intended call site: immediately before a branch is merged (wire-in is a
separate decision -- this script is unarmed).

Targets are resolved from one of:
  --pr N [N...]        PR numbers (requires `gh`, resolves headRefName)
  --branch NAME [...]  explicit branch names (local or origin/NAME)
  --all-open           every open PR (requires `gh`)

For each resolved branch, this computes how many commits main has that the
branch does NOT have since their merge-base (`git rev-list --count
<branch>..origin/main`). A branch is STALE if that count exceeds
--max-behind (default 0: at merge time the branch must contain every
commit currently on main).

Exit codes:
  0 = clean (all resolved branches fresh, or --all-open found zero open PRs)
  1 = findings (one or more branches are stale)
  2 = could not evaluate (git/gh unavailable or failed, no targets could be
      resolved, or no target-selection flag was given at all)

Never exits 0 having scanned nothing: an empty target LIST from a
successful --all-open query is a legitimate clean result (0); a FAILURE to
resolve targets (bad --pr number, gh unavailable, no flag given) is 2.
"""

import argparse
import json
import subprocess
import sys


def run(cmd, cwd=None, timeout=30):
    """Run a subprocess command, returning (ok, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            cwd=cwd, timeout=timeout,
        )
        return (result.returncode == 0, result.stdout.strip(), result.stderr.strip())
    except FileNotFoundError as e:
        return (False, "", f"command not found: {e}")
    except subprocess.TimeoutExpired:
        return (False, "", f"timed out: {' '.join(cmd)}")
    except Exception as e:
        return (False, "", str(e))


def gh_json(args, timeout=30):
    """Run a `gh` CLI command expecting JSON output. Returns (ok, data, error)."""
    ok, out, err = run(["gh"] + args, timeout=timeout)
    if not ok:
        return (False, None, err or "gh command failed")
    try:
        return (True, json.loads(out), None)
    except (json.JSONDecodeError, ValueError) as e:
        return (False, None, f"gh returned non-JSON output: {e}")


def resolve_branches_from_prs(pr_numbers, root=None):
    """Resolve PR numbers to (branch_name, pr_number) via gh. Returns (targets, errors)."""
    targets = []
    errors = []
    for n in pr_numbers:
        ok, data, err = gh_json(
            ["pr", "view", str(n), "--json", "headRefName,state"], timeout=30
        )
        if not ok or not isinstance(data, dict):
            errors.append(f"PR #{n}: could not resolve branch ({err})")
            continue
        branch = data.get("headRefName")
        if not branch:
            errors.append(f"PR #{n}: no headRefName in gh response")
            continue
        targets.append({"branch": branch, "pr": n})
    return targets, errors


def resolve_all_open_prs(root=None):
    """List every open PR via gh. Returns (targets, error_or_None)."""
    ok, data, err = gh_json(
        ["pr", "list", "--state", "open", "--json", "number,headRefName", "--limit", "500"],
        timeout=60,
    )
    if not ok:
        return (None, err or "gh pr list failed")
    if not isinstance(data, list):
        return (None, "gh pr list returned unexpected shape")
    targets = [{"branch": item.get("headRefName"), "pr": item.get("number")}
               for item in data if item.get("headRefName")]
    return (targets, None)


def fetch_ref(branch, root=None):
    """Ensure origin/<branch> exists locally and up to date. Returns (ok, ref, error)."""
    # Prefer a direct fetch into a remote-tracking ref so we don't depend on
    # the ref already existing locally (agents work from worktrees that may
    # not have every branch's remote-tracking ref present).
    ok, _, err = run(
        ["git", "fetch", "--quiet", "origin",
         f"refs/heads/{branch}:refs/remotes/origin/{branch}"],
        cwd=root, timeout=60,
    )
    if ok:
        return (True, f"origin/{branch}", None)
    # Fall back to whatever remote-tracking ref may already be present
    # locally (covers the case where the branch was deleted upstream but a
    # stale ref remains -- still useful to report as a finding-adjacent
    # error rather than silently skipping).
    ok2, _, _ = run(["git", "rev-parse", "--verify", f"origin/{branch}"], cwd=root, timeout=10)
    if ok2:
        return (True, f"origin/{branch}", None)
    return (False, None, err or f"could not fetch branch '{branch}'")


def check_branch_freshness(branch, root=None, max_behind=0):
    """Compute staleness of one branch vs origin/main.

    Returns a dict: branch, ok (bool: could evaluate), behind, ahead,
    stale (bool), error (str or None).
    """
    result = {
        "branch": branch, "ok": False, "behind": None, "ahead": None,
        "stale": False, "error": None,
    }

    fetched_ok, ref, fetch_err = fetch_ref(branch, root=root)
    if not fetched_ok:
        result["error"] = fetch_err
        return result

    ok_main, _, err_main = run(
        ["git", "fetch", "--quiet", "origin", "main:refs/remotes/origin/main"],
        cwd=root, timeout=60,
    )
    if not ok_main:
        # origin/main may already be present and current; don't fail solely
        # because a redundant fetch errored (e.g. already up to date can
        # still return non-zero on some git builds for non-fast-forward
        # local ref updates while running from inside a worktree).
        ok_check, _, _ = run(["git", "rev-parse", "--verify", "origin/main"], cwd=root, timeout=10)
        if not ok_check:
            result["error"] = err_main or "could not resolve origin/main"
            return result

    ok_mb, mb_out, mb_err = run(["git", "merge-base", ref, "origin/main"], cwd=root, timeout=15)
    if not ok_mb or not mb_out:
        result["error"] = mb_err or f"no merge-base between {ref} and origin/main"
        return result

    ok_behind, behind_out, behind_err = run(
        ["git", "rev-list", "--count", f"{ref}..origin/main"], cwd=root, timeout=15
    )
    ok_ahead, ahead_out, ahead_err = run(
        ["git", "rev-list", "--count", f"origin/main..{ref}"], cwd=root, timeout=15
    )
    if not ok_behind or not ok_ahead:
        result["error"] = behind_err or ahead_err or "rev-list failed"
        return result

    try:
        behind = int(behind_out)
        ahead = int(ahead_out)
    except ValueError:
        result["error"] = f"non-numeric rev-list output: behind={behind_out!r} ahead={ahead_out!r}"
        return result

    result["ok"] = True
    result["behind"] = behind
    result["ahead"] = ahead
    result["stale"] = behind > max_behind
    return result


def run_check(pr_numbers=None, branch_names=None, all_open=False, max_behind=0,
               root=None):
    """Main entry point. Returns (exit_code, report_dict)."""
    report = {
        "targets_requested": [],
        "results": [],
        "findings": [],
        "errors": [],
        "max_behind": max_behind,
    }

    if not pr_numbers and not branch_names and not all_open:
        report["errors"].append(
            "no target selected: pass --pr, --branch, or --all-open"
        )
        return 2, report

    targets = []

    if all_open:
        resolved, err = resolve_all_open_prs(root=root)
        if err is not None:
            report["errors"].append(f"--all-open: {err}")
            return 2, report
        targets.extend(resolved)

    if pr_numbers:
        resolved, errs = resolve_branches_from_prs(pr_numbers, root=root)
        targets.extend(resolved)
        report["errors"].extend(errs)

    if branch_names:
        targets.extend({"branch": b, "pr": None} for b in branch_names)

    report["targets_requested"] = [t["branch"] for t in targets]

    # If every explicitly-requested target failed to resolve, we scanned
    # nothing meaningful -- that is a could-not-evaluate, not a clean pass.
    if not targets:
        if all_open:
            # A genuine, successfully-queried empty open-PR list is a real
            # clean result: there was something to scan (the query ran) and
            # it found nothing stale because there was nothing open.
            return 0, report
        report["errors"].append("no targets could be resolved")
        return 2, report

    any_eval_failure = False
    for t in targets:
        branch = t["branch"]
        res = check_branch_freshness(branch, root=root, max_behind=max_behind)
        res["pr"] = t.get("pr")
        report["results"].append(res)
        if not res["ok"]:
            any_eval_failure = True
            report["errors"].append(f"{branch}: {res['error']}")
            continue
        if res["stale"]:
            pr_tag = f" (PR #{res['pr']})" if res["pr"] else ""
            report["findings"].append(
                f"{branch}{pr_tag} is {res['behind']} commit(s) behind origin/main "
                f"(max allowed: {max_behind}) -- rebase/merge main before merging"
            )

    if report["findings"]:
        return 1, report
    if any_eval_failure and not report["findings"]:
        # Some targets could not be evaluated at all and none of the ones
        # that DID evaluate were stale -- we did not fully scan the
        # requested set, so this is not a clean pass.
        return 2, report
    return 0, report


def main():
    parser = argparse.ArgumentParser(
        description="Pre-merge branch freshness gate: flags branches missing "
                    "commits present on origin/main (independent of GitHub's "
                    "mergeStateStatus, which stops reporting BEHIND when "
                    "strict/up-to-date branch protection is disabled)."
    )
    parser.add_argument("--check", action="store_true",
                        help="Run the check (default behavior; flag kept for "
                             "convention parity with neighbouring gates)")
    parser.add_argument("--pr", type=int, nargs="+", metavar="N",
                        help="PR number(s) to check (resolves branch via gh)")
    parser.add_argument("--branch", nargs="+", metavar="NAME",
                        help="Explicit branch name(s) to check")
    parser.add_argument("--all-open", action="store_true",
                        help="Check every open PR (via gh pr list)")
    parser.add_argument("--max-behind", type=int, default=0,
                        help="Commits behind origin/main allowed before a "
                             "branch is flagged stale (default: 0)")
    parser.add_argument("--root", default=None,
                        help="Repository root (default: current directory)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON report")
    args = parser.parse_args()

    exit_code, report = run_check(
        pr_numbers=args.pr, branch_names=args.branch, all_open=args.all_open,
        max_behind=args.max_behind, root=args.root,
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if exit_code == 2:
            print("COULD NOT EVALUATE:", file=sys.stderr)
            for e in report["errors"]:
                print(f"  - {e}", file=sys.stderr)
        elif exit_code == 1:
            print(f"FINDINGS ({len(report['findings'])}):", file=sys.stderr)
            for f in report["findings"]:
                print(f"  - {f}", file=sys.stderr)
            if report["errors"]:
                print("Also could not evaluate:", file=sys.stderr)
                for e in report["errors"]:
                    print(f"  - {e}", file=sys.stderr)
        else:
            n = len(report["results"])
            print(f"OK: {n} branch(es) fresh (<= {args.max_behind} commits behind origin/main)")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
