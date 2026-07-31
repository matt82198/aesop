#!/usr/bin/env python3
"""Branch merge-conflict gate.

Root cause this closes: PR #657 (refactor/transcript-health-modules) had a real
textual merge conflict against main, so GitHub could not even materialize a
merge commit to run CI on ("mergeable": "CONFLICTING", "failingChecks": []).
No CI gate can catch a content merge-conflict before it happens -- it is
inherent to two branches editing the same lines -- but nothing was periodically
checking long-lived open branches for drift against main either, so the
conflict was only discovered when someone looked at the PR.

This tool simulates a merge of each candidate branch against a base ref using
`git merge-tree` (index/working-tree free -- it never touches HEAD or checks
anything out) and reports any branch that would produce a real textual
conflict, plus the conflicted file paths. It is meant to run on a schedule
(or on demand) against open PR branches so a conflict surfaces as a finding
long before someone opens the PR and watches CI never run.

Candidate-branch discovery, in order of preference:
  1. --branches REF [REF ...]     explicit list (no discovery; also what the
                                   hermetic unit tests use)
  2. default (no flags)           `gh pr list --state open` headRefNames --
                                   this is the actual class of branch the
                                   root-cause incident (PR #657) hit: an open
                                   PR whose head diverged from main
  3. --all-remote-branches        every refs/remotes/<remote-prefix>/* ref
                                   (opt-in, advisory/audit use only -- a repo
                                   accumulates long-dead backup/wip/superseded
                                   branches whose historical diff no longer
                                   applies to current main; that is real
                                   drift, not a false positive, but it is a
                                   different question than "will this open PR
                                   fail to get CI", so it is not the default)

Exit codes:
  0 = clean (every evaluated branch merges cleanly into the base)
  1 = findings (at least one branch has a real textual conflict)
  2 = COULD NOT EVALUATE (git missing, base ref unresolvable, no branches to
      check, gh unavailable/failed in default mode, or every candidate branch
      failed to resolve) -- never collapsed into 0.

CLI: branch_merge_conflict_gate.py [--check] [--json] [--root DIR]
     [--base REF] [--branches REF [REF ...]] [--all-remote-branches]
     [--remote-prefix PREFIX] [--include-local] [--fetch] [--gh-limit N]

stdlib-only (gh CLI is an external dependency only for the default
open-PR discovery path; --branches / --all-remote-branches never invoke it).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_BASE_CANDIDATES = ["origin/main", "origin/master", "main", "master"]


def run_git(root: str, args: list, timeout: int = 30):
    """Run a git command rooted at `root`. Returns (returncode, stdout, stderr).

    On a failure to even invoke git (missing binary, timeout), returncode is
    None so callers can distinguish "git ran and said no" from "git could not
    be run at all".
    """
    try:
        result = subprocess.run(
            ["git", "-C", root] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        return (result.returncode, result.stdout, result.stderr)
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        return (None, "", str(exc))


def resolve_ref(root: str, ref: str) -> bool:
    """True if `ref` resolves to a commit in `root`."""
    code, _out, _err = run_git(root, ["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
    return code == 0


def resolve_base(root: str, explicit_base: str = None):
    """Resolve the base ref to merge candidate branches into.

    Returns (base_ref, error_message). error_message is None on success.
    """
    if explicit_base:
        if resolve_ref(root, explicit_base):
            return explicit_base, None
        return None, f"base ref {explicit_base!r} does not resolve to a commit"

    for candidate in DEFAULT_BASE_CANDIDATES:
        if resolve_ref(root, candidate):
            return candidate, None

    return None, (
        "could not resolve a base ref (tried: "
        + ", ".join(DEFAULT_BASE_CANDIDATES)
        + "); pass --base explicitly"
    )


def discover_branches(root: str, remote_prefix: str, include_local: bool, base_ref: str):
    """Auto-discover candidate branches, excluding the base ref itself and
    the remote's symbolic HEAD pointer."""
    branches = []

    prefix = remote_prefix if remote_prefix.endswith("/") else remote_prefix + "/"
    code, out, _err = run_git(root, ["for-each-ref", "--format=%(refname:short)", "refs/remotes/" + prefix])
    if code == 0:
        for line in out.splitlines():
            name = line.strip()
            if not name:
                continue
            if name == prefix.rstrip("/") + "/HEAD":
                continue
            if name == base_ref:
                continue
            branches.append(name)

    if include_local:
        code, out, _err = run_git(root, ["for-each-ref", "--format=%(refname:short)", "refs/heads/"])
        if code == 0:
            for line in out.splitlines():
                name = line.strip()
                if not name:
                    continue
                if name == base_ref:
                    continue
                branches.append(name)

    # Dedupe, preserve order.
    seen = set()
    deduped = []
    for name in branches:
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    return deduped


def discover_open_pr_branches(root: str, remote_prefix: str, limit: int = 200, timeout: int = 30):
    """Discover candidate branches from open PRs via `gh pr list`.

    Returns (branches, error_message). error_message is None on success.
    Cross-repo (fork) PR heads are skipped with a note in the branch's own
    check later (they won't resolve as `<remote_prefix>/<headRefName>` and
    will surface as a per-branch "error", not a silent skip).
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "headRefName,number", "--limit", str(limit)],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        return None, f"gh pr list failed to run: {exc} (pass --branches or --all-remote-branches to skip gh)"

    if result.returncode != 0:
        return None, (
            "gh pr list --state open exited "
            f"{result.returncode}: {result.stderr.strip()} "
            "(pass --branches or --all-remote-branches to skip gh)"
        )

    try:
        prs = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"gh pr list returned unparseable JSON: {exc}"

    branches = []
    seen = set()
    for pr in prs:
        head = (pr.get("headRefName") or "").strip()
        if not head or head in seen:
            continue
        seen.add(head)
        branches.append(f"{remote_prefix}/{head}")
    return branches, None


def check_branch(root: str, base_ref: str, branch: str, timeout: int = 60):
    """Simulate merging `branch` into `base_ref` without touching the working
    tree or index.

    Returns a dict:
      {"branch": ..., "status": "clean" | "conflict" | "error",
       "conflicted_files": [...],  # only for status == "conflict"
       "error": "..."}             # only for status == "error"
    """
    code, out, err = run_git(
        root,
        ["merge-tree", "--write-tree", "--name-only", "--no-messages", base_ref, branch],
        timeout=timeout,
    )

    if code is None:
        return {"branch": branch, "status": "error", "error": err.strip() or "git invocation failed"}

    lines = out.splitlines()

    if code == 0:
        return {"branch": branch, "status": "clean"}

    if code == 1 and lines:
        # First line is the (partial) written tree oid; remaining lines are
        # conflicted file paths (--name-only, --no-messages keeps this to
        # just paths, no "Auto-merging"/"CONFLICT" prose).
        conflicted_files = [ln.strip() for ln in lines[1:] if ln.strip()]
        if conflicted_files:
            return {"branch": branch, "status": "conflict", "conflicted_files": conflicted_files}
        # exit 1 with only a tree line and no file lines is not a content
        # conflict merge-tree can name (e.g. it still failed some other way);
        # treat conservatively as an evaluation error rather than a silent
        # clean pass.
        return {
            "branch": branch,
            "status": "error",
            "error": "merge-tree reported failure without naming conflicted files",
        }

    # exit 1 with empty stdout (bad ref, unrelated histories, etc.) or any
    # other nonzero code: could not evaluate this branch.
    message = err.strip() or out.strip() or f"merge-tree exited {code}"
    return {"branch": branch, "status": "error", "error": message}


def main():
    parser = argparse.ArgumentParser(
        description="Detect branches that would produce a real textual merge conflict against a base ref."
    )
    parser.add_argument("--check", action="store_true", default=True, help="Check mode (default)")
    parser.add_argument("--json", action="store_true", help="Output findings as JSON")
    parser.add_argument("--root", type=str, default=".", help="Repository root directory")
    parser.add_argument("--base", type=str, default=None, help="Base ref to merge into (default: auto-detect origin/main etc.)")
    parser.add_argument("--branches", type=str, nargs="+", default=None, help="Explicit branch/ref list to check (skips discovery entirely)")
    parser.add_argument("--all-remote-branches", action="store_true", help="Discover candidates from every refs/remotes/<remote-prefix>/* ref instead of open PRs (advisory/audit mode -- includes dead/backup/superseded branches)")
    parser.add_argument("--remote-prefix", type=str, default="origin", help="Remote name used both for open-PR branch refs and --all-remote-branches discovery (default: origin)")
    parser.add_argument("--include-local", action="store_true", help="Also scan local branches (refs/heads/*); only applies with --all-remote-branches")
    parser.add_argument("--fetch", action="store_true", help="Run 'git fetch <remote-prefix>' before evaluating (network; off by default for hermetic runs)")
    parser.add_argument("--gh-limit", type=int, default=200, help="Max open PRs to fetch via gh in default discovery mode (default: 200)")

    args = parser.parse_args()

    root = str(Path(args.root).resolve())

    git_version_code, _out, _err = run_git(root, ["--version"])
    if git_version_code is None:
        return emit(2, None, [], [], "git is not available", args.json)

    is_repo_code, _out, _err = run_git(root, ["rev-parse", "--git-dir"])
    if is_repo_code != 0:
        return emit(2, None, [], [], f"{root} is not a git repository", args.json)

    if args.fetch:
        fetch_code, _out, fetch_err = run_git(root, ["fetch", args.remote_prefix, "--prune"], timeout=120)
        if fetch_code not in (0,):
            return emit(2, None, [], [], f"git fetch {args.remote_prefix} failed: {fetch_err.strip()}", args.json)

    base_ref, base_err = resolve_base(root, args.base)
    if base_err:
        return emit(2, None, [], [], base_err, args.json)

    if args.branches:
        candidates = []
        seen = set()
        for b in args.branches:
            b = b.strip()
            if b and b not in seen:
                seen.add(b)
                candidates.append(b)
    elif args.all_remote_branches:
        candidates = discover_branches(root, args.remote_prefix, args.include_local, base_ref)
        if not candidates:
            return emit(2, base_ref, [], [], "no candidate branches to check (empty remote-tracking branch set)", args.json)
    else:
        candidates, gh_err = discover_open_pr_branches(root, args.remote_prefix, args.gh_limit)
        if gh_err:
            return emit(2, base_ref, [], [], gh_err, args.json)
        if not candidates:
            return emit(2, base_ref, [], [], "no open PRs found (nothing to check); pass --branches or --all-remote-branches explicitly if this is expected", args.json)

    results = [check_branch(root, base_ref, b) for b in candidates]

    findings = [r for r in results if r["status"] == "conflict"]
    errors = [r for r in results if r["status"] == "error"]
    clean = [r for r in results if r["status"] == "clean"]
    checked_count = len(findings) + len(clean)

    if checked_count == 0:
        detail = "; ".join(f"{e['branch']}: {e['error']}" for e in errors) or "no branch could be evaluated"
        return emit(2, base_ref, [], errors, f"could not evaluate any candidate branch ({detail})", args.json)

    exit_code = 1 if findings else 0
    return emit(exit_code, base_ref, findings, errors, None, args.json, checked_count=checked_count, clean_count=len(clean))


def emit(exit_code, base_ref, findings, errors, fatal_error, as_json, checked_count=0, clean_count=0):
    if as_json:
        output = {
            "status": "clean" if exit_code == 0 else ("findings" if exit_code == 1 else "error"),
            "exit_code": exit_code,
            "base_ref": base_ref,
            "checked_count": checked_count,
            "clean_count": clean_count,
            "findings": findings,
            "errors": errors,
        }
        if fatal_error:
            output["error"] = fatal_error
        print(json.dumps(output, indent=2))
    else:
        if fatal_error:
            print(f"ERROR: {fatal_error}", file=sys.stderr)
        else:
            print(f"Base ref: {base_ref}")
            print(f"Checked {checked_count} branch(es); {clean_count} clean, {len(findings)} conflicting, {len(errors)} could not evaluate.")
            for f in findings:
                print(f"CONFLICT: {f['branch']} vs base -- {', '.join(f['conflicted_files'])}")
            for e in errors:
                print(f"WARN: could not evaluate {e['branch']}: {e['error']}")
            if exit_code == 0 and not errors:
                print("[OK] No branches have a real textual merge conflict against the base ref")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
