#!/usr/bin/env python3
r"""
Publish a compact fleet-status snapshot to a phone-reachable location.

Gathers high-signal data from existing sources (fleet status, PRs, BUILDLOG, heartbeats,
pending items) into a small, phone-optimized snapshot. Publishes to a SECRET GitHub
gist (private by default) so a phone can read one always-current page without exposing
the local machine or publishing to a public repo/issue.

Usage:
    python tools/status_publish.py [--once] [--gist-id GIST] [--dry-run]

Options:
    --once              Publish once and exit (default)
    --gist-id GIST      Target secret gist ID (default from config, error if missing)
    --dry-run           Print the payload, do not publish

Exit codes:
    0 = success, no changes, or --dry-run completed
    1 = publish attempt failed (visibility check failure, redaction failure, gh error)
    2 = error (malformed config, missing state, missing gist-id, etc.)

Visibility Enforcement (FAIL-CLOSED):
    - Queries target gist visibility BEFORE publishing (gh gist view --json)
    - REFUSES to publish to PUBLIC gists or targets (privacy=false)
    - Aborts with clear error if target is public
    - Explicit gist-id required (no default to aesop repo)

Redaction (defense-in-depth):
    - Removes tokens matching: sk-*, ghp-*, pat-*
    - Removes Windows paths containing username: C:\Users\<user>\...
    - Removes POSIX home paths: /home/<user>/...
    - Removes local fleet-state references
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

try:  # dual-path import: runs both as a script and inside the tools package
    from health_checks import check_watchdog_heartbeat, check_monitor_heartbeat
except ImportError:  # pragma: no cover - package-context fallback
    from tools.health_checks import (
        check_watchdog_heartbeat,
        check_monitor_heartbeat,
    )

# State root (default: ./state or AESOP_STATE_ROOT env var)
AESOP_STATE_ROOT = Path(
    os.environ.get('AESOP_STATE_ROOT', './state')
).expanduser()

# Last-publish marker file
LAST_PUBLISH_FILE = AESOP_STATE_ROOT / '.status-publish-last'

# Where the fleet daemons write their heartbeats. Deliberately NOT tied to any
# operator's private directory layout -- this is a public project, so it defaults to
# the aesop state root and a deployment points AESOP_FLEET_STATE_DIR wherever its own
# daemons write. The staleness logic itself lives in health_checks, not here.
FLEET_STATE_DIR = Path(
    os.environ.get('AESOP_FLEET_STATE_DIR', str(AESOP_STATE_ROOT))
).expanduser()

# Heartbeat staleness thresholds derived from daemon cadences with headroom.
# These MUST match the actual scheduled task intervals to avoid false alarms.
# Watchdog: AesopWatchdogDaemon runs every 5 minutes (300s).
#   Threshold 900s = 3x cadence (15 min headroom). Beats STALE only if missing >15min.
# Monitor: AesopRefinementMonitor runs every 60 minutes (3600s).
#   Threshold 5400s = 1.5x cadence (90 min headroom). Beats STALE only if missing >90min.
# Config-driven so a future cadence change updates one place instead of chasing false alarms.
HEARTBEAT_THRESHOLDS = {
    'watchdog': 900,   # 3x 5-min cadence
    'monitor': 5400,   # 1.5x 60-min cadence
}

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


def check_gist_visibility(gist_id):
    """
    Query gist visibility via gh. FAIL-CLOSED: refuse public gists.

    Returns: True if gist is private (privacy=true), False if public
    Raises: RuntimeError if gist is public or query fails
    """
    try:
        stdout, rc = run_command(
            ['gh', 'gist', 'view', gist_id, '--json', 'isPublic'],
            timeout=10
        )
        if rc != 0:
            raise RuntimeError(f"gh gist view failed (rc {rc}): gist may not exist or not be accessible")

        data = json.loads(stdout) if stdout.strip() else {}
        is_public = data.get('isPublic', True)  # Default to public if uncertain

        if is_public:
            raise RuntimeError(
                f"VISIBILITY CHECK FAILED: gist {gist_id} is PUBLIC. "
                f"This tool refuses to publish fleet status to public targets. "
                f"Create a secret gist instead: gh gist create --secret <file>"
            )

        return True
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Gist visibility check error: {e}")


def gather_agent_status():
    """Gather live agents info. Returns compact string."""
    try:
        stdout, rc = run_command(['node', 'tools/status.js'], timeout=5)
        if rc == 0:
            lines = stdout.strip().split('\n')
            return "status reported" if lines else "unknown"
    except Exception:
        pass
    return "unavailable"


def gather_pr_status():
    """Gather open PRs and CI status. Returns compact string with RED count."""
    try:
        stdout, rc = run_command(
            ['gh', 'pr', 'list', '--state', 'open', '--json', 'number,title,statusCheckRollup'],
            timeout=10
        )
        if rc != 0:
            return "gh unavailable"

        prs = json.loads(stdout) if stdout.strip() else []
        if not prs:
            return "0 open PRs"

        red_count = sum(
            1 for pr in prs
            if any(c.get('conclusion') == 'FAILURE' for c in pr.get('statusCheckRollup', []))
        )
        summary = f"{len(prs)} open"
        if red_count > 0:
            summary += f" · {red_count} RED"
        return summary
    except Exception as e:
        return "PR status unavailable"


def gather_heartbeat_status():
    """
    Check heartbeat freshness using MTIME (not content). Returns compact status.

    Three distinct states per source:
    - FRESH: age < threshold (daemon is running normally)
    - STALE: age >= threshold (daemon has missed scheduled runs)
    - ERROR: file missing or unreadable (unable to monitor)

    Thresholds are per-source and derived from daemon cadences:
    - watchdog (900s = 3x 5-min cadence): FRESH < 15min, STALE >= 15min
    - monitor (5400s = 1.5x 60-min cadence): FRESH < 90min, STALE >= 90min
    """
    now = datetime.now(timezone.utc)
    statuses = []

    # Heartbeat freshness comes from health_checks, which fails closed on a missing
    # or unreadable file rather than reporting a healthy fleet it could not verify.
    for label, check in (
        ('watchdog', check_watchdog_heartbeat),
        ('monitor', check_monitor_heartbeat),
    ):
        try:
            is_stale, age_s, info = check(FLEET_STATE_DIR)
        except Exception as e:
            statuses.append("%s: ERROR (%s)" % (label, type(e).__name__))
            continue
        if not is_stale:
            statuses.append("%s: %ds" % (label, age_s))
        else:
            statuses.append("%s: STALE (%s)" % (label, info or ("%ds" % age_s)))


    return " · ".join(statuses)


def gather_buildlog_summary():
    """Extract last few lines from BUILDLOG.md. Returns compact summary."""
    buildlog = FLEET_STATE_DIR / 'BUILDLOG.md'
    if not buildlog.exists():
        return "BUILDLOG not found"

    try:
        with open(buildlog, encoding='utf-8') as f:
            lines = f.readlines()
        relevant = [l.strip() for l in lines if l.strip() and not l.startswith('#')][-3:]
        return '\n'.join(relevant) if relevant else "BUILDLOG empty"
    except Exception as e:
        return f"BUILDLOG read error: {e}"


def gather_pending_items():
    """Get unprocessed inbox items. Returns compact count or 'none'."""
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

    Raises RuntimeError if redaction would remove more than 10% of content (fail-closed).
    """
    original_len = len(text)
    redacted = text

    for pattern, replacement in REDACTION_PATTERNS:
        redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)

    removed_chars = original_len - len(redacted)
    if removed_chars > original_len * 0.1:
        raise RuntimeError(
            f"Redaction would remove {removed_chars} chars ({100*removed_chars/original_len:.1f}%): "
            f"likely overly aggressive. Payload suspicious; aborting publish."
        )

    return redacted


def build_payload(config):
    """Build the fleet status snapshot payload."""
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
        "*Published by status_publish.py (secret gist)*",
    ]

    return '\n'.join(parts)


def publish_to_gist(payload, gist_id, dry_run=False):
    """
    Publish payload to a secret gist via gh.

    FAIL-CLOSED: queries visibility before publishing.
    """
    # Step 1: Verify visibility (FAIL-CLOSED)
    if not dry_run:
        try:
            check_gist_visibility(gist_id)
        except RuntimeError as e:
            raise RuntimeError(f"Visibility check failed: {e}")

    # Step 2: Redact payload
    try:
        redacted = redact_payload(payload)
    except RuntimeError as e:
        raise RuntimeError(f"Redaction failed: {e}")

    if dry_run:
        print(redacted)
        return True

    # Step 3: Check idempotence
    payload_hash = hashlib.sha256(redacted.encode()).hexdigest()[:8]
    if LAST_PUBLISH_FILE.exists():
        try:
            with open(LAST_PUBLISH_FILE, encoding='utf-8') as f:
                last_hash = f.read().strip()
            if last_hash == payload_hash:
                print("No changes since last publish; skipping update.")
                return True
        except Exception:
            pass

    # Step 4: Publish via gh gist edit
    try:
        # Write payload to temp file for gh to read
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write(redacted)
            temp_file = f.name

        try:
            stdout, rc = run_command(
                ['gh', 'gist', 'edit', gist_id, temp_file],
                timeout=15
            )
            if rc != 0:
                raise RuntimeError(f"gh gist edit failed (rc {rc})")
        finally:
            os.unlink(temp_file)
    except Exception as e:
        raise RuntimeError(f"Failed to update gist: {e}")

    # Step 5: Record publish
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
        '--gist-id', type=str, default=None,
        help='Target secret gist ID (REQUIRED; default from config if present)'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Print payload, do not publish'
    )

    args = parser.parse_args()

    config = load_config()
    gist_id = args.gist_id or config.get('status_publish_gist_id')

    if not gist_id and not args.dry_run:
        print("ERROR: --gist-id required (or set status_publish_gist_id in aesop.config.json)", file=sys.stderr)
        sys.exit(2)

    try:
        payload = build_payload(config)
        publish_to_gist(payload, gist_id or 'none', dry_run=args.dry_run)
        sys.exit(0)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
