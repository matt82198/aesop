#!/usr/bin/env python3
"""
Test coverage gap finder: identifies Python source files with no corresponding test file.
INDEX: Test coverage gap finder (identifies untested modules)

Scans source directories (tools/, ui/, driver/, state_store/, bench/, monitor/, scan/,
mcp/, hooks/) for .py files and checks whether a corresponding test_*.py file exists
under tests/. Reports uncovered files and a coverage percentage.

Supports `# coverage-ok` in the first 5 lines of a source file to suppress it.

Usage:
  python tools/test_coverage_gaps.py                # report gaps
  python tools/test_coverage_gaps.py --check        # exit 1 if any gaps
  python tools/test_coverage_gaps.py --json          # JSON output
  python tools/test_coverage_gaps.py --threshold 80  # exit 1 if below 80%

Exit codes:
  0: above threshold (or report-only mode with no --check)
  1: below threshold, or gaps found with --check
  2: error
"""

import json
import os
import sys
from pathlib import Path

# Directories containing source .py files to check
SOURCE_DIRS = [
    "tools",
    "ui",
    "driver",
    "state_store",
    "bench",
    "monitor",
    "scan",
    "mcp",
    "hooks",
]

# Files that are not expected to have dedicated test files
SKIP_PATTERNS = {
    "__init__.py",
    "common.py",
}


def find_source_files(root):
    """Find all Python source files in source directories."""
    sources = []
    for src_dir in SOURCE_DIRS:
        dir_path = Path(root) / src_dir
        if not dir_path.is_dir():
            continue
        for py_file in sorted(dir_path.glob("*.py")):
            if py_file.name in SKIP_PATTERNS:
                continue
            rel = py_file.relative_to(root)
            sources.append(str(rel).replace("\\", "/"))
    return sources


def has_coverage_ok(filepath):
    """Check if file has # coverage-ok in its first 5 lines."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 5:
                    break
                if "# coverage-ok" in line:
                    return True
    except (OSError, UnicodeDecodeError):
        pass
    return False


def find_test_candidates(source_rel):
    """Generate candidate test file names for a source file.

    tools/foo.py -> [tests/test_tools_foo.py, tests/test_foo.py]
    ui/bar.py -> [tests/test_ui_bar.py, tests/test_bar.py]
    state_store/baz.py -> [tests/test_state_store_baz.py, tests/test_baz.py]
    """
    parts = source_rel.replace("\\", "/").split("/")
    stem = Path(parts[-1]).stem
    if len(parts) == 2:
        domain = parts[0]
        return [
            f"tests/test_{domain}_{stem}.py",
            f"tests/test_{stem}.py",
        ]
    return [f"tests/test_{stem}.py"]


def analyze_coverage(root):
    """Analyze test coverage gaps.

    Returns (covered, uncovered, suppressed) where each is a list of
    source-relative paths.
    """
    root = Path(root)
    sources = find_source_files(root)
    covered = []
    uncovered = []
    suppressed = []

    for src in sources:
        src_path = root / src
        if has_coverage_ok(src_path):
            suppressed.append(src)
            continue

        candidates = find_test_candidates(src)
        found = False
        for candidate in candidates:
            if (root / candidate).is_file():
                found = True
                break
        if found:
            covered.append(src)
        else:
            uncovered.append(src)

    return covered, uncovered, suppressed


def main():
    args = sys.argv[1:]

    check_mode = False
    json_mode = False
    threshold = 0
    root = "."

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--check":
            check_mode = True
        elif arg == "--json":
            json_mode = True
        elif arg == "--threshold":
            i += 1
            if i >= len(args):
                print("error: --threshold requires a value", file=sys.stderr)
                sys.exit(2)
            try:
                threshold = int(args[i])
            except ValueError:
                print(f"error: invalid threshold value: {args[i]}", file=sys.stderr)
                sys.exit(2)
        elif arg == "--root":
            i += 1
            if i >= len(args):
                print("error: --root requires a value", file=sys.stderr)
                sys.exit(2)
            root = args[i]
        elif arg == "--help":
            print(__doc__.strip())
            sys.exit(0)
        else:
            print(f"error: unknown flag: {arg}", file=sys.stderr)
            sys.exit(2)
        i += 1

    root = os.path.abspath(root)
    if not os.path.isdir(root):
        print(f"error: root directory not found: {root}", file=sys.stderr)
        sys.exit(2)

    covered, uncovered, suppressed = analyze_coverage(root)
    total = len(covered) + len(uncovered)
    pct = (len(covered) / total * 100) if total > 0 else 100.0

    if json_mode:
        result = {
            "covered": covered,
            "uncovered": uncovered,
            "suppressed": suppressed,
            "total_source_files": total,
            "covered_count": len(covered),
            "uncovered_count": len(uncovered),
            "suppressed_count": len(suppressed),
            "coverage_pct": round(pct, 1),
            "threshold": threshold,
            "pass": pct >= threshold and (not check_mode or len(uncovered) == 0),
        }
        print(json.dumps(result, indent=2))
    else:
        if uncovered:
            print(f"Test coverage gaps ({len(uncovered)} files without tests):\n")
            for src in uncovered:
                candidates = find_test_candidates(src)
                print(f"  {src}  (expected: {candidates[0]})")
            print()
        if suppressed:
            print(f"Suppressed ({len(suppressed)} files with # coverage-ok):")
            for src in suppressed:
                print(f"  {src}")
            print()
        print(f"Coverage: {len(covered)}/{total} source files have tests ({pct:.1f}%)")
        if threshold > 0:
            print(f"Threshold: {threshold}%  {'PASS' if pct >= threshold else 'FAIL'}")

    if check_mode and len(uncovered) > 0:
        sys.exit(1)
    if threshold > 0 and pct < threshold:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
