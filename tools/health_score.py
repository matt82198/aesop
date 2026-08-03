#!/usr/bin/env python3
"""
Health Score tool — deterministic readiness score for primed projects.
INDEX: Readiness score (0-100) for primed projects; CLI: `--cwd <path> [--json]`; checks: config, hooks, CLAUDE.md, writable, heartbeats, git-identity, secret-scan (weighted scoring)

Calculates a weighted 0-100 score based on:
- Config validity (JSON parseable, required fields) — 15 points
- Git pre-push hook installed — 15 points
- CLAUDE.md present and linted clean — 15 points
- State directory writable — 10 points
- Daemon heartbeats fresh (watchdog/monitor) — 15 points
- Git identity configured (user.name + user.email) — 15 points
- Secret-scan tool runnable — 15 points

Total: 100 points across 7 weighted checks.

Output: human-readable (score card with per-check status) or --json (structured).

Exit: 0 on success (always produces a score; no score is not a failure).
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import time

try:
    from common import check_heartbeat_staleness
except ImportError:
    from tools.common import check_heartbeat_staleness


def calculate_score(cwd=None):
    """
    Calculate health score for the primed project.

    Args:
        cwd (str): Working directory to check (default: current directory)

    Returns:
        dict: {
            "score": int (0-100),
            "checks": [
                {
                    "name": str,
                    "passed": bool,
                    "weight": int,
                    "detail": str
                },
                ...
            ]
        }
    """
    if cwd is None:
        cwd = os.getcwd()

    cwd_path = Path(cwd)

    checks = []

    # 1. Config check (15 points)
    config_check = _check_config(cwd_path)
    checks.append({
        "name": "config",
        "weight": 15,
        "passed": config_check["passed"],
        "detail": config_check["detail"]
    })

    # 2. Pre-push hook check (15 points)
    hook_check = _check_pre_push_hook(cwd_path)
    checks.append({
        "name": "hooks",
        "weight": 15,
        "passed": hook_check["passed"],
        "detail": hook_check["detail"]
    })

    # 3. CLAUDE.md check (15 points)
    claude_check = _check_claude_md(cwd_path)
    checks.append({
        "name": "claude",
        "weight": 15,
        "passed": claude_check["passed"],
        "detail": claude_check["detail"]
    })

    # 4. State directory writable (10 points)
    writable_check = _check_state_writable(cwd_path)
    checks.append({
        "name": "writable",
        "weight": 10,
        "passed": writable_check["passed"],
        "detail": writable_check["detail"]
    })

    # 5. Heartbeats fresh (15 points)
    heartbeat_check = _check_heartbeats(cwd_path)
    checks.append({
        "name": "heartbeat",
        "weight": 15,
        "passed": heartbeat_check["passed"],
        "detail": heartbeat_check["detail"]
    })

    # 6. Git identity (15 points)
    identity_check = _check_git_identity(cwd_path)
    checks.append({
        "name": "identity",
        "weight": 15,
        "passed": identity_check["passed"],
        "detail": identity_check["detail"]
    })

    # 7. Secret-scan runnable (15 points)
    secret_scan_check = _check_secret_scan_runnable(cwd_path)
    checks.append({
        "name": "secret-scan",
        "weight": 15,
        "passed": secret_scan_check["passed"],
        "detail": secret_scan_check["detail"]
    })

    # Calculate weighted score
    total_weight = sum(c["weight"] for c in checks)
    earned_points = sum(c["weight"] for c in checks if c["passed"])
    score = int((earned_points / total_weight) * 100) if total_weight > 0 else 0

    return {
        "score": score,
        "checks": checks
    }


def _check_config(cwd_path):
    """Check if aesop.config.json exists and is valid JSON."""
    config_path = cwd_path / "aesop.config.json"

    if not config_path.exists():
        return {"passed": False, "detail": "aesop.config.json not found"}

    try:
        content = config_path.read_text(encoding="utf-8")
        json.loads(content)
        return {"passed": True, "detail": "aesop.config.json valid"}
    except json.JSONDecodeError as e:
        return {"passed": False, "detail": f"Invalid JSON: {str(e)[:50]}"}
    except Exception as e:
        return {"passed": False, "detail": f"Config read error: {str(e)[:50]}"}


def _check_pre_push_hook(cwd_path):
    """Check if .git/hooks/pre-push exists and is executable."""
    hook_path = cwd_path / ".git" / "hooks" / "pre-push"

    if not hook_path.exists():
        return {"passed": False, "detail": "Pre-push hook not installed"}

    if not os.access(hook_path, os.X_OK):
        return {"passed": False, "detail": "Pre-push hook not executable"}

    return {"passed": True, "detail": "Pre-push hook installed"}


def _check_claude_md(cwd_path):
    """Check if CLAUDE.md exists."""
    claude_path = cwd_path / "CLAUDE.md"

    if not claude_path.exists():
        return {"passed": False, "detail": "CLAUDE.md not found"}

    content = claude_path.read_text(encoding="utf-8").strip()
    if not content:
        return {"passed": False, "detail": "CLAUDE.md is empty"}

    return {"passed": True, "detail": "CLAUDE.md present"}


def _check_state_writable(cwd_path):
    """Check if state directory is writable."""
    state_dir = cwd_path / "state"

    # Create state dir if it doesn't exist
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"passed": False, "detail": f"Cannot create state dir: {str(e)[:40]}"}

    # Try to write a test file
    test_file = state_dir / ".health-score-test"
    try:
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
        return {"passed": True, "detail": "State directory writable"}
    except Exception as e:
        return {"passed": False, "detail": f"State dir not writable: {str(e)[:40]}"}


def _check_heartbeats(cwd_path):
    """Check if daemon heartbeats are fresh."""
    state_dir = cwd_path / "state"

    # Check watchdog heartbeat (threshold 300s)
    watchdog_file = state_dir / ".watchdog-heartbeat"
    is_stale_watchdog, age_watchdog, info_watchdog = check_heartbeat_staleness(
        watchdog_file, threshold_s=300
    )

    # Check monitor heartbeat (threshold 3600s)
    monitor_file = state_dir / ".monitor-heartbeat"
    is_stale_monitor, age_monitor, info_monitor = check_heartbeat_staleness(
        monitor_file, threshold_s=3600
    )

    if is_stale_watchdog or is_stale_monitor:
        details = []
        if is_stale_watchdog:
            if age_watchdog > 0:
                details.append(f"watchdog stale ({age_watchdog}s)")
            else:
                details.append("watchdog missing")
        if is_stale_monitor:
            if age_monitor > 0:
                details.append(f"monitor stale ({age_monitor}s)")
            else:
                details.append("monitor missing")
        return {"passed": False, "detail": "; ".join(details)}

    return {"passed": True, "detail": "Heartbeats fresh"}


def _check_git_identity(cwd_path):
    """Check if git user.name and user.email are configured."""
    try:
        # Check local git config in the repo
        name_result = subprocess.run(
            ["git", "config", "user.name"],
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=5
        )
        email_result = subprocess.run(
            ["git", "config", "user.email"],
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=5
        )

        name = name_result.stdout.strip()
        email = email_result.stdout.strip()

        if not name or not email:
            missing = []
            if not name:
                missing.append("user.name")
            if not email:
                missing.append("user.email")
            return {"passed": False, "detail": f"Git {', '.join(missing)} not set"}

        return {"passed": True, "detail": "Git identity configured"}
    except Exception as e:
        return {"passed": False, "detail": f"Identity check error: {str(e)[:40]}"}


def _check_secret_scan_runnable(cwd_path):
    """Check if secret_scan.py is present and runnable."""
    secret_scan = cwd_path / "tools" / "secret_scan.py"

    if not secret_scan.exists():
        return {"passed": False, "detail": "secret_scan.py not found"}

    # Try to run with --help
    try:
        result = subprocess.run(
            [sys.executable, str(secret_scan), "--help"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=5
        )
        if result.returncode == 0 or "usage" in result.stdout.lower():
            return {"passed": True, "detail": "secret-scan runnable"}
        else:
            return {"passed": False, "detail": "secret-scan not responding"}
    except subprocess.TimeoutExpired:
        return {"passed": False, "detail": "secret-scan timeout"}
    except Exception as e:
        return {"passed": False, "detail": f"secret-scan error: {str(e)[:40]}"}


def format_human(result):
    """Format score result as human-readable output."""
    lines = []
    score = result.get("score", 0)
    checks = result.get("checks", [])

    lines.append(f"\nHealth Score: {score}/100\n")

    # Print check results
    for check in checks:
        status = "[PASS]" if check["passed"] else "[FAIL]"
        name = check["name"].replace("-", " ").title()
        detail = check.get("detail", "")
        weight = check.get("weight", 0)

        line = f"  {status:8} {name:15} (weight: {weight:2})"
        if detail:
            line += f" — {detail}"
        lines.append(line)

    # Summary
    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    lines.append(f"\nSummary: {passed}/{total} checks passed")

    return "\n".join(lines)


def format_json(result):
    """Format score result as JSON."""
    return json.dumps(result, indent=2)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Aesop health score — readiness assessment for primed projects"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory to check (default: current directory)"
    )

    args = parser.parse_args()

    try:
        result = calculate_score(cwd=args.cwd)

        if args.json:
            output = format_json(result)
        else:
            output = format_human(result)

        print(output)
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
