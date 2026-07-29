#!/usr/bin/env python3
"""
Gate detecting forbidden patterns in agent/dispatch prompt templates.

Scans skill files and dispatch template files for patterns that could
lead to unsafe behavior:
- grep/search for API keys or tokens in prompts
- Searching .env files
- Token hunting patterns
- Credential hunting
- File read/cat operations on credential files

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
    # Natural language file operations: cat/open/print/copy the .env file
    (r"(cat|open|print|copy)\s+.*\.env", "Reading credential file"),
]

# Negation keywords that indicate policy/documentation rather than instructions
NEGATION_CONTEXT = {
    "never",
    "do not",
    "not",
    "don't",
    "forbidden",
    "must not",
    "prohibited",
    "should not",
    "shouldn't",
    "cannot",
    "can't",
}


def normalize_path(p):
    """Normalize path for display."""
    return str(Path(p)).replace("\\", "/")


def has_negation_context(line):
    """Check if line contains negation keywords (policy/documentation)."""
    line_lower = line.lower()
    for keyword in NEGATION_CONTEXT:
        if keyword in line_lower:
            return True
    return False


def check_file_for_patterns(filepath):
    """Check a file for forbidden patterns. Returns (is_clean, violations list)."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        return False, [f"Cannot read file: {e}"]

    violations = []
    lines = content.split("\n")

    # Multi-line scan window for semantic patterns (e.g., file ops + credential paths)
    window_size = 3

    for i, line in enumerate(lines, 1):
        # Skip comments and docstrings (heuristic)
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""'):
            continue

        # Skip lines with # hygiene-ok suppression comment
        if "# hygiene-ok" in line or "#hygiene-ok" in line:
            continue

        # Skip lines with negation context (policy documentation)
        if has_negation_context(line):
            continue

        line_lower = line.lower()

        # Check against single-line patterns
        for pattern, description in FORBIDDEN_PATTERNS:
            if re.search(pattern, line_lower, re.IGNORECASE):
                violations.append(f"Line {i}: {description}\n    {line.strip()[:80]}")
                break  # Only report first match per line

        # Semantic multi-line scan: catch split-line credential hunting patterns
        # Case 1: Action verb (read/get/fetch) + credential file path on next line
        # Case 2: Credential keywords (api/secret/key/token) on this line + file path next line
        action_verbs = ["read", "get", "fetch", "find", "look", "grep", "search", "scan"]
        cred_keywords = ["api", "secret", "key", "token", "password"]
        file_paths = [".env", ".local", ".secrets", ".credentials"]

        # Check for action verbs + file paths
        has_action_verb = any(verb in line_lower for verb in action_verbs)
        has_cred_keyword = any(kw in line_lower for kw in cred_keywords)

        if has_action_verb or has_cred_keyword:
            # Scan forward for file path indicators (look at next 3 lines)
            for j in range(i + 1, min(i + window_size + 1, len(lines) + 1)):
                # Convert 1-indexed j to 0-indexed for lines array access
                future_line = lines[j - 1].lower()

                # Look for credential file paths
                if any(path in future_line for path in file_paths):
                    # Exclude patterns with environment variable reads (legitimate)
                    if "os.environ" in lines[j - 1] or "getenv" in lines[j - 1]:
                        continue

                    # Exclude patterns with negation or suppression
                    if "# hygiene-ok" not in lines[j - 1] and not has_negation_context(
                        lines[j - 1]
                    ):
                        violations.append(
                            f"Line {i}-{j}: Credential hunting pattern across lines\n    {line.strip()[:60]}...\n    {lines[j - 1].strip()[:60]}"
                        )
                        break

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

    # Scan ALL driver/ files (F3 fix: not just prompt/dispatch-named files)
    driver_dir = root_path / "driver"
    if driver_dir.exists():
        for filepath in driver_dir.glob("*.py"):
            prompt_files.append(filepath)

    # Scan monitor/ files (F3 fix: also monitor/ directory)
    monitor_dir = root_path / "monitor"
    if monitor_dir.exists():
        for filepath in monitor_dir.glob("*.py"):
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
