#!/usr/bin/env python3
"""CLAUDE.md synchronization gate — ensures code changes are accompanied by domain CLAUDE.md updates.
INDEX: CLAUDE.md synchronization gate (Guardrail G5): for each domain directory with code changes, verifies a domain documentation surface was also modified in the same PR. The surfaces are `CLAUDE.md` and `INDEX.md` — the latter because #751 moved the per-tool index out of tools/CLAUDE.md into the generated tools/INDEX.md (the inline list was the top merge-queue conflict surface), so a tool change accompanied by an INDEX.md change is a documented change and must not read as drift. Exempts: test-only changes, docs-only, meta files (stats.json, README.md, CHANGELOG.md, package.json, .nvmrc), .github/ (CI), documentation-only changes; CLI: `--check` (default, fail-closed) | `--json` | `--base-ref` [BRANCH] (default main); exit 0=synced, 1=drift, 2=error

For each domain directory (state_store/, tools/, ui/, driver/, etc.) with code changes,
verifies that the corresponding domain/CLAUDE.md was also modified in the same commit/PR.

Exemptions:
- Changes only in tests/ (test files)
- Changes only in docs/ (documentation)
- Changes only to meta files (stats.json, README.md, CHANGELOG.md, package.json, .nvmrc)
- Changes only to .github/ (CI config)
- Changes within a domain that are ONLY to its documentation (CLAUDE.md, INDEX.md)

Domain documentation surfaces: a domain normally documents itself through its
CLAUDE.md. tools/ additionally documents each tool through the generated
tools/INDEX.md, built from the tools' own `INDEX:` header lines (see
tools/gen_tool_index.py). Either file satisfies the sync requirement.

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


# Documentation surfaces that satisfy a domain's "you changed code, document it"
# requirement. CLAUDE.md is the universal one; tools/INDEX.md is the generated
# per-tool index that PR #751 split out of tools/CLAUDE.md.
DOMAIN_DOC_FILES = ("CLAUDE.md", "INDEX.md")


def is_domain_doc(path: str) -> bool:
    """True if the path is a domain documentation surface, not code."""
    return path.replace("\\", "/").split("/")[-1] in DOMAIN_DOC_FILES


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
                encoding='utf-8', errors='replace',
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

        # Separate domain-documentation changes from code changes.
        # A domain documents itself through its CLAUDE.md, but tools/ also
        # documents each tool through the generated tools/INDEX.md (PR #751
        # moved the per-tool index there, out of tools/CLAUDE.md, because the
        # inline list was the top merge-queue conflict surface). A tool change
        # accompanied by an INDEX.md change IS a documented change -- the
        # generated file only moves when the tool's own INDEX: line moves.
        doc_changed = any(is_domain_doc(f) for f in files)

        # Filter out domain documentation from code changes
        code_changes = [f for f in files if not is_domain_doc(f)]

        # If there are code changes but no documentation update, flag it
        if code_changes and not doc_changed:
            findings.append({
                "domain": domain,
                "issue": "code changes without CLAUDE.md update",
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

    # Get changed files from git
    changed_files, git_error = get_git_changed_files(repo_root, args.base_ref)

    if git_error:
        if args.json:
            output = {
                "status": "error",
                "message": "Failed to get git diff",
                "findings": [],
            }
            print(json.dumps(output, indent=2))
        else:
            print("Error: Failed to get git diff from origin/main or HEAD~1", file=sys.stderr)
        sys.exit(2)

    # If no changes, pass
    if not changed_files:
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

    # Classify changes by domain
    classified = classify_changes(changed_files)

    # Check sync
    findings, exit_code = check_domain_claudemd_sync(repo_root, classified)

    if args.json:
        output = {
            "status": "ok" if exit_code == 0 else "drift",
            "exit_code": exit_code,
            "findings": findings,
            "summary": {
                "domains_with_drift": len(findings),
                "total_changed_files": len(changed_files),
                "exempted_files": len(classified.get("_exempted", [])),
                "meta_files": len(classified.get("_meta", [])),
            },
        }
        print(json.dumps(output, indent=2))
    else:
        if findings:
            print(f"[DRIFT] {len(findings)} domain(s) have code changes without CLAUDE.md updates:")
            for i, finding in enumerate(findings, 1):
                print(f"\n{i}. {finding['domain']}/CLAUDE.md")
                print(f"   Issue: {finding['issue']}")
                print(f"   Changed files in {finding['domain']}/:")
                for file_path in finding["changed_files"]:
                    print(f"     - {file_path}")
        else:
            print("[OK] All domain code changes accompanied by CLAUDE.md updates")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
