#!/usr/bin/env python3
"""
Gate detecting forbidden patterns in agent/dispatch prompt templates.

Scans skill files and dispatch template files for patterns that could
lead to unsafe behavior:
- grep/search for API keys or tokens in prompts
- Searching .env files
- Token hunting patterns
- Credential hunting

Fail-closed: exit 1 on violation, exit 0 on success, exit 2 on usage error.
"""

import sys
import os
import re
from pathlib import Path


FORBIDDEN_PATTERNS = [
    # Patterns that look for credentials/keys
    (r"grep\s+.*\b(api_?key|token|secret|password)", "Grep for credentials"),
    (r"find\s+.*\.env", "Searching .env files"),
    (r"look.*for.*api.*key", "Token hunting pattern"),
    (r"search.*credential", "Credential search pattern"),
    (r"find.*secret", "Secret search pattern"),
    (r"grep.*\btoken\b", "Token grep pattern"),
    (r"hunt\s+(for\s+)?(key|token|secret)", "Token hunting"),
    (r"env\s+variable.*secret", "Secret env var pattern"),
]


def normalize_path(p):
    """Normalize path for display."""
    return str(Path(p)).replace("\\", "/")


def check_file_for_patterns(filepath):
    """Check a file for forbidden patterns. Returns (is_clean, violations list)."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        return False, [f"Cannot read file: {e}"]

    violations = []
    lines = content.split("\n")

    for i, line in enumerate(lines, 1):
        # Skip comments and docstrings (heuristic)
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""'):
            continue

        line_lower = line.lower()
        for pattern, description in FORBIDDEN_PATTERNS:
            if re.search(pattern, line_lower, re.IGNORECASE):
                violations.append(f"Line {i}: {description}\n    {line.strip()[:80]}")

    return len(violations) == 0, violations


def find_prompt_files(root_dir="."):
    """Find agent/dispatch prompt template files."""
    root_path = Path(root_dir).resolve()

    prompt_files = []

    # Scan skills/ directory for prompt files
    skills_dir = root_path / "skills"
    if skills_dir.exists():
        for filepath in skills_dir.rglob("*.md"):
            prompt_files.append(filepath)

    # Scan tools/ directory for dispatch templates
    tools_dir = root_path / "tools"
    if tools_dir.exists():
        for filepath in tools_dir.glob("*dispatch*.py"):
            prompt_files.append(filepath)
        # Check for wave template files
        for filepath in tools_dir.glob("*wave*.py"):
            prompt_files.append(filepath)

    # Also check driver/ for agent prompts
    driver_dir = root_path / "driver"
    if driver_dir.exists():
        for filepath in driver_dir.glob("*.py"):
            if "prompt" in filepath.name.lower() or "dispatch" in filepath.name.lower():
                prompt_files.append(filepath)

    return sorted(set(prompt_files))


def main():
    """Main entry point."""
    # Parse CLI args
    root_dir = "."

    for arg in sys.argv[1:]:
        if arg == "--help" or arg == "-h":
            print(__doc__)
            print("\nUsage: agent_prompt_hygiene.py [--help] [root_dir]")
            print("\n  --help   Show this help message")
            print("  root_dir Root directory to scan (default: .)")
            sys.exit(0)
        elif not arg.startswith("-"):
            root_dir = arg

    # Find all prompt files
    prompt_files = find_prompt_files(root_dir)

    if not prompt_files:
        print("No prompt template files found to check", file=sys.stderr)
        sys.exit(1)

    # Check each file
    all_clean = True
    failed_files = []

    for filepath in prompt_files:
        is_clean, violations = check_file_for_patterns(filepath)
        rel_path = normalize_path(filepath.relative_to(Path(root_dir).resolve()))

        if not is_clean:
            all_clean = False
            failed_files.append(rel_path)
            print(f"FAIL: {rel_path}", file=sys.stderr)
            for v in violations:
                print(f"  {v}", file=sys.stderr)
        else:
            print(f"OK: {rel_path}")

    # Summary
    if not all_clean:
        print(
            f"\n{len(failed_files)} prompt file(s) contain forbidden patterns",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\nAll {len(prompt_files)} prompt files passed hygiene check")
    sys.exit(0)


if __name__ == "__main__":
    main()
