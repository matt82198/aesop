#!/usr/bin/env python3
"""
Common utilities shared across tools.

Functions (state layer):
  get_state_dir() -> Path
    Resolve state directory from AESOP_STATE_ROOT env var or default to ./state

  get_state_db_path() -> Path
    Return the canonical SQLite DB path for the event store.

  check_heartbeat_staleness(hb_file, threshold_s) -> (is_stale, age_s, info)
    Check if a heartbeat file is stale and return staleness, age, and descriptive info

Functions (CLI layer — delegate to tools/cli.py):
  run_subprocess(cmd, timeout=30, cwd=None) -> (rc, stdout, stderr)
    Execute subprocess with fail-closed error handling.

  resolve_repo_root(args=None, env_key='AESOP_STATE_ROOT') -> Path
    Resolve repo root from args.root / args.repo / env var / cwd.

  mask_secrets(text) -> str
    Replace known secret patterns with MASKED-<TYPE>.

  deterministic_json_dumps(obj, pretty=True) -> str
    JSON output with sorted keys for hermetic/reproducible output.

  exit_code(findings=None, error=None) -> int
    Return deterministic exit code (0/1/2).

Constants:
  STATE_DB_FILENAME: The canonical filename for the event-sourced state DB.
    Multi-instance coordination requires all instances to point to the same file.
"""

import os
import time
from pathlib import Path

# Canonical filename for the event-sourced state database.
# Multi-instance requires all instances (including reconcile.py, ui/collectors.py, etc.)
# to point at the SAME shared file. Previously inconsistent (tracker_events.db vs events.db).
STATE_DB_FILENAME = "tracker_events.db"


def get_state_dir():
    """Resolve state directory from env var or current working directory.

    Returns:
        Path: Directory path for state files. Either from AESOP_STATE_ROOT env var
              or defaults to ./state relative to cwd.
    """
    if os.environ.get("AESOP_STATE_ROOT"):
        return Path(os.environ["AESOP_STATE_ROOT"])
    # Default to ./state (relative to cwd)
    return Path.cwd() / "state"


def get_state_db_path():
    """Return the canonical SQLite DB path for the event store.

    Multi-instance coordination requires all orchestrators to point at the
    SAME shared database file. This function centralizes the DB path resolution.

    Returns:
        Path: The canonical path to the state database (state/tracker_events.db).
    """
    return get_state_dir() / STATE_DB_FILENAME


def check_heartbeat_staleness(hb_file, threshold_s):
    """Check if a heartbeat file is stale.

    Args:
        hb_file: Path to heartbeat file (contains epoch timestamp as first line)
        threshold_s: Staleness threshold in seconds; age >= threshold is stale

    Returns:
        Tuple of (is_stale, age_s, info):
          is_stale (bool): True if file missing, unreadable, or age >= threshold_s
          age_s (int): Age in seconds (0 if file missing/unreadable)
          info (str or None): Descriptive message if stale/missing, None if fresh
    """
    try:
        if not hb_file.exists():
            return True, 0, "Heartbeat file missing"
    except OSError:
        # Parent dir unreadable (permissions) — cannot verify, report stale
        # (fail-closed, per the documented contract: unreadable => stale)
        return True, 0, "Heartbeat file unreadable"

    try:
        content = hb_file.read_text(encoding="utf-8").strip()
        if not content:
            return True, 0, "Heartbeat file empty"

        timestamp = int(content)
    except (ValueError, IOError):
        return True, 0, "Heartbeat file unreadable"

    age_seconds = int(time.time()) - timestamp

    # Check for future-dated timestamp (clock skew beyond tolerance)
    # More than 120s in the future is treated as stale, not clamped-to-fresh
    if age_seconds < -120:
        return True, 0, "Heartbeat timestamp in future (clock skew)"

    # Clamp small negative ages to 0 (normal clock skew recovery)
    age_seconds = max(0, age_seconds)

    if age_seconds >= threshold_s:
        return True, age_seconds, f"Heartbeat stale ({age_seconds}s >= {threshold_s}s)"

    return False, age_seconds, None


# ============================================================================
# CLI layer — Delegation to tools/cli.py (fail-open compatibility)
# ============================================================================

def run_subprocess(cmd, timeout=30, cwd=None):
    """
    Execute a subprocess with explicit timeout and Windows/Linux compatibility.

    Delegates to tools/cli.run_subprocess() for centralized subprocess handling.

    Args:
        cmd: Command as list (no shell=True; safe across platforms)
        timeout: Timeout in seconds (default 30)
        cwd: Working directory (default None = inherit from parent)

    Returns:
        Tuple of (returncode, stdout, stderr) as strings

    Raises:
        Exception: On timeout, file not found, or other OS errors
    """
    try:
        from tools import cli
        return cli.run_subprocess(cmd, timeout=timeout, cwd=cwd)
    except ImportError:
        # Fallback: raise ImportError if cli module not available
        raise ImportError("tools.cli module not found; install it or use cli.run_subprocess() directly")


def resolve_repo_root(args=None, env_key="AESOP_STATE_ROOT"):
    """
    Resolve repository/state root from multiple sources.

    Delegates to tools/cli.resolve_repo_root() for centralized root discovery.

    Args:
        args: argparse.Namespace with optional .root or .repo attribute
        env_key: Environment variable name for state root (default AESOP_STATE_ROOT)

    Returns:
        Resolved Path (always absolute, normalized)
    """
    try:
        from tools import cli
        return cli.resolve_repo_root(args=args, env_key=env_key)
    except ImportError:
        # Fallback: simple cwd() resolution
        if args:
            root_arg = getattr(args, "root", None) or getattr(args, "repo", None)
            if root_arg:
                return Path(root_arg).resolve()
        env_root = os.environ.get(env_key)
        if env_root:
            return Path(env_root).resolve()
        return Path.cwd()


def mask_secrets(text):
    """
    Replace known secret patterns with MASKED-<TYPE>.

    Delegates to tools/cli.mask_secrets() for centralized secret masking.

    Args:
        text: Input text to mask

    Returns:
        Text with secret patterns replaced by MASKED-<TYPE>
    """
    try:
        from tools import cli
        return cli.mask_secrets(text)
    except ImportError:
        # Fallback: no masking, return text as-is
        return text


def deterministic_json_dumps(obj, pretty=True):
    """
    JSON output with sorted keys for hermetic/reproducible output.

    Delegates to tools/cli.deterministic_json_dumps().

    Args:
        obj: Object to serialize
        pretty: If True, indent=2 for readability; else compact

    Returns:
        JSON string
    """
    try:
        from tools import cli
        return cli.deterministic_json_dumps(obj, pretty=pretty)
    except ImportError:
        # Fallback: basic json.dumps with sorted keys
        import json
        return json.dumps(
            obj,
            indent=2 if pretty else None,
            sort_keys=True,
            ensure_ascii=True,
        )


def exit_code(findings=None, error=None):
    """
    Return deterministic exit code.

    Delegates to tools/cli.exit_code() for centralized exit semantics.

    Convention:
      - 0 = success/clean (no findings, no error)
      - 1 = findings/violations detected (gate mode)
      - 2 = error (file read failure, subprocess failure, etc.)

    Args:
        findings: Number of findings (0 → exit 0, >0 → exit 1)
        error: Exception that occurred (if any, exit 2)

    Returns:
        Exit code (0, 1, or 2)
    """
    try:
        from tools import cli
        return cli.exit_code(findings=findings, error=error)
    except ImportError:
        # Fallback: simple logic
        if error is not None:
            return 2
        if findings is not None:
            return 1 if findings > 0 else 0
        return 0
