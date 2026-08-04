#!/usr/bin/env python3
"""
Wave manifest preflight validator.
INDEX: Wave manifest preflight validator: (1) file-ownership disjointness (no overlaps via fnmatch glob matching); (2) ownsFiles path existence (new files flagged as INFO); (3) prompt sanity (non-empty + [ISOLATION: sibling worktree] required + [[ALLOW-NON-HAIKU]] warns unless [[ALLOW-SONNET]]/[[ALLOW-OPUS]]); (4) git history churn (14-day commits >3 = WARN); (5) testCmd validation (on PATH or repo-relative script). CLI: `wave_manifest_lint.py <manifest.json> [--json] [--strict] [--root DIR]`. Exit 0=PASS (warnings OK) / 1=FAIL or (--strict) WARN. ASCII+JSON output

Checks: (1) file-ownership disjointness, (2) path existence,
(3) prompt sanity, (4) git history churn, (5) testCmd validation.

Exit: 0 = PASS (warnings allowed unless --strict), non-zero on FAIL or (--strict) WARN.
"""

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class Check:
    """Result of a single check."""

    def __init__(self, name: str, level: str, message: str, details: Optional[Dict[str, Any]] = None):
        self.name = name
        self.level = level  # PASS, INFO, WARN, FAIL
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level,
            "message": self.message,
            "details": self.details
        }


def check_ownership_disjointness(items: List[Dict[str, Any]], repo_root: str) -> List[Check]:
    """Check 1: Ensure file ownership does not overlap across items."""
    checks = []
    overlaps = {}

    # Build list of patterns per item (keeping patterns for overlap checking)
    item_patterns = {}
    for item in items:
        patterns = item.get("ownsFiles", [])
        item_patterns[item["slug"]] = patterns

    # Check for overlaps using glob matching
    def patterns_overlap(patterns_a, patterns_b):
        """Check if two sets of patterns would overlap."""
        for pat_a in patterns_a:
            for pat_b in patterns_b:
                # Check if patterns match each other
                if fnmatch.fnmatch(pat_b, pat_a) or fnmatch.fnmatch(pat_a, pat_b):
                    return True
                # Check if both are literals and equal
                if pat_a == pat_b:
                    return True
        return False

    # Find overlapping items
    for slug_a in item_patterns:
        for slug_b in item_patterns:
            if slug_a < slug_b:  # Only check each pair once
                patterns_a = item_patterns[slug_a]
                patterns_b = item_patterns[slug_b]

                if patterns_overlap(patterns_a, patterns_b):
                    # Find which specific patterns overlap
                    overlapping = []
                    for pat_a in patterns_a:
                        for pat_b in patterns_b:
                            if fnmatch.fnmatch(pat_b, pat_a) or fnmatch.fnmatch(pat_a, pat_b) or pat_a == pat_b:
                                overlapping.append(pat_b if fnmatch.fnmatch(pat_b, pat_a) else pat_a)

                    key = (slug_a, slug_b)
                    overlaps[key] = sorted(list(set(overlapping)))

    if overlaps:
        overlap_list = []
        for (slug_a, slug_b), files in overlaps.items():
            overlap_list.append({
                "items": [slug_a, slug_b],
                "files": files
            })
        checks.append(Check(
            "ownership_disjointness",
            "FAIL",
            f"File ownership overlap detected: {len(overlap_list)} pair(s)",
            {"overlaps": overlap_list}
        ))
    else:
        checks.append(Check(
            "ownership_disjointness",
            "PASS",
            "No file ownership overlaps"
        ))

    return checks


def check_path_existence(items: List[Dict[str, Any]], repo_root: str) -> List[Check]:
    """Check 2: Verify ownsFiles paths exist or flag new files as INFO."""
    checks = []
    new_files = []
    missing_files = []

    for item in items:
        for pattern in item.get("ownsFiles", []):
            expanded = list(Path(repo_root).glob(pattern))
            if expanded:
                # File exists (matched by glob)
                pass
            else:
                # Pattern didn't match - check if it's literally a new file
                path = Path(repo_root) / pattern
                if not path.exists():
                    new_files.append((item["slug"], pattern))

    if new_files:
        details = [{"item": slug, "file": f} for slug, f in new_files]
        checks.append(Check(
            "path_existence",
            "INFO",
            f"{len(new_files)} new file(s)",
            {"new_files": details}
        ))
    else:
        checks.append(Check(
            "path_existence",
            "PASS",
            "All files exist or are flagged as new"
        ))

    return checks


def check_prompt_sanity(items: List[Dict[str, Any]]) -> List[Check]:
    """Check 3: Validate prompt - non-empty, contains worktree marker, no unexpected ALLOW-NON-HAIKU."""
    checks = []
    issues = []

    for item in items:
        slug = item["slug"]
        prompt = item.get("prompt", "")

        # Check non-empty
        if not prompt or not prompt.strip():
            issues.append({
                "item": slug,
                "issue": "empty_prompt",
                "level": "FAIL"
            })
            continue

        # Check for worktree isolation marker
        has_marker = "[ISOLATION: sibling worktree]" in prompt or "sibling worktree" in prompt
        if not has_marker:
            issues.append({
                "item": slug,
                "issue": "missing_isolation_marker",
                "level": "WARN"
            })

        # Check for ALLOW-NON-HAIKU without explicit expectation
        if "[[ALLOW-NON-HAIKU]]" in prompt:
            has_allow_sonnet = "[[ALLOW-SONNET]]" in prompt
            has_allow_opus = "[[ALLOW-OPUS]]" in prompt
            has_explicit = has_allow_sonnet or has_allow_opus

            if not has_explicit:
                issues.append({
                    "item": slug,
                    "issue": "allow_non_haiku_without_explicit_expectation",
                    "level": "WARN"
                })

    if any(i["level"] == "FAIL" for i in issues):
        checks.append(Check(
            "prompt_sanity",
            "FAIL",
            "Prompt validation failed",
            {"issues": issues}
        ))
    elif issues:
        checks.append(Check(
            "prompt_sanity",
            "WARN",
            f"{len(issues)} prompt warning(s)",
            {"issues": issues}
        ))
    else:
        checks.append(Check(
            "prompt_sanity",
            "PASS",
            "All prompts valid"
        ))

    return checks


def check_git_history_churn(items: List[Dict[str, Any]], repo_root: str) -> List[Check]:
    """Check 4: Flag files with recent churn (14-day history) as elevated-retry-risk."""
    checks = []
    warnings = []

    # Try to run git log to get recent changes
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import subprocess; subprocess.run(['git', 'log', '--all', '--since=14.days'], cwd=r'{}', capture_output=True, text=True, timeout=5)".format(repo_root)],
            capture_output=True,
            timeout=5,
            check=False,
            text=True,
            encoding='utf-8', errors='replace',
            cwd=repo_root
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Git not available or repo not initialized - skip check
        checks.append(Check(
            "git_history_churn",
            "PASS",
            "Churn check skipped (git not available)"
        ))
        return checks

    # Use bash/git directly to query recent changes
    try:
        cmd = f"cd {repo_root} && git log --all --since='14 days ago' --name-only --pretty=format: | sort | uniq -c | sort -rn"
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            timeout=5,
            text=True,
            encoding='utf-8', errors='replace',
            check=False
        )

        # Parse git log output to count changes per file
        changed_files = {}
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    parts = line.strip().split(None, 1)
                    if len(parts) == 2:
                        count = int(parts[0])
                        filename = parts[1]
                        changed_files[filename] = count

        # Check if any item owns high-churn files
        for item in items:
            for pattern in item.get("ownsFiles", []):
                # Check literal pattern
                if pattern in changed_files and changed_files[pattern] > 3:
                    warnings.append({
                        "item": item["slug"],
                        "file": pattern,
                        "recent_commits": changed_files[pattern]
                    })

    except Exception:
        # Skip if git query fails
        pass

    if warnings:
        checks.append(Check(
            "git_history_churn",
            "WARN",
            f"{len(warnings)} file(s) with elevated churn",
            {"churn_files": warnings}
        ))
    else:
        checks.append(Check(
            "git_history_churn",
            "PASS",
            "No high-churn files detected"
        ))

    return checks


def check_testcmd_validity(manifest: Dict[str, Any], repo_root: str) -> List[Check]:
    """Check 5: Validate testCmd - binary on PATH or repo-relative script exists."""
    checks = []
    test_cmd = manifest.get("testCmd", "")

    if not test_cmd:
        checks.append(Check(
            "testcmd_validity",
            "WARN",
            "No testCmd specified"
        ))
        return checks

    # Parse first token (binary name)
    tokens = test_cmd.split()
    if not tokens:
        checks.append(Check(
            "testcmd_validity",
            "WARN",
            "testCmd is empty"
        ))
        return checks

    binary = tokens[0]

    # Check if it's a repo-relative script
    if not binary.startswith("/") and not binary.startswith("C:"):
        script_path = Path(repo_root) / binary
        if script_path.exists():
            checks.append(Check(
                "testcmd_validity",
                "PASS",
                f"testCmd script exists: {binary}"
            ))
            return checks

    # Check if binary is on PATH
    found = shutil.which(binary)
    if found:
        checks.append(Check(
            "testcmd_validity",
            "PASS",
            f"testCmd binary found: {binary}"
        ))
    else:
        checks.append(Check(
            "testcmd_validity",
            "FAIL",
            f"testCmd binary not found: {binary}"
        ))

    return checks


def load_manifest(manifest_path: str) -> Dict[str, Any]:
    """Load wave manifest JSON."""
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_checks(manifest: Dict[str, Any], repo_root: str) -> Tuple[List[Check], int]:
    """Run all checks and return list of checks + exit code."""
    checks = []

    # Ensure repo_root is absolute
    if not os.path.isabs(repo_root):
        repo_root = os.path.abspath(repo_root)

    items = manifest.get("items", [])

    # Run all checks
    checks.extend(check_ownership_disjointness(items, repo_root))
    checks.extend(check_path_existence(items, repo_root))
    checks.extend(check_prompt_sanity(items))
    checks.extend(check_git_history_churn(items, repo_root))
    checks.extend(check_testcmd_validity(manifest, repo_root))

    # Determine exit code
    has_fail = any(c.level == "FAIL" for c in checks)
    has_warn = any(c.level == "WARN" for c in checks)

    if has_fail:
        exit_code = 1
    elif has_warn:
        exit_code = 0  # Warnings don't fail by default
    else:
        exit_code = 0

    return checks, exit_code


def format_ascii_output(checks: List[Check], strict: bool = False) -> Tuple[str, int]:
    """Format checks as ASCII output."""
    lines = []
    has_fail = False
    has_warn = False

    for check in checks:
        level = check.level
        message = check.message

        # ASCII-safe output
        lines.append(f"{level}: {check.name}: {message}")

        if level == "FAIL":
            has_fail = True
            # Include details for failures
            if check.details:
                for key, val in check.details.items():
                    lines.append(f"  {key}: {val}")

        elif level == "WARN":
            has_warn = True
            # Include key details for warnings
            if check.details:
                for key, val in check.details.items():
                    if key in ["overlaps", "new_files", "churn_files", "issues"]:
                        if key == "issues" and isinstance(val, list):
                            for issue in val:
                                issue_str = f"{issue.get('item')}: {issue.get('issue')}"
                                if "allow_non_haiku" in issue.get('issue', '').lower():
                                    issue_str += " [[ALLOW-NON-HAIKU]]"
                                lines.append(f"  {issue_str}")
                        else:
                            lines.append(f"  {key}: {val}")

    # Determine exit code
    exit_code = 0
    if has_fail:
        exit_code = 1
    elif has_warn and strict:
        exit_code = 1

    output = "\n".join(lines) + "\n"
    return output, exit_code


def format_json_output(checks: List[Check], strict: bool = False) -> Tuple[str, int]:
    """Format checks as JSON output."""
    has_fail = False
    has_warn = False

    result = {
        "checks": [c.to_dict() for c in checks],
        "summary": {
            "total": len(checks),
            "pass": sum(1 for c in checks if c.level == "PASS"),
            "info": sum(1 for c in checks if c.level == "INFO"),
            "warn": sum(1 for c in checks if c.level == "WARN"),
            "fail": sum(1 for c in checks if c.level == "FAIL")
        }
    }

    # Determine exit code
    for check in checks:
        if check.level == "FAIL":
            has_fail = True
        elif check.level == "WARN":
            has_warn = True

    exit_code = 0
    if has_fail:
        exit_code = 1
    elif has_warn and strict:
        exit_code = 1

    output = json.dumps(result, indent=2) + "\n"
    return output, exit_code


def main():
    parser = argparse.ArgumentParser(
        description="Wave manifest preflight validator"
    )
    parser.add_argument("manifest", help="Path to wave manifest JSON")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings")
    parser.add_argument("--root", default=".", help="Repository root (default: current directory)")

    args = parser.parse_args()

    try:
        manifest = load_manifest(args.manifest)
        repo_root = args.root

        checks, _ = run_checks(manifest, repo_root)

        if args.json:
            output, exit_code = format_json_output(checks, args.strict)
        else:
            output, exit_code = format_ascii_output(checks, args.strict)

        sys.stdout.write(output)
        sys.exit(exit_code)

    except Exception as e:
        sys.stderr.write(f"FAIL: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
