#!/usr/bin/env python3
"""
tools.workflow_model_linter -- Guardrail G7: model pin enforcement for workflow scripts.

Scans JavaScript workflow scripts (.js/.mjs files) for agent() calls and verifies that
each call includes an explicit model:'haiku' parameter in its options object.

Background: Workflow scripts call agent() directly and bypass the PreToolUse hook that
enforces model='haiku' for Agent/Task dispatches. This linter catches agent() calls
without explicit model pins and flags them as violations.

Workflow scripts are automation/orchestration files that call agent() to spawn workers.
They appear in: tools/, monitor/, driver/, skills/, .claude/ directories and their
subdirectories.

Exit codes:
  0 = no violations found (clean)
  1 = violations detected
  2 = usage/argument error (unknown flags, bad paths)

CLI:
  python tools/workflow_model_linter.py [--check] [--json] [PATH...]

  --check       Scan and report violations (default mode)
  --json        Output findings as JSON array
  --help        Show usage and exit 0
  PATH...       Override default scan targets (directories/files scanned for *.js/.mjs)

Default scan targets (if no PATH given):
  - tools/ (non-recursive: *.js, *.mjs only)
  - monitor/ (recursive)
  - driver/ (recursive)
  - skills/ (recursive)
  - .claude/ (recursive)

ASCII-only output. Stdlib only, no external dependencies.

Suppression: Add `// model-ok` comment on the same line as agent() call to suppress
a finding for that specific call site.

Output format (text, default):
  VIOLATION: path/to/file.mjs:42 — agent() call without model pin
    agent('do something', {label: 'foo'})

Output format (--json):
  [
    {
      "path": "path/to/file.mjs",
      "line": 42,
      "call": "agent('do something', {label: 'foo'})",
      "message": "agent() call without model pin"
    }
  ]
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Any


def find_js_files(paths: List[Path]) -> List[Path]:
    """
    Find all .js and .mjs files in the given paths.

    If a path is a file, include it directly.
    If a path is a directory, glob for .js/.mjs files (recursive).
    """
    result = []
    for path in paths:
        if path.is_file():
            if path.suffix in ('.js', '.mjs'):
                result.append(path)
        elif path.is_dir():
            result.extend(path.glob('**/*.js'))
            result.extend(path.glob('**/*.mjs'))
    return sorted(set(result))


def extract_line_context(lines: List[str], line_num: int, width: int = 80) -> str:
    """
    Extract context around a line number (1-indexed).
    Returns the line with leading/trailing whitespace trimmed, truncated if too long.
    """
    if 0 < line_num <= len(lines):
        context = lines[line_num - 1].strip()
        if len(context) > width:
            context = context[:width - 3] + '...'
        return context
    return ''


def has_suppression(line: str) -> bool:
    """Check if line contains // model-ok suppression marker."""
    return '// model-ok' in line


def is_in_string_or_comment(line: str, pos: int) -> bool:
    """
    Check if position pos in line is inside a string or comment.
    Handles single quotes, double quotes, backticks, and // comments.
    """
    # Check if in a comment (not in string)
    comment_pos = line.find('//')
    if comment_pos >= 0 and comment_pos < pos:
        return True

    # Check if in a string (single, double, or backtick)
    in_single = False
    in_double = False
    in_backtick = False
    escaped = False

    for i in range(pos):
        ch = line[i]
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            escaped = True
            continue
        if ch == "'" and not in_double and not in_backtick:
            in_single = not in_single
        elif ch == '"' and not in_single and not in_backtick:
            in_double = not in_double
        elif ch == '`' and not in_single and not in_double:
            in_backtick = not in_backtick

    return in_single or in_double or in_backtick


def sanitize_to_ascii(text: str) -> str:
    """Remove non-ASCII characters, replacing with '?'."""
    return ''.join(c if ord(c) < 128 else '?' for c in text)


def find_agent_call_range(lines: List[str], start_line_idx: int, match_end: int) -> Tuple[int, int]:
    """
    Find the end line and column of an agent(...) call that starts at start_line_idx.

    Returns (end_line_idx, end_col) where the closing paren is located.
    Uses paren/brace depth tracking to find the matching close.
    """
    # Track paren/brace depth starting from the agent( opening
    paren_depth = 1
    brace_depth = 0
    line_idx = start_line_idx
    col = match_end

    # Start from the position after 'agent('
    line = lines[start_line_idx]
    in_string = False
    string_char = None

    # Scan through lines until we find the closing paren for agent(...)
    while line_idx < len(lines):
        line = lines[line_idx]
        start_col = col if line_idx == start_line_idx else 0

        for i in range(start_col, len(line)):
            ch = line[i]

            # Check for escape sequences
            if i > 0 and line[i - 1] == '\\':
                continue

            # Toggle string state
            if ch in ('"', "'", '`'):
                if not in_string:
                    in_string = True
                    string_char = ch
                elif ch == string_char:
                    in_string = False
                    string_char = None
                continue

            # Skip if in string
            if in_string:
                continue

            # Track paren/brace depth
            if ch == '(':
                paren_depth += 1
            elif ch == ')':
                paren_depth -= 1
                if paren_depth == 0:
                    # Found the closing paren for agent(...)
                    return (line_idx, i)
            elif ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1

        col = 0
        line_idx += 1

    # If we didn't find a closing paren, assume end of file
    return (len(lines) - 1, len(lines[-1]) if lines else 0)


def scan_file(file_path: Path) -> List[Dict[str, Any]]:
    """
    Scan a single .js/.mjs file for agent() calls without model parameter.

    Returns list of violations, each with:
    - path: file path (string)
    - line: line number (1-indexed)
    - call: extracted call text (truncated context)
    - message: violation description
    """
    violations = []

    try:
        content = file_path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return violations

    lines = content.split('\n')

    for i, line in enumerate(lines):
        line_num = i + 1

        # Quick check: does this line mention agent(?
        if 'agent(' not in line:
            continue

        # Check for suppression marker on this line
        if has_suppression(line):
            continue

        # Try to find agent(...) call pattern on this line
        agent_match = re.search(r'\bagent\s*\(', line)
        if not agent_match:
            continue

        # Check if this match is inside a string or comment
        if is_in_string_or_comment(line, agent_match.start()):
            continue

        # Find the range of the agent() call
        end_line_idx, end_col = find_agent_call_range(lines, i, agent_match.end())

        # Extract the full call text
        if end_line_idx == i:
            # Single-line call
            call_text = line[agent_match.start():end_col + 1]
        else:
            # Multi-line call: from agent( on start line to ) on end line
            parts = [line[agent_match.start():]]
            for mid_line_idx in range(i + 1, end_line_idx):
                parts.append(lines[mid_line_idx])
            parts.append(lines[end_line_idx][:end_col + 1])
            call_text = '\n'.join(parts)

        # Check if call_text contains model:
        has_model = bool(re.search(r'\bmodel\s*:', call_text))

        if not has_model:
            # Extract call context (just the line with agent() for now)
            call_context = extract_line_context(lines, line_num)
            violations.append({
                'path': str(file_path),
                'line': line_num,
                'call': call_context,
                'message': 'agent() call without model pin',
            })

    return violations


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog='workflow_model_linter.py',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,  # Custom help handling
    )

    parser.add_argument(
        '--check',
        action='store_true',
        help='Check mode (default); scan and report violations, exit 1 if found',
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output findings as JSON array',
    )
    parser.add_argument(
        '--help',
        action='store_true',
        help='Show usage and exit 0',
    )
    parser.add_argument(
        'paths',
        nargs='*',
        help='Override default scan targets (directories/files)',
    )

    # Parse known args to allow custom --help handling
    args, unknown = parser.parse_known_args()

    # Custom help
    if args.help:
        print(__doc__)
        return 0

    # Reject unknown flags
    if unknown:
        print(f'error: unknown flag: {unknown[0]}', file=sys.stderr)
        return 2

    # Determine scan paths
    if args.paths:
        # User provided explicit paths
        scan_paths = []
        for p in args.paths:
            path = Path(p)
            if not path.exists():
                print(f'error: path does not exist: {p}', file=sys.stderr)
                return 2
            scan_paths.append(path)
    else:
        # Default scan directories (relative to cwd)
        defaults = ['tools', 'monitor', 'driver', 'skills', '.claude']
        scan_paths = []
        for d in defaults:
            path = Path(d)
            if path.exists():
                scan_paths.append(path)

    # Find all .js/.mjs files
    js_files = find_js_files(scan_paths)

    # Scan all files
    all_violations = []
    for file_path in js_files:
        violations = scan_file(file_path)
        all_violations.extend(violations)

    # Output results
    if args.json:
        # JSON output
        print(json.dumps(all_violations, indent=2))
    else:
        # Text output (ASCII-safe)
        for violation in all_violations:
            path = violation['path']
            line = violation['line']
            call = violation['call']
            msg = violation['message']
            # Sanitize to ASCII for Windows console compatibility
            path_safe = sanitize_to_ascii(path)
            msg_safe = sanitize_to_ascii(msg)
            call_safe = sanitize_to_ascii(call)
            print(f'VIOLATION: {path_safe}:{line} -- {msg_safe}')
            print(f'  {call_safe}')

    # Exit code
    if all_violations:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
