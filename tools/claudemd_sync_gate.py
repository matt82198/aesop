#!/usr/bin/env python3
"""CLAUDE.md synchronization gate — ensures code changes are accompanied by domain CLAUDE.md updates.

For each domain directory (state_store/, tools/, ui/, driver/, etc.) with code changes,
verifies that the corresponding domain/CLAUDE.md was also modified in the same commit/PR.

Exemptions:
- Changes only in tests/ (test files)
- Changes only in docs/ (documentation)
- Changes only to meta files (stats.json, README.md, CHANGELOG.md, package.json, .nvmrc)
- Changes only to .github/ (CI config)
- Changes within a domain that are ONLY to the CLAUDE.md itself

Exit: 0=all synced, 1=drift found, 2=error
Supports: --check (default), --json output, --help
"""

import json
import re
import sys
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Tuple


# Meta files that don't require CLAUDE.md updates
META_FILES = {
    "stats.json",
    "README.md",
    "CHANGELOG.md",
    "package.json",
    ".nvmrc",
    "package-lock.json",
    ".gitignore",
    ".editorconfig",
    "LICENSE",
}

# Exempted directories (changes here don't require CLAUDE.md updates)
EXEMPTED_DIRS = {
    "tests/",  # Test files
    "docs/",   # Documentation
    ".github/",  # CI configuration
}

# Known domains (directories that should have CLAUDE.md files)
KNOWN_DOMAINS = {
    "bin",
    "daemons",
    "dash",
    "driver",
    "hooks",
    "mcp",
    "monitor",
    "scan",
    "skills",
    "state_store",
    "tests",
    "tools",
    "ui",
}


def is_exempted_path(path: str) -> bool:
    """Check if a path is in an exempted directory."""
    path_normalized = path.replace("\\", "/")
    for exempted in EXEMPTED_DIRS:
        if path_normalized.startswith(exempted):
            return True
    return False


def is_meta_file(path: str) -> bool:
    """Check if a path is a meta file (doesn't require CLAUDE.md update)."""
    filename = Path(path).name
    return filename in META_FILES


def get_domain_for_path(path: str) -> str:
    """Extract the domain (top-level directory) from a path.

    Returns the domain name (e.g., 'state_store') or None if not in a known domain.
    """
    parts = path.replace("\\", "/").split("/")
    if parts and parts[0] in KNOWN_DOMAINS:
        return parts[0]
    return None


def get_git_changed_files(repo_root: Path, base_ref: str = "main") -> Tuple[List[str], bool]:
    """Get list of changed files via git diff.

    Args:
        repo_root: Repository root directory
        base_ref: Base reference for comparison (default: main)

    Returns:
        Tuple of (changed_files, is_error)
    """
    import os

    # In GitHub Actions PRs, GITHUB_BASE_REF is the target branch name
    github_base = os.environ.get("GITHUB_BASE_REF", "")

    refs_to_try = []
    if github_base:
        refs_to_try.append(f"origin/{github_base}...HEAD")
    refs_to_try.append(f"origin/{base_ref}...HEAD")
    refs_to_try.append("HEAD~1...HEAD")

    try:
        for ref_spec in refs_to_try:
            result = subprocess.run(
                ["git", "diff", "--name-only", ref_spec],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=repo_root,
            )
            if result.returncode == 0:
                files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
                return files, False

        return [], True
    except subprocess.TimeoutExpired:
        return [], True
    except Exception:
        return [], True


def get_commit_changes(repo_root: Path, base_ref: str = "main") -> Tuple[Dict[str, Set[str]], bool]:
    """Get files changed per commit (commit_sha -> set of changed files).

    Returns per-commit tracking for stricter validation: each commit must have
    accompanying CLAUDE.md updates for code changes in the same commit.

    Args:
        repo_root: Repository root directory
        base_ref: Base reference for comparison (default: main)

    Returns:
        Tuple of (dict mapping commit_sha to set of files, is_error)
    """
    import os

    # In GitHub Actions PRs, GITHUB_BASE_REF is the target branch name
    github_base = os.environ.get("GITHUB_BASE_REF", "")

    refs_to_try = []
    if github_base:
        refs_to_try.append(f"origin/{github_base}...HEAD")
    refs_to_try.append(f"origin/{base_ref}...HEAD")
    refs_to_try.append("HEAD~1...HEAD")

    try:
        for ref_spec in refs_to_try:
            # Get commit hashes in the range
            result = subprocess.run(
                ["git", "log", "--oneline", ref_spec],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=repo_root,
            )
            if result.returncode != 0:
                continue

            # Extract commit hashes
            commit_hashes = [line.split()[0] for line in result.stdout.strip().split("\n") if line.strip()]

            commit_changes = {}
            for commit_hash in commit_hashes:
                # Get files changed in this commit
                result = subprocess.run(
                    ["git", "show", "--name-only", "--format=", commit_hash],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=repo_root,
                )
                if result.returncode == 0:
                    files = {f.strip() for f in result.stdout.strip().split("\n") if f.strip()}
                    commit_changes[commit_hash] = files

            return commit_changes, False

        return {}, True
    except subprocess.TimeoutExpired:
        return {}, True
    except Exception:
        return {}, True


def classify_changes(changed_files: List[str]) -> Dict[str, List[str]]:
    """Classify changed files by domain.

    Returns a dict mapping domain -> [list of changed files in that domain]
    Also includes special keys: '_exempted', '_meta', '_root'
    """
    classified = {
        "_exempted": [],
        "_meta": [],
        "_root": [],
    }

    for file_path in changed_files:
        # Check for exempted paths
        if is_exempted_path(file_path):
            classified["_exempted"].append(file_path)
            continue

        # Check for meta files
        if is_meta_file(file_path):
            classified["_meta"].append(file_path)
            continue

        # Get domain
        domain = get_domain_for_path(file_path)
        if domain:
            if domain not in classified:
                classified[domain] = []
            classified[domain].append(file_path)
        else:
            # Root-level files (not in a domain)
            classified["_root"].append(file_path)

    return classified


def check_domain_claudemd_sync(repo_root: Path, classified: Dict[str, List[str]]) -> Tuple[List[Dict], int]:
    """Check if domains with code changes also have CLAUDE.md updates.

    Returns:
        Tuple of (findings, exit_code)
        findings: List of dicts with 'domain', 'issue', 'changed_files'
    """
    findings = []

    # For each domain with changes, check if CLAUDE.md was also updated
    for domain, files in classified.items():
        # Skip special keys
        if domain.startswith("_"):
            continue

        # Separate CLAUDE.md changes from code changes
        claudemd_changed = any(f.endswith("CLAUDE.md") for f in files)

        # Filter out CLAUDE.md from code changes
        code_changes = [f for f in files if not f.endswith("CLAUDE.md")]

        # If there are code changes but no CLAUDE.md update, flag it
        if code_changes and not claudemd_changed:
            findings.append({
                "domain": domain,
                "issue": "code changes without CLAUDE.md update",
                "changed_files": code_changes,
            })

    # Exit 0 if no findings, 1 if any drift
    exit_code = 1 if findings else 0
    return findings, exit_code


def check_domain_claudemd_sync_per_commit(repo_root: Path, commit_changes: Dict[str, set]) -> Tuple[List[Dict], int]:
    """Check if each commit's code changes are accompanied by CLAUDE.md updates in the SAME commit.

    This is stricter than branch-wide checking: it ensures that when a commit adds or modifies
    code in a domain, that same commit must also update the domain's CLAUDE.md (not a later commit).

    Args:
        repo_root: Repository root directory
        commit_changes: Dict mapping commit_sha -> set of changed files

    Returns:
        Tuple of (findings, exit_code)
        findings: List of dicts with 'commit', 'domain', 'issue', 'changed_files'
    """
    findings = []

    for commit_sha, files in commit_changes.items():
        # Classify this commit's changes
        committed_classified = classify_changes(list(files))

        # For each domain in this commit, verify CLAUDE.md is also in this commit
        for domain, domain_files in committed_classified.items():
            # Skip special keys
            if domain.startswith("_"):
                continue

            # Check if CLAUDE.md was updated in THIS commit
            claudemd_in_commit = any(f.endswith("CLAUDE.md") for f in domain_files)

            # Filter out CLAUDE.md from code changes
            code_changes = [f for f in domain_files if not f.endswith("CLAUDE.md")]

            # If there are code changes but no CLAUDE.md in THIS commit, flag it
            if code_changes and not claudemd_in_commit:
                findings.append({
                    "commit": commit_sha[:8],  # Short SHA
                    "domain": domain,
                    "issue": "code changes without CLAUDE.md update in the same commit",
                    "changed_files": code_changes,
                })

    # Exit 0 if no findings, 1 if any drift
    exit_code = 1 if findings else 0
    return findings, exit_code


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Check if code changes in domains are accompanied by CLAUDE.md updates"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=True,
        help="Check mode (default; fail if drift found)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--base-ref",
        default="main",
        help="Base reference for git diff (default: main)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: cwd)",
    )

    args = parser.parse_args()
    repo_root = args.root.resolve()

    if not repo_root.exists():
        print(f"Error: repo root {repo_root} does not exist", file=sys.stderr)
        sys.exit(2)

    # Get per-commit changes for stricter validation
    commit_changes, git_error = get_commit_changes(repo_root, args.base_ref)

    if git_error:
        if args.json:
            output = {
                "status": "error",
                "message": "Failed to get commit history",
                "findings": [],
            }
            print(json.dumps(output, indent=2))
        else:
            print("Error: Failed to get commit history from origin/main or HEAD~1", file=sys.stderr)
        sys.exit(2)

    # If no changes, pass
    if not commit_changes:
        if args.json:
            output = {
                "status": "ok",
                "message": "No changes detected",
                "exit_code": 0,
                "findings": [],
                "summary": {
                    "domains_with_drift": 0,
                    "total_changed_files": 0,
                    "exempted_files": 0,
                    "meta_files": 0,
                },
            }
            print(json.dumps(output, indent=2))
        else:
            print("[OK] No changes detected")
        sys.exit(0)

    # Check per-commit sync (stricter: each commit must have doc updates)
    findings, exit_code = check_domain_claudemd_sync_per_commit(repo_root, commit_changes)

    if args.json:
        output = {
            "status": "ok" if exit_code == 0 else "drift",
            "exit_code": exit_code,
            "findings": findings,
            "summary": {
                "domains_with_drift": len(findings),
                "total_commits_checked": len(commit_changes),
            },
        }
        print(json.dumps(output, indent=2))
    else:
        if findings:
            print(f"[DRIFT] {len(findings)} commit(s) have code changes without CLAUDE.md updates in the same commit:")
            for i, finding in enumerate(findings, 1):
                print(f"\n{i}. Commit {finding['commit']}, {finding['domain']}/CLAUDE.md")
                print(f"   Issue: {finding['issue']}")
                print(f"   Changed files in {finding['domain']}/:")
                for file_path in finding["changed_files"]:
                    print(f"     - {file_path}")
        else:
            print("[OK] All domain code changes accompanied by CLAUDE.md updates in the same commit")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
