#!/usr/bin/env python3
"""
Portability gate: scan shipped surface for hardcoded personal/environment paths.
INDEX: Shipped-surface gate: scan for hardcoded personal/environment paths (Windows user paths, POSIX home paths, private-machine project and home-dir tokens); exit 0 clean / 1 with findings; --json output, --root flag for base directory; stdlib only

Detects absolute Windows user paths (C:\\Users\\<name> / C:/Users/<name>),
POSIX home paths (/home/<name>, /Users/<name>), and private-machine tokens
('conductor3', 'matt8'). Allows clearly-marked examples/defaults (lines containing
'example', 'default', or 'e.g.').

Exit 0 clean, 1 with numbered file:line findings.
Supports --json output and --root for base directory.

Ratchet mode (--baseline FILE, same pattern as .stateapi-baseline.json):
compare findings against a committed baseline of "file@type" -> count
entries. PASS only when the current scan EXACTLY matches the baseline.
New findings (new key, or count above baseline) FAIL; stale entries
(key gone, or count below baseline) also FAIL so the baseline must be
regenerated (--update-baseline) and the burn-down is recorded in git.
A missing baseline file behaves as an empty baseline (fail-closed on
every finding). CI MUST NEVER pass --update-baseline.
"""

import sys
import os
import json
import re
import glob
import argparse
from pathlib import Path


def read_package_json(root):
    """Read package.json and extract 'files' array."""
    pkg_path = os.path.join(root, 'package.json')
    try:
        with open(pkg_path, 'r', encoding='utf-8') as f:
            content = json.load(f)
        return content.get('files', [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def expand_globs(root, patterns):
    """Expand glob patterns from package.json 'files' array."""
    files = set()
    for pattern in patterns:
        # Normalize pattern to use forward slashes for glob
        pattern = pattern.replace('\\', '/')
        full_pattern = os.path.join(root, pattern).replace('\\', '/')

        matches = glob.glob(full_pattern, recursive=True)
        for match in matches:
            # Use Path to normalize, convert back to string
            normalized = str(Path(match))
            files.add(normalized)

    return sorted(files)


def is_text_file(filepath):
    """Check if file is likely text (not binary)."""
    binary_extensions = {
        '.bin', '.exe', '.dll', '.so', '.dylib',
        '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp',
        '.pdf', '.zip', '.tar', '.gz', '.rar',
        '.woff', '.woff2', '.ttf', '.eot',
        '.mp3', '.mp4', '.wav', '.mov'
    }
    _, ext = os.path.splitext(filepath.lower())
    return ext not in binary_extensions


def read_file_lines(filepath):
    """Read file lines, handling encoding issues gracefully."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.readlines()
    except UnicodeDecodeError:
        try:
            with open(filepath, 'r', encoding='latin-1') as f:
                return f.readlines()
        except Exception:
            return []
    except Exception:
        return []


def is_exception_line(line):
    """Check if line is marked as example/default."""
    line_lower = line.lower()
    return any(marker in line_lower for marker in ['example', 'default', 'e.g.'])


def scan_line_for_paths(line):
    """Scan a line for problematic paths and tokens."""
    findings = []

    # Windows absolute paths: C:\Users\<name> or C:/Users/<name>
    windows_user_patterns = [
        r'C:\\Users\\[a-zA-Z0-9_\-\.]+',
        r'C:/Users/[a-zA-Z0-9_\-\.]+'
    ]
    for pattern in windows_user_patterns:
        matches = re.finditer(pattern, line)
        for match in matches:
            findings.append({
                'type': 'windows_user_path',
                'path': match.group(0)
            })

    # POSIX home paths: /home/<name> or /Users/<name>
    posix_patterns = [
        r'/home/[a-zA-Z0-9_\-\.]+',
        r'/Users/[a-zA-Z0-9_\-\.]+'
    ]
    for pattern in posix_patterns:
        matches = re.finditer(pattern, line)
        for match in matches:
            findings.append({
                'type': 'posix_home_path',
                'path': match.group(0)
            })

    # Private machine tokens: 'conductor3' and 'matt8'
    # These are simple word boundary matches (whole word)
    for token in ['conductor3', 'matt8']:
        # Use word boundaries to avoid false positives in longer identifiers
        pattern = r'\b' + re.escape(token) + r'\b'
        matches = re.finditer(pattern, line)
        for match in matches:
            findings.append({
                'type': 'private_token',
                'token': token
            })

    return findings


def scan_shipped_surface(root, json_output=False):
    """Scan shipped surface for portability issues."""
    patterns = read_package_json(root)
    if not patterns:
        print("Warning: Could not read package.json 'files' array", file=sys.stderr)
        return []

    files = expand_globs(root, patterns)
    all_findings = []

    for filepath in files:
        if not os.path.isfile(filepath) or not is_text_file(filepath):
            continue

        lines = read_file_lines(filepath)
        for line_num, line in enumerate(lines, 1):
            # Skip exception lines (marked as example/default)
            if is_exception_line(line):
                continue

            # Scan for issues
            issues = scan_line_for_paths(line)
            for issue in issues:
                relative_path = os.path.relpath(filepath, root)
                finding = {
                    'file': relative_path,
                    'line': line_num,
                    'content': line.rstrip()[:100],  # First 100 chars
                    **issue
                }
                all_findings.append(finding)

    return all_findings


def findings_to_baseline_keys(findings):
    """Aggregate findings into a {"file@type": count} dict (posix separators)."""
    keys = {}
    for f in findings:
        key = '{0}@{1}'.format(
            str(f['file']).replace('\\', '/'), f.get('type', 'unknown'))
        keys[key] = keys.get(key, 0) + 1
    return keys


def load_baseline(baseline_file):
    """Load a baseline file; missing/unreadable means empty baseline (fail-closed)."""
    p = Path(baseline_file)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    violations = data.get('violations', {})
    if not isinstance(violations, dict):
        return {}
    return {str(k).replace('\\', '/'): int(v) for k, v in violations.items()}


def save_baseline(baseline_file, keys):
    """Write the baseline file from a {"file@type": count} dict."""
    data = {
        '_comment': (
            'Portability-gate ratchet baseline (see tools/portability_check.py '
            '--help). Regenerate ONLY via --update-baseline after reviewing the '
            'diff; CI must never pass --update-baseline.'
        ),
        'violations': {k: keys[k] for k in sorted(keys)},
    }
    Path(baseline_file).write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def check_ratchet(baseline_keys, current_keys):
    """Bidirectional exact-match ratchet.

    Returns (is_ok, stale, new) where stale/new are sorted lists of
    "key (baseline N, current M)" description strings.
    """
    stale = []
    new = []
    for key in sorted(set(baseline_keys) | set(current_keys)):
        b = baseline_keys.get(key, 0)
        c = current_keys.get(key, 0)
        if c > b:
            new.append('{0} (baseline {1}, current {2})'.format(key, b, c))
        elif c < b:
            stale.append('{0} (baseline {1}, current {2})'.format(key, b, c))
    return (not stale and not new), stale, new


def main():
    parser = argparse.ArgumentParser(
        description='Portability gate: scan for hardcoded personal paths'
    )
    parser.add_argument(
        '--root',
        default='.',
        help='Root directory to scan (default: current directory)'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output findings as JSON'
    )
    parser.add_argument(
        '--baseline',
        default=None,
        help='Ratchet mode: compare findings against this committed baseline '
             'file (exact-match, fail-closed; see module docstring)'
    )
    parser.add_argument(
        '--update-baseline',
        action='store_true',
        help='Regenerate the --baseline file from the current scan '
             '(CI must never pass this)'
    )

    args = parser.parse_args()
    root = os.path.abspath(args.root)

    if args.update_baseline and not args.baseline:
        print('portability_check: --update-baseline requires --baseline FILE',
              file=sys.stderr)
        return 2

    findings = scan_shipped_surface(root, json_output=args.json)

    if args.baseline and args.update_baseline:
        current_keys = findings_to_baseline_keys(findings)
        save_baseline(args.baseline, current_keys)
        print('portability_check: baseline updated ({0} entries, {1} finding(s)) '
              '-> {2}'.format(len(current_keys), len(findings), args.baseline))
        return 0

    if args.baseline:
        current_keys = findings_to_baseline_keys(findings)
        baseline_keys = load_baseline(args.baseline)
        is_ok, stale, new = check_ratchet(baseline_keys, current_keys)
        if args.json:
            print(json.dumps({
                'ok': is_ok,
                'mode': 'ratchet',
                'baseline': str(args.baseline),
                'stale': stale,
                'new': new,
                'findings': findings,
            }, indent=2))
        else:
            if is_ok:
                print('portability_check: PASS (ratchet: {0} baselined finding(s) '
                      'across {1} entries)'.format(len(findings), len(baseline_keys)))
            else:
                if new:
                    print('portability_check: {0} NEW violation key(s) above '
                          'baseline:'.format(len(new)), file=sys.stderr)
                    for item in new:
                        print('  NEW   {0}'.format(item), file=sys.stderr)
                if stale:
                    print('portability_check: {0} STALE baseline entries (violations '
                          'fixed; regenerate the baseline to record the '
                          'burn-down):'.format(len(stale)), file=sys.stderr)
                    for item in stale:
                        print('  STALE {0}'.format(item), file=sys.stderr)
                print('\nFAIL: baseline mismatch. Fix new violations, then '
                      'regenerate with --update-baseline if intentional.',
                      file=sys.stderr)
        return 0 if is_ok else 1

    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        if findings:
            print(f"Found {len(findings)} portability issue(s):", file=sys.stderr)
            for i, finding in enumerate(findings, 1):
                print(
                    f"{i}. {finding['file']}:{finding['line']}: "
                    f"{finding.get('type', 'unknown')}",
                    file=sys.stderr
                )
                if finding.get('path'):
                    print(f"   Path: {finding['path']}", file=sys.stderr)
                if finding.get('token'):
                    print(f"   Token: {finding['token']}", file=sys.stderr)
                print(f"   {finding['content']}", file=sys.stderr)

    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
