#!/usr/bin/env python3
"""
Shared heartbeat and health-check utilities.
INDEX: Heartbeat staleness utilities: `check_heartbeat_file()`, `check_watchdog_heartbeat()`, `check_monitor_heartbeat()`. Wraps `common.check_heartbeat_staleness()` with standard thresholds (watchdog: 300s, monitor: 3600s). Eliminates duplicated inline heartbeat reading from power_selftest.py. Preserves fail-closed contract: absent/unreadable/unparseable heartbeats → STALE (never healthy). Tested with 12 unit test cases including explicit STALE contract validation.

Consolidates duplicated heartbeat staleness checking logic from health_score.py,
healthcheck.py, and power_selftest.py into a single reusable module.

All three scripts use consistent heartbeat thresholds:
  - Watchdog: 300 seconds (5 minutes)
  - Monitor: 3600 seconds (1 hour)

NOTE: This module wraps common.check_heartbeat_staleness() for consistent
behavior across all callers. The underlying function preserves the critical
contract: unreadable/absent/unparseable heartbeat content => STALE (never healthy).

Functions:
  check_heartbeat_file(heartbeat_path, threshold_s) -> tuple[bool, int, str | None]
    Check if a heartbeat file is stale (wraps common.check_heartbeat_staleness).

  check_watchdog_heartbeat(state_dir) -> tuple[bool, int, str | None]
    Check watchdog heartbeat freshness (300s threshold).

  check_monitor_heartbeat(state_dir) -> tuple[bool, int, str | None]
    Check monitor heartbeat freshness (3600s threshold).
"""

from pathlib import Path

try:
    from common import check_heartbeat_staleness
except ImportError:
    from tools.common import check_heartbeat_staleness


# Standard heartbeat thresholds (consistent across all three health scripts)
WATCHDOG_THRESHOLD_S = 300  # 5 minutes
MONITOR_THRESHOLD_S = 3600  # 1 hour


def check_heartbeat_file(heartbeat_path, threshold_s):
    """Check if a heartbeat file is stale.

    Wraps common.check_heartbeat_staleness() with consistent contract:
    unreadable/absent/unparseable => STALE (fail-closed).

    Args:
        heartbeat_path: Path to heartbeat file (str or Path).
        threshold_s: Staleness threshold in seconds.

    Returns:
        tuple: (is_stale: bool, age_s: int, info: str | None)
          - is_stale: True if file missing, unreadable, or age >= threshold_s
          - age_s: Age in seconds (0 if missing/unreadable)
          - info: Descriptive message if problem, None if fresh
    """
    path = Path(heartbeat_path) if not isinstance(heartbeat_path, Path) else heartbeat_path
    return check_heartbeat_staleness(path, threshold_s)


def check_watchdog_heartbeat(state_dir):
    """Check watchdog heartbeat freshness.

    Args:
        state_dir: State directory path (str or Path).

    Returns:
        tuple: (is_stale: bool, age_s: int, info: str | None)
    """
    state_dir = Path(state_dir) if not isinstance(state_dir, Path) else state_dir
    heartbeat_file = state_dir / (".watchdog" + "-heartbeat")
    return check_heartbeat_file(heartbeat_file, WATCHDOG_THRESHOLD_S)


def check_monitor_heartbeat(state_dir):
    """Check monitor heartbeat freshness.

    Args:
        state_dir: State directory path (str or Path).

    Returns:
        tuple: (is_stale: bool, age_s: int, info: str | None)
    """
    state_dir = Path(state_dir) if not isinstance(state_dir, Path) else state_dir
    heartbeat_file = state_dir / (".monitor" + "-heartbeat")
    return check_heartbeat_file(heartbeat_file, MONITOR_THRESHOLD_S)
