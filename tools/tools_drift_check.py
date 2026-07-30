#!/usr/bin/env python3
"""
Tools files drift checker: Guardrail to ensure all tools/*.{py,mjs,sh} files
are documented in tools/CLAUDE.md with backtick-quoted filenames.

This mirrors the CI gate `tests/domain-map-drift.test.mjs` "tools FILES drift"
test, but runs locally as a pre-push check to catch drift before the commit
leaves the developer's machine.

Root cause caught: tools/state_rebuild.py added without documenting in tools/CLAUDE.md
per the cardinal rule "Domain docs stay minimal-but-complete; update this file in
the same PR as code it describes."

Exit codes:
  0: All tools/*.{py,mjs,sh} files documented in tools/CLAUDE.md
  1: One or more files are undocumented (drift detected)
  2: Error (missing CLAUDE.md, read failure, etc.)

Usage:
  python tools/tools_drift_check.py [--json]
  Runs from repo root; searches for tools/*.{py,mjs,sh} files and verifies
  each appears as `filename.ext` in tools/CLAUDE.md

Options:
  --json    Output findings as JSON array of undocumented files
"""

import sys
import json
import re
from pathlib import Path


def get_tools_dir():
    """Resolve tools directory relative to repo root."""
    # Start from cwd and walk up to find .git/
    cwd = Path.cwd()
    while cwd != cwd.parent:
        if (cwd / ".git").exists():
            return cwd / "tools"
        cwd = cwd.parent
    # Fallback to cwd/tools
    return Path.cwd() / "tools"


def get_documented_files(claude_md_path):
    """Extract backtick-quoted filenames from tools/CLAUDE.md.

    Looks for patterns like `filename.py`, `script.sh`, `tool.mjs` and returns
    the set of documented filenames.

    Args:
        claude_md_path (Path): Path to tools/CLAUDE.md

    Returns:
        set: Set of documented filenames (e.g., {'secret_scan.py', 'lock.mjs'})
    """
    try:
        content = claude_md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise RuntimeError(f"Failed to read {claude_md_path}: {e}")

    # Extract backtick-quoted filenames: `filename.ext`
    # Pattern matches backtick-quoted words ending in .py, .mjs, or .sh
    pattern = r"`([a-zA-Z0-9_-]+\.(py|mjs|sh))`"
    matches = re.findall(pattern, content)

    # matches is a list of tuples: [(filename, extension), ...]
    # Extract just the filenames
    documented = {match[0] for match in matches}
    return documented


def find_tool_files(tools_dir):
    """Find all .py, .mjs, .sh files in the tools directory.

    Args:
        tools_dir (Path): Path to tools directory

    Returns:
        list: Sorted list of filenames (strings, not full paths)
    """
    if not tools_dir.exists():
        return []

    tool_files = []
    for ext in ("*.py", "*.mjs", "*.sh"):
        for file_path in tools_dir.glob(ext):
            if file_path.is_file():
                tool_files.append(file_path.name)

    return sorted(tool_files)


def check_drift(tools_dir, claude_md_path, json_output=False):
    """Check for undocumented tool files.

    Args:
        tools_dir (Path): Path to tools directory
        claude_md_path (Path): Path to tools/CLAUDE.md
        json_output (bool): If True, output findings as JSON

    Returns:
        tuple: (exit_code, findings_list)
          exit_code: 0 (clean), 1 (drift found), 2 (error)
          findings_list: List of undocumented filenames
    """
    if not claude_md_path.exists():
        if json_output:
            print(json.dumps({"error": f"tools/CLAUDE.md not found at {claude_md_path}"}))
        else:
            print(f"Error: tools/CLAUDE.md not found at {claude_md_path}", file=sys.stderr)
        return 2, []

    try:
        documented = get_documented_files(claude_md_path)
    except RuntimeError as e:
        if json_output:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 2, []

    tool_files = find_tool_files(tools_dir)

    # Find undocumented files
    undocumented = [f for f in tool_files if f not in documented]

    if undocumented:
        if json_output:
            print(json.dumps({"undocumented": sorted(undocumented)}))
        else:
            print(
                "tools/*.{py,mjs,sh} files missing from tools/CLAUDE.md FILES section:",
                file=sys.stderr,
            )
            for filename in sorted(undocumented):
                print(f"  tools/{filename}", file=sys.stderr)
            print(
                "\nAdd these to the '## Tool index' section in tools/CLAUDE.md with a one-line purpose.",
                file=sys.stderr,
            )
        return 1, undocumented

    return 0, []


def main():
    """Main entry point."""
    json_output = "--json" in sys.argv
    help_requested = "--help" in sys.argv or "-h" in sys.argv

    if help_requested:
        print(__doc__)
        sys.exit(0)

    tools_dir = get_tools_dir()
    claude_md_path = tools_dir.parent / "tools" / "CLAUDE.md"

    exit_code, findings = check_drift(tools_dir, claude_md_path, json_output)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
