#!/usr/bin/env python3
"""TODO/FIXME/HACK/XXX comment tracker.

Scans Python, JavaScript, TypeScript, and shell files for tagged comments
(TODO, FIXME, HACK, XXX). Extracts comment text, file path, line number,
and tag type; groups by tag and sorts by file; reports totals per tag.

Exit: 0=clean (or informational TODOs only), 1=critical tags found (--check),
     2=error.
CLI: todo_tracker.py [--check] [--json] [--tag TODO,FIXME] [--paths DIR...] [--root DIR]
"""

import argparse
import json
import os
import re
import sys

from lint_core import exit_code

# File extensions to scan
EXTENSIONS = frozenset([
    '.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.sh', '.bash',
])

# Default tags to scan for
DEFAULT_TAGS = ['TODO', 'FIXME', 'HACK', 'XXX']

# Tags that cause --check to fail (critical)
CRITICAL_TAGS = frozenset(['FIXME', 'HACK'])

# Pattern cache
_TAG_RE_CACHE = {}


def _build_pattern(tags):
    """Build compiled regex for given tag list."""
    key = tuple(sorted(tags))
    if key not in _TAG_RE_CACHE:
        escaped = '|'.join(re.escape(t) for t in tags)
        # Match TAG optionally followed by colon/paren, then capture rest of line
        _TAG_RE_CACHE[key] = re.compile(
            r'(?:#|//|/\*|\*)\s*\b(' + escaped + r')\b[\s:(\-]*(.*)$',
            re.IGNORECASE,
        )
    return _TAG_RE_CACHE[key]


def _should_scan(filepath):
    """Return True if file extension is in our scan set."""
    _, ext = os.path.splitext(filepath)
    return ext.lower() in EXTENSIONS


def _walk_files(paths, root):
    """Yield absolute file paths under the given directories/files."""
    for p in paths:
        target = os.path.join(root, p) if not os.path.isabs(p) else p
        if os.path.isfile(target):
            if _should_scan(target):
                yield target
        elif os.path.isdir(target):
            for dirpath, dirnames, filenames in os.walk(target):
                # Skip hidden dirs and common non-source dirs
                dirnames[:] = [
                    d for d in dirnames
                    if not d.startswith('.') and d not in ('node_modules', '__pycache__', '.git')
                ]
                for fname in sorted(filenames):
                    fpath = os.path.join(dirpath, fname)
                    if _should_scan(fpath):
                        yield fpath


def scan_file(filepath, pattern):
    """Scan a single file for tagged comments. Returns list of finding dicts."""
    findings = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for lineno, line in enumerate(f, 1):
                m = pattern.search(line)
                if m:
                    tag = m.group(1).upper()
                    text = m.group(2).strip().rstrip('*/).').strip()
                    findings.append({
                        'file': filepath,
                        'line': lineno,
                        'tag': tag,
                        'text': text,
                    })
    except (OSError, IOError):
        # Unreadable file -- skip silently
        pass
    return findings


def scan(tags=None, paths=None, root=None):
    """Run a full scan. Returns list of finding dicts."""
    if tags is None:
        tags = list(DEFAULT_TAGS)
    if root is None:
        root = os.getcwd()
    if paths is None:
        paths = [root]

    pattern = _build_pattern(tags)
    all_findings = []
    for fpath in _walk_files(paths, root):
        all_findings.extend(scan_file(fpath, pattern))

    # Sort by tag then file then line
    all_findings.sort(key=lambda f: (f['tag'], f['file'], f['line']))
    return all_findings


def group_by_tag(findings):
    """Group findings by tag type. Returns dict of tag -> list of findings."""
    groups = {}
    for f in findings:
        groups.setdefault(f['tag'], []).append(f)
    return groups


def format_text(findings, root):
    """Format findings as human-readable text."""
    if not findings:
        return 'No tagged comments found.\n'

    groups = group_by_tag(findings)
    lines = []
    for tag in sorted(groups):
        items = groups[tag]
        lines.append(f'\n=== {tag} ({len(items)}) ===')
        for item in items:
            rel = os.path.relpath(item['file'], root)
            lines.append(f'  {rel}:{item["line"]}: {item["text"]}')

    # Summary
    lines.append('')
    lines.append('Summary:')
    for tag in sorted(groups):
        lines.append(f'  {tag}: {len(groups[tag])}')
    lines.append(f'  Total: {len(findings)}')
    lines.append('')
    return '\n'.join(lines)


def format_json(findings):
    """Format findings as JSON."""
    groups = group_by_tag(findings)
    summary = {tag: len(items) for tag, items in groups.items()}
    return json.dumps({
        'findings': findings,
        'summary': summary,
        'total': len(findings),
    }, indent=2)


def main(argv=None):
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Scan codebase for TODO/FIXME/HACK/XXX comments.',
    )
    parser.add_argument(
        '--check', action='store_true',
        help='Exit 1 if any FIXME or HACK found (TODOs are informational)',
    )
    parser.add_argument(
        '--json', action='store_true', dest='json_output',
        help='Output results as JSON',
    )
    parser.add_argument(
        '--tag', type=str, default=None,
        help='Comma-separated list of tags to scan for (default: TODO,FIXME,HACK,XXX)',
    )
    parser.add_argument(
        '--paths', nargs='+', default=None,
        help='Directories or files to scan (default: root)',
    )
    parser.add_argument(
        '--root', type=str, default=None,
        help='Root directory (default: cwd)',
    )

    # Fail-closed on unknown flags
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        print(f'Error: unknown arguments: {" ".join(unknown)}', file=sys.stderr)
        return 2

    root = args.root or os.getcwd()
    tags = [t.strip().upper() for t in args.tag.split(',')] if args.tag else None
    paths = args.paths

    try:
        findings = scan(tags=tags, paths=paths, root=root)
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2

    if args.json_output:
        print(format_json(findings))
    else:
        print(format_text(findings, root))

    if args.check:
        # Only FIXME and HACK are critical
        active_critical = CRITICAL_TAGS
        if tags is not None:
            active_critical = CRITICAL_TAGS & set(tags)
        critical_found = any(f['tag'] in active_critical for f in findings)
        if critical_found:
            return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
