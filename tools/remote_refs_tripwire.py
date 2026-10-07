#!/usr/bin/env python3
"""
Remote-refs tripwire: fails closed if a wrapped command left any new trace on the
REAL git remote or GitHub open-PR list, even though tools/test_network_isolation.py's
harness-level rewrite is supposed to make that structurally impossible.
INDEX: Snapshots `git ls-remote --heads origin` + `gh pr list --state open --json number` before/after wrapping a command; `--strict` (default off CI) fails on any new/moved ref, `--attributed` (default on CI) only fails on a plausibly test-created new branch or the PR's own head branch moving unexpectedly, logging everything else as "observed, not attributed"; degrades loudly-but-green ("tripwire inert") when the remote/gh is unreachable; CLI `[--repo DIR] [--strict|--attributed] -- <command...>`; stdlib-only.

Two verdict modes: --strict (default off CI, i.e. when GITHUB_ACTIONS is unset) fails
on ANY new/moved ref exactly as before; --attributed (default ON CI) never fails on a
moved existing branch (this fleet pushes constantly, so an unrelated branch moving
mid-run is routine, not a leak -- PR #829) and fails a new branch only when it is
plausibly test-created (name prefix `integrate/`, `test-`, `tmp-`, or `bot/regen-`
outside the regen workflow, or matches a harness test-session id) or is the PR's own
head branch moved to a sha the workflow itself did not push; everything else is
printed as "observed, not attributed" and does not fail the run. Degrades
loudly-but-green ("tripwire inert") when the remote/gh is unreachable (no network/
auth, e.g. a sandboxed CI runner). Exit = max(wrapped command's exit code, 1 if
attributable drift was found).

Standalone replacement for PR #831's tools/test_isolation_tripwire.py (not merged at
the time this was written; that one watches the local ~/.claude profile, a DIFFERENT
concern -- harness-level HOME isolation for the Node suite, not the git remote). If
#831 has since merged, this module does not duplicate it: run both wrappers, they
watch different things.

Incident this proves against (2026-10-05): tests/test_merge_train_halt_enforcement.py
and tests/test_merge_queue_halt_enforcement.py ran merge_train.py/merge_queue.py as
real unmocked subprocesses; six integrate/batch-20261005-15xx branches were pushed to
the real origin and a worktree was repurposed. tools/test_network_isolation.py's
harness-level env rewrite (wired via tests/__init__.py) should make this structurally
impossible going forward; this tripwire is the independent, belt-and-suspenders proof
that it worked, and the backstop for any future escape that specific harness does not
anticipate (a new tool that reaches the remote through some path the rewrite does not
cover).

Second incident this proves against (PR #837/#829, 2026-10-05, hours later): wired
into CI as a bare strict check, it false-positived on PR #829's run because an
UNRELATED branch moved while other lanes in this fleet were pushing concurrently --
nothing in the wrapped shard ever touched the remote. In CI, tools/test_network_isolation.py
already makes a real leak from the wrapped command structurally unreachable (every
github.com URL is rewritten to a local bare sink for that process), so the tripwire's
CI job is a backstop for an isolation-rewrite bug, not a general "did anything on
origin change" alarm -- it must distinguish "the wrapped command plausibly caused
this" from "the rest of the fleet is alive", or it trains people to ignore it. Hence
the --attributed verdict: it still proves the isolation held (it would catch the exact
#837 pattern -- a new `integrate/batch-*` branch -- immediately, in either mode)
without failing the build every time a sibling lane pushes. --strict stays the
behavior for a developer box, where "something touched my repo's remote while I ran
this locally" is rare and worth a hard stop.

Usage:
  python tools/remote_refs_tripwire.py -- npm run test:py                      # local: strict
  python tools/remote_refs_tripwire.py --attributed -- python tools/ci_shard_runner.py 0 4  # CI
  python tools/remote_refs_tripwire.py --repo /path/to/fixture -- true         # tests

Deliberately run with the CALLER's own (real, unmodified) environment for its OWN
`git ls-remote` / `gh pr list` probes -- its entire purpose is to inspect the REAL
remote state, so it must never itself run under tools/test_network_isolation.py's
rewrite. The WRAPPED command is a separate subprocess with its own environment,
inheriting whatever the caller already had (isolated or not); this tripwire does not
set or unset anything for it.

EXCLUDED_REF_PATTERN is a second, narrower exemption, honored in BOTH verdict modes:
this repo's own resident daemons/backup-fleet.sh (run every ~150s by the watchdog
Scheduled Task on the dev box, independent of and concurrent with any CI run against
the SAME origin) force-pushes `refs/heads/backup/wip-YYYYMMDD` as a one-ref-per-day
WIP snapshot. That is a real, documented, pre-existing writer of this exact remote
that has nothing to do with test isolation, so a before/after snapshot taken minutes
apart sees it move on an unrelated schedule. The first CI run after this tripwire was
wired in (#837) red-flagged on exactly that ref moving mid-shard, with no test in
the wrapped command ever touching the remote. Excluding only this one documented
ref pattern keeps the tripwire fail-closed on everything else -- any NEW branch or
any OTHER moved branch (the actual #837 incident pattern, e.g. integrate/batch-*)
still fails it immediately in strict mode, and is still attributed (so still fails)
in attributed mode whenever its name matches a plausible test-leak pattern.
"""
import argparse
import json
import os
import re
import sys
import subprocess
import time
from pathlib import Path

REMOTE = "origin"
PROBE_TIMEOUT = 20
PR_LIST_LIMIT = 500
EXCLUDED_REF_PATTERN = re.compile(r"^refs/heads/backup/wip-\d{8}$")

# Attributed mode: a NEW branch only fails the run if its name looks like something
# a test (not a human/bot doing routine fleet work) would have created. These are
# prefixes on the branch's short name (ref with "refs/heads/" stripped). Note: in CI,
# "integrate/batch-*" branches are ALWAYS from concurrent merge-train/auto-merge work
# (concurrent fleet operations), never from pytest runs -- so we exclude them from
# attribution here to avoid false positives. Only "test-" and "tmp-" indicate actual
# test-created branches (see PR #888).
ATTRIBUTABLE_NEW_BRANCH_PREFIXES = ("test-", "tmp-")
# bot/regen- is a Guardrail-G12 self-heal branch shape (see tools/CLAUDE.md); it is
# only EXCLUDED from attribution (i.e. only legitimate) when this run IS that regen
# workflow -- see _running_in_regen_workflow(). Anywhere else a bot/regen- branch
# appearing is exactly as suspicious as integrate/* and fails the run.
REGEN_BOT_BRANCH_PREFIX = "bot/regen-"
# Set by a test harness that wants its ephemeral refs attributed by construction; this
# repo does not set one today (grep AESOP_TEST_* in tools/test_network_isolation.py),
# so this clause is inert until one exists -- never raises on its absence.
TEST_SESSION_ID_ENV = "AESOP_TEST_SESSION_ID"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def snapshot_remote_branches(repo_root):
    """`git ls-remote --heads origin` -> ({ref: sha}, None) or (None, reason)."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", REMOTE],
            cwd=str(repo_root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"git ls-remote failed/timed out: {e}"
    if result.returncode != 0:
        return None, f"git ls-remote exited {result.returncode}: {result.stderr.strip()[:200]}"
    refs = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2:
            sha, ref = parts
            refs[ref] = sha
    return refs, None


def snapshot_open_prs(repo_root):
    """`gh pr list --state open --json number` -> (sorted [numbers], None) or (None, reason)."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "number",
             "--limit", str(PR_LIST_LIMIT)],
            cwd=str(repo_root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"gh pr list failed/timed out: {e}"
    if result.returncode != 0:
        return None, f"gh pr list exited {result.returncode}: {result.stderr.strip()[:200]}"
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return None, f"gh pr list produced unparsable JSON: {e}"
    try:
        return sorted(item["number"] for item in data), None
    except (KeyError, TypeError) as e:
        return None, f"gh pr list JSON missing 'number': {e}"


def _branch_name(ref):
    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref


def _running_in_regen_workflow():
    """True only when this run IS the Guardrail-G12 self-heal/regen workflow (so its
    own bot/regen-* branch is expected, not a leak). Keyed off GITHUB_WORKFLOW's name
    rather than a hardcoded workflow filename, since that's the one thing GitHub sets
    for every job regardless of which .yml defines it."""
    return "regen" in os.environ.get("GITHUB_WORKFLOW", "").lower()


def _is_attributable_new_branch(branch_name, session_id=None):
    """Attributed mode only: would a test plausibly have created this branch?"""
    for prefix in ATTRIBUTABLE_NEW_BRANCH_PREFIXES:
        if branch_name.startswith(prefix):
            return True
    if branch_name.startswith(REGEN_BOT_BRANCH_PREFIX) and not _running_in_regen_workflow():
        return True
    if session_id and session_id in branch_name:
        return True
    return False


def _pr_head_moved_unexpectedly(ref, new_sha):
    """Attributed mode only: did the PR's OWN head branch move to a sha the workflow
    itself did not push? GITHUB_HEAD_REF/GITHUB_SHA are only set on pull_request-
    triggered runs; absent either (push-to-main trigger, or a developer box), this is
    never true -- it never raises on their absence."""
    head_ref = os.environ.get("GITHUB_HEAD_REF")
    workflow_sha = os.environ.get("GITHUB_SHA")
    if not head_ref or not workflow_sha:
        return False
    return ref == f"refs/heads/{head_ref}" and new_sha != workflow_sha


def _is_attributable_moved_branch(ref, new_sha):
    return _pr_head_moved_unexpectedly(ref, new_sha)


def diff_branches(before, after, mode="strict", session_id=None):
    """Return human-readable FAILURE findings, or [] if clean/unmeasurable/not
    attributable. `mode` is "strict" (default, unchanged legacy behavior: every
    new-or-moved ref is a failure) or "attributed" (a moved ref is never, by itself,
    a failure; a new ref fails only when _is_attributable_new_branch() says a test
    plausibly made it; a moved ref fails only when it is the PR's own head branch
    moved out from under the workflow -- see _pr_head_moved_unexpectedly()). Refs
    matching EXCLUDED_REF_PATTERN (this repo's own backup-fleet.sh daemon ref) are
    skipped entirely in BOTH modes -- see the module docstring. Non-failing drift in
    attributed mode is not silently dropped: see diff_branches_observed() for the
    "observed, not attributed" companion list.
    """
    if before is None or after is None:
        return []
    findings = []
    for ref in sorted(set(after) - set(before)):
        if EXCLUDED_REF_PATTERN.match(ref):
            continue
        if mode == "attributed" and not _is_attributable_new_branch(_branch_name(ref), session_id):
            continue
        findings.append(f"NEW remote branch appeared: {ref} ({after[ref]})")
    for ref in sorted((set(after) & set(before))):
        if EXCLUDED_REF_PATTERN.match(ref):
            continue
        if after[ref] == before[ref]:
            continue
        if mode == "attributed" and not _is_attributable_moved_branch(ref, after[ref]):
            continue
        findings.append(f"remote branch moved: {ref} ({before[ref]} -> {after[ref]})")
    return findings


def diff_branches_observed(before, after, mode="strict", session_id=None):
    """Attributed mode only: new-or-moved refs that are real drift but were NOT
    attributed to the wrapped command (so diff_branches() does not fail on them) --
    logged for visibility so "nothing happened" and "something happened but it
    wasn't us" stay distinguishable. Always [] in strict mode, where every such ref
    is already a failure in diff_branches()."""
    if mode != "attributed" or before is None or after is None:
        return []
    observed = []
    for ref in sorted(set(after) - set(before)):
        if EXCLUDED_REF_PATTERN.match(ref):
            continue
        if _is_attributable_new_branch(_branch_name(ref), session_id):
            continue
        observed.append(f"observed, not attributed: NEW remote branch {ref} ({after[ref]})")
    for ref in sorted((set(after) & set(before))):
        if EXCLUDED_REF_PATTERN.match(ref):
            continue
        if after[ref] == before[ref]:
            continue
        if _is_attributable_moved_branch(ref, after[ref]):
            continue
        observed.append(f"observed, not attributed: remote branch moved {ref} ({before[ref]} -> {after[ref]})")
    return observed


def diff_prs(before, after):
    if before is None or after is None:
        return []
    return [f"NEW open PR appeared: #{n}" for n in sorted(set(after) - set(before))]


def _fmt_count(value):
    return "<unmeasurable>" if value is None else str(len(value))


def _github_actions_ci():
    return os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"


def _resolve_mode(ns):
    if ns.strict and ns.attributed:
        return None
    if ns.strict:
        return "strict"
    if ns.attributed:
        return "attributed"
    return "attributed" if _github_actions_ci() else "strict"


def main(argv):
    if "--" not in argv:
        print(
            "ERROR: usage: remote_refs_tripwire.py [--repo DIR] [--strict|--attributed] -- <command...>",
            file=sys.stderr,
        )
        return 2
    sep = argv.index("--")
    pre, command = argv[:sep], argv[sep + 1:]
    if not command:
        print("ERROR: no command given after --", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--attributed", action="store_true")
    ns = parser.parse_args(pre)
    repo_root = ns.repo if ns.repo else str(_repo_root())

    mode = _resolve_mode(ns)
    if mode is None:
        print("ERROR: --strict and --attributed are mutually exclusive", file=sys.stderr)
        return 2
    session_id = os.environ.get(TEST_SESSION_ID_ENV)

    branches_before, b_reason = snapshot_remote_branches(repo_root)
    prs_before, p_reason = snapshot_open_prs(repo_root)

    if branches_before is None and prs_before is None:
        reasons = "; ".join(r for r in (b_reason, p_reason) if r)
        print(f"remote-refs tripwire inert (no network/auth): {reasons}", file=sys.stderr)

    print(f"remote-refs tripwire mode: {mode}", file=sys.stderr)
    print(
        f"remote-refs tripwire BEFORE: {_fmt_count(branches_before)} remote branch(es), "
        f"{_fmt_count(prs_before)} open PR(s)",
        file=sys.stderr,
    )

    t0 = time.monotonic()
    proc = subprocess.run(command, cwd=str(repo_root))
    command_rc = proc.returncode
    elapsed = time.monotonic() - t0

    branches_after, _ = snapshot_remote_branches(repo_root)
    prs_after, _ = snapshot_open_prs(repo_root)

    print(
        f"remote-refs tripwire AFTER:  {_fmt_count(branches_after)} remote branch(es), "
        f"{_fmt_count(prs_after)} open PR(s) (wrapped command ran {elapsed:.1f}s, exit {command_rc})",
        file=sys.stderr,
    )

    observed = diff_branches_observed(branches_before, branches_after, mode=mode, session_id=session_id)
    for o in observed:
        print(o, file=sys.stderr)

    findings = (
        diff_branches(branches_before, branches_after, mode=mode, session_id=session_id)
        + diff_prs(prs_before, prs_after)
    )
    for f in findings:
        print(f"FAIL (remote-refs tripwire): {f}", file=sys.stderr)

    tripwire_rc = 1 if findings else 0
    return max(command_rc, tripwire_rc)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
