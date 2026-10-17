#!/usr/bin/env python3
"""Single resolver for the live event-store root (state_root).
INDEX: Shared state_root resolver: --state-dir > AESOP_STATE_ROOT > aesop.config.json state_root > fail closed (exit 2); --config-only prints the configured value for the watchdog

Precedence (first non-empty wins):
  1. explicit --state-dir argument
  2. AESOP_STATE_ROOT environment variable
  3. "state_root" key in aesop.config.json (absolute path, ~ allowed)
  4. FAIL CLOSED: StateRootError naming the missing key. There is deliberately NO
     silent default to <repo>/state, because that is the stale store that the
     watchdog no longer writes to.

The config file is read from AESOP_ROOT when set, else from this checkout's root.

CLI:
  python tools/state_root.py [--state-dir DIR] [--config-dir DIR]
      print the resolved root, exit 0; on failure one stderr line, exit 2
  python tools/state_root.py --config-only [--config-dir DIR]
      print the configured state_root (or nothing when unset), exit 0;
      exit 2 only when aesop.config.json exists but is unreadable

Migration (2026-10-17, decision b): this module is the STRICT resolver. common.get_state_dir()
is LEGACY and keeps its cwd fallback, unchanged. Callers migrate one at a time, only after the
primary aesop.config.json state_root is absolute and fleet processes export AESOP_STATE_ROOT.
Migration surface (all still on get_state_dir): tools/heartbeat.py, tools/tracker_guard.py,
tools/halt.py, tools/merge_queue.py, orchestrator_status, healthcheck, toolchain_health,
otel_sink, buildlog, eod_sweep, fleet_ledger, wave_scorecard, merge_telemetry,
tracker_autoclose, driver/wave_scheduler.py.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

STATE_ROOT_ENV = "AESOP_STATE_ROOT"
CONFIG_KEY = "state_root"
CONFIG_FILENAME = "aesop.config.json"


class StateRootError(Exception):
    """Raised when no state root can be resolved or the config is unusable."""


def config_dir(override: Optional[str] = None) -> Path:
    """Directory holding aesop.config.json: explicit override, AESOP_ROOT, else this checkout."""
    if override:
        return Path(override)
    env_root = os.environ.get("AESOP_ROOT")
    if env_root:
        return Path(env_root)
    return REPO_ROOT


def config_path(override: Optional[str] = None) -> Path:
    return config_dir(override) / CONFIG_FILENAME


def configured_state_root(override: Optional[str] = None) -> Optional[Path]:
    """Return the state_root from aesop.config.json, or None when the key is not set.

    Raises StateRootError when the file exists but is unreadable JSON, when the value
    is not a string, or when the value is not an absolute path after ~ expansion.
    A missing config file is not an error here: it simply configures nothing.
    """
    path = config_path(override)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StateRootError(f"aesop.config.json unreadable at {path}: {exc}") from exc
    if not isinstance(data, dict) or CONFIG_KEY not in data:
        return None
    value = data[CONFIG_KEY]
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise StateRootError(f"{CONFIG_KEY} in {path} must be a string path")
    resolved = Path(value.strip()).expanduser()
    if not resolved.is_absolute():
        raise StateRootError(f"{CONFIG_KEY} in {path} must be an absolute path (got {value!r})")
    return resolved


def resolve_state_root(state_dir: Optional[str] = None, config_override: Optional[str] = None) -> Path:
    """Resolve the live store root or raise StateRootError (fail closed)."""
    if state_dir:
        return Path(state_dir).expanduser()
    env_value = os.environ.get(STATE_ROOT_ENV, "")
    if env_value.strip():
        return Path(env_value.strip()).expanduser()
    configured = configured_state_root(config_override)
    if configured is not None:
        return configured
    raise StateRootError(
        f"no state root configured: set {CONFIG_KEY} in {config_path(config_override)} "
        f"(or {STATE_ROOT_ENV}, or pass --state-dir)"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Resolve the live event-store root (state_root).")
    parser.add_argument("--state-dir", default=None, help="explicit store directory (highest precedence)")
    parser.add_argument("--config-dir", default=None, help="directory holding aesop.config.json (default: AESOP_ROOT or this checkout)")
    parser.add_argument("--config-only", action="store_true", help="print only the configured state_root, empty when unset")
    args = parser.parse_args(argv)
    try:
        if args.config_only:
            configured = configured_state_root(args.config_dir)
            if configured is not None:
                sys.stdout.write(str(configured) + "\n")
            return 0
        sys.stdout.write(str(resolve_state_root(args.state_dir, args.config_dir)) + "\n")
        return 0
    except StateRootError as exc:
        print(f"state_root: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
