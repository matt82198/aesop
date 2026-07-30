#!/usr/bin/env python3
"""
state_store.read_api — Typed read-only facade for state surfaces.

Consolidates read patterns for: tracker snapshot, orchestrator-status,
heartbeat freshness, and ledger rows. This facade allows the underlying
state representation to change (git → SQLite → Postgres) without altering
caller code.

Callers use:
  api = ReadAPI(state_dir)
  tracker = api.read_tracker_snapshot()
  status = api.read_orchestrator_status()
  is_fresh = api.is_orchestrator_status_fresh(threshold_s=300)
  is_fresh = api.check_heartbeat_fresh(".watchdog-heartbeat", 200)
  rows = api.read_ledger_rows()

Projection-first reads (Inc 2+): orchestrator_status reads from the event
store projection first, falling back to the materialized view if the DB
is absent. This ensures fresh data when the event store is live while
maintaining compatibility with legacy file-only setups.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure tools module is importable (sys.path fix for bootstrapping)
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from tools.common import check_heartbeat_staleness
from tools.fleet_ledger import parse_ledger_rows as parse_ledger_rows_impl


class ReadAPI:
    """Read-only facade over state surfaces (tracker, orchestrator-status, heartbeats, ledger).

    Designed to be swappable: backends can change (git → SQLite → Postgres) without
    altering call sites. Current implementation:
    - tracker: reads from materialized tracker.json
    - orchestrator-status (Inc 2+): reads from StateAPI projection, falls back to file
    - heartbeats: reads from materialized .heartbeat files
    - ledger: reads from materialized OUTCOMES-LEDGER.md

    Projection-first reads (Inc 2+) ensure fresh data from the event store while
    maintaining compatibility with legacy file-only setups (DB absent → file fallback).
    """

    def __init__(self, state_dir):
        """Initialize the read API with a state directory.

        Args:
            state_dir: Path to the state directory (e.g., "state" or "/absolute/path/state")
        """
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._api = None  # Lazy-loaded StateAPI for projection reads

    def _get_api(self):
        """Lazy-load StateAPI for projection reads.

        Returns:
            StateAPI instance, or None if the DB is absent or unreadable.
        """
        if self._api is None:
            try:
                from state_store.api import StateAPI
                db_path = str(self.state_dir / "tracker_events.db")
                if Path(db_path).exists():
                    self._api = StateAPI(db_path)
            except Exception:
                # DB absent or unreadable; use file fallback
                pass
        return self._api

    def read_tracker_snapshot(self):
        """Read the current tracker snapshot.

        Returns the tracker.json file, or an empty dict if missing/unreadable.
        In the future, this may read from the SQLite projection instead.

        Returns:
            dict: Tracker snapshot (items, metadata, etc.) or {} if unavailable.
        """
        tracker_file = self.state_dir / "tracker.json"
        if not tracker_file.exists():
            return {}

        try:
            content = tracker_file.read_text(encoding="utf-8")
            return json.loads(content)
        except Exception:
            # Malformed JSON or read error: return empty dict (fail-open)
            return {}

    def read_orchestrator_status(self):
        """Read orchestrator status (projection-first with file fallback).

        Attempts to read from the event store projection first (if available),
        falling back to the materialized orchestrator-status.json file if the
        DB is absent or unreadable. This ensures fresh data when the store is
        live while maintaining compatibility with legacy file-only setups.

        Returns:
            dict with "id", "role", "activity", "phase", "updated_at" fields,
            or None if both sources are missing/unreadable.
        """
        # Try projection first (Inc 2+)
        api = self._get_api()
        if api is not None:
            try:
                return api.project("orchestrator_status")
            except Exception:
                # Projection read failed; fall through to file fallback
                pass

        # Fall back to materialized file (legacy/fallback)
        status_file = self.state_dir / "orchestrator-status.json"
        if not status_file.exists():
            return None

        try:
            content = status_file.read_text(encoding="utf-8")
            data = json.loads(content)
            # Don't validate here—let callers decide what fields are required
            return data
        except Exception:
            return None

    def is_orchestrator_status_fresh(self, threshold_s=300):
        """Check if orchestrator-status.json exists and is fresh.

        Reads updated_at timestamp and compares to current time.
        Returns False if file missing, unparseable, or stale.

        Args:
            threshold_s: Staleness threshold in seconds (default: 300s)

        Returns:
            bool: True if file is fresh, False if stale/missing/malformed.
        """
        status = self.read_orchestrator_status()
        if status is None:
            return False

        try:
            updated_at_str = status.get("updated_at")
            if not updated_at_str:
                return False

            # Parse ISO format timestamp (handle both "Z" and "+00:00")
            normalized = updated_at_str.replace("Z", "+00:00")
            updated_at = datetime.fromisoformat(normalized)

            now = datetime.now(timezone.utc)
            age = (now - updated_at).total_seconds()

            # Treat future-dated timestamps as stale (fail-closed)
            if age < -120:  # More than 2min in future = clock skew, treat as stale
                return False

            # Clamp small negative ages to 0
            age = max(0, age)

            return age < threshold_s
        except Exception:
            return False

    def check_heartbeat_fresh(self, heartbeat_filename, threshold_s=200):
        """Check if a heartbeat file is fresh.

        Delegates to tools.common.check_heartbeat_staleness (single source of truth).

        Args:
            heartbeat_filename: Filename in state_dir (e.g., ".watchdog-heartbeat")
            threshold_s: Staleness threshold in seconds (default: 200s)

        Returns:
            bool: True if file exists and is fresh, False otherwise.
        """
        hb_file = self.state_dir / heartbeat_filename
        # Use the shared implementation from common.py; invert staleness to freshness
        is_stale, _, _ = check_heartbeat_staleness(hb_file, threshold_s)
        return not is_stale

    def read_ledger_rows(self):
        """Read OUTCOMES-LEDGER.md and parse rows.

        Delegates to tools.fleet_ledger.parse_ledger_rows() (single source of truth).
        Returns empty list if file missing or unparseable.

        Returns:
            list: List of row dicts parsed from the ledger table, or [].
        """
        # Use the shared implementation from fleet_ledger.py
        return parse_ledger_rows_impl()
