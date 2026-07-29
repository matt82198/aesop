#!/usr/bin/env python3
"""
Validate domain CLAUDE.md files for minimum contract sections.

Enforces:
1. Purpose statement (marked by "What" or equivalent heading)
2. Key files/ownership section (headers mentioning files, invariants, rules, etc.)
3. Non-empty content

Fail-closed: exit 1 on violation, exit 2 on usage error, exit 0 on success.
"""

import sys
import os
import re
from pathlib import Path


def normalize_path(p):
    """Normalize path for display."""
    return str(Path(p)).replace("\\", "/")


def has_purpose_statement(content):
    """Check if file has a purpose statement (e.g., **What**:, **Purpose**:, or heading with dash)."""
    # Look for purpose in multiple formats:
    # 1. **What**: or **Purpose**: patterns
    # 2. Heading with dash (# domain/ - or # domain/ — description format)
    lines = content.split("\n")
    for i, line in enumerate(lines[:20]):  # Check first 20 lines
        # Check for **What**: or **Purpose**: patterns
        if "**What**" in line or "**Purpose**" in line:
            if len(line) > 10:
                return True
        # Check for heading patterns with dash/em-dash: # domain/ - or # domain/ —
        # Pattern: # followed by word chars or /, followed by dash (- or em-dash —), followed by description
        if re.search(r"^#\s+[\w/]+\s*[-–—]\s+\w", line):
            if len(line) > 15:  # Heading + dash + meaningful description
                return True
    return False


def has_key_sections(content):
    """Check if file has key sections describing domain (rules, files, invariants)."""
    # Look for headers describing domain structure: rules, files, invariants, ownership, contracts
    keywords = [
        "universal rules",
        "key invariants",
        "core invariants",
        "contracts",
        "purpose",
        "files",
        "ownership",
        "design",
        "mechanism",
        "specification",
        "definitions",
    ]

    content_lower = content.lower()
    # Count how many keyword sections are present
    found_count = sum(1 for kw in keywords if re.search(r"^## .*" + kw, content_lower, re.MULTILINE))

    # At least one key section should exist
    return found_count > 0


def is_not_empty(content):
    """Check if file has meaningful content (more than just whitespace/heading)."""
    meaningful = content.strip()
    # Must have at least 50 characters of meaningful content
    return len(meaningful) > 50


def validate_file(filepath):
    """Validate a single CLAUDE.md file. Returns (is_valid, violations list)."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return False, [f"Cannot read file: {e}"]

    violations = []

    if not is_not_empty(content):
        violations.append("File is empty or contains only whitespace")

    if not has_purpose_statement(content):
        violations.append('Missing purpose statement (look for "**What**:" or equivalent heading)')

    if not has_key_sections(content):
        violations.append(
            "Missing key sections (must have at least one of: "
            "Universal rules, Key invariants, Contracts, Purpose, Files, etc.)"
        )

    return len(violations) == 0, violations


def find_domain_claude_files(root_dir="."):
    """Find all domain CLAUDE.md files (excluding root CLAUDE.md)."""
    root_path = Path(root_dir).resolve()
    root_claude = root_path / "CLAUDE.md"

    domain_files = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        if "CLAUDE.md" in filenames:
            filepath = Path(dirpath) / "CLAUDE.md"
            # Skip root CLAUDE.md and .git directories
            if filepath != root_claude and ".git" not in str(filepath):
                domain_files.append(filepath)

    return sorted(domain_files)


def main():
    """Main entry point."""
    # Parse CLI args
    check_mode = True
    root_dir = "."

    for arg in sys.argv[1:]:
        if arg == "--help" or arg == "-h":
            print(__doc__)
            print("\nUsage: claudemd_contract.py [--help] [--regenerate] [root_dir]")
            print("\n  --help           Show this help message")
            print("  --regenerate     Not implemented (reserved)")
            print("  root_dir         Root directory to scan (default: .)")
            sys.exit(0)
        elif arg == "--regenerate":
            print("Error: --regenerate is not yet implemented", file=sys.stderr)
            sys.exit(2)
        elif not arg.startswith("-"):
            root_dir = arg

    # Find all domain CLAUDE.md files
    domain_files = find_domain_claude_files(root_dir)

    if not domain_files:
        print("No domain CLAUDE.md files found", file=sys.stderr)
        sys.exit(1)

    # Validate each file
    all_valid = True
    failed_files = []

    for filepath in domain_files:
        is_valid, violations = validate_file(filepath)
        rel_path = normalize_path(filepath.relative_to(Path(root_dir).resolve()))

        if not is_valid:
            all_valid = False
            failed_files.append(rel_path)
            print(f"FAIL: {rel_path}", file=sys.stderr)
            for v in violations:
                print(f"  - {v}", file=sys.stderr)
        else:
            print(f"OK: {rel_path}")

    # Summary
    if not all_valid:
        print(f"\n{len(failed_files)} domain CLAUDE.md files failed validation", file=sys.stderr)
        sys.exit(1)

    print(f"\nAll {len(domain_files)} domain CLAUDE.md files passed validation")
    sys.exit(0)


if __name__ == "__main__":
    main()
