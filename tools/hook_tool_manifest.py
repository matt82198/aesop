#!/usr/bin/env python3
"""
INDEX: Hook tool-existence manifest gate: parses `hooks/pre-push-policy.sh` for the tool paths it dispatches to (a `tools/<file>` reached through an interpolated root var, which is what excludes bare-path prose mentions in comments and `cat >` fixture writes in `run_test_mode`) and asserts each exists and is non-empty (whitespace-only counts as EMPTY: it satisfies the hook's `[ -f ]` test and runs as a no-op gate). Closes the fail-open hole: 7 of the hook's 8 tool call sites `return 0` + log `<gate>_skipped_tool_missing` when the tool is absent, so deleting/renaming/truncating a gate script silently disarms it — only `secret_scan.py` fails closed. Read-only w.r.t. the hook; deliberately does NOT change the hook's own fail-open semantics, which are correct for the foreign repos the hook also installs into. CLI: `[--root DIR] [--hook PATH] [--json] [--list]`; exit 0=all present/1=missing or empty/2=error. Zero parsed references is exit 2, never 0 — a parser that matches nothing would report the same vacuous green this gate exists to catch. Guards 8 tools today; stdlib-only
Hook tool-existence manifest gate.

hooks/pre-push-policy.sh dispatches to a set of gate scripts under
"$aesop_root/tools/". Seven of its eight call sites deliberately fail OPEN: a
missing tool file logs "<gate>_skipped_tool_missing" and returns 0, because the
hook is also installed into repos that have no aesop checkout. That fail-open is
correct for foreign repos and wrong for THIS one -- inside aesop, deleting,
renaming or truncating a gate script silently disarms that gate and every
subsequent push is green for a gate that no longer runs. (Only secret_scan.py
fails closed today; see check_secret_scan's "secret_scan_unavailable" block.)

This gate closes that hole from the outside: parse the hook for the tool paths
it references, then assert each one exists on disk and is non-empty. It does not
modify the hook and does not change the hook's own fail-open semantics.

Exit codes:
  0 = every referenced tool exists and is non-empty
  1 = one or more referenced tools are missing or empty
  2 = error (hook unreadable, or no references parsed at all -- see below)

Zero parsed references is exit 2, never exit 0: a parser that silently matches
nothing would report a vacuous green, which is the same failure class the gate
exists to detect.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# A tool reference is a tools/<file> path reached through a shell variable that
# holds the repo root: "$aesop_root/tools/x.py", "$r/tools/x.py",
# "${AESOP_ROOT}/tools/x.py". Requiring the "$var/" prefix is what keeps prose
# mentions ("# runs tools/foo.py --check") out of the manifest: comments in the
# hook name tools by bare relative path, call sites always interpolate a root.
REFERENCE_RE = re.compile(
    r'\$\{?[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}?/tools/([A-Za-z0-9_.\-]+\.(?:py|sh|mjs|js))'
)

# Fixture writes, not invocations: run_test_mode() materialises mock scanners
# with `cat > "$AESOP_ROOT/tools/secret_scan.py" <<'SCANNER'` inside a temp
# AESOP_ROOT. Those paths must never be asserted against the real tools/ dir.
WRITE_RE = re.compile(r'(?:^|[;&|]|\bthen\b|\bdo\b)\s*(?:cat|tee|printf|echo)\b[^\n]*>')

DEFAULT_HOOK = 'hooks/pre-push-policy.sh'


def extract_references(hook_text):
    """
    Return the deduplicated, sorted list of "tools/<file>" paths the hook
    dispatches to, preserving first-seen order for stable output.
    """
    seen = []
    for raw_line in hook_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if WRITE_RE.search(line):
            continue
        for name in REFERENCE_RE.findall(line):
            ref = 'tools/' + name
            if ref not in seen:
                seen.append(ref)
    return seen


def check_reference(root, ref):
    """
    Return a finding dict for `ref`, or None when it is present and non-empty.
    A whitespace-only file counts as EMPTY: it satisfies `[ -f ]` in the hook
    and would run as a no-op gate, which is the disarmed state we are hunting.
    """
    path = Path(root) / ref
    if not path.exists() or not path.is_file():
        return {'kind': 'MISSING', 'tool': ref, 'path': str(path),
                'detail': 'referenced by the pre-push hook but not on disk'}
    try:
        content = path.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        return {'kind': 'MISSING', 'tool': ref, 'path': str(path),
                'detail': f'unreadable: {exc}'}
    if not content.strip():
        return {'kind': 'EMPTY', 'tool': ref, 'path': str(path),
                'detail': 'file exists but has no content; the gate would be a no-op'}
    return None


def main(argv=None):
    """CLI entry point. Returns the process exit code (0 clean / 1 findings / 2 error)."""
    parser = argparse.ArgumentParser(
        description='Assert every tool referenced by the pre-push hook exists and is non-empty'
    )
    parser.add_argument('--root', default='.',
                        help='repository root (default: cwd)')
    parser.add_argument('--hook', default=None,
                        help=f'hook script to parse (default: <root>/{DEFAULT_HOOK})')
    parser.add_argument('--json', action='store_true',
                        help='emit findings as JSON')
    parser.add_argument('--list', action='store_true',
                        help='list parsed references and exit 0 without asserting existence')
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    hook_path = Path(args.hook) if args.hook else root / DEFAULT_HOOK

    try:
        hook_text = hook_path.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        print(f'Error: cannot read hook script {hook_path}: {exc}', file=sys.stderr)
        return 2

    references = extract_references(hook_text)

    if args.list:
        for ref in references:
            print(ref)
        return 0

    if not references:
        print(
            f'Error: no tool references parsed from {hook_path}. Either the hook '
            'was gutted or its dispatch idiom changed; refusing to report a '
            'vacuous pass.',
            file=sys.stderr,
        )
        return 2

    findings = [f for f in (check_reference(root, ref) for ref in references) if f]
    exit_code = 1 if findings else 0

    if args.json:
        print(json.dumps({
            'status': 'findings' if findings else 'clean',
            'exit_code': exit_code,
            'hook': str(hook_path),
            'referenced': references,
            'findings': findings,
        }, indent=2))
    elif findings:
        print(f'Hook tool manifest drift ({hook_path}):')
        for f in findings:
            print(f"  {f['kind']}: {f['tool']} -- {f['detail']}")
        print(f'{len(findings)} of {len(references)} referenced tools are unusable.')
        print('A missing tool makes its pre-push gate fail OPEN (silently skipped).')
    else:
        print(f'[OK] all {len(references)} tools referenced by {hook_path.name} '
              'exist and are non-empty')
        for ref in references:
            print(f'  {ref}')

    return exit_code


if __name__ == '__main__':
    sys.exit(main())
