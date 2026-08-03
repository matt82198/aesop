#!/usr/bin/env python3
"""
Shared heartbeat and health-check utilities.

Consolidates duplicated heartbeat staleness checking logic from health_score.py,
healthcheck.py, and power_selftest.py into a single reusable module.

All three scripts use consistent heartbeat thresholds:
  - Watchdog: 300 seconds (5 minutes)
  - Monitor: 3600 seconds (1 hour)

NOTE: This module wraps common.check_heartbeat_staleness() for consistent
behavior across all callers. The underlying function preserves the critical
contract: unreadable/absent/unparseable heartbeat content => STALE (never healthy).

STATE API NOTE: the two heartbeat filenames below are deliberate literals, and the
two resulting stateapi_lint violations are recorded honestly in
.stateapi-baseline.json. They are NOT routed through state_store.read_api because
ReadAPI.check_heartbeat_fresh() returns only a bool, while every caller of this
module (tools/status_publish.py, tools/power_selftest.py) needs the full
(is_stale, age_s, info) triple to distinguish "missing" from "stale" from
"unreadable" in its report. Widening the facade to return the triple is the real
fix and is filed as a follow-up; until then these stay file-level reads.
Do NOT split these literals to dodge the lint -- that falsifies the ratchet.
See tests/test_health_checks.py::TestNoLintEvasion.

Functions:
  check_heartbeat_file(heartbeat_path, threshold_s) -> tuple[bool, int, str | None]
    Check if a heartbeat file is stale (wraps common.check_heartbeat_staleness).

  check_watchdog_heartbeat(state_dir) -> tuple[bool, int, str | None]
    Check watchdog heartbeat freshness (300s threshold).

  check_monitor_heartbeat(state_dir) -> tuple[bool, int, str | None]
    Check monitor heartbeat freshness (3600s threshold).
"""

import sys
from pathlib import Path

# Ensure the repo root is importable so `tools.common` resolves whether the
# caller put tools/ or the repo root on sys.path. Mirrors the same bootstrap in
# state_store/read_api.py. The previous `try: from common ... except ImportError:`
# form worked at runtime but was not statically resolvable, so
# tools/import_resolution_check.py could not verify it.
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

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
    heartbeat_file = state_dir / ".watchdog-heartbeat"
    return check_heartbeat_file(heartbeat_file, WATCHDOG_THRESHOLD_S)


def check_monitor_heartbeat(state_dir):
    """Check monitor heartbeat freshness.

    Args:
        state_dir: State directory path (str or Path).

    Returns:
        tuple: (is_stale: bool, age_s: int, info: str | None)
    """
    state_dir = Path(state_dir) if not isinstance(state_dir, Path) else state_dir
    heartbeat_file = state_dir / ".monitor-heartbeat"
    return check_heartbeat_file(heartbeat_file, MONITOR_THRESHOLD_S)
