#!/usr/bin/env python3
"""
STATE.md checkpoint-accuracy verifier (guardrail #1 + #5 + #6).
INDEX: Guardrail #1: STATE.md checkpoint-accuracy verifier; parses STATE.md for falsifiable progress claims ("**Current Version:** vX.Y.Z", "resolved", "pushed", "MERGED", "current HEAD <sha>") and verifies against on-disk git truth (git tags + package.json for versions, git status --porcelain for unmerged files, git ls-remote --heads for pushed branches, gh pr view for PR states, git rev-list for commit lag); exit 0=no contradictions + at least one claim verified / 1=contradictions found / 2=error or zero verifiable claims (fail-closed); reports UNVERIFIABLE/SKIP for unparseable/unavailable-tool claims; subprocess timeout 30s for Windows CI compatibility; stdlib-only; ASCII-only output (ascii_safe()/safe_print() transliterate+sanitize any non-ASCII content read FROM the verified files before printing, so Unicode in real-world checkpoint prose never crashes a non-UTF-8 console). Guardrail #5: STATE.md freshness gate detects stale checkpoints (>50 commits behind HEAD). Guardrail #6 (--check-buildlog-drift, included in the default run): STATE.md's newest "## CURRENT (<ts>)" header must not be older than BUILDLOG.md's newest "--- <ts> [checkpoint...]" line (BUILDLOG.md is append-only history, never the current-state source; STATE.md is the only current-state surface -- a checkpoint that advances BUILDLOG.md without also advancing STATE.md is drift and fails closed, including when STATE.md has no parseable CURRENT header at all).

Parses STATE.md for falsifiable progress claims and verifies each against on-disk git truth.
Catches cases where the orchestrator's checkpoint overstates progress (e.g., "resolved" while
git status still shows unmerged files).

Claim classes verified:
  (a) "Current Version: vX.Y.Z" -> git tags + package.json (version matches latest tag and package.json)
  (b) "resolved"/"conflicts resolved"/"clean" -> git status --porcelain (no UU/AA for those paths)
  (c) "pushed" -> git ls-remote --heads (ref exists on origin)
  (d) "MERGED" PR -> gh pr view --json state (if gh available, else SKIP)
  (e) "current HEAD <sha>" -> git rev-list --count <sha>..HEAD (fails if >50 commits stale)
  (f) STATE.md "## CURRENT (<ts>)" vs BUILDLOG.md "--- <ts> [checkpoint...]" (Guardrail #6,
      --check-buildlog-drift): STATE.md must not be stale relative to BUILDLOG.md's checkpoints

Exit codes:
  0: No contradictions found, and at least one verifiable claim was extracted
  1: At least one claim contradicted by disk truth
  2: Usage error, subprocess failure, or ZERO verifiable claims extracted (fail-closed)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


# Common Unicode punctuation -> ASCII transliteration. Applied before the
# catch-all replace so familiar characters (arrows, dashes, smart quotes)
# degrade to a readable ASCII equivalent instead of a bare '?' placeholder.
_ASCII_TRANSLITERATIONS = {
    "→": "->",
    "←": "<-",
    "—": "--",
    "–": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    "•": "*",
    "﻿": "",
}


def ascii_safe(text):
    """Make text safe to print on ANY stdout/stderr codepage (e.g. Windows
    cp1252), regardless of what it contains.

    This module's own literal strings are already ASCII, but every claim,
    detail, and path it prints can embed arbitrary content read FROM the
    STATE.md/BUILDLOG.md being verified -- real-world checkpoint prose
    routinely contains Unicode arrows and dashes. Without sanitizing that at
    the print boundary, printing it on a non-UTF-8 console raises
    UnicodeEncodeError before any verdict is emitted (the Windows crash this
    guards against), even though every literal string in this file is ASCII.

    Transliterates common Unicode punctuation to ASCII equivalents, then
    replaces anything still non-ASCII with '?' so this function never raises.
    """
    if text is None:
        return text
    text = str(text)
    for unicode_char, ascii_equivalent in _ASCII_TRANSLITERATIONS.items():
        text = text.replace(unicode_char, ascii_equivalent)
    return text.encode("ascii", errors="replace").decode("ascii")


def safe_print(text, file=None):
    """print() that is ASCII-safe regardless of the active stdout/stderr
    codepage. See ascii_safe() for why this is necessary."""
    if file is None:
        print(ascii_safe(text))
    else:
        print(ascii_safe(text), file=file)


def run_command(cmd, cwd=None):
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding='utf-8', errors='replace',
            timeout=30
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
        "merged": [],
        "version": [],
        "freshness": []
    }

    try:
        with open(state_md_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        safe_print(f"ERROR: Cannot read {state_md_path}: {e}", file=sys.stderr)
        return None

    # Pattern: "X resolved" or "conflicts resolved" with file paths
    resolved_pattern = r'((?:[\w\-/\.]+\.py[w]?|worktree\s+\w+|conflicts?)\s+(?:conflicts?\s+)?resolved|resolved\s+(?:conflicts?|[\w\-/\.]+))'

    # Pattern: "pushed"
    pushed_pattern = r'(pushed|push\s+(?:to\s+)?origin)'

    # Pattern: "MERGED" PR
    merged_pattern = r'(MERGED|merged\s+PR)'

    # Pattern: "**Current Version:** vX.Y.Z" (markdown bold)
    # Note: The closing ** comes before the colon in markdown: "**Current Version:**"
    version_pattern = r'\*\*Current\s+Version:\*\*\s*(v[\d\.]+)'

    # Pattern: "current HEAD <sha>" (freshness check)
    # Matches: "current HEAD e5e6e22" or similar SHA patterns (case-insensitive)
    freshness_pattern = r'current\s+head\s+([a-f0-9]{7,})'

    for i, line in enumerate(lines, 1):
        lower_line = line.lower()

        # Version claims (match "**Current Version: vX.Y.Z**")
        version_match = re.search(version_pattern, line)
        if version_match:
            version_str = version_match.group(1)  # e.g., "v0.5.0"
            claims["version"].append({
                "claim": line.strip(),
                "line": i,
                "context": line,
                "version": version_str
            })

        # Freshness claims (match "current HEAD <sha>")
        freshness_match = re.search(freshness_pattern, lower_line)
        if freshness_match:
            sha = freshness_match.group(1)  # e.g., "e5e6e22"
            claims["freshness"].append({
                "claim": line.strip(),
                "line": i,
                "context": line,
                "sha": sha
            })

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


def verify_version_claims(claims, git_root):
    """Verify "Current Version" claims against git tags and package.json.

    Checks:
      - git describe --tags or latest tag matches the claimed version
      - package.json version field matches the claimed version

    Returns findings list.
    """
    findings = []

    if not claims:
        return findings

    for claim_info in claims:
        claim = claim_info["claim"]
        line = claim_info["line"]
        claimed_version = claim_info.get("version", "")

        # Read package.json version (if it exists)
        package_json_path = git_root / "package.json"
        package_version = None
        if package_json_path.exists():
            try:
                with open(package_json_path, 'r', encoding='utf-8') as f:
                    pkg_data = json.load(f)
                    package_version = pkg_data.get("version")
            except Exception:
                pass

        # Get latest git tag
        rc, stdout, stderr = run_command(
            ["git", "tag", "-l"],
            cwd=git_root
        )
        git_tags = []
        if rc == 0:
            git_tags = [tag.strip() for tag in stdout.strip().split('\n') if tag.strip()]

        # Find the latest v-prefixed tag
        latest_git_version = None
        if git_tags:
            v_tags = [t for t in git_tags if t.startswith('v')]
            if v_tags:
                # Sort by version number (simple string sort works for v-prefixed semantic versions)
                v_tags.sort(reverse=True)
                latest_git_version = v_tags[0]

        # Normalize versions for comparison (remove 'v' prefix)
        claimed_ver_normalized = claimed_version.lstrip('v') if claimed_version else ""
        pkg_ver_normalized = package_version.lstrip('v') if package_version else ""
        git_ver_normalized = latest_git_version.lstrip('v') if latest_git_version else ""

        # Check for contradictions
        contradictions = []

        if package_version and claimed_ver_normalized != pkg_ver_normalized:
            contradictions.append(
                f"Claimed version {claimed_version} but package.json has {package_version}"
            )

        if latest_git_version and claimed_ver_normalized != git_ver_normalized:
            contradictions.append(
                f"Claimed version {claimed_version} but latest git tag is {latest_git_version}"
            )

        if contradictions:
            findings.append({
                "claim": claim,
                "line": line,
                "status": "CONTRADICTION",
                "detail": "; ".join(contradictions)
            })

    return findings


def verify_freshness_claims(claims, git_root):
    """Verify STATE.md freshness: current HEAD <sha> should not lag >50 commits behind HEAD.

    Checks:
      - Parse the claimed HEAD sha from "current HEAD <sha>"
      - Verify sha exists in repo (fail-closed if unknown)
      - Compute git rev-list --count <sha>..HEAD (commits since claimed sha)
      - CONTRADICTION if count > 50 (stale)

    Returns findings list.
    """
    findings = []

    if not claims:
        return findings

    for claim_info in claims:
        claim = claim_info["claim"]
        line = claim_info["line"]
        sha = claim_info.get("sha", "")

        if not sha:
            findings.append({
                "claim": claim,
                "line": line,
                "status": "ERROR",
                "detail": "Could not extract SHA from freshness claim"
            })
            continue

        # Verify the SHA exists in the repo
        rc, _, stderr = run_command(
            ["git", "cat-file", "-t", sha],
            cwd=git_root
        )

        if rc != 0:
            findings.append({
                "claim": claim,
                "line": line,
                "status": "ERROR",
                "detail": f"SHA {sha} not found in repository: {stderr}"
            })
            continue

        # Count commits since the claimed sha
        rc, stdout, stderr = run_command(
            ["git", "rev-list", "--count", f"{sha}..HEAD"],
            cwd=git_root
        )

        if rc != 0:
            findings.append({
                "claim": claim,
                "line": line,
                "status": "ERROR",
                "detail": f"Failed to count commits: {stderr}"
            })
            continue

        try:
            commit_lag = int(stdout.strip())
        except ValueError:
            findings.append({
                "claim": claim,
                "line": line,
                "status": "ERROR",
                "detail": f"Could not parse commit count: {stdout}"
            })
            continue

        # Check freshness (limit: 50 commits)
        if commit_lag > 50:
            findings.append({
                "claim": claim,
                "line": line,
                "status": "CONTRADICTION",
                "detail": f"STATE.md is {commit_lag} commits stale (limit 50)"
            })

    return findings


# --- Guardrail #6: STATE.md vs BUILDLOG.md checkpoint-drift ----------------
#
# Contract (decided, not revisited here): BUILDLOG.md is append-only history
# and is NEVER the current-state source; STATE.md is the only current-state
# surface. A checkpoint must update BOTH. This check catches the case where
# BUILDLOG.md was appended to (a checkpoint happened) but STATE.md's "##
# CURRENT (<timestamp>)" header was never advanced to match.

_DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')
_TIME_RE = re.compile(r'[T ]~?(\d{2}):(\d{2})(?::(\d{2}))?')
_CURRENT_HEADER_RE = re.compile(r'^## CURRENT \(([^)]*)\)', re.MULTILINE)
_CHECKPOINT_LINE_RE = re.compile(r'^-{3,}\s+(\S+)\s+\[checkpoint(?::[^\]]+)?\]', re.MULTILINE)


def extract_date_time(text):
    """Extract a loose (date_str, time_str_or_None) tuple from checkpoint-ish
    text. Tolerates both full-ISO forms ("2026-09-15T00:55Z") and date-only /
    human-annotated forms ("2026-09-02 12:50 CDT", "2026-08-04 ~04:25Z",
    "2026-08-03 late") -- real STATE.md CURRENT headers use all of these.

    Returns None if no YYYY-MM-DD date is present at all. time_str, when
    present, is normalized to "HH:MM:SS" (seconds default to "00").
    """
    date_match = _DATE_RE.search(text)
    if not date_match:
        return None
    date_str = date_match.group(1)
    rest = text[date_match.end():]
    time_match = _TIME_RE.search(rest)
    if time_match:
        hh, mm, ss = time_match.group(1), time_match.group(2), time_match.group(3) or "00"
        time_str = f"{hh}:{mm}:{ss}"
    else:
        time_str = None
    return (date_str, time_str)


def compare_timestamps(a, b):
    """Compare two (date_str, time_str_or_None) tuples from extract_date_time.

    Returns -1 if a is older than b, 1 if a is newer, 0 if equal OR if the
    relative order cannot be determined (same date, time-of-day missing on
    either side) -- intentionally conservative rather than guessing.
    """
    a_date, a_time = a
    b_date, b_time = b
    if a_date != b_date:
        return -1 if a_date < b_date else 1
    if a_time is None or b_time is None or a_time == b_time:
        return 0
    return -1 if a_time < b_time else 1


def _format_ts(ts):
    date_str, time_str = ts
    return f"{date_str} {time_str}" if time_str else date_str


def parse_current_blocks(state_md_path):
    """Find every "## CURRENT (<timestamp>)" header in STATE.md.

    Returns a list of (timestamp_tuple, line_no, raw_header) for every
    header with an extractable date. Headers with no parseable date are
    skipped, never fabricated as a false timestamp.
    """
    if not state_md_path:
        return []
    try:
        text = Path(state_md_path).read_text(encoding='utf-8')
    except Exception:
        return []

    results = []
    for match in _CURRENT_HEADER_RE.finditer(text):
        ts = extract_date_time(match.group(1))
        if ts is None:
            continue
        line_no = text.count("\n", 0, match.start()) + 1
        results.append((ts, line_no, match.group(0).strip()))
    return results


def parse_checkpoint_lines(buildlog_path):
    """Find every "--- <timestamp> [checkpoint...] ..." line in BUILDLOG.md.

    Returns a list of (timestamp_tuple, line_no, raw_line) for every line
    with an extractable date.
    """
    if not buildlog_path:
        return []
    try:
        text = Path(buildlog_path).read_text(encoding='utf-8')
    except Exception:
        return []

    results = []
    for match in _CHECKPOINT_LINE_RE.finditer(text):
        ts = extract_date_time(match.group(1))
        if ts is None:
            continue
        line_no = text.count("\n", 0, match.start()) + 1
        results.append((ts, line_no, match.group(0).strip()))
    return results


def verify_buildlog_drift(state_md_path, buildlog_path):
    """Guardrail #6: the newest STATE.md "## CURRENT (<ts>)" header must not
    be older than the newest BUILDLOG.md "--- <ts> [checkpoint...]" line.

    Fails CLOSED: if BUILDLOG.md has checkpoint lines but STATE.md has no
    parseable CURRENT header at all, that is reported as a CONTRADICTION
    (drift), never silently skipped.

    Returns a list of finding dicts (empty when STATE.md is at least as
    fresh as BUILDLOG.md -- matching the no-problem-found convention of the
    other verify_* functions in this module).
    """
    current_blocks = parse_current_blocks(state_md_path)
    checkpoints = parse_checkpoint_lines(buildlog_path)

    label = "STATE.md CURRENT header vs BUILDLOG.md checkpoint (Guardrail #6)"

    if not checkpoints and not current_blocks:
        return [{
            "claim": label,
            "line": 0,
            "status": "SKIP",
            "detail": ("No BUILDLOG.md checkpoint lines and no STATE.md "
                       "CURRENT header found; nothing to verify"),
        }]

    if checkpoints and not current_blocks:
        newest_bl = max(checkpoints, key=lambda item: item[0])
        return [{
            "claim": label,
            "line": 0,
            "status": "CONTRADICTION",
            "detail": (f"BUILDLOG.md has checkpoint(s) (newest "
                       f"{_format_ts(newest_bl[0])}) but STATE.md has no "
                       f"parseable CURRENT header -- failing closed"),
        }]

    if current_blocks and not checkpoints:
        return [{
            "claim": label,
            "line": 0,
            "status": "SKIP",
            "detail": ("STATE.md has a CURRENT header but BUILDLOG.md has "
                       "no checkpoint lines to compare it against"),
        }]

    newest_state = max(current_blocks, key=lambda item: item[0])
    newest_bl = max(checkpoints, key=lambda item: item[0])

    if compare_timestamps(newest_state[0], newest_bl[0]) < 0:
        return [{
            "claim": newest_state[2],
            "line": newest_state[1],
            "status": "CONTRADICTION",
            "detail": (f"STATE.md newest CURRENT timestamp "
                       f"({_format_ts(newest_state[0])}) is older than "
                       f"BUILDLOG.md's newest checkpoint "
                       f"({_format_ts(newest_bl[0])})"),
        }]

    return []


def _resolve_buildlog_path(args, state_md_path):
    """Resolve BUILDLOG.md: explicit --buildlog, else (when --state-md is
    explicit) alongside --state-md, else ./BUILDLOG.md, else $AESOP_BUILDLOG_MD.
    When an explicit --state-md path is given, we prioritize the BUILDLOG.md
    next to it (covers the aesop convention of STATE.md + BUILDLOG.md living
    together, e.g. under C:/Users/matt8/conductor3/). Returns None if nothing
    exists -- verify_buildlog_drift treats a missing file the same as an empty
    one."""
    if args.buildlog:
        return Path(args.buildlog).resolve()

    candidates = []

    # When --state-md is explicit, prioritize BUILDLOG.md next to it
    if args.state_md:
        candidates.append(state_md_path.parent / "BUILDLOG.md")

    # Then try repo-local BUILDLOG.md
    candidates.append(Path("BUILDLOG.md").resolve())

    # Then try environment variable
    env_buildlog = os.environ.get("AESOP_BUILDLOG_MD")
    if env_buildlog:
        candidates.append(Path(env_buildlog).resolve())

    # Add state_md's directory again as fallback (in case it wasn't added above)
    state_sibling = state_md_path.parent / "BUILDLOG.md"
    if state_sibling not in candidates:
        candidates.append(state_sibling)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _print_findings(findings, json_mode, header_lines):
    """Shared text/JSON finding-report renderer for both the default run and
    the --check-buildlog-drift standalone mode. ASCII-safe throughout."""
    if json_mode:
        print(json.dumps({
            "findings": findings,
            "contradiction_count": sum(1 for f in findings if f["status"] == "CONTRADICTION"),
            "error_count": sum(1 for f in findings if f["status"] == "ERROR"),
            "unverifiable_count": sum(1 for f in findings if f["status"] == "UNVERIFIABLE"),
            "skip_count": sum(1 for f in findings if f["status"] == "SKIP"),
            **header_lines,
        }, indent=2))
    elif findings:
        for key, value in header_lines.items():
            safe_print(f"{key}: {value}")
        safe_print("")
        for finding in findings:
            safe_print(f"[{finding['status']}] Line {finding['line']}: {finding['claim']}")
            safe_print(f"  -> {finding['detail']}\n")


def _exit_code_for(findings):
    contradiction_count = sum(1 for f in findings if f["status"] == "CONTRADICTION")
    error_count = sum(1 for f in findings if f["status"] == "ERROR")
    if contradiction_count > 0:
        return 1
    if error_count > 0:
        return 2
    return 0


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
        "--buildlog",
        type=str,
        default=None,
        help="Path to BUILDLOG.md for Guardrail #6 (default: ./BUILDLOG.md, "
             "else $AESOP_BUILDLOG_MD, else alongside --state-md)"
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
    parser.add_argument(
        "--check-buildlog-drift",
        action="store_true",
        help="Run ONLY Guardrail #6 (STATE.md CURRENT header vs BUILDLOG.md "
             "checkpoint-drift), skipping the version/resolved/pushed/merged/"
             "freshness claim checks. The drift check always also runs as "
             "part of the default (no-flag) run."
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
            safe_print("ERROR: STATE.md not found. Use --state-md to specify path.", file=sys.stderr)
            return 2

    if not state_md_path.exists():
        safe_print(f"ERROR: {state_md_path} does not exist", file=sys.stderr)
        return 2

    buildlog_path = _resolve_buildlog_path(args, state_md_path)

    if args.check_buildlog_drift:
        # Standalone mode: pure text/timestamp comparison, no git truth
        # needed -- usable even when STATE.md doesn't live inside a repo.
        drift_findings = verify_buildlog_drift(state_md_path, buildlog_path)
        _print_findings(drift_findings, args.json, {
            "state_md": str(state_md_path),
            "buildlog": str(buildlog_path) if buildlog_path else None,
        })
        return _exit_code_for(drift_findings)

    # Find git root
    git_root = find_repo_root(state_md_path)
    if not git_root:
        safe_print(f"ERROR: Could not find git root for {state_md_path}", file=sys.stderr)
        return 2

    # Parse claims
    claims = parse_state_md(state_md_path)
    if claims is None:
        return 2

    # Check if we extracted ANY verifiable claims (fail-closed if zero).
    # STATE.md's own "## CURRENT (<ts>)" headers count as claims too (they
    # are the input to Guardrail #6 below), so a STATE.md whose only
    # falsifiable content is its CURRENT header is not treated as empty.
    current_blocks = parse_current_blocks(state_md_path)
    total_claims = sum(len(v) for v in claims.values()) + len(current_blocks)
    if total_claims == 0:
        safe_print("ERROR: STATE.md contains no verifiable claims (no version, resolved, pushed, merged, freshness, or CURRENT-header statements). Cannot verify.", file=sys.stderr)
        return 2

    # Verify claims
    all_findings = []
    all_findings.extend(verify_version_claims(claims["version"], git_root))
    all_findings.extend(verify_resolved_claims(claims["resolved"], git_root))
    all_findings.extend(verify_pushed_claims(claims["pushed"], git_root))
    all_findings.extend(verify_merged_claims(claims["merged"], git_root))
    all_findings.extend(verify_freshness_claims(claims["freshness"], git_root))
    all_findings.extend(verify_buildlog_drift(state_md_path, buildlog_path))

    # Output findings
    _print_findings(all_findings, args.json, {
        "state_md": str(state_md_path),
        "git_root": str(git_root),
    })

    return _exit_code_for(all_findings)


if __name__ == "__main__":
    sys.exit(main())
