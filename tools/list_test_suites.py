#!/usr/bin/env python3
"""
Generate a live inventory of all test suites by scanning the filesystem.
INDEX: Generate live test suite inventory: scans filesystem for test files (tests/*.test.mjs, tests/test_*.py, tests/*.test.sh, tests/test_*.sh, tests/test-*.sh, hooks/pre-push-policy.sh --test) and outputs grouped listing with first-line doc summaries; ASCII-safe, deterministic; CLI: `list_test_suites.py [--repo ROOT]`; used in tests/CLAUDE.md docs and CI coverage gates; replaces hand-maintained suite listings (kills conflict magnet in merge conflicts)

Discovers test files from:
- tests/*.test.mjs (Node.js)
- tests/test_*.py (Python)
- tests/*.test.sh, tests/test_*.sh, tests/test-*.sh (Shell)
- hooks/pre-push-policy.sh (with --test flag)

Groups by type with each file's first docstring/comment line if available.

Usage:
    python tools/list_test_suites.py [--repo ROOT]

Output: Grouped inventory with counts and first-line summaries (read-only, deterministic, ASCII).
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple


def sanitize_to_ascii(text: str) -> str:
    """Remove non-ASCII characters from text."""
    return "".join(c if ord(c) < 128 else "" for c in text)


def get_first_doc_line(file_path: Path) -> str:
    """Extract first docstring/comment line from a file.

    For Python: looks for triple-quoted docstrings or # comments.
    For shell/Node: looks for # comments or /* */ block comments.
    Returns empty string if no doc found. Non-ASCII characters are filtered.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        lines = content.split("\n")

        for i, line in enumerate(lines[:50]):  # Check first 50 lines
            stripped = line.strip()

            # Skip shebangs and empty lines
            if not stripped or stripped.startswith("#!"):
                continue

            # Python: triple-quoted docstrings
            if stripped.startswith('"""') or stripped.startswith("'''"):
                quote = '"""' if stripped.startswith('"""') else "'''"
                # Extract content between quotes
                rest = stripped[3:]
                if quote in rest:
                    return sanitize_to_ascii(rest[: rest.index(quote)].strip())
                # Multi-line docstring: look for closing quote
                for j in range(i + 1, min(i + 10, len(lines))):
                    if quote in lines[j]:
                        full_doc = " ".join(
                            [rest] + lines[i + 1 : j]
                        ).split(quote)[0]
                        return sanitize_to_ascii(full_doc.strip())
                continue

            # Comment line: # or //
            if stripped.startswith("#"):
                doc = stripped[1:].strip()
                if doc and not doc.startswith("!"):  # Skip shebang
                    return sanitize_to_ascii(doc)
            if stripped.startswith("//"):
                return sanitize_to_ascii(stripped[2:].strip())

            # Block comment start: /* or /*! or /**
            if stripped.startswith("/*"):
                rest = stripped[2:]
                if "*/" in rest:
                    return sanitize_to_ascii(rest[: rest.index("*/")].strip())
                # Multi-line block
                for j in range(i + 1, min(i + 10, len(lines))):
                    if "*/" in lines[j]:
                        full_doc = " ".join([rest] + lines[i + 1 : j]).split("*/")[0]
                        return sanitize_to_ascii(full_doc.strip())

    except Exception:
        pass

    return ""


def scan_test_files(repo_root: Path) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]], List[Tuple[str, str]], bool]:
    """Scan repo for test files.

    Returns:
        (node_files, shell_files, python_files, has_hook_test)
        Each file tuple: (relative_path, first_doc_line)
        has_hook_test: True if hooks/pre-push-policy.sh exists and supports --test
    """
    tests_dir = repo_root / "tests"
    hooks_dir = repo_root / "hooks"

    node_files = []
    shell_files = []
    python_files = []

    # Scan Node.js tests (*.test.mjs)
    for f in sorted(tests_dir.glob("*.test.mjs")):
        doc = get_first_doc_line(f)
        node_files.append((str(f.relative_to(repo_root)), doc))

    # Scan Python tests (test_*.py)
    for f in sorted(tests_dir.glob("test_*.py")):
        doc = get_first_doc_line(f)
        python_files.append((str(f.relative_to(repo_root)), doc))

    # Scan Shell tests (*.test.sh, test_*.sh, test-*.sh)
    shell_patterns = ["*.test.sh", "test_*.sh", "test-*.sh"]
    for pattern in shell_patterns:
        for f in sorted(tests_dir.glob(pattern)):
            rel_path = str(f.relative_to(repo_root))
            if not any(rel_path == existing[0] for existing in shell_files):
                doc = get_first_doc_line(f)
                shell_files.append((rel_path, doc))

    # Check for hooks/pre-push-policy.sh --test support
    hook_file = hooks_dir / "pre-push-policy.sh"
    has_hook_test = False
    if hook_file.exists():
        try:
            content = hook_file.read_text(encoding="utf-8")
            if '--test"' in content or "== \"--test\"" in content:
                has_hook_test = True
        except Exception:
            pass

    return node_files, shell_files, python_files, has_hook_test


def format_inventory(
    node_files: List[Tuple[str, str]],
    shell_files: List[Tuple[str, str]],
    python_files: List[Tuple[str, str]],
    has_hook_test: bool,
) -> str:
    """Format the inventory as human-readable markdown.

    Note: hook_test is listed separately but NOT included in the shell_count
    (count matches files in tests/ only, per verify_test_suite_count.py).
    """
    output = []

    output.append("# Test Suite Inventory (Auto-Generated)")
    output.append("")

    # Node.js
    output.append(f"## Node.js ({len(node_files)} suites)")
    for path, doc in node_files:
        if doc:
            output.append(f"- {path}: {doc}")
        else:
            output.append(f"- {path}")
    output.append("")

    # Shell: count is just the files, not the hook
    output.append(f"## Shell ({len(shell_files)} suites)")
    for path, doc in shell_files:
        if doc:
            output.append(f"- {path}: {doc}")
        else:
            output.append(f"- {path}")
    if has_hook_test:
        output.append("- hooks/pre-push-policy.sh --test: Secret-scan gate validation (run as part of shell suite)")
    output.append("")

    # Python
    output.append(f"## Python ({len(python_files)} suites)")
    for path, doc in python_files:
        if doc:
            output.append(f"- {path}: {doc}")
        else:
            output.append(f"- {path}")
    output.append("")

    output.append("=" * 40)
    output.append("")
    # Total counts match what verify_test_suite_count.py would report (hook not counted)
    output.append(
        f"Total: {len(node_files)} Node + {len(shell_files)} Shell + {len(python_files)} Python"
    )

    return "\n".join(output)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root (default: current directory)",
    )

    args = parser.parse_args()
    repo_root = (args.repo or Path.cwd()).resolve()

    node_files, shell_files, python_files, has_hook_test = scan_test_files(repo_root)

    inventory = format_inventory(node_files, shell_files, python_files, has_hook_test)
    sys.stdout.write(inventory)
    sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
