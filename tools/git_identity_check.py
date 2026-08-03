#!/usr/bin/env python3
"""
git_identity_check.py — Validate a repo's effective git user.name/user.email.
INDEX: Validate repo git user.name/user.email via --expect-name/--expect-email CLI args OR aesop.config.json identity block; verifies .git/config physically (not config cache); added 30s timeout to git config calls (critical fix: no prior timeout)

Validates that a repository's git user identity matches expected values. Can read
expected values from CLI args (--expect-name/--expect-email) or from an 'identity'
block in aesop.config.json. Verifies values are physically persisted in .git/config
(via grep, not just cache).

Usage:
    python tools/git_identity_check.py --repo <path> --expect-name <name> --expect-email <email> [--mode warn|fail]
    python tools/git_identity_check.py --repo <path> --config aesop.config.json [--mode warn|fail]

Modes:
    --mode warn  — Print drift report and exit 0 (default)
    --mode fail  — Exit 1 if drift detected, exit 0 if matched

Examples:
    python tools/git_identity_check.py --repo /path/to/repo --expect-name "Matt Culliton" --expect-email "matt82198@gmail.com" --mode fail
    python tools/git_identity_check.py --repo /path/to/repo --config aesop.config.json --mode fail
"""

import sys
import json
import subprocess
from pathlib import Path
from typing import Optional, Tuple, List


def get_identity_from_args(args: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse CLI arguments for --expect-name and --expect-email.

    Args:
        args: Command line arguments list

    Returns:
        Tuple of (name, email) where either can be None if not provided
    """
    name = None
    email = None

    i = 0
    while i < len(args):
        if args[i] == "--expect-name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif args[i] == "--expect-email" and i + 1 < len(args):
            email = args[i + 1]
            i += 2
        else:
            i += 1

    return name, email


def get_identity_from_config_file(config_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Read identity expectations from aesop.config.json 'identity' block.

    Config file format:
    {
        "identity": {
            "user_name": "Expected Name",
            "user_email": "expected@example.com"
        }
    }

    Args:
        config_path: Path to aesop.config.json

    Returns:
        Tuple of (name, email) where either can be None if not found
    """
    try:
        config_file = Path(config_path)
        if not config_file.exists():
            return None, None

        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)

        identity = config.get("identity", {})
        name = identity.get("user_name")
        email = identity.get("user_email")

        return name, email
    except (json.JSONDecodeError, IOError, OSError):
        return None, None


def get_git_identity(repo_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Get current git user.name and user.email via git config command (local scope only).

    Uses: git -C <repo> config --local user.name/user.email

    Only reads from local repo config, not system/global fallback.

    Args:
        repo_path: Path to git repository

    Returns:
        Tuple of (name, email) where either can be None if not set
    """
    name = None
    email = None

    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "config", "--local", "user.name"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            name = result.stdout.strip()
            if not name:
                name = None
    except (subprocess.SubprocessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "config", "--local", "user.email"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            email = result.stdout.strip()
            if not email:
                email = None
    except (subprocess.SubprocessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return name, email


def get_physical_git_identity(repo_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Read git user identity directly from .git/config file (grep-based, not cache).

    This function bypasses git's config cache to detect if the .git/config file
    has physically persisted values that differ from what git command reports.

    Args:
        repo_path: Path to git repository

    Returns:
        Tuple of (name, email) where either can be None if not found in file
    """
    name = None
    email = None

    git_config = Path(repo_path) / ".git" / "config"

    if not git_config.exists():
        return None, None

    try:
        content = git_config.read_text(encoding="utf-8")

        # Parse user.name
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("name = "):
                name = line[7:]  # Remove "name = " prefix
                break

        # Parse user.email
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("email = "):
                email = line[8:]  # Remove "email = " prefix
                break

        return name, email
    except (IOError, OSError):
        return None, None


def validate_identity(
    repo_path: str,
    expected_name: Optional[str] = None,
    expected_email: Optional[str] = None,
) -> List[str]:
    """
    Validate repo's git identity against expected values.

    Checks both git config cache and physical .git/config file. Returns list of
    error messages if validation fails (empty list if all checks pass).

    Args:
        repo_path: Path to git repository
        expected_name: Expected git user.name (None to skip validation)
        expected_email: Expected git user.email (None to skip validation)

    Returns:
        List of error messages (empty if validation passes)
    """
    errors = []

    # Get current identity from git config
    current_name, current_email = get_git_identity(repo_path)

    # Get physical identity from .git/config file
    physical_name, physical_email = get_physical_git_identity(repo_path)

    # Check for config cache vs physical file drift
    if current_name != physical_name:
        errors.append(
            f"Git config drift for user.name: cache='{current_name}' vs physical='{physical_name}'"
        )
    if current_email != physical_email:
        errors.append(
            f"Git config drift for user.email: cache='{current_email}' vs physical='{physical_email}'"
        )

    # Validate against expected values (using physical values as source of truth)
    if expected_name is not None:
        if physical_name != expected_name:
            errors.append(
                f"user.name mismatch: expected '{expected_name}' but git has '{physical_name}'"
            )

    if expected_email is not None:
        if physical_email != expected_email:
            errors.append(
                f"user.email mismatch: expected '{expected_email}' but git has '{physical_email}'"
            )

    return errors


def main(argv: Optional[List[str]] = None) -> int:
    """
    Main entry point for git identity check.

    Args:
        argv: Command line arguments (uses sys.argv[1:] if None)

    Returns:
        Exit code: 0 on success or --warn, 1 on validation failure in --fail mode
    """
    if argv is None:
        argv = sys.argv[1:]

    # Parse arguments
    repo_path = None
    config_path = None
    mode = "warn"  # default mode
    expect_name = None
    expect_email = None

    usage = (
        "Usage: git_identity_check.py --repo PATH [--config PATH] "
        "[--mode warn|fail] [--expect-name NAME] [--expect-email EMAIL]"
    )

    i = 0
    while i < len(argv):
        if argv[i] in ("-h", "--help"):
            print(usage)
            return 0
        elif argv[i] == "--repo" and i + 1 < len(argv):
            repo_path = argv[i + 1]
            i += 2
        elif argv[i] == "--config" and i + 1 < len(argv):
            config_path = argv[i + 1]
            i += 2
        elif argv[i] == "--mode" and i + 1 < len(argv):
            mode = argv[i + 1]
            i += 2
        elif argv[i] == "--expect-name" and i + 1 < len(argv):
            expect_name = argv[i + 1]
            i += 2
        elif argv[i] == "--expect-email" and i + 1 < len(argv):
            expect_email = argv[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {argv[i]}", file=sys.stderr)
            return 2

    # Validate required arguments
    if not repo_path:
        print("Error: --repo is required", file=sys.stderr)
        return 2

    # Read expectations: CLI args take precedence over config file
    cli_name, cli_email = get_identity_from_args(argv)
    if cli_name is not None or cli_email is not None:
        expect_name = cli_name if cli_name is not None else expect_name
        expect_email = cli_email if cli_email is not None else expect_email
    elif config_path:
        config_name, config_email = get_identity_from_config_file(config_path)
        expect_name = config_name if config_name is not None else expect_name
        expect_email = config_email if config_email is not None else expect_email

    # If still no expectations, nothing to validate
    if expect_name is None and expect_email is None:
        print("Error: Must provide --expect-name/--expect-email or --config", file=sys.stderr)
        return 2

    # Validate
    errors = validate_identity(repo_path, expect_name, expect_email)

    # Report results
    if errors:
        print(f"Git identity validation failed for {repo_path}:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)

        if mode == "fail":
            return 1
        else:  # warn mode
            return 0
    else:
        print(f"Git identity validation passed for {repo_path}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
