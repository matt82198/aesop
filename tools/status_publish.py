#!/usr/bin/env python3
r"""
Publish a compact fleet-status snapshot to a phone-reachable location.

Gathers high-signal data from existing sources (fleet status, PRs, BUILDLOG, heartbeats,
pending items) into a small, phone-optimized snapshot. Publishes to a private GitHub
issue via gh CLI so a phone can read one always-current page without exposing the local
machine.

Usage:
    python tools/status_publish.py [--once] [--issue N] [--dry-run] [--comment]

Options:
    --once              Publish once and exit (default)
    --issue N           Target GitHub issue N (default from aesop.config.json or 1)
    --dry-run           Print the payload, do not publish
    --comment           Append as comment instead of updating body

Exit codes:
    0 = success, no changes, or --dry-run completed
    1 = publish attempt failed (redaction failure, gh error, etc.)
    2 = error (malformed config, missing state, etc.)

Redaction:
    - Removes tokens matching: sk-*, ghp-*, pat-*
    - Removes Windows paths containing username: C:\Users\<user>\...
    - Removes POSIX home paths: /home/<user>/...
    - Removes local conductor3 reference
    - Redaction failure = exit 1, never publish

Idempotence:
    - Compares new payload to last-published version
    - Skips update if unchanged (no noise on repeated runs)
    - Last-publish hash stored in state/.status-publish-last
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# State root (default: ./state)
AESOP_STATE_ROOT = Path(
    os.environ.get('AESOP_STATE_ROOT', './state')
).expanduser()

# Last-publish marker file
LAST_PUBLISH_FILE = AESOP_STATE_ROOT / '.status-publish-last'

# Redaction patterns (token-shaped and path-shaped)
REDACTION_PATTERNS = [
    (r'\bsk-[A-Za-z0-9_-]{20,}\b', '[REDACTED_API_KEY]'),
    (r'\bghp-[A-Za-z0-9_-]{36,}\b', '[REDACTED_GH_PAT]'),
    (r'\bpat-[A-Za-z0-9_-]{30,}\b', '[REDACTED_PAT]'),
    (r'C:\\Users\\[A-Za-z0-9_-]+', '[REDACTED_HOME]'),
    (r'/home/[A-Za-z0-9_-]+', '[REDACTED_HOME]'),
    (r'\bconductor3\b', '[REDACTED_STATE]'),
]


def load_config():
    """Load aesop.config.json; return dict or empty dict on error."""
    try:
        config_path = Path('aesop.config.json')
        if config_path.exists():
            with open(config_path, encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def run_command(cmd, timeout=10):
    """Run subprocess command with explicit encoding. Return (stdout, returncode) or raise."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=timeout,
            shell=False
        )
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Command timeout after {timeout}s")
    except FileNotFoundError as e:
        raise RuntimeError(f"Command not found: {cmd[0]}")


def gather_agent_status():
    """
    Gather live agents info from tools/status.js or worktree agents.

    Returns compact string (e.g. "3 active · 0 stalled" or "unavailable").
    """
    try:
        # Try to use status.js if available
        stdout, rc = run_command(['node', 'tools/status.js'], timeout=5)
        if rc == 0:
            # Parse output for agent counts
            lines = stdout.strip().split('\n')
            # Look for agent-related lines (implementation depends on status.js output)
            return "status reported" if lines else "unknown"
    except Exception:
        pass
    return "unavailable"


def gather_pr_status():
    """
    Gather open PRs and CI status from 'gh pr list'.

    Returns compact string with PR count and any RED runs.
    """
    try:
        # Get open PRs: number, title, state, and if CI is red
        stdout, rc = run_command(
            ['gh', 'pr', 'list', '--state', 'open', '--json', 'number,title,statusCheckRollup'],
            timeout=10
        )
        if rc != 0:
            return "gh unavailable"

        prs = json.loads(stdout) if stdout.strip() else []
        if not prs:
            return "0 open PRs"

        # Count RED vs PASS
        red_count = 0
        pass_count = 0
        for pr in prs:
            rollup = pr.get('statusCheckRollup', [])
            if any(c.get('conclusion') == 'FAILURE' for c in rollup):
                red_count += 1
            else:
                pass_count += 1

        summary = f"{len(prs)} open"
        if red_count > 0:
            summary += f" · {red_count} RED"
        return summary
    except Exception as e:
        return "PR status unavailable"


def gather_heartbeat_status():
    """
    Check heartbeat freshness (MTIME, not content).

    Returns compact status string.
    """
    watchdog_heartbeat = AESOP_STATE_ROOT / '.watchdog-heartbeat'
    monitor_heartbeat = AESOP_STATE_ROOT / '.monitor-heartbeat'

    now = datetime.now(timezone.utc)
    statuses = []

    # Watchdog
    try:
        mtime = watchdog_heartbeat.stat().st_mtime
        ts = datetime.fromtimestamp(mtime, tz=timezone.utc)
        age = now - ts
        if age < timedelta(seconds=300):
            statuses.append(f"watchdog: {age.seconds}s")
        else:
            statuses.append(f"watchdog: STALE ({age.seconds}s)")
    except FileNotFoundError:
        statuses.append("watchdog: missing")

    # Monitor
    try:
        mtime = monitor_heartbeat.stat().st_mtime
        ts = datetime.fromtimestamp(mtime, tz=timezone.utc)
        age = now - ts
        if age < timedelta(seconds=300):
            statuses.append(f"monitor: {age.seconds}s")
        else:
            statuses.append(f"monitor: STALE ({age.seconds}s)")
    except FileNotFoundError:
        statuses.append("monitor: missing")

    return " · ".join(statuses)


def gather_buildlog_summary():
    """
    Extract last few lines from BUILDLOG.md or BUILDLOG.archive.

    Returns compact summary (2-3 lines).
    """
    buildlog = AESOP_STATE_ROOT.parent / 'BUILDLOG.md'  # conductor3/BUILDLOG.md
    if not buildlog.exists():
        return "BUILDLOG not found"

    try:
        with open(buildlog, encoding='utf-8') as f:
            lines = f.readlines()

        # Return last 3 non-empty lines, reversed (most recent last)
        relevant = [l.strip() for l in lines if l.strip() and not l.startswith('#')][-3:]
        return '\n'.join(relevant) if relevant else "BUILDLOG empty"
    except Exception as e:
        return f"BUILDLOG read error: {e}"


def gather_pending_items():
    """
    Get unprocessed inbox items from 'python tools/inbox_drain.py pending'.

    Returns compact list or "none".
    """
    try:
        stdout, rc = run_command(
            ['python', 'tools/inbox_drain.py', 'pending'],
            timeout=5
        )
        if rc != 0 or 'NO PENDING' in stdout:
            return "none"
        items = stdout.strip().split('\n')
        return f"{len(items)} pending"
    except Exception:
        return "unavailable"


def redact_payload(text):
    """
    Apply redaction patterns to remove secrets and paths.

    Args:
        text: String to redact

    Returns:
        Redacted string

    Raises:
        RuntimeError if redaction would remove more than 10% of content (suspicious)
    """
    original_len = len(text)
    redacted = text

    for pattern, replacement in REDACTION_PATTERNS:
        redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)

    removed_chars = original_len - len(redacted)
    if removed_chars > original_len * 0.1:  # More than 10% removed
        raise RuntimeError(
            f"Redaction would remove {removed_chars} chars ({100*removed_chars/original_len:.1f}%): "
            f"likely overly aggressive. Payload suspicious; aborting publish."
        )

    return redacted


def build_payload(config):
    """Build the fleet status snapshot payload."""
    issue_num = config.get('status_publish_issue', 1)

    parts = [
        f"# Fleet Status – {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Live Status",
        f"- Agents: {gather_agent_status()}",
        f"- PRs: {gather_pr_status()}",
        f"- Heartbeats: {gather_heartbeat_status()}",
        "",
        "## Recent Activity",
        "```",
        gather_buildlog_summary(),
        "```",
        "",
        f"## Pending Items: {gather_pending_items()}",
        "",
        "*Published by status_publish.py*",
    ]

    return '\n'.join(parts)


def publish_to_github(payload, issue_num, as_comment=False, dry_run=False):
    """
    Publish payload to GitHub issue via gh.

    Args:
        payload: The markdown snapshot
        issue_num: Issue number to publish to
        as_comment: If True, append as comment; else update body
        dry_run: If True, print but don't publish

    Returns:
        True if published (or dry-run), False otherwise

    Raises:
        RuntimeError on gh error
    """
    try:
        redacted = redact_payload(payload)
    except RuntimeError as e:
        raise RuntimeError(f"Redaction failed: {e}")

    if dry_run:
        print(redacted)
        return True

    # Compute hash for idempotence check
    payload_hash = hashlib.sha256(redacted.encode()).hexdigest()[:8]

    # Check if unchanged
    if LAST_PUBLISH_FILE.exists():
        try:
            with open(LAST_PUBLISH_FILE, encoding='utf-8') as f:
                last_hash = f.read().strip()
            if last_hash == payload_hash:
                print("No changes since last publish; skipping update.")
                return True
        except Exception:
            pass

    # Publish
    if as_comment:
        try:
            stdout, rc = run_command(
                ['gh', 'issue', 'comment', str(issue_num), '--body', redacted],
                timeout=15
            )
            if rc != 0:
                raise RuntimeError(f"gh issue comment failed: rc {rc}")
        except Exception as e:
            raise RuntimeError(f"Failed to comment: {e}")
    else:
        try:
            stdout, rc = run_command(
                ['gh', 'issue', 'edit', str(issue_num), '--body', redacted],
                timeout=15
            )
            if rc != 0:
                raise RuntimeError(f"gh issue edit failed: rc {rc}")
        except Exception as e:
            raise RuntimeError(f"Failed to update issue: {e}")

    # Record publish
    try:
        LAST_PUBLISH_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LAST_PUBLISH_FILE, 'w', encoding='utf-8') as f:
            f.write(payload_hash)
    except Exception as e:
        print(f"Warning: could not record publish: {e}", file=sys.stderr)

    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--once', action='store_true', default=True,
        help='Publish once and exit (default)'
    )
    parser.add_argument(
        '--issue', type=int, default=None,
        help='Target GitHub issue number (default from config or 1)'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Print payload, do not publish'
    )
    parser.add_argument(
        '--comment', action='store_true',
        help='Append as comment instead of updating body'
    )

    args = parser.parse_args()

    config = load_config()
    issue_num = args.issue or config.get('status_publish_issue', 1)

    try:
        payload = build_payload(config)
        publish_to_github(payload, issue_num, as_comment=args.comment, dry_run=args.dry_run)
        sys.exit(0)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
