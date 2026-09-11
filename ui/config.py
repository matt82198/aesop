#!/usr/bin/env python3
"""
Aesop UI Configuration — Path and environment resolution.

This module centralizes all path configuration, environment variable resolution,
and config file loading for the UI dashboard. It provides a reload() function to
recompute all paths when environment changes (e.g., between test fixtures).

Config precedence: env vars > aesop.config.json > built-in defaults.
"""
import json
import os
import sys
from pathlib import Path


# ==============================================================================
# Path Resolution Functions
# ==============================================================================

def _resolve_heartbeat_path(env_var, conductor3_path, fallback_path):
    """Resolve heartbeat path with fallback chain to conductor3 location.

    Precedence:
    1. Environment variable (e.g., AESOP_WATCHDOG_HEARTBEAT)
    2. conductor3 location (shared orchestration state)
    3. Local fallback (aesop/state location)

    Returns:
        Tuple of (resolved_path, availability_status) where status is:
        - "configured" if the file exists at the resolved path
        - "not configured" if none of the paths exist
    """
    # Environment variable takes highest priority
    env_value = os.getenv(env_var)
    if env_value:
        env_path = Path(env_value).expanduser()
        if env_path.exists():
            return env_path, "configured"

    # Try conductor3 location (preferred for multi-instance state)
    if conductor3_path and conductor3_path.exists():
        return conductor3_path, "configured"

    # Try fallback location (local aesop state)
    if fallback_path and fallback_path.exists():
        return fallback_path, "configured"

    # None of the paths exist; return preferred location but mark as not configured
    return conductor3_path if conductor3_path else fallback_path, "not configured"


def reload():
    """Recompute all configuration from current environment.

    Called at module load and whenever environment changes (test fixtures).
    Mutates module-level globals in place so that importers see the current state.
    """
    global PORT, AESOP_ROOT, CONFIG_FILE, STATE_DIR, TRANSCRIPTS_ROOT
    global WATCHDOG_HEARTBEAT, MONITOR_HEARTBEAT, REPOS_JSON, BACKUP_LOG
    global ALERTS_LOG, INBOX_FILE, AUDIT_BACKLOG_FILE
    global UI_SESSION_TOKEN_FILE, TRACKER_FILE, ORCH_STATUS_FILE
    global WEB_DIST, LEDGER_FILE, QUEUE_STATE_DIR
    global COLLECTOR_INTERVAL, SSE_KEEPALIVE_SECONDS, SSE_MAX_CLIENTS, SSE_QUEUE_MAXSIZE, SSE_WRITE_TIMEOUT
    global WATCHDOG_HEARTBEAT_AVAILABILITY, MONITOR_HEARTBEAT_AVAILABILITY

    # PORT: env PORT > default 8770
    PORT = int(os.getenv("PORT", "8770"))

    # Determine AESOP_ROOT with fallback tiers (matching daemons/run-watchdog.sh pattern):
    # (1) AESOP_ROOT env var if set
    # (2) Derive from file location: Path(__file__).resolve().parents[1]
    # (3) Load config from derived location; if it has aesop_root, use that
    env_root = os.getenv("AESOP_ROOT")
    if env_root:
        AESOP_ROOT = Path(env_root)
    else:
        # Derive from file location (matches daemons/run-watchdog.sh pattern)
        AESOP_ROOT = Path(__file__).resolve().parents[1]

        # Check if derived location's config has aesop_root key
        derived_config_file = AESOP_ROOT / "aesop.config.json"
        if derived_config_file.exists():
            try:
                with open(derived_config_file, encoding="utf-8") as f:
                    derived_config = json.load(f)
                    if "aesop_root" in derived_config:
                        AESOP_ROOT = Path(derived_config["aesop_root"]).expanduser()
            except Exception:
                # Silently ignore config errors here; will attempt full load below
                pass

    # Try to load config file for additional settings
    CONFIG_FILE = AESOP_ROOT / "aesop.config.json"
    config_data = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as e:
            print(f"[config] Failed to load {CONFIG_FILE}: {e}", file=sys.stderr)

    # Derive paths with precedence: env var > config file > built-in default
    # STATE_DIR: env AESOP_STATE_ROOT > config state_root > AESOP_ROOT/state
    STATE_DIR = Path(
        os.getenv(
            "AESOP_STATE_ROOT",
            config_data.get("state_root", str(AESOP_ROOT / "state"))
        )
    )

    # TRANSCRIPTS_ROOT: env AESOP_TRANSCRIPTS_ROOT > config transcripts_root > ~/.claude/projects
    TRANSCRIPTS_ROOT = Path(
        os.getenv(
            "AESOP_TRANSCRIPTS_ROOT",
            config_data.get("transcripts_root", "~/.claude/projects")
        )
    ).expanduser()

    # Data file paths (all derived from STATE_DIR and AESOP_ROOT)
    # Heartbeat paths: prefer conductor3 orchestration state over local aesop state
    # AESOP_CONDUCTOR3_ROOT env var (optional) allows overriding conductor3 location for testing
    conductor3_root = Path(
        os.getenv("AESOP_CONDUCTOR3_ROOT", str(Path.home() / "conductor3"))
    ).expanduser()
    WATCHDOG_HEARTBEAT, WATCHDOG_HEARTBEAT_AVAILABILITY = _resolve_heartbeat_path(
        "AESOP_WATCHDOG_HEARTBEAT",
        conductor3_root / "state" / ".watchdog-heartbeat",
        STATE_DIR / ".watchdog-heartbeat"
    )
    MONITOR_HEARTBEAT, MONITOR_HEARTBEAT_AVAILABILITY = _resolve_heartbeat_path(
        "AESOP_MONITOR_HEARTBEAT",
        conductor3_root / "monitor" / ".monitor-heartbeat",
        STATE_DIR / ".monitor-heartbeat"
    )
    REPOS_JSON = STATE_DIR / ".watchdog-repos.json"
    BACKUP_LOG = STATE_DIR / "FLEET-BACKUP.log"
    ALERTS_LOG = STATE_DIR / "SECURITY-ALERTS.log"
    INBOX_FILE = STATE_DIR / "ui-inbox.md"
    # AUDIT_BACKLOG_FILE: env AESOP_AUDIT_BACKLOG > AESOP_ROOT/AUDIT-BACKLOG.md.
    # The env override lets demo mode (ui/demo.py) point the backlog panel at a
    # seeded snapshot without moving AESOP_ROOT (which must stay on the real
    # install so WEB_DIST keeps resolving the committed dist).
    AUDIT_BACKLOG_FILE = Path(
        os.getenv("AESOP_AUDIT_BACKLOG", str(AESOP_ROOT / "AUDIT-BACKLOG.md"))
    ).expanduser()
    UI_SESSION_TOKEN_FILE = STATE_DIR / ".ui-session-token"
    TRACKER_FILE = STATE_DIR / "tracker.json"
    ORCH_STATUS_FILE = STATE_DIR / "orchestrator-status.json"

    # Wave-14 dashboard rewrite (plan D3): built frontend + cost ledger paths.
    # WEB_DIST: env AESOP_WEB_DIST > AESOP_ROOT/ui/web/dist (test override for fixtures).
    WEB_DIST = Path(
        os.getenv(
            "AESOP_WEB_DIST",
            str(AESOP_ROOT / "ui" / "web" / "dist")
        )
    )
    # LEDGER_FILE: outcomes ledger parsed by ui/cost.py; sse.py mtime-gates on it.
    LEDGER_FILE = STATE_DIR / "ledger" / "OUTCOMES-LEDGER.md"

    # QUEUE_STATE_DIR: merge-queue operator data (exceptions.jsonl + heartbeat).
    # Env AESOP_QUEUE_STATE overrides default (allows pointing to QUEUE worktree state).
    QUEUE_STATE_DIR = Path(
        os.getenv("AESOP_QUEUE_STATE", str(STATE_DIR / "merge-queue"))
    ).expanduser()

    # Collector and SSE configuration
    COLLECTOR_INTERVAL = float(os.getenv("AESOP_UI_COLLECT_INTERVAL", "1.0"))
    SSE_KEEPALIVE_SECONDS = 15
    SSE_MAX_CLIENTS = 100  # Resource cap: reject new connections past this
    SSE_QUEUE_MAXSIZE = 50  # Per-client bounded queue (drops oldest on overflow)
    SSE_WRITE_TIMEOUT = 5.0  # Write timeout in seconds to prevent stalled clients


# ==============================================================================
# Module-level initialization
# ==============================================================================

# Initialize configuration at module load time
# These globals are recomputed by reload() and accessed by other modules
PORT = 8770
AESOP_ROOT = Path.home() / "aesop"
CONFIG_FILE = AESOP_ROOT / "aesop.config.json"
STATE_DIR = AESOP_ROOT / "state"
TRANSCRIPTS_ROOT = Path("~/.claude/projects").expanduser()
WATCHDOG_HEARTBEAT = STATE_DIR / ".watchdog-heartbeat"
WATCHDOG_HEARTBEAT_AVAILABILITY = "not configured"
MONITOR_HEARTBEAT = STATE_DIR / ".monitor-heartbeat"
MONITOR_HEARTBEAT_AVAILABILITY = "not configured"
REPOS_JSON = STATE_DIR / ".watchdog-repos.json"
BACKUP_LOG = STATE_DIR / "FLEET-BACKUP.log"
ALERTS_LOG = STATE_DIR / "SECURITY-ALERTS.log"
INBOX_FILE = STATE_DIR / "ui-inbox.md"
AUDIT_BACKLOG_FILE = AESOP_ROOT / "AUDIT-BACKLOG.md"
UI_SESSION_TOKEN_FILE = STATE_DIR / ".ui-session-token"
TRACKER_FILE = STATE_DIR / "tracker.json"
ORCH_STATUS_FILE = STATE_DIR / "orchestrator-status.json"
WEB_DIST = AESOP_ROOT / "ui" / "web" / "dist"
LEDGER_FILE = STATE_DIR / "ledger" / "OUTCOMES-LEDGER.md"
QUEUE_STATE_DIR = STATE_DIR / "merge-queue"
COLLECTOR_INTERVAL = 1.0
SSE_KEEPALIVE_SECONDS = 15
SSE_MAX_CLIENTS = 100
SSE_QUEUE_MAXSIZE = 50
SSE_WRITE_TIMEOUT = 5.0

# Perform initial load from environment
reload()
