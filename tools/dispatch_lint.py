#!/usr/bin/env python3
"""Dispatch linter — enforces merge automation and security rules for agent prompts.

Scans Python/JS/MD files for agent dispatch patterns and flags FORBIDDEN patterns:
  - `gh pr merge` (must use tools/auto_merge.py instead)
  - `--admin` flag (merge automation bypass)
  - `--auto` flag (merge automation bypass)
  - `--no-verify` flag (pre-commit hook bypass)
  - `--force` in git context (dangerous history rewrite)
  - `git stash` (shared across worktrees, cross-contamination risk)
  - Credential/key hunting patterns (find.*key, grep.*token, env.*KEY)

Modes:
  dispatch_lint.py --check [PATH]          Exit 1 if violations found
  dispatch_lint.py --fix [PATH]            Show suggested fixes
  dispatch_lint.py --json [PATH]           Output violations as JSON
  dispatch_lint.py [PATH]                  Default: check mode on cwd

Suppression:
  Add '# dispatch-ok' on the line with a violation to suppress it.

Exit: 0=clean, 1=violations found, 2=error
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional


# Forbidden patterns and their suggested fixes
FORBIDDEN_PATTERNS = {
    "gh_pr_merge": {
        "pattern": r"\bgh\s+pr\s+merge\b",
        "description": "Use tools/auto_merge.py instead of manual gh pr merge",
        "fix": "Replace with: python tools/auto_merge.py -u <pr-number>",
    },
    "admin_flag": {
        "pattern": r"--admin\b",
        "description": "Merge automation bypass flag forbidden in dispatch prompts",
        "fix": "Remove --admin flag; use merge automation instead",
    },
    "auto_flag": {
        "pattern": r"--auto\b",
        "description": "Merge automation bypass flag forbidden in dispatch prompts",
        "fix": "Remove --auto flag; use merge automation instead",
    },
    "no_verify_flag": {
        "pattern": r"--no-verify\b",
        "description": "Pre-commit hook bypass forbidden (security gate)",
        "fix": "Remove --no-verify flag; commit must pass security scanning",
    },
    "force_flag": {
        "pattern": r"\bgit\s+.*\s+--force\b|\bgit\s+.*\s+-f\b",
        "description": "Dangerous history rewrite flag forbidden",
        "fix": "Remove --force/-f flag; use safe git operations",
    },
    "git_stash": {
        "pattern": r"\bgit\s+stash\b",
        "description": "git stash is shared across worktrees; causes cross-contamination",
        "fix": "Use diff>patch + checkout + apply instead; see MEMORY.md",
    },
    "find_key_hunting": {
        "pattern": r"\bfind\s+.*\s+\(-\w*name\w*\s+.*['\"]?[^'\"]*(?:key|secret|token|password)[^'\"]*['\"]?",
        "description": "Credential hunting pattern forbidden (missing key = SKIP, never search)",
        "fix": "Specify exact transport and allowed env vars instead; see no-credential-hunting memory",
    },
    "grep_token_hunting": {
        "pattern": r"\bgrep\s+.*(?:token|secret|password|api[_-]?key|auth)\b",
        "description": "Credential hunting pattern forbidden",
        "fix": "Specify exact transport and allowed env vars instead",
    },
    "env_key_hunting": {
        "pattern": r"\benv\s+.*\b(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)\b",
        "description": "Credential hunting pattern forbidden",
        "fix": "Name exact env vars, never scan all env vars",
    },
}

# File patterns to scan (glob patterns, not regex)
SCANNABLE_GLOB_PATTERNS = ["*.py", "*.js", "*.mjs", "*.md", "*.sh"]

# Patterns indicating dispatch context (must be in file to trigger full scan)
DISPATCH_INDICATORS = [
    r"\bAgent\s*\(",
    r"\bagent\s*\(",
    r"\bTaskCreate\s*\(",
    r"\bSendMessage\s*\(",
    r"agent\(\)",
    r"/\*\s*dispatch",
    r"dispatch\s*{",
]


def is_dispatch_file(content: str) -> bool:
    """Check if file contains dispatch-related code."""
    for indicator in DISPATCH_INDICATORS:
        if re.search(indicator, content):
            return True
    return False


def check_suppression(line: str) -> bool:
    """Check if line has # dispatch-ok suppression."""
    return "# dispatch-ok" in line or "// dispatch-ok" in line


def find_violations(
    file_path: Path, content: str
) -> List[Dict]:
    """Find all dispatch policy violations in a file."""
    violations = []

    # Don't scan files that don't contain dispatch patterns
    if not is_dispatch_file(content):
        return violations

    lines = content.split("\n")

    for line_num, line in enumerate(lines, 1):
        # Skip suppressed lines
        if check_suppression(line):
            continue

        for pattern_key, pattern_info in FORBIDDEN_PATTERNS.items():
            if re.search(pattern_info["pattern"], line, re.IGNORECASE):
                violations.append({
                    "file": str(file_path),
                    "line": line_num,
                    "pattern": pattern_key,
                    "description": pattern_info["description"],
                    "fix": pattern_info["fix"],
                    "code": line.strip(),
                })

    return violations


def scan_directory(
    start_path: Path, recursive: bool = True
) -> Tuple[Dict[str, List], List[str]]:
    """Scan directory for dispatch violations.

    Returns: (violations_by_file, errors)
    """
    violations_by_file = {}
    errors = []

    if not start_path.exists():
        errors.append(f"Path does not exist: {start_path}")
        return violations_by_file, errors

    if start_path.is_file():
        paths_to_scan = [start_path]
    else:
        # Find all scannable files
        paths_to_scan = []
        for pattern in SCANNABLE_GLOB_PATTERNS:
            if recursive:
                paths_to_scan.extend(start_path.glob(f"**/{pattern}"))
            else:
                paths_to_scan.extend(start_path.glob(pattern))

    for file_path in sorted(set(paths_to_scan)):
        # Skip certain directories
        if any(skip in str(file_path) for skip in [".git", "node_modules", ".pytest_cache", "state"]):
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except (UnicodeDecodeError, PermissionError) as e:
            errors.append(f"Error reading {file_path}: {e}")
            continue

        violations = find_violations(file_path, content)
        if violations:
            violations_by_file[str(file_path)] = violations

    return violations_by_file, errors


def format_violations(violations_by_file: Dict, as_json: bool = False) -> str:
    """Format violations for output."""
    if as_json:
        result = {
            "violations": [],
            "total_violations": 0,
        }
        for file_path, violations in violations_by_file.items():
            for v in violations:
                result["violations"].append(v)
        result["total_violations"] = len(result["violations"])
        return json.dumps(result, indent=2)

    lines = []
    for file_path, violations in violations_by_file.items():
        lines.append(f"{file_path}:")
        for v in violations:
            lines.append(f"  Line {v['line']}: {v['pattern']}")
            lines.append(f"    {v['description']}")
            lines.append(f"    Code: {v['code']}")
            lines.append(f"    Fix: {v['fix']}")
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Dispatch linter — enforces merge automation and security rules"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="File or directory to scan (default: current directory)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with code 1 if violations found (default behavior)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Show suggested fixes for violations",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output violations as JSON",
    )

    args = parser.parse_args()

    start_path = Path(args.path).resolve()
    violations_by_file, errors = scan_directory(start_path)

    # Print errors
    if errors:
        for error in errors:
            print(f"Warning: {error}", file=sys.stderr)

    # Print violations
    if violations_by_file:
        output = format_violations(violations_by_file, as_json=args.json)
        print(output)
        return 1

    # Clean
    if args.json:
        print(json.dumps({"violations": [], "total_violations": 0}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
