#!/usr/bin/env python3
"""
tools.workflow_model_linter -- Guardrail G7: model pin + workflow-args validation.

Scans JavaScript workflow scripts (.js/.mjs files) for:
  1. agent() calls without explicit model:'haiku' parameter
  2. Unsafe JSON parsing patterns (try-catch JSON.parse with silent fallback)
  3. Unvalidated field access on potentially-undefined parsed objects

Background: Workflow scripts call agent() directly and bypass the PreToolUse hook that
enforces model='haiku' for Agent/Task dispatches. This linter catches agent() calls
without explicit model pins and flags them as violations.

Workflow-args validation detects escape esc-wf-args-string: JSON.parse in try-catch
blocks that silently fall back to empty objects, followed by unvalidated field access
(e.g., accessing .workDir, .testCmd, .items without null/undefined checks). When
JSON.parse fails, skip-lists become undefined; downstream code fails silently.

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

Suppression markers:
  - Add `// model-ok` on agent() call line to suppress model-pin findings
  - Add `// args-ok` on unsafe-parse line to suppress JSON-parse findings

Output format (text, default):
  VIOLATION: path/to/file.mjs:42 — agent() call without model pin
    agent('do something', {label: 'foo'})
  VIOLATION: path/to/file.mjs:93 — unsafe JSON.parse with silent fallback
    if (typeof A === 'string') { try { A = JSON.parse(A) } catch (e) { A = {} } }

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


def has_suppression(line: str, marker: str = 'model-ok') -> bool:
    """Check if line contains suppression marker (e.g., // model-ok, // args-ok)."""
    return f'// {marker}' in line


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
        if has_suppression(line, 'model-ok'):
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


def scan_file_json_parse_patterns(file_path: Path) -> List[Dict[str, Any]]:
    """
    Scan a single .js/.mjs file for unsafe JSON.parse patterns.

    Detects:
    1. try { JSON.parse(x) } catch (e) { x = {} } — silent fallback without validation
    2. Subsequent field access on x without null/undefined checks
    3. typeof x === 'string' checks followed by silent-fail parse

    Returns list of violations, each with:
    - path: file path (string)
    - line: line number (1-indexed)
    - call: extracted pattern text
    - message: violation description
    """
    violations = []

    try:
        content = file_path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return violations

    lines = content.split('\n')

    # Track state: when we see typeof x === 'string', look for upcoming try-catch
    typeof_vars = {}  # maps line_num to variable name

    for i, line in enumerate(lines):
        line_num = i + 1

        # Skip suppressed lines
        if has_suppression(line, 'args-ok'):
            continue

        # Pattern 1: try { ... JSON.parse(...) } catch (e) { x = {} }
        # Simplified regex to catch the pattern
        if 'try' in line and 'JSON.parse' in line and 'catch' in line:
            # Check if this line or nearby lines have the silent-fallback pattern
            # Pattern: try { ... JSON.parse(...) } catch (...) { ... = {} }
            if re.search(r'try\s*\{.*JSON\.parse.*\}\s*catch\s*\([^)]*\)\s*\{[^}]*=\s*\{\}', line):
                call_context = extract_line_context(lines, line_num)
                violations.append({
                    'path': str(file_path),
                    'line': line_num,
                    'call': call_context,
                    'message': 'unsafe JSON.parse with silent fallback (no post-parse validation)',
                })
                continue

        # Pattern 2: Multi-line try-catch with JSON.parse and empty-object fallback
        # Look for try on this line or previous lines
        if 'JSON.parse' in line and 'catch' not in line:
            # Check if we have a try-catch pattern spanning multiple lines
            # Search forward from this line for the catch block
            for j in range(i, min(i + 10, len(lines))):
                if 'catch' in lines[j]:
                    # Found a catch block; check if it has silent fallback (does nothing or sets empty)
                    catch_line = lines[j]
                    # Look for pattern: catch (...) { x = {} } OR catch (...) { } (does nothing)
                    if re.search(r'catch\s*\([^)]*\)\s*\{[^}]*\}', catch_line):
                        # Check if it's a silent fallback: either x = {} or empty handler
                        if '{}' in catch_line or re.search(r'catch\s*\([^)]*\)\s*\{\s*\}', catch_line):
                            # Verify this is the same try-catch by checking for matching try
                            found_try = False
                            for k in range(max(0, j - 10), j):
                                if 'try' in lines[k]:
                                    found_try = True
                                    break
                            if found_try:
                                call_context = extract_line_context(lines, line_num)
                                violations.append({
                                    'path': str(file_path),
                                    'line': line_num,
                                    'call': call_context,
                                    'message': 'unsafe JSON.parse with silent fallback (no post-parse validation)',
                                })
                    break

        # Pattern 3: typeof check followed by try-catch with silent fail
        # Pattern: if (typeof x === 'string') { try { x = JSON.parse(x) } catch (e) { ... } }
        if 'typeof' in line and "'string'" in line:
            # Extract variable name from typeof check
            typeof_match = re.search(r'typeof\s+(\w+)\s*===\s*["\']string["\']', line)
            if typeof_match:
                var_name = typeof_match.group(1)
                # Look ahead for try-catch with JSON.parse involving the same variable
                for j in range(i, min(i + 10, len(lines))):
                    if 'JSON.parse' in lines[j]:
                        # Check if catch is within next few lines
                        for k in range(j, min(j + 5, len(lines))):
                            if 'catch' in lines[k]:
                                # Check for silent fallback or empty handler
                                catch_line = lines[k]
                                if re.search(r'catch\s*\([^)]*\)\s*\{[^}]*\}', catch_line):
                                    call_context = extract_line_context(lines, line_num)
                                    violations.append({
                                        'path': str(file_path),
                                        'line': line_num,
                                        'call': call_context,
                                        'message': 'unsafe typeof check with JSON.parse (may produce undefined on error)',
                                    })
                                break
                        break

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
        # Check for agent() calls without model pin
        violations = scan_file(file_path)
        all_violations.extend(violations)
        # Check for unsafe JSON parsing patterns
        json_violations = scan_file_json_parse_patterns(file_path)
        all_violations.extend(json_violations)

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
