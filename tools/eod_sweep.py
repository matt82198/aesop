#!/usr/bin/env python3
"""
eod_sweep.py — End-of-day safety check for repository health.
INDEX: End-of-day safety check (dirty trees, unpushed commits); verdict appended to BUILDLOG.md via state_store WriteAPI (--buildlog filename must be BUILDLOG.md, fail-closed)

Verifies git repositories are safe (no data loss risk):
- Working tree clean/dirty
- Branch pushed (ahead-count 0)
- Untracked files not in .gitignore

Output contract:
  Line 1: EOD-SWEEP: SAFE or EOD-SWEEP: AT-RISK — <n> findings
  Lines 2+: One finding per line (if any)
  Exit code 0 only when SAFE.
  Verdict also appended to BUILDLOG.md (if --buildlog provided or AESOP_STATE_ROOT set).

Usage: eod_sweep.py [--repos PATHS] [--readonly-repos PATHS] [--fix-push]
                    [--buildlog PATH] [--timestamp YYYY-MM-DD HH:MM]

  --repos: Colon-separated paths to scan (default: empty; use env var or flag to specify)
  --readonly-repos: Colon-separated paths that should NOT be auto-pushed
  --fix-push: Auto-push unpushed commits in repos where safe
  --buildlog: Path to BUILDLOG.md (default: AESOP_STATE_ROOT/BUILDLOG.md or ./state/BUILDLOG.md);
              the filename must be BUILDLOG.md (WriteAPI owns the canonical name)
  --timestamp: Timestamp for BUILDLOG entry (format: YYYY-MM-DD HH:MM; omit to exclude timestamp)
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from common import get_state_dir
except ImportError:
    from tools.common import get_state_dir

from state_store.write_api import WriteAPI, WriteConflict


class Finding:
    """A single finding with repo + message."""
    def __init__(self, repo, msg):
        self.repo = repo
        self.msg = msg

    def __str__(self):
        return f"{self.repo.name}: {self.msg}"


def get_git_status(repo_path):
    """Return (is_clean, dirty_files_list) for a repo.

    Returns:
      (True, []): Repo is clean
      (False, list): Repo is dirty with file list
      (None, error_msg): Git command failed (FAIL-CLOSED: treat as AT-RISK)
    """
    try:
        # Resolve path to normalize 8.3 short names on Windows
        resolved_path = Path(repo_path).resolve()
        result = subprocess.run(
            ['git', '-C', str(resolved_path), 'status', '--porcelain'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
        )

        # FAIL-CLOSED: Check return code before processing output
        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr.strip() else f"exit code {result.returncode}"
            return (None, f"git status check failed: {error_msg}")

        output = result.stdout.strip()
        if not output:
            return (True, [])
        else:
            dirty = [line for line in output.split('\n') if line]
            return (False, dirty)
    except Exception as e:
        return (None, str(e))


def get_ahead_count(repo_path):
    """Return count of commits ahead of origin/HEAD (or None on error).

    Returns:
      int >= 0: Number of commits ahead (0 = all pushed or no remote)
      None: Git command failed unexpectedly (FAIL-CLOSED: treat as AT-RISK)
    """
    try:
        # Resolve path to normalize 8.3 short names on Windows
        resolved_path = Path(repo_path).resolve()
        # First check if there's a tracking branch
        result = subprocess.run(
            ['git', '-C', str(resolved_path), 'rev-list', '--left-only', '--count', 'HEAD...@{u}'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
        )

        # If upstream tracking exists, use that result
        if result.returncode == 0:
            output = result.stdout.strip()
            try:
                return int(output) if output else 0
            except:
                return None

        # If no upstream tracking, try origin/HEAD (fallback for single-branch or no-remote repos)
        # Expected error for local-only repos: "fatal: no upstream configured"
        stderr_lower = result.stderr.lower()
        if 'no upstream configured' in stderr_lower:
            # Local-only repo (no tracking branch), try origin/HEAD as fallback
            result = subprocess.run(
                ['git', '-C', str(resolved_path), 'rev-list', '--left-only', '--count', 'HEAD...origin/HEAD'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
            )

        # Check return code; if fails, determine if it's an expected "no remote" error or a real error
        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            # Expected errors for local-only repos (no remote configured)
            expected_errors = [
                'no upstream configured',
                'ambiguous argument',
                'unknown revision',
                'not a valid object name',
                'no such ref'
            ]
            if any(err in stderr_lower for err in expected_errors):
                return 0  # No remote/tracking, treat as "all pushed"
            else:
                return None  # Real error, fail-closed

        output = result.stdout.strip()
        try:
            return int(output) if output else 0
        except:
            return None
    except Exception:
        return None


def check_untracked_files(repo_path):
    """Return list of untracked files not in .gitignore.

    Returns:
      []: No untracked files
      [list]: Untracked files found
      None: Git command failed (FAIL-CLOSED: treat as AT-RISK)
    """
    try:
        # Resolve path to normalize 8.3 short names on Windows
        resolved_path = Path(repo_path).resolve()
        result = subprocess.run(
            ['git', '-C', str(resolved_path), 'ls-files', '--others', '--exclude-standard'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
        )

        # FAIL-CLOSED: Check return code before processing output
        if result.returncode != 0:
            return None

        output = result.stdout.strip()
        if output:
            return output.split('\n')
        return []
    except Exception:
        return None


def check_repo(repo_path):
    """Check a single repo; returns a list of Finding objects.

    FAIL-CLOSED (runner incident): a repo the caller ASKED to check that does
    not exist or is not a git repo is an AT-RISK finding, never a silent skip
    — the silent-None path made the sweep report a vacuous SAFE (exit 0) when
    fixture/real repos failed to initialize.
    """
    if not repo_path.exists():
        return [Finding(repo_path, "repo path does not exist")]

    if not (repo_path / '.git').exists():
        return [Finding(repo_path, "not a git repository (.git missing)")]

    findings = []

    # Check 1: Working tree clean
    is_clean, dirty = get_git_status(repo_path)
    if is_clean is None:
        findings.append(Finding(repo_path, f"git status check failed: {dirty}"))
    elif not is_clean:
        findings.append(Finding(repo_path, f"dirty working tree: {len(dirty)} files"))

    # Check 2: Branch pushed
    ahead = get_ahead_count(repo_path)
    if ahead is None:
        findings.append(Finding(repo_path, "ahead-count check failed"))
    elif ahead > 0:
        findings.append(Finding(repo_path, f"ahead of origin: {ahead} commits unpushed"))

    # Check 3: Untracked files
    untracked = check_untracked_files(repo_path)
    if untracked is None:
        findings.append(Finding(repo_path, "untracked file check failed"))
    elif untracked:
        findings.append(Finding(repo_path, f"untracked files: {len(untracked)} items"))

    return findings


def push_repo(repo_path):
    """Push commits for a repo (return True if successful)."""
    try:
        # Resolve path to normalize 8.3 short names on Windows
        resolved_path = Path(repo_path).resolve()
        result = subprocess.run(
            ['git', '-C', str(resolved_path), 'push'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30
        )
        return result.returncode == 0
    except Exception:
        return False


def run_secret_scan(repo_path):
    """Run secret_scan.py on staged files (return True if no secrets found)."""
    try:
        script_path = Path(__file__).parent / 'secret_scan.py'
        result = subprocess.run(
            [sys.executable, str(script_path), '--staged'],
            cwd=str(repo_path),
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30
        )
        return result.returncode == 0
    except Exception:
        return False


def append_to_buildlog(buildlog_path, verdict_line, timestamp_str=None):
    """Append verdict to BUILDLOG.md via the WriteAPI facade (unified write path).

    The facade writes the file atomically (with OCC + file locking, replacing
    the old manual retry loop) and mirrors the entry as a buildlog_entry event
    in the event store, so markdown and SQLite state can never drift.

    Args:
        buildlog_path: Path to the BUILDLOG.md file. The filename MUST be
                       BUILDLOG.md — the facade owns the canonical name; any
                       other name is refused (fail-closed, never silently
                       redirected).
        verdict_line: The verdict line to append (e.g., "EOD-SWEEP: SAFE").
        timestamp_str: Optional timestamp string (format: YYYY-MM-DD HH:MM).
                      If None, timestamp is omitted from the entry.

    Raises:
        OSError: If the path is not named BUILDLOG.md or the facade write fails.
    """
    buildlog_path = Path(buildlog_path)
    if buildlog_path.name != "BUILDLOG.md":
        raise OSError(
            f"--buildlog must point at a file named BUILDLOG.md (got "
            f"{buildlog_path.name!r}); the unified write path (WriteAPI) "
            f"owns the canonical name"
        )

    # Build entry line with optional timestamp
    if timestamp_str:
        entry_line = f"### [{timestamp_str}] {verdict_line}"
    else:
        entry_line = f"### {verdict_line}"

    try:
        api = WriteAPI(buildlog_path.parent)  # creates parent dirs if missing
        api.ensure_buildlog_exists(header="# Build Log (append-only)\n")
        api.append_buildlog(entry_line, actor="eod-sweep")
    except WriteConflict as e:
        # Preserve this function's OSError contract for callers
        raise OSError(f"Failed to append to BUILDLOG via WriteAPI: {e}") from e


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--repos',
        default='',
        help="os.pathsep-separated paths (; on Windows, : on POSIX)"
    )
    parser.add_argument(
        '--readonly-repos',
        default='',
        help='Colon-separated paths that should NOT be auto-pushed'
    )
    parser.add_argument(
        '--fix-push',
        action='store_true',
        help='Auto-push unpushed commits'
    )
    parser.add_argument(
        '--buildlog',
        default=None,
        help='Path to BUILDLOG.md (default: AESOP_STATE_ROOT/BUILDLOG.md or ./state/BUILDLOG.md)'
    )
    parser.add_argument(
        '--timestamp',
        default=None,
        help='Timestamp for BUILDLOG entry (format: YYYY-MM-DD HH:MM; omit to exclude timestamp)'
    )
    args = parser.parse_args()

    # Parse repos
    # Split on os.pathsep (';' on Windows, ':' on POSIX): a ':' delimiter
    # eats Windows drive letters (the runner's vacuous-SAFE incident: tmp on
    # C:, checkout on D:, drive-relative remainder resolved to nothing and
    # the old silent-skip reported SAFE with zero repos scanned).
    repos_to_check = []
    if args.repos:
        repos_to_check = [Path(p) for p in args.repos.split(os.pathsep) if p]

    # Parse readonly repos
    readonly_repos = set()
    if args.readonly_repos:
        readonly_repos = {Path(p) for p in args.readonly_repos.split(os.pathsep) if p}

    findings = []

    # Scan all repos
    for repo_path in repos_to_check:
        repo_findings = check_repo(repo_path)
        if repo_findings is not None:
            findings.extend(repo_findings)

    # Determine verdict
    if not findings:
        verdict = "SAFE"
        verdict_line = "EOD-SWEEP: SAFE"
        exit_code = 0
    else:
        verdict = f"AT-RISK — {len(findings)} findings"
        verdict_line = f"EOD-SWEEP: AT-RISK — {len(findings)} findings"
        exit_code = 1

    # Handle --fix-push if requested and conditions are met
    if args.fix_push and findings:
        # Filter for ahead-only findings that we can push
        ahead_findings = [f for f in findings if 'unpushed' in f.msg and f.repo not in readonly_repos]
        if ahead_findings:
            for finding in ahead_findings:
                repo_path = finding.repo
                if repo_path and repo_path not in readonly_repos:
                    # Run secret scan first
                    if run_secret_scan(repo_path):
                        if push_repo(repo_path):
                            print(f"Pushed: {repo_path.name}")
                            findings.remove(finding)
                        else:
                            print(f"Push failed: {repo_path.name}")
                    else:
                        print(f"Secret scan blocked: {repo_path.name}")

            # Re-evaluate verdict
            if not findings:
                verdict = "SAFE"
                verdict_line = "EOD-SWEEP: SAFE"
                exit_code = 0
            else:
                verdict_line = f"EOD-SWEEP: AT-RISK — {len(findings)} findings"

    # Print output
    print(verdict_line)
    for finding in findings:
        print(f"  {finding}")

    # Append to BUILDLOG if path is available
    buildlog_path = None
    if args.buildlog:
        buildlog_path = Path(args.buildlog)
    else:
        # Try to derive from AESOP_STATE_ROOT or default to ./state
        state_dir = get_state_dir()
        buildlog_path = state_dir / "BUILDLOG.md"

    if buildlog_path:
        try:
            append_to_buildlog(buildlog_path, verdict_line, args.timestamp)
        except OSError as e:
            # Log error but don't fail the verdict — exit code depends on findings, not BUILDLOG
            print(f"WARNING: Failed to append to BUILDLOG: {e}", file=sys.stderr)

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
