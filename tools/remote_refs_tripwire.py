#!/usr/bin/env python3
"""
Remote-refs tripwire: fails closed if a wrapped command left any new trace on the
REAL git remote or GitHub open-PR list, even though tools/test_network_isolation.py's
harness-level rewrite is supposed to make that structurally impossible.
INDEX: Snapshots `git ls-remote --heads origin` + `gh pr list --state open --json number`
before/after wrapping a command; FAILS, naming every new/changed remote branch or new
PR, when drift is detected; degrades loudly-but-green ("tripwire inert") when the
remote/gh is unreachable (no network/auth, e.g. a sandboxed CI runner). CLI:
`[--repo DIR] -- <command...>`; exit = max(wrapped command's exit code, 1 if drift was
measurable and found). stdlib-only.

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

Usage:
  python tools/remote_refs_tripwire.py -- npm run test:py
  python tools/remote_refs_tripwire.py -- python tools/ci_shard_runner.py 0 4
  python tools/remote_refs_tripwire.py --repo /path/to/fixture -- true   # tests

Deliberately run with the CALLER's own (real, unmodified) environment for its OWN
`git ls-remote` / `gh pr list` probes -- its entire purpose is to inspect the REAL
remote state, so it must never itself run under tools/test_network_isolation.py's
rewrite. The WRAPPED command is a separate subprocess with its own environment,
inheriting whatever the caller already had (isolated or not); this tripwire does not
set or unset anything for it.

EXCLUDED_REF_PATTERN is a second, narrower exemption: this repo's own resident
daemons/backup-fleet.sh (run every ~150s by the watchdog Scheduled Task on the dev
box, independent of and concurrent with any CI run against the SAME origin)
force-pushes `refs/heads/backup/wip-YYYYMMDD` as a one-ref-per-day WIP snapshot.
That is a real, documented, pre-existing writer of this exact remote that has
nothing to do with test isolation, so a before/after snapshot taken minutes apart
sees it move on an unrelated schedule. The first CI run after this tripwire was
wired in (#837) red-flagged on exactly that ref moving mid-shard, with no test in
the wrapped command ever touching the remote. Excluding only this one documented
ref pattern keeps the tripwire fail-closed on everything else -- any NEW branch or
any OTHER moved branch (the actual #837 incident pattern, e.g. integrate/batch-*)
still fails it immediately.
"""
import argparse
import json
import re
import sys
import subprocess
import time
from pathlib import Path

REMOTE = "origin"
PROBE_TIMEOUT = 20
PR_LIST_LIMIT = 500
EXCLUDED_REF_PATTERN = re.compile(r"^refs/heads/backup/wip-\d{8}$")


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


def diff_branches(before, after):
    """Return human-readable findings, or [] if clean/unmeasurable.

    Refs matching EXCLUDED_REF_PATTERN (this repo's own backup-fleet.sh daemon
    ref) are skipped entirely -- see the module docstring. Every other ref is
    still held to the full new-or-moved standard.
    """
    if before is None or after is None:
        return []
    findings = []
    for ref in sorted(set(after) - set(before)):
        if EXCLUDED_REF_PATTERN.match(ref):
            continue
        findings.append(f"NEW remote branch appeared: {ref} ({after[ref]})")
    for ref in sorted((set(after) & set(before))):
        if EXCLUDED_REF_PATTERN.match(ref):
            continue
        if after[ref] != before[ref]:
            findings.append(f"remote branch moved: {ref} ({before[ref]} -> {after[ref]})")
    return findings


def diff_prs(before, after):
    if before is None or after is None:
        return []
    return [f"NEW open PR appeared: #{n}" for n in sorted(set(after) - set(before))]


def _fmt_count(value):
    return "<unmeasurable>" if value is None else str(len(value))


def main(argv):
    if "--" not in argv:
        print("ERROR: usage: remote_refs_tripwire.py [--repo DIR] -- <command...>", file=sys.stderr)
        return 2
    sep = argv.index("--")
    pre, command = argv[:sep], argv[sep + 1:]
    if not command:
        print("ERROR: no command given after --", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repo", default=None)
    ns = parser.parse_args(pre)
    repo_root = ns.repo if ns.repo else str(_repo_root())

    branches_before, b_reason = snapshot_remote_branches(repo_root)
    prs_before, p_reason = snapshot_open_prs(repo_root)

    if branches_before is None and prs_before is None:
        reasons = "; ".join(r for r in (b_reason, p_reason) if r)
        print(f"remote-refs tripwire inert (no network/auth): {reasons}", file=sys.stderr)

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

    findings = diff_branches(branches_before, branches_after) + diff_prs(prs_before, prs_after)
    for f in findings:
        print(f"FAIL (remote-refs tripwire): {f}", file=sys.stderr)

    tripwire_rc = 1 if findings else 0
    return max(command_rc, tripwire_rc)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
