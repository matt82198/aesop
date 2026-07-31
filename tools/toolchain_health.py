#!/usr/bin/env python3
"""
Toolchain health check — verifies interpreter and binary availability.

Detects:
  - Binaries that exist but cannot execute (e.g., Git for Windows bash.exe
    wrapper pointing to a deleted usr/bin/bash.exe)
  - Missing critical binaries (git, python, node, curl, bash)
  - Stale or missing heartbeat files (watchdog, monitor)

Exit codes:
  0 = All checks passed (or --json with no issues)
  1 = One or more issues found (binary broken/missing, heartbeat stale)
  2 = Check itself failed (cannot determine binary status, unreadable paths)

Usage:
  python tools/toolchain_health.py [--check] [--json] [--max-age SECONDS]

Flags:
  --check         Default mode: run checks and report findings (exit 0/1/2)
  --json          Machine-readable JSON output (includes findings array)
  --max-age N     Override heartbeat staleness threshold (default 300s)

Output:
  Human-readable: Compact summary (PASS/FAIL), one finding per line
  JSON: Machine object {status, findings: [{type, binary/file, message}], summary}
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional

try:
    from common import get_state_dir, check_heartbeat_staleness
except ImportError:
    from tools.common import get_state_dir, check_heartbeat_staleness


# Required binaries to check
REQUIRED_BINARIES = {
    "bash": [
        "C:\\Program Files\\Git\\bin\\bash.exe",
        "C:\\Program Files\\Git\\usr\\bin\\bash.exe",
        "/usr/bin/bash",
        "/bin/bash",
    ],
    "git": ["git"],
    "python": ["python", "python3"],
    "node": ["node"],
    "curl": ["curl"],
}

# Heartbeat files to monitor
HEARTBEAT_FILES = {
    "watchdog": "conductor3/state/.watchdog-heartbeat",
    "monitor": "conductor3/monitor/.monitor-heartbeat",
}


def find_binary(name: str, candidates: List[str]) -> Optional[Path]:
    """Find the first available binary from a list of candidates.

    Args:
        name: Binary name for error messages
        candidates: List of executable names or paths to try

    Returns:
        Path object if found, None if not found
    """
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                timeout=5,
                encoding="utf-8",
            )
            # Successful execution means it's available
            if result.returncode == 0:
                return Path(candidate)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            # Try the next candidate
            pass

    return None


def check_binary(name: str, candidates: List[str]) -> Tuple[bool, Optional[str]]:
    """Check if a binary is available and executable.

    Args:
        name: Binary name (for diagnostics)
        candidates: List of possible paths/commands to try

    Returns:
        (is_ok, message) where is_ok=True means binary is working,
        message contains diagnostic info on failure
    """
    found_binary = find_binary(name, candidates)

    if found_binary is None:
        # Try to give more specific error
        # Check if at least the first candidate exists as a file
        first = candidates[0]
        first_path = Path(first) if first.startswith("/") or ":\\" in first else None

        if first_path and first_path.exists():
            # File exists but cannot execute
            return False, f"Binary exists but cannot execute: {first_path}"
        else:
            # Binary not found
            return False, f"Binary not found: {name}"

    # Binary found and executes
    return True, None


def get_heartbeat_path(relative_path: str) -> Path:
    """Resolve heartbeat file path.

    Tries to resolve relative to various base directories (git root, cwd, etc).

    Args:
        relative_path: Relative path like "conductor3/state/.watchdog-heartbeat"

    Returns:
        Path object (may not exist)
    """
    # Try relative to current working directory first
    path = Path(relative_path)
    if path.is_absolute():
        return path

    # If it's relative, try to find it from common base directories
    candidates = [
        Path.cwd() / relative_path,  # cwd-relative
        Path.cwd().parent / relative_path,  # parent of cwd
        Path.home() / relative_path,  # home-relative
    ]

    # Return the first that exists, or the cwd-relative version as fallback
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return Path.cwd() / relative_path


def check_heartbeat(
    name: str, relative_path: str, max_age_seconds: int
) -> Tuple[bool, Optional[str]]:
    """Check if a heartbeat file is fresh.

    Args:
        name: Heartbeat name (for diagnostics)
        relative_path: Path to heartbeat file
        max_age_seconds: Maximum age before considering stale

    Returns:
        (is_ok, message) where is_ok=True means heartbeat is fresh
    """
    hb_path = get_heartbeat_path(relative_path)

    is_stale, age_s, info = check_heartbeat_staleness(hb_path, max_age_seconds)

    if is_stale:
        if age_s == 0:
            # Missing or unreadable
            return False, f"Heartbeat {name} is {info.lower()}"
        else:
            # Stale but exists
            return False, f"Heartbeat {name} stale: {age_s}s > {max_age_seconds}s threshold"

    return True, None


def run_checks(json_mode: bool = False, max_age_seconds: int = 300) -> int:
    """Run all toolchain health checks.

    Args:
        json_mode: If True, output JSON; else human-readable
        max_age_seconds: Heartbeat staleness threshold

    Returns:
        Exit code: 0 = all ok, 1 = issues found, 2 = check error
    """
    findings: List[Dict[str, str]] = []
    check_count = 0

    # Phase 1: Check required binaries
    for binary_name, candidates in REQUIRED_BINARIES.items():
        check_count += 1
        is_ok, message = check_binary(binary_name, candidates)
        if not is_ok:
            findings.append(
                {
                    "type": "BINARY_BROKEN" if "exists" in (message or "").lower() else "BINARY_MISSING",
                    "binary": binary_name,
                    "message": message or f"{binary_name} not available",
                }
            )

    # Phase 2: Check heartbeat files
    for hb_name, hb_path in HEARTBEAT_FILES.items():
        check_count += 1
        is_ok, message = check_heartbeat(hb_name, hb_path, max_age_seconds)
        if not is_ok:
            findings.append(
                {
                    "type": "HEARTBEAT_STALE",
                    "file": hb_path,
                    "message": message or f"Heartbeat {hb_name} stale",
                }
            )

    # Fail-closed: zero checks performed means check itself failed
    if check_count == 0:
        if json_mode:
            output = {
                "status": "ERROR",
                "findings": [{"type": "CHECK_ERROR", "message": "Zero checks performed"}],
                "summary": "Check infrastructure error",
            }
            print(json.dumps(output, indent=2))
        else:
            print("ERROR: Zero checks performed (toolchain check infrastructure failure)")
        return 2

    # Format output
    if json_mode:
        status = "PASS" if not findings else "FAIL"
        output = {
            "status": status,
            "findings": findings,
            "summary": f"{len(findings)} issue(s) found" if findings else "All checks passed",
            "checks_performed": check_count,
        }
        print(json.dumps(output, indent=2))
    else:
        if findings:
            print("FAIL: Toolchain health check found issues:")
            for finding in findings:
                ftype = finding.get("type", "UNKNOWN")
                if ftype == "BINARY_BROKEN":
                    print(f"  [BROKEN] {finding.get('binary')}: {finding.get('message')}")
                elif ftype == "BINARY_MISSING":
                    print(f"  [MISSING] {finding.get('binary')}: {finding.get('message')}")
                elif ftype == "HEARTBEAT_STALE":
                    print(f"  [STALE] {finding.get('file')}: {finding.get('message')}")
                else:
                    print(f"  [{ftype}] {finding.get('message')}")
        else:
            print("PASS: All toolchain checks passed")

    return 1 if findings else 0


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Verify interpreter and binary availability for fleet machinery",
        epilog="Exit: 0=all OK, 1=issues found, 2=check error",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="Run checks (default behavior)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--max-age",
        type=int,
        default=300,
        metavar="SECONDS",
        help="Heartbeat staleness threshold in seconds (default 300)",
    )

    args = parser.parse_args()

    # Always run checks (--check is default)
    exit_code = run_checks(json_mode=args.json, max_age_seconds=args.max_age)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
