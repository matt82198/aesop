#!/usr/bin/env python3
"""
prepublish_scan.py — Pre-publish gate for public repos: scans full git history + staged changes.

Usage:
  prepublish_scan.py [--repo PATH]  Scan full history and staged changes before publishing

Exit codes: 0=clean, 1=findings, 2=usage error
Output: CLEAR-TO-PUBLISH or STOP with offending commits/files

This gate runs TWO scans (both must pass for CLEAR-TO-PUBLISH):
  1. Full git history (--history mode): detects secrets in any prior commit
  2. Staged changes (--staged mode): detects secrets in uncommitted changes
"""

import argparse
import os
import sys
import subprocess
from pathlib import Path


def run_secret_scan(mode, repo_path):
    """Run secret_scan.py in the specified mode; return (exit_code, output, stderr)."""
    scripts_dir = Path(__file__).parent
    secret_scan_path = scripts_dir / "secret_scan.py"

    try:
        cmd = [sys.executable, str(secret_scan_path), mode, "--repo", repo_path]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=120
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "secret_scan.py timed out"
    except Exception as e:
        return 1, "", f"Error running secret_scan.py: {e}"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--repo",
        default=os.getcwd(),
        help="Git repo path (default: current directory)",
    )
    args = parser.parse_args()

    repo_path = args.repo

    print("[1/2] Scanning full git history for secrets...")
    history_exit, history_out, history_err = run_secret_scan("--history", repo_path)

    print("[2/2] Scanning staged changes for secrets...")
    staged_exit, staged_out, staged_err = run_secret_scan("--staged", repo_path)

    # Combine outputs
    print("\n=== History Scan ===")
    print(history_out)
    if history_err:
        print(history_err, file=sys.stderr)

    print("\n=== Staged Scan ===")
    print(staged_out)
    if staged_err:
        print(staged_err, file=sys.stderr)

    # Verdict
    print("\n" + "=" * 50)
    if history_exit == 0 and staged_exit == 0:
        print("CLEAR-TO-PUBLISH: Full history and staged changes are clean")
        sys.exit(0)
    else:
        print("STOP: Secrets found in history or staged changes")
        sys.exit(1)


if __name__ == "__main__":
    main()
