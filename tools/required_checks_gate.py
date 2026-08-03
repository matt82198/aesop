#!/usr/bin/env python3
"""
Required-checks drift gate.

This is the REVERSE of tools/ci_gate_runability.py. That tool asks "can this
required check actually run?" -- it reads .github/workflows and catches gates
that are skipped, continue-on-error'd, or point at missing files. It cannot see
the other half of the problem, because the other half is not in the repo at all:
a ci.yml job intended to gate merges but ABSENT from branch protection's
required set never blocks anything. The job runs, goes red, and the PR merges
anyway. Nothing in the workflow file records that the job was supposed to gate.

tools/required-checks.json is that missing record -- the declared intent,
captured from live protection and reviewed like code. This gate diffs it against
the live required set and fails on drift in EITHER direction:

  MISSING     declared as gating, absent from protection -> never blocks (the escape)
  UNDECLARED  enforced by protection, absent from intent -> a gate nobody reviewed

Live protection is read with `gh api repos/<repo>/branches/<branch>/protection`.
Use --protection-file to diff against a captured payload instead (hermetic
tests, or reviewing a proposed change before applying it).

Exit codes:
  0 = declared set == enforced set
  1 = drift in either direction
  2 = error (gh missing/unauthenticated, unreadable or malformed JSON,
      unusable intent manifest)
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_INTENT = Path(__file__).resolve().parent / 'required-checks.json'

# Deliberately NOT tools/subprocess_common.gh(): that helper is imported via a
# sys.path.insert on tools/, and import_resolution_check.py (a fail-closed
# pre-push gate) cannot resolve it -- its repo module map only indexes top-level
# modules and __init__.py packages, and tools/ is neither. Existing callers
# (auto_merge, ci_merge_wait, defect_escape, incident_report) only slip through
# because that gate scans STAGED files, so they are never re-checked. Rather
# than widen this lane into a shared gate, this tool stays stdlib-only and
# self-contained. See the PR body for the follow-up.
GH_TIMEOUT_SEC = 60


def load_intent(path):
    """
    Read the declared-intent manifest. Raises ValueError with an operator-facing
    message on anything that would make the comparison meaningless.
    """
    try:
        raw = Path(path).read_text(encoding='utf-8')
    except OSError as exc:
        raise ValueError(f'cannot read intent manifest {path}: {exc}')
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f'intent manifest {path} is not valid JSON: {exc}')
    if not isinstance(data, dict):
        raise ValueError(f'intent manifest {path} must be a JSON object')

    contexts = data.get('required_contexts')
    if not isinstance(contexts, list) or not contexts:
        # An empty declared set would make every comparison vacuously satisfiable
        # in the MISSING direction -- exactly the vacuous-green shape this gate
        # exists to prevent. Treat it as a broken manifest.
        raise ValueError(
            f'intent manifest {path} has an empty or invalid "required_contexts" list'
        )
    if not all(isinstance(c, str) and c.strip() for c in contexts):
        raise ValueError(f'intent manifest {path} has a non-string/blank context entry')

    repo = data.get('repo')
    branch = data.get('branch', 'main')
    if not isinstance(repo, str) or '/' not in repo:
        raise ValueError(f'intent manifest {path} needs a "repo" of the form OWNER/NAME')
    return repo, branch, contexts


def fetch_protection(repo, branch):
    """Read live branch protection via gh. Raises ValueError on any transport failure."""
    if shutil.which('gh') is None:
        raise ValueError('gh CLI not found on PATH; cannot read branch protection')
    cmd = ['gh', 'api', f'repos/{repo}/branches/{branch}/protection']
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8', timeout=GH_TIMEOUT_SEC
        )
    except subprocess.TimeoutExpired:
        raise ValueError(f'gh api timed out after {GH_TIMEOUT_SEC}s')
    except OSError as exc:
        raise ValueError(f'could not invoke gh: {exc}')
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or '').strip()
        raise ValueError(f'gh api failed (rc={proc.returncode}): {detail[:400]}')
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f'gh api returned non-JSON: {exc}')


def load_protection_file(path):
    """Read a captured protection payload from disk instead of calling gh."""
    try:
        raw = Path(path).read_text(encoding='utf-8')
    except OSError as exc:
        raise ValueError(f'cannot read protection payload {path}: {exc}')
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f'protection payload {path} is not valid JSON: {exc}')


def enforced_contexts(payload):
    """
    Extract the enforced required-check contexts from a protection payload.

    Prefers required_status_checks.contexts, falling back to checks[].context
    (GitHub is deprecating the flat contexts list). A payload carrying a bare
    `message` with no required_status_checks is an API error object -- 404
    "Branch not protected", 403, rate limit -- not an empty required set, so it
    raises rather than silently reporting total drift against a payload we never
    actually read.
    """
    if not isinstance(payload, dict):
        raise ValueError('protection payload must be a JSON object')
    rsc = payload.get('required_status_checks')
    if rsc is None:
        if 'message' in payload:
            raise ValueError(f"GitHub API error: {payload.get('message')}")
        # Protection exists but status checks are switched off entirely: a real,
        # reportable state in which nothing gates. Fall through as an empty set.
        return []
    if not isinstance(rsc, dict):
        raise ValueError('required_status_checks must be an object')

    contexts = rsc.get('contexts')
    if isinstance(contexts, list) and contexts:
        return [c for c in contexts if isinstance(c, str)]
    checks = rsc.get('checks')
    if isinstance(checks, list):
        return [c.get('context') for c in checks
                if isinstance(c, dict) and isinstance(c.get('context'), str)]
    return []


def diff(declared, enforced):
    """Findings for drift in both directions, MISSING first then UNDECLARED."""
    declared_set, enforced_set = set(declared), set(enforced)
    findings = []
    for ctx in sorted(declared_set - enforced_set):
        findings.append({
            'kind': 'MISSING',
            'context': ctx,
            'detail': 'declared as gating but not in branch protection; '
                      'this check never blocks a merge',
        })
    for ctx in sorted(enforced_set - declared_set):
        findings.append({
            'kind': 'UNDECLARED',
            'context': ctx,
            'detail': 'enforced by branch protection but not declared in the '
                      'intent manifest; unreviewed gate',
        })
    return findings


def main(argv=None):
    """CLI entry point. Returns the process exit code (0 clean / 1 drift / 2 error)."""
    parser = argparse.ArgumentParser(
        description='Fail on drift between declared gating checks and live branch protection'
    )
    parser.add_argument('--intent', default=str(DEFAULT_INTENT),
                        help='declared-intent manifest (default: tools/required-checks.json)')
    parser.add_argument('--protection-file', default=None,
                        help='read a captured protection payload instead of calling gh')
    parser.add_argument('--repo', default=None,
                        help='override OWNER/NAME from the intent manifest')
    parser.add_argument('--branch', default=None,
                        help='override the branch from the intent manifest')
    parser.add_argument('--json', action='store_true', help='emit findings as JSON')
    args = parser.parse_args(argv)

    try:
        repo, branch, declared = load_intent(args.intent)
        repo = args.repo or repo
        branch = args.branch or branch
        payload = (load_protection_file(args.protection_file)
                   if args.protection_file else fetch_protection(repo, branch))
        enforced = enforced_contexts(payload)
    except ValueError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2

    findings = diff(declared, enforced)
    exit_code = 1 if findings else 0

    if args.json:
        print(json.dumps({
            'status': 'findings' if findings else 'clean',
            'exit_code': exit_code,
            'repo': repo,
            'branch': branch,
            'declared': sorted(declared),
            'enforced': sorted(enforced),
            'findings': findings,
        }, indent=2))
    elif findings:
        print(f'Required-checks drift on {repo}@{branch}:')
        for f in findings:
            print(f"  {f['kind']}: {f['context']} -- {f['detail']}")
        print(f'declared: {sorted(declared)}')
        print(f'enforced: {sorted(enforced)}')
        print('Fix by updating branch protection, or by updating '
              'tools/required-checks.json if the change was intended.')
    else:
        print(f'[OK] {repo}@{branch}: {len(declared)} declared gating checks all '
              'enforced by branch protection, with no undeclared extras')
        for ctx in sorted(declared):
            print(f'  {ctx}')

    return exit_code


if __name__ == '__main__':
    sys.exit(main())
