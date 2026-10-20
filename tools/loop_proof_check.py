#!/usr/bin/env python3
"""Loop-Proof gate: a change to a listed loop file needs a red-then-green trailer.

LOOP-MANIFEST.json (repo root) lists the loop-machinery files (daemons and the scripts they run),
each with the test files that prove it. For the range base..head:

- an added, modified or type-changed listed file needs a commit trailer
  `Loop-Proof: <test> red=a/b green=c/c` naming one of that file's declared tests, with
  0 < a <= b (red before the change) and c == d > 0 (fully green after);
- a listed file with no declared test is refused (a test must exist first);
- deleting a listed file, or removing a manifest entry or declared test, is refused;
- a malformed Loop-Proof trailer is refused;
- a missing or unparseable manifest (base or head) is "cannot evaluate", exit 2 (fail closed).

Usage:  loop_proof_check.py --base <rev> [--head <rev>] [--repo PATH]
Exit:   0 clean, 1 unproved or refused change, 2 cannot evaluate.

Scope: the trailer's red/green counts are checked for shape and arithmetic; the named test is
not re-executed here. Paths are POSIX-style and never trusted to be absolute.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

LOOP_MANIFEST_PATH = 'LOOP-MANIFEST.json'
PROOF_LINE_RE = re.compile(r'^Loop-Proof:\s*(\S+)\s+red=(\d+)/(\d+)\s+green=(\d+)/(\d+)\s*$')
EXIT_OK, EXIT_UNPROVED, EXIT_CANNOT = 0, 1, 2


class CheckError(Exception):
    """The gate cannot evaluate the range; the caller exits 2."""


def _git(repo, *args):
    proc = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    if proc.returncode != 0:
        raise CheckError(f'git {" ".join(args)} failed: {proc.stderr.strip()}')
    return proc.stdout


def safe_rel(path):
    """True for a non-empty POSIX-relative path with no backslash, no absolute root, no '..'."""
    if not isinstance(path, str) or not path or '\\' in path:
        return False
    pure = PurePosixPath(path)
    return not pure.is_absolute() and '..' not in pure.parts and pure.as_posix() == path


def parse_manifest(text):
    """{path: [declared tests]} from LOOP-MANIFEST.json text; CheckError on any bad shape."""
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise CheckError(f'{LOOP_MANIFEST_PATH} is not valid JSON: {exc}')
    files = doc.get('files') if isinstance(doc, dict) else None
    if not isinstance(files, list):
        raise CheckError(f'{LOOP_MANIFEST_PATH} needs an object with files[]')
    entries = {}
    for entry in files:
        if not isinstance(entry, dict) or not safe_rel(entry.get('path')):
            raise CheckError(f'{LOOP_MANIFEST_PATH} entry needs a safe relative "path": {entry!r}')
        path = entry['path']
        reason = entry.get('reason')
        if not isinstance(reason, str) or not reason.strip() or '\n' in reason:
            raise CheckError(f'{LOOP_MANIFEST_PATH} entry {path} needs a one-line "reason"')
        tests = entry.get('tests', [])
        if not isinstance(tests, list) or not all(safe_rel(t) for t in tests):
            raise CheckError(f'{LOOP_MANIFEST_PATH} entry {path} has bad "tests"')
        if path in entries:
            raise CheckError(f'{LOOP_MANIFEST_PATH} lists {path} twice')
        entries[path] = list(tests)
    return entries


def manifest_at(repo, rev):
    return parse_manifest(_git(repo, 'show', f'{rev}:{LOOP_MANIFEST_PATH}'))


def manifest_exists_at(repo, rev):
    proc = subprocess.run(['git', '-C', str(repo), 'cat-file', '-e', f'{rev}:{LOOP_MANIFEST_PATH}'],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
    return proc.returncode == 0


def name_status(repo, base, head):
    """[(status letter, path)] for the range; a rename yields a delete of the old path and an add of the new."""
    rows = []
    for line in _git(repo, 'diff', '--name-status', '-M', f'{base}...{head}').splitlines():
        if not line.strip():
            continue
        parts = line.split('\t')
        status = parts[0][:1]
        if status in ('R', 'C'):
            rows.append(('D', parts[1]))
            rows.append(('A', parts[2]))
        else:
            rows.append((status, parts[1]))
    return rows


def parse_proofs(messages):
    """(proved test paths, malformed-trailer errors) from commit messages."""
    proved, errors = set(), []
    for raw in messages.splitlines():
        line = raw.rstrip('\r')
        if not line.startswith('Loop-Proof:'):
            continue
        match = PROOF_LINE_RE.match(line)
        if not match:
            errors.append(f'malformed Loop-Proof trailer: "{line}" (want: Loop-Proof: <test> red=a/b green=c/c)')
            continue
        test = match.group(1)
        red_fail, red_total, green_pass, green_total = (int(g) for g in match.groups()[1:])
        if not safe_rel(test):
            errors.append(f'Loop-Proof names an unacceptable test path: {test}')
        elif not (0 < red_fail <= red_total):
            errors.append(f'Loop-Proof for {test} shows no red-before (red={red_fail}/{red_total})')
        elif not (green_total > 0 and green_pass == green_total):
            errors.append(f'Loop-Proof for {test} is not fully green (green={green_pass}/{green_total})')
        else:
            proved.add(test)
    return proved, errors


def check_range(repo, base, head):
    """List of violation strings for base..head; empty means the gate passes. Raises CheckError."""
    head_entries = manifest_at(repo, head)  # missing at head is CheckError: fail closed
    # Bootstrap: a manifest introduced in this range has no base to compare against, so every
    # listed file is protected from the first commit that carries the manifest. A manifest
    # that existed at base and is gone at head is still refused (CheckError) above.
    base_entries = manifest_at(repo, base) if manifest_exists_at(repo, base) else {}
    violations = []

    for path in sorted(base_entries):
        if path not in head_entries:
            violations.append(f'manifest entry removed: {path} (removal is refused by this gate)')
            continue
        for test in base_entries[path]:
            if test not in head_entries[path]:
                violations.append(f'declared test removed from {path}: {test}')

    changed_loop = []
    for status, path in name_status(repo, base, head):
        if status == 'D' and path in base_entries:
            violations.append(f'listed loop file deleted: {path} (removal is refused by this gate)')
        elif status in ('A', 'M', 'T') and path in head_entries:
            changed_loop.append(path)

    if not changed_loop:
        return violations

    messages = _git(repo, 'log', '--format=%B', f'{base}..{head}')
    proved, trailer_errors = parse_proofs(messages)
    violations.extend(trailer_errors)
    for path in changed_loop:
        declared = head_entries[path]
        if not declared:
            violations.append(f'{path}: no test exists for this loop file; write a test and list it under '
                              f'"tests" in {LOOP_MANIFEST_PATH} first')
        elif not any(t in proved for t in declared):
            if not proved and not trailer_errors:
                violations.append(f'{path}: no Loop-Proof trailer; a loop-machinery change needs '
                                  f'"Loop-Proof: <test> red=a/b green=c/c" naming one of: {", ".join(declared)}')
            elif proved and not trailer_errors:
                violations.append(f'{path}: Loop-Proof must name one of its declared tests: {", ".join(declared)}')
            else:
                violations.append(f'{path}: no valid Loop-Proof trailer for a declared test ({", ".join(declared)})')
    return violations


def main(argv=None):
    ap = argparse.ArgumentParser(description='Loop-Proof gate for LOOP-MANIFEST.json files.')
    ap.add_argument('--base', required=True, help='base revision of the range')
    ap.add_argument('--head', default='HEAD', help='head revision of the range (default HEAD)')
    ap.add_argument('--repo', default='.', help='repository root (default: current directory)')
    args = ap.parse_args(argv)
    try:
        violations = check_range(Path(args.repo), args.base, args.head)
    except CheckError as exc:
        print(f'loop-proof: CANNOT EVALUATE -- {exc}', file=sys.stderr)
        return EXIT_CANNOT
    if violations:
        for violation in violations:
            print(f'loop-proof: FAIL -- {violation}', file=sys.stderr)
        return EXIT_UNPROVED
    print(f'loop-proof: OK ({args.base}..{args.head})')
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
