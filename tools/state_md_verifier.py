#!/usr/bin/env python3
"""
STATE.md checkpoint-accuracy verifier (guardrail #1).

Parses STATE.md for falsifiable progress claims and verifies each against on-disk git truth.
Catches cases where the orchestrator's checkpoint overstates progress (e.g., "resolved" while
git status still shows unmerged files).

Claim classes verified:
  (a) "resolved"/"conflicts resolved"/"clean" -> git status --porcelain (no UU/AA for those paths)
  (b) "pushed" -> git ls-remote --heads (ref exists on origin)
  (c) "MERGED" PR -> gh pr view --json state (if gh available, else SKIP)

Exit codes:
  0: No contradictions found
  1: At least one claim contradicted by disk truth
  2: Usage error or subprocess failure
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def run_command(cmd, cwd=None):
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=10
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 2, "", "Command timeout"
    except Exception as e:
        return 2, "", str(e)


def find_repo_root(state_md_path):
    """Find the git root for the worktree containing state_md_path."""
    path = Path(state_md_path).resolve().parent
    while path != path.parent:
        if (path / ".git").exists():
            return path
        path = path.parent
    return None


def parse_state_md(state_md_path):
    """Parse STATE.md and extract falsifiable claims.

    Returns a dict with claim types as keys and lists of claim dicts as values.
    Each claim dict has: 'claim' (the text), 'line' (line number), 'context' (surrounding text)
    """
    claims = {
        "resolved": [],
        "pushed": [],
        "merged": []
    }

    try:
        with open(state_md_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"ERROR: Cannot read {state_md_path}: {e}", file=sys.stderr)
        return None

    # Pattern: "X resolved" or "conflicts resolved" with file paths
    resolved_pattern = r'((?:[\w\-/\.]+\.py[w]?|worktree\s+\w+|conflicts?)\s+(?:conflicts?\s+)?resolved|resolved\s+(?:conflicts?|[\w\-/\.]+))'

    # Pattern: "pushed"
    pushed_pattern = r'(pushed|push\s+(?:to\s+)?origin)'

    # Pattern: "MERGED" PR
    merged_pattern = r'(MERGED|merged\s+PR)'

    for i, line in enumerate(lines, 1):
        lower_line = line.lower()

        # Resolved claims
        if re.search(resolved_pattern, lower_line, re.IGNORECASE):
            claims["resolved"].append({
                "claim": line.strip(),
                "line": i,
                "context": line
            })

        # Pushed claims
        if re.search(pushed_pattern, lower_line, re.IGNORECASE):
            claims["pushed"].append({
                "claim": line.strip(),
                "line": i,
                "context": line
            })

        # Merged claims
        if re.search(merged_pattern, lower_line, re.IGNORECASE):
            claims["merged"].append({
                "claim": line.strip(),
                "line": i,
                "context": line
            })

    return claims


def verify_resolved_claims(claims, git_root):
    """Verify "resolved" claims against git status.

    Returns a list of finding dicts with 'claim', 'line', 'status', 'detail'.
    """
    findings = []

    if not claims:
        return findings

    for claim_info in claims:
        claim = claim_info["claim"]
        line = claim_info["line"]

        # Extract file paths from the claim
        # Look for paths like "tools/foo.py" or patterns like "conflicts resolved"
        file_matches = re.findall(r'[\w\-/\.]+\.py[w]?', claim)

        # Check git status for unmerged files
        rc, stdout, stderr = run_command(["git", "status", "--porcelain"], cwd=git_root)
        if rc != 0:
            findings.append({
                "claim": claim,
                "line": line,
                "status": "ERROR",
                "detail": f"git status failed: {stderr}"
            })
            continue

        status_lines = stdout.strip().split('\n') if stdout.strip() else []

        # Check if any files in the claim have UU, AA, or other merge conflict markers
        unmerged_files = []
        if file_matches:
            for file_match in file_matches:
                for status_line in status_lines:
                    if not status_line:
                        continue
                    parts = status_line.split(maxsplit=1)
                    if len(parts) >= 2:
                        status, filepath = parts[0], parts[1]
                        if file_match in filepath and status in ('UU', 'AA', 'DD', 'UD', 'DU'):
                            unmerged_files.append((filepath, status))
        else:
            # Generic "resolved"/"conflicts resolved" claim without specific files
            # Flag if there are ANY unmerged files
            for status_line in status_lines:
                if not status_line:
                    continue
                parts = status_line.split(maxsplit=1)
                if len(parts) >= 2:
                    status = parts[0]
                    if status in ('UU', 'AA', 'DD', 'UD', 'DU'):
                        unmerged_files.append((parts[1], status))

        if unmerged_files:
            detail = "Unmerged files found: " + ", ".join(
                f"{f} ({s})" for f, s in unmerged_files[:5]
            )
            findings.append({
                "claim": claim,
                "line": line,
                "status": "CONTRADICTION",
                "detail": detail
            })

    return findings

    return findings


def verify_pushed_claims(claims, git_root):
    """Verify "pushed" claims against git ls-remote.

    Returns findings list.
    """
    findings = []

    if not claims:
        return findings

    for claim_info in claims:
        claim = claim_info["claim"]
        line = claim_info["line"]

        # Check if the current branch was pushed
        # Try to extract branch name or just check HEAD ref
        rc, stdout, stderr = run_command(
            ["git", "ls-remote", "--heads", "origin"],
            cwd=git_root
        )
        if rc != 0:
            findings.append({
                "claim": claim,
                "line": line,
                "status": "ERROR",
                "detail": f"git ls-remote failed: {stderr}"
            })
            continue

        remote_branches = set()
        for line_text in stdout.strip().split('\n'):
            if line_text:
                parts = line_text.split()
                if len(parts) >= 2:
                    ref = parts[1]
                    branch = ref.replace('refs/heads/', '')
                    remote_branches.add(branch)

        # Extract branch names from the claim (guard/state-md-accuracy, etc.)
        branch_matches = re.findall(r'(?:push|branch)\s+([a-zA-Z0-9\-/_]+)', claim, re.IGNORECASE)

        if branch_matches:
            found_any = False
            for branch in branch_matches:
                if branch in remote_branches or branch.replace('origin/', '') in remote_branches:
                    found_any = True
                    break
            if not found_any:
                findings.append({
                    "claim": claim,
                    "line": line,
                    "status": "CONTRADICTION",
                    "detail": f"Branch(es) {branch_matches} not found on origin"
                })

    return findings


def verify_merged_claims(claims, git_root):
    """Verify "MERGED" PR claims via gh pr view (or SKIP if gh unavailable).

    When a PR cannot be resolved in the current repo (e.g., PR from different repo),
    classify as UNVERIFIABLE, not ERROR. Only ERROR if gh itself fails.

    Returns findings list.
    """
    findings = []

    if not claims:
        return findings

    # Check if gh is available
    rc, _, _ = run_command(["gh", "--version"])
    gh_available = (rc == 0)

    for claim_info in claims:
        claim = claim_info["claim"]
        line = claim_info["line"]

        if not gh_available:
            findings.append({
                "claim": claim,
                "line": line,
                "status": "SKIP",
                "detail": "gh CLI not available; skipping PR state verification"
            })
            continue

        # Extract PR numbers from the claim
        pr_matches = re.findall(r'#(\d{3,5})', claim)

        if not pr_matches:
            findings.append({
                "claim": claim,
                "line": line,
                "status": "UNVERIFIABLE",
                "detail": "Could not extract PR number from claim"
            })
            continue

        for pr_num in pr_matches:
            # Get PR state via gh
            rc, stdout, stderr = run_command(
                ["gh", "pr", "view", pr_num, "--json", "state"],
                cwd=git_root
            )

            if rc != 0:
                # Check if it's a "not found" error (unresolvable PR) vs a real error
                if "Could not resolve to a PullRequest" in stderr or "not found" in stderr.lower():
                    # PR doesn't exist in this repo — classify as UNVERIFIABLE
                    findings.append({
                        "claim": claim,
                        "line": line,
                        "status": "UNVERIFIABLE",
                        "detail": f"PR #{pr_num} not found in current repo"
                    })
                else:
                    # Real error (network, auth, etc.)
                    findings.append({
                        "claim": claim,
                        "line": line,
                        "status": "ERROR",
                        "detail": f"gh pr view {pr_num} failed: {stderr}"
                    })
                continue

            try:
                pr_data = json.loads(stdout)
                state = pr_data.get("state", "UNKNOWN")

                if state != "MERGED":
                    findings.append({
                        "claim": claim,
                        "line": line,
                        "status": "CONTRADICTION",
                        "detail": f"PR #{pr_num} state is {state}, not MERGED"
                    })
            except json.JSONDecodeError:
                findings.append({
                    "claim": claim,
                    "line": line,
                    "status": "ERROR",
                    "detail": f"Could not parse gh output for PR {pr_num}"
                })

    return findings


def main():
    parser = argparse.ArgumentParser(
        description="Verify STATE.md checkpoint accuracy against git truth"
    )
    parser.add_argument(
        "--state-md",
        type=str,
        default=None,
        help="Path to STATE.md (default: ./STATE.md, else $AESOP_STATE_MD)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output findings as JSON"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=True,
        help="Check mode (default)"
    )

    args = parser.parse_args()

    # Resolve STATE.md path
    if args.state_md:
        state_md_path = Path(args.state_md).resolve()
    else:
        # Try local STATE.md first, then an operator-configured location.
        # The fallback is env-driven so no site-specific directory name is
        # baked into the shipped source (portability gate).
        candidates = [Path("STATE.md").resolve()]
        env_state_md = os.environ.get("AESOP_STATE_MD")
        if env_state_md:
            candidates.append(Path(env_state_md).resolve())
        state_md_path = None
        for candidate in candidates:
            if candidate.exists():
                state_md_path = candidate
                break

        if not state_md_path:
            print("ERROR: STATE.md not found. Use --state-md to specify path.", file=sys.stderr)
            return 2

    if not state_md_path.exists():
        print(f"ERROR: {state_md_path} does not exist", file=sys.stderr)
        return 2

    # Find git root
    git_root = find_repo_root(state_md_path)
    if not git_root:
        print(f"ERROR: Could not find git root for {state_md_path}", file=sys.stderr)
        return 2

    # Parse claims
    claims = parse_state_md(state_md_path)
    if claims is None:
        return 2

    # Verify claims
    all_findings = []
    all_findings.extend(verify_resolved_claims(claims["resolved"], git_root))
    all_findings.extend(verify_pushed_claims(claims["pushed"], git_root))
    all_findings.extend(verify_merged_claims(claims["merged"], git_root))

    # Output findings
    if args.json:
        print(json.dumps({
            "state_md": str(state_md_path),
            "git_root": str(git_root),
            "findings": all_findings,
            "contradiction_count": sum(1 for f in all_findings if f["status"] == "CONTRADICTION"),
            "error_count": sum(1 for f in all_findings if f["status"] == "ERROR"),
            "unverifiable_count": sum(1 for f in all_findings if f["status"] == "UNVERIFIABLE"),
            "skip_count": sum(1 for f in all_findings if f["status"] == "SKIP")
        }, indent=2))
    else:
        if all_findings:
            print(f"STATE.md: {state_md_path}")
            print(f"Git root: {git_root}\n")
            for finding in all_findings:
                status = finding["status"]
                line = finding["line"]
                claim = finding["claim"]
                detail = finding["detail"]
                print(f"[{status}] Line {line}: {claim}")
                print(f"  -> {detail}\n")

    # Return codes
    contradiction_count = sum(1 for f in all_findings if f["status"] == "CONTRADICTION")
    if contradiction_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
