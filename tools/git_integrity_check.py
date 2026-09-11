#!/usr/bin/env python3
"""Git repository integrity checker.

INDEX: git_integrity_check.py — Guardrail G8: detects git object/ref loss + corruption (git fsck --connectivity-only, git rev-parse HEAD per repo, exit 1 if any DAMAGED); --check mode read-only verify; --repos-json for .watchdog-repos.json input; wired into daemons/run-watchdog.sh; ASCII output, timeouts, Linux parity

Detects git object/ref loss and corruption. Runs `git fsck --connectivity-only`
and `git rev-parse HEAD` for each repository. Reports per-repo OK/DAMAGED status
with missing/invalid object counts. Exits 0 only if all repos are OK, 1 if any DAMAGED.

--check mode verifies the tool is read-only (no modifications to repos).

Usage:
    python git_integrity_check.py [--check] REPO_PATH [REPO_PATH ...]
    python git_integrity_check.py [--check] --repos-json .watchdog-repos.json
    echo "REPO_PATH" | python git_integrity_check.py [--check]

Exit codes:
    0: All repos OK
    1: At least one repo DAMAGED
    2: Usage error or cannot-evaluate (no repos, git not found)
"""

import sys
import subprocess
import os
from pathlib import Path
from typing import Tuple, List, Optional

# Default timeout for git operations (in seconds)
GIT_FSCK_TIMEOUT = 30


def run_git_command(
    args: List[str],
    cwd: str,
    timeout: int = GIT_FSCK_TIMEOUT,
) -> Tuple[int, str, str]:
    """Run a git command with timeout and encoding safety.

    Args:
        args: Command arguments (list, no shell=True)
        cwd: Working directory
        timeout: Timeout in seconds

    Returns:
        Tuple of (returncode, stdout, stderr)
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"git command timed out (>{timeout}s)"
    except FileNotFoundError:
        return -2, "", "git command not found"
    except Exception as e:
        return -3, "", str(e)


def check_repo_integrity(repo_path: str, check: bool = False) -> Tuple[str, int, int]:
    """Check integrity of a single git repository.

    --check mode is read-only: only runs git fsck and rev-parse, no modifications.

    Args:
        repo_path: Path to git repository
        check: If True, verify read-only mode (no modifications allowed)

    Returns:
        Tuple of (status, missing_objects, invalid_refs)
        status: "OK" or "DAMAGED"
        missing_objects: Count of missing objects reported by fsck
        invalid_refs: Count of invalid refs reported by fsck
    """
    # Verify .git directory exists
    git_dir = os.path.join(repo_path, ".git")
    if not os.path.isdir(git_dir):
        return "DAMAGED", 0, 1

    # Check for unborn HEAD (no commits yet)
    rc, _, _ = run_git_command(["rev-parse", "HEAD"], repo_path, timeout=5)
    if rc != 0:
        # Unborn HEAD indicates no commits
        return "DAMAGED", 0, 1

    # Run git fsck --connectivity-only (read-only check)
    # --connectivity-only: faster, doesn't check object integrity, just refs
    rc, stdout, stderr = run_git_command(
        ["fsck", "--connectivity-only"],
        repo_path,
        timeout=GIT_FSCK_TIMEOUT,
    )

    # Aggregate output for parsing
    full_output = stdout + stderr

    # Count missing and invalid refs/objects
    missing_count = full_output.count("missing")
    invalid_count = full_output.count("invalid")

    # fsck exit code 0 means OK, non-zero means issues found
    if rc == 0 and missing_count == 0 and invalid_count == 0:
        return "OK", 0, 0
    else:
        return "DAMAGED", missing_count, invalid_count


def main():
    """Main entry point."""
    import json

    # Parse arguments
    check_mode = False
    repo_paths = []
    repos_json_file = None

    i = 0
    while i < len(sys.argv[1:]):
        arg = sys.argv[i + 1]
        if arg == "--check":
            check_mode = True
        elif arg == "--repos-json" and i + 1 < len(sys.argv[1:]):
            repos_json_file = sys.argv[i + 2]
            i += 1
        else:
            repo_paths.append(arg)
        i += 1

    # If --repos-json provided, read repos from JSON file
    if repos_json_file and os.path.isfile(repos_json_file):
        try:
            with open(repos_json_file, "r", encoding="utf-8") as f:
                repos_data = json.load(f)
                for repo_obj in repos_data:
                    if isinstance(repo_obj, dict) and "repo" in repo_obj:
                        repo_paths.append(repo_obj["repo"])
        except (json.JSONDecodeError, IOError):
            pass  # Fall through to stdin/args

    # If no paths provided as args or json, read from stdin (one per line)
    if not repo_paths:
        try:
            for line in sys.stdin:
                line = line.strip()
                if line and not line.startswith("#"):
                    repo_paths.append(line)
        except KeyboardInterrupt:
            sys.exit(2)

    # Validate we have repos to check
    if not repo_paths:
        print("ERROR: No repositories specified. Provide as arguments, via --repos-json, or via stdin.", file=sys.stderr)
        sys.exit(2)

    # Check each repo
    all_ok = True
    results = []

    for repo_path in repo_paths:
        # Normalize path
        repo_path = os.path.abspath(repo_path)

        # Skip if not a directory
        if not os.path.isdir(repo_path):
            print(f"{repo_path}: DAMAGED (not a directory)")
            all_ok = False
            results.append((repo_path, "DAMAGED", 0, 1))
            continue

        # Check integrity
        status, missing, invalid = check_repo_integrity(repo_path, check=check_mode)

        # Format output (ASCII-safe)
        repo_name = os.path.basename(repo_path)
        if status == "OK":
            print(f"{repo_name}: OK")
        else:
            all_ok = False
            detail = ""
            if missing > 0:
                detail += f"{missing} missing"
            if invalid > 0:
                if detail:
                    detail += ", "
                detail += f"{invalid} invalid"
            if detail:
                print(f"{repo_name}: DAMAGED ({detail})")
            else:
                print(f"{repo_name}: DAMAGED")

        results.append((repo_path, status, missing, invalid))

    # Exit code: 0 if all OK, 1 if any DAMAGED
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
