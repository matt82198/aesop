#!/usr/bin/env python3
"""
Bash BASH_SOURCE exec guard validator.

Validates that sourceable shell scripts with both function definitions and
top-level executable commands have BASH_SOURCE exec guards to prevent
accidental execution when sourced.

Background: Wave-25 incident — backup-fleet.sh sourced for function-only
testing triggered a real backup cycle force-pushing to 7 remotes. Scripts
that define functions AND have top-level executable commands need a guard.

Pattern: [[ $0 == "${BASH_SOURCE[0]}" ]] or equivalent.
Guard must appear before any executable commands.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set


def find_shell_files(root_dirs: List[str]) -> List[str]:
    """Find all .sh and .bash files in given directories."""
    files = []
    for root in root_dirs:
        root_path = Path(root)
        if not root_path.exists():
            continue
        for pattern in ("**/*.sh", "**/*.bash"):
            files.extend(str(f) for f in root_path.glob(pattern) if f.is_file())
    return sorted(files)


def has_suppression(lines: List[str]) -> bool:
    """Check if file has guard-ok suppression in first 3 lines."""
    for i, line in enumerate(lines[:3]):
        if "guard-ok" in line:
            return True
    return False


def extract_functions(lines: List[str]) -> Set[int]:
    """Extract line numbers where functions are defined.

    Returns set of line numbers (1-indexed) containing function definitions.
    """
    function_lines = set()
    for i, line in enumerate(lines, 1):
        # Match: function_name() or function func_name
        if re.search(r'^\s*\w+\s*\(\s*\)\s*\{', line):
            # function_name() {
            function_lines.add(i)
        elif re.search(r'^\s*function\s+\w+', line):
            # function func_name { or function func_name()
            function_lines.add(i)
    return function_lines


def is_executable_command(line: str) -> bool:
    """Check if a line is an executable command (not just def/assign/comment)."""
    stripped = line.strip()

    # Skip empty lines and comments
    if not stripped or stripped.startswith('#'):
        return False

    # Skip if it's a BASH_SOURCE guard (special case)
    if 'BASH_SOURCE' in stripped and '$0' in stripped:
        return False

    # Skip structural keywords
    if re.match(r'^\s*(function\s+|\w+\s*\(\s*\)|\s*if\s+|\s*then\s*$|\s*elif\s+|\s*else\s*$|\s*while\s+|\s*until\s+|\s*for\s+|\s*do\s*$|\s*case\s+|\s*in\s*$|\s*{|\s*}\s*$|\s*}\s*&)', stripped):
        return False

    # Skip lines that are just closing braces or continuation keywords
    if stripped in ('}', 'done', 'fi', 'esac', ')', ';;'):
        return False

    # Skip pure variable assignments (X=value or X="value" but not with pipes/redirects/operators)
    if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', stripped):
        # Check if it's just an assignment (no command after it)
        # Allow: VAR=value, VAR="${FOO:-.}", etc.
        # Reject: VAR=value | cmd, VAR=value && cmd, etc.
        if not any(op in stripped for op in ['|', '&&', '||', '>', '<', ';']):
            return False

    # Remaining lines are executable commands
    return True


def find_executable_commands(lines: List[str]) -> Set[int]:
    """Find line numbers of executable commands (excluding function defs).

    Only considers top-level commands (no indentation) outside function bodies.
    """
    executable_lines = set()
    function_defs = extract_functions(lines)

    # Build a set of lines that are part of function bodies
    function_body_lines = set()
    for func_line in function_defs:
        brace_depth = 0
        for i in range(func_line - 1, len(lines)):
            line = lines[i]
            stripped = line.strip()

            # Count braces on the function definition line
            if i + 1 == func_line:
                brace_depth = stripped.count('{') - stripped.count('}')
                # If braces balance on the same line, function is complete
                if brace_depth <= 0:
                    break
                continue

            # For lines inside the function body
            brace_depth += stripped.count('{') - stripped.count('}')
            function_body_lines.add(i + 1)

            # Stop when we close all braces
            if brace_depth <= 0:
                break

    # Now find top-level executable commands (not in functions, not indented)
    for i, line in enumerate(lines, 1):
        # Skip if in function body
        if i in function_body_lines or i in function_defs:
            continue

        # Only consider lines with no leading whitespace (top-level)
        if line and line[0] in (' ', '\t'):
            continue

        # Check if this is an executable command
        if is_executable_command(line):
            executable_lines.add(i)

    return executable_lines


def find_bash_source_guard(lines: List[str]) -> Optional[int]:
    """Find line number of BASH_SOURCE guard (if present).

    Returns line number (1-indexed) of first guard found, or None.
    Guards match patterns like:
      [[ $0 == "${BASH_SOURCE[0]}" ]]
      [[ "${BASH_SOURCE[0]}" == "${0}" ]]
      if [[ ... ]]; then ... fi
    """
    # Look for both BASH_SOURCE and $0 on the same line (not necessarily in that order)
    bash_source_pattern = re.compile(r'BASH_SOURCE', re.IGNORECASE)
    dollar_zero_pattern = re.compile(r'\$0|\$\{0\}')
    equality_pattern = re.compile(r'==|!=')

    for i, line in enumerate(lines, 1):
        # A guard line must have BASH_SOURCE, $0, and an equality check
        if (bash_source_pattern.search(line) and
            dollar_zero_pattern.search(line) and
            equality_pattern.search(line)):
            return i
    return None


def check_file(filepath: str) -> Tuple[bool, Optional[str]]:
    """Check if a file has proper BASH_SOURCE guard (if needed).

    Returns (is_clean, error_message).
    - is_clean=True: No guard needed or guard found before commands
    - is_clean=False: Guard missing or misplaced
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except (IOError, OSError) as e:
        return False, f"Cannot read file: {e}"

    # Check for suppression
    if has_suppression(lines):
        return True, None

    # Extract function defs and executable commands
    function_defs = extract_functions(lines)
    executable_cmds = find_executable_commands(lines)

    # If no functions OR no executable commands, no guard needed
    if not function_defs or not executable_cmds:
        return True, None

    # File has both functions and executable commands - guard required
    guard_line = find_bash_source_guard(lines)

    if guard_line is None:
        min_cmd_line = min(executable_cmds)
        return False, f"Missing BASH_SOURCE guard (has functions at {min(function_defs)} and commands at {min_cmd_line})"

    # Guard found - check it comes before or on same line as executable commands
    # If guard and command are on the same line and connected by && or ||, it's valid
    min_cmd_line = min(executable_cmds)
    if guard_line < min_cmd_line:
        return True, None
    elif guard_line == min_cmd_line:
        # Guard and command on same line - check if they're connected by && or ||
        line = lines[guard_line - 1]
        if '&&' in line or '||' in line:
            return True, None
        else:
            return False, f"Guard and command on same line but not connected by && or ||"
    else:
        return False, f"Guard at line {guard_line} comes after executable command at line {min_cmd_line}"


def main():
    parser = argparse.ArgumentParser(
        description='Validate BASH_SOURCE exec guards in shell scripts'
    )
    parser.add_argument('--check', action='store_true', default=True,
                        help='Check mode (default)')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')
    parser.add_argument('--paths', nargs='+', default=None,
                        help='Directories to scan (default: daemons/ tools/ scripts/ hooks/)')
    parser.add_argument('--root', default='.',
                        help='Root directory for relative paths (default: .)')

    args = parser.parse_args()

    # Determine directories to scan
    if args.paths:
        scan_dirs = args.paths
    else:
        scan_dirs = ['daemons', 'tools', 'scripts', 'hooks']

    # Convert to absolute paths relative to root
    root = Path(args.root)
    scan_dirs = [str(root / d) for d in scan_dirs]

    # Find all shell files
    files = find_shell_files(scan_dirs)

    # Check each file
    findings = []
    clean_count = 0

    for filepath in files:
        is_clean, error_msg = check_file(filepath)
        if is_clean:
            clean_count += 1
        else:
            rel_path = os.path.relpath(filepath, args.root)
            findings.append({
                'file': rel_path,
                'error': error_msg
            })

    # Output results
    if args.json:
        result = {
            'clean': clean_count,
            'total': len(files),
            'findings': findings
        }
        print(json.dumps(result, indent=2))
    else:
        if findings:
            for finding in findings:
                print(f"{finding['file']}: {finding['error']}")

    # Exit codes: 0=clean, 1=findings, 2=error
    if findings:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
