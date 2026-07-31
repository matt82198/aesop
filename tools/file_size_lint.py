#!/usr/bin/env python3
"""Python file size linter — warns on .py files exceeding size thresholds.

Scans all .py files in the repo (or specified directories) and reports files
exceeding configurable line count and byte size thresholds.

Suppression:
- ``# filesize-ok`` in the first 3 lines of a file suppresses that file.
- ALLOWED_OVERSIZE dict for known large files (keyed by repo-relative path).

Exit: 0=clean, 1=oversized files found, 2=error.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

from lint_core import Finding, exit_code

# Known large files that are allowed to exceed thresholds.
# Key: repo-relative POSIX path, value: {"max_lines": N, "max_bytes": N}
# Omit a key to use the global default for that dimension.
ALLOWED_OVERSIZE: Dict[str, Dict[str, int]] = {}

SKIP_DIRS = {"node_modules", ".git", "dist", "__pycache__", ".pytest_cache", "venv", ".venv"}
SUPPRESS_MARKER = "# filesize-ok"


def _has_suppress_marker(path: Path) -> bool:
    """Check if the file has ``# filesize-ok`` in its first 3 lines."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= 3:
                    break
                if SUPPRESS_MARKER in line:
                    return True
    except OSError:
        pass
    return False


def _should_skip_dir(name: str) -> bool:
    return name in SKIP_DIRS


def discover_py_files(paths: List[Path], root: Path) -> List[Path]:
    """Discover all .py files under the given paths, respecting skip dirs."""
    result: List[Path] = []
    for base in paths:
        if base.is_file():
            if base.suffix == ".py":
                result.append(base)
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            # Prune skip dirs in place
            dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
            for fname in filenames:
                if fname.endswith(".py"):
                    result.append(Path(dirpath) / fname)
    return sorted(set(result))


def lint_file(
    path: Path,
    root: Path,
    max_lines: int,
    max_bytes: int,
) -> List[Dict[str, str]]:
    """Lint a single .py file. Returns list of finding dicts."""
    findings: List[Dict[str, str]] = []
    rel = str(path.relative_to(root)).replace("\\", "/")

    # Check suppress marker
    if _has_suppress_marker(path):
        return findings

    # Per-file overrides
    overrides = ALLOWED_OVERSIZE.get(rel, {})
    eff_max_lines = overrides.get("max_lines", max_lines)
    eff_max_bytes = overrides.get("max_bytes", max_bytes)

    # Byte size
    try:
        byte_size = path.stat().st_size
    except OSError as exc:
        findings.append({
            "file": rel,
            "type": "error",
            "message": f"Cannot stat: {exc}",
        })
        return findings

    # Line count
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            line_count = sum(1 for _ in f)
    except OSError as exc:
        findings.append({
            "file": rel,
            "type": "error",
            "message": f"Cannot read: {exc}",
        })
        return findings

    if line_count > eff_max_lines:
        findings.append({
            "file": rel,
            "type": "lines",
            "message": f"{rel}: {line_count} lines exceeds max {eff_max_lines}",
        })

    if byte_size > eff_max_bytes:
        findings.append({
            "file": rel,
            "type": "bytes",
            "message": f"{rel}: {byte_size} bytes exceeds max {eff_max_bytes}",
        })

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description="Python file size linter")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 on findings (default behaviour; explicit for CI scripts)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--max-lines",
        type=int,
        default=500,
        help="Line count threshold (default: 500)",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=20000,
        help="Byte size threshold (default: 20000)",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        type=Path,
        default=None,
        help="Directories/files to scan (default: repo root)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: cwd)",
    )
    args = parser.parse_args()

    root = (args.root or Path.cwd()).resolve()
    if not root.exists():
        print(f"Error: root {root} does not exist", file=sys.stderr)
        sys.exit(2)

    scan_paths = [p.resolve() for p in args.paths] if args.paths else [root]
    for p in scan_paths:
        if not p.exists():
            print(f"Error: path {p} does not exist", file=sys.stderr)
            sys.exit(2)

    py_files = discover_py_files(scan_paths, root)
    all_findings: List[Dict[str, str]] = []
    for py_file in py_files:
        try:
            rel_check = py_file.relative_to(root)  # noqa: F841
        except ValueError:
            continue  # outside root, skip
        all_findings.extend(lint_file(py_file, root, args.max_lines, args.max_bytes))

    if args.json:
        output = {
            "findings": all_findings,
            "count": len(all_findings),
            "root": str(root),
        }
        print(json.dumps(output, indent=2))
    else:
        if all_findings:
            for i, f in enumerate(all_findings, 1):
                print(f"{i}. [{f['type']}] {f['message']}")
            print(f"\n{len(all_findings)} finding(s)")
        else:
            print("[OK] No oversized Python files found")

    sys.exit(1 if all_findings else 0)


if __name__ == "__main__":
    main()
