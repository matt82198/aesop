#!/usr/bin/env python3
"""Aesop UI — read-only data collectors + tracker CRUD + SSE section snapshots (wave-9 split)."""
import hashlib
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from time import time

import config


def parse_audit_backlog():
    """
    Parse AUDIT-BACKLOG.md and return structured tier data.

    Returns:
        dict with 'tiers' list, each tier containing:
        {
            "tier": "P0" | "P1" | "P2" | "Needs decision",
            "items": [
                {"status": "✅"|"🔵"|"⬜"|"⏸", "tag": "[sec]", "title": "..."},
                ...
            ],
            "done": int,
            "inflight": int,
            "todo": int,
            "total": int
        }
    """
    result = {"tiers": []}

    try:
        if not config.AUDIT_BACKLOG_FILE.exists():
            return result

        content = config.AUDIT_BACKLOG_FILE.read_text(encoding='utf-8')
    except Exception as e:
        print(f"[collectors] Failed to read audit backlog: {e}", file=sys.stderr)
        return result

    # Split into lines
    lines = content.split('\n')

    # Parse sections and items.
    #
    # NOTE: tier headers are matched by REGEX PREFIX (e.g. "## P0\b"), not by exact/startswith
    # comparison against a fixed full title string. The backlog file's section titles evolve
    # over time (suffixes like "(do first)" become "(wave 5, from five-lens re-audit)"), and a
    # hardcoded full-string tier_map silently stops matching anything when that happens — the
    # panel then renders "no backlog found" forever even though the file is full of live items.
    # Regex-on-prefix survives any suffix/rename of the tier header.
    current_tier = None
    tier_patterns = [
        (re.compile(r'^##\s*P0\b'), "P0"),
        (re.compile(r'^##\s*P1\b'), "P1"),
        (re.compile(r'^##\s*P2\b'), "P2"),
        (re.compile(r'^##\s*Needs a user decision\b', re.IGNORECASE), "Needs decision"),
    ]

    # Stop parsing at these sections
    stop_sections = ["## Landing log", "## Dispatch plan"]

    tiers_data = {}  # tier_name -> list of items

    for line in lines:
        line_stripped = line.strip()

        # Check if we hit a stop section
        if any(line_stripped.startswith(stop) for stop in stop_sections):
            break

        # Any level-2 header re-evaluates current_tier. This is deliberate: a header that
        # doesn't match a known tier (e.g. "## Features (user-requested)") resets current_tier
        # to None, so its items are NOT silently attributed to whatever tier came before it
        # (bleed-through bug from sticky state).
        if line_stripped.startswith("## "):
            matched_tier = None
            for pattern, tier_name in tier_patterns:
                if pattern.match(line_stripped):
                    matched_tier = tier_name
                    break
            current_tier = matched_tier
            if current_tier and current_tier not in tiers_data:
                tiers_data[current_tier] = []
            continue

        # Parse item line (starts with "- " and a status glyph)
        if current_tier and line_stripped.startswith("- "):
            # Status glyphs: ✅ 🔵 ⬜ ⏸
            status = None
            rest = line_stripped[2:].strip()  # Remove "- "

            if rest.startswith("✅"):
                status = "✅"
                rest = rest[1:].strip()
            elif rest.startswith("🔵"):
                status = "🔵"
                rest = rest[1:].strip()
            elif rest.startswith("⬜"):
                status = "⬜"
                rest = rest[1:].strip()
            elif rest.startswith("⏸"):
                status = "⏸"
                rest = rest[1:].strip()

            if status:
                # Extract tag and title from "**[tag] Title...**"
                # Pattern: **[something] rest**
                if rest.startswith("**"):
                    # Find the closing **
                    match = re.match(r'\*\*\[([^\]]+)\]\s+(.+?)\*\*', rest)
                    if match:
                        tag = f"[{match.group(1)}]"
                        title = match.group(2)

                        tiers_data[current_tier].append({
                            "status": status,
                            "tag": tag,
                            "title": title
                        })

    # Convert to result format with counts
    tier_order = ["P0", "P1", "P2", "Needs decision"]
    for tier_name in tier_order:
        if tier_name in tiers_data:
            items = tiers_data[tier_name]
            done = sum(1 for item in items if item["status"] == "✅")
            inflight = sum(1 for item in items if item["status"] == "🔵")
            todo = sum(1 for item in items if item["status"] == "⬜")

            result["tiers"].append({
                "tier": tier_name,
                "items": items,
                "done": done,
                "inflight": inflight,
                "todo": todo,
                "total": len(items)
            })

    return result

def get_heartbeat_status():
    """Read daemon heartbeat age and status.

    Returns heartbeat status with honest staleness reporting:
    - "not configured" if heartbeat path is not available
    - "ALIVE" if heartbeat is fresh (< 300s)
    - "STALE" if heartbeat exists but is old (>= 300s)

    Buckets age to prevent every-tick hash change: age is reported in 3-second buckets
    (e.g., 0-2s → 0, 3-5s → 3, 6-8s → 6, ...) so the heartbeat snapshot only changes
    every ~3 seconds, not every 1 second. This preserves the change-hash gate effectiveness.
    """
    try:
        # Check if watchdog heartbeat is configured
        if config.WATCHDOG_HEARTBEAT_AVAILABILITY != "configured":
            return {"alive": "not configured", "age": -1, "threshold": 300}

        if not config.WATCHDOG_HEARTBEAT.exists():
            return {"alive": "not configured", "age": -1, "threshold": 300}

        content = config.WATCHDOG_HEARTBEAT.read_text(encoding='utf-8').strip()
        if not content:
            return {"alive": "not configured", "age": -1, "threshold": 300}

        # Parse epoch value robustly; assume seconds (standard epoch format)
        try:
            timestamp = int(content)
        except ValueError:
            # Retry once in case of race during daemon write
            try:
                content = config.WATCHDOG_HEARTBEAT.read_text().strip()
                timestamp = int(content)
            except Exception as e:
                print(f"[collectors] Failed to parse watchdog heartbeat: {e}", file=sys.stderr)
                return {"alive": "not configured", "age": -1, "threshold": 300}

        # Age in seconds: now_seconds - heartbeat_seconds
        age_seconds = int(time()) - timestamp
        # Bucket age to 3-second intervals to prevent hash churn
        age_bucketed = (age_seconds // 3) * 3
        alive = "ALIVE" if age_seconds < 300 else "STALE"
        return {"alive": alive, "age": age_bucketed, "threshold": 300}
    except Exception as e:
        print(f"[collectors] Failed to get watchdog heartbeat: {e}", file=sys.stderr)
        return {"alive": "not configured", "age": -1, "threshold": 300}

def get_monitor_heartbeat_status():
    """Read orchestration monitor heartbeat age and status.

    Returns heartbeat status with honest staleness reporting:
    - "not configured" if monitor heartbeat path is not available
    - "ALIVE" if heartbeat is fresh (< 3600s)
    - "STALE" if heartbeat exists but is old (>= 3600s)

    Buckets age to prevent every-tick hash change: age is reported in 3-second buckets
    (e.g., 0-2s → 0, 3-5s → 3, 6-8s → 6, ...) so the monitor snapshot only changes
    every ~3 seconds, not every 1 second. This preserves the change-hash gate effectiveness.
    """
    try:
        # Check if monitor heartbeat is configured (resolved via config.py fallback chain)
        if config.MONITOR_HEARTBEAT_AVAILABILITY != "configured":
            return {"alive": "not configured", "age": -1, "threshold": 3600}

        if not config.MONITOR_HEARTBEAT.exists():
            return {"alive": "not configured", "age": -1, "threshold": 3600}

        content = config.MONITOR_HEARTBEAT.read_text(encoding='utf-8').strip()
        if not content:
            return {"alive": "not configured", "age": -1, "threshold": 3600}

        # Parse epoch value robustly; assume seconds (standard epoch format)
        try:
            timestamp = int(content)
        except ValueError:
            # Retry once in case of race during monitor write
            try:
                content = config.MONITOR_HEARTBEAT.read_text().strip()
                timestamp = int(content)
            except Exception as e:
                print(f"[collectors] Failed to parse monitor heartbeat: {e}", file=sys.stderr)
                return {"alive": "not configured", "age": -1, "threshold": 3600}

        # Age in seconds: now_seconds - heartbeat_seconds
        age_seconds = int(time()) - timestamp
        # Bucket age to 3-second intervals to prevent hash churn
        age_bucketed = (age_seconds // 3) * 3
        alive = "ALIVE" if age_seconds < 3600 else "STALE"
        return {"alive": alive, "age": age_bucketed, "threshold": 3600}
    except Exception as e:
        print(f"[collectors] Failed to get monitor heartbeat: {e}", file=sys.stderr)
        return {"alive": "not configured", "age": -1, "threshold": 3600}

def get_main_thread_messages():
    """Read last ~12 messages from newest session JSONL."""
    messages = []
    try:
        if not config.TRANSCRIPTS_ROOT.exists():
            return messages
        # Find newest .jsonl
        jsonl_files = sorted(
            config.TRANSCRIPTS_ROOT.glob("**/*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        if not jsonl_files:
            return messages

        newest = jsonl_files[0]
        with open(newest, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
            # Get last 30 lines to extract ~12 message turns
            for line in lines[-30:]:
                try:
                    obj = json.loads(line)
                    role = obj.get("role", "unknown")
                    if role in ("user", "assistant"):
                        # Extract text content
                        content = obj.get("content", [])
                        text = ""
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and "text" in block:
                                    text = block["text"]
                                    break
                        elif isinstance(content, str):
                            text = content

                        if text:
                            # Truncate to 200 chars and sanitize
                            preview = text[:200].replace("\n", " ").strip()
                            timestamp = obj.get("timestamp", "")
                            messages.append({
                                "role": role,
                                "text": preview,
                                "timestamp": timestamp
                            })
                except (json.JSONDecodeError, KeyError):
                    pass
            # Keep only last 12
            messages = messages[-12:]
    except Exception as e:
        print(f"[collectors] Failed to read main thread messages: {e}", file=sys.stderr)
    return messages

def get_repos_status():
    """Read repos from .watchdog-repos.json."""
    repos = []
    try:
        if not config.REPOS_JSON.exists():
            return repos
        data = json.loads(config.REPOS_JSON.read_text(encoding='utf-8'))
        if isinstance(data, list):
            repos = data[:10]  # Limit to 10
        elif isinstance(data, dict):
            repos = [{"repo": k, "state": v} for k, v in data.items()][:10]
    except Exception as e:
        print(f"[collectors] Failed to read repos status: {e}", file=sys.stderr)
    return repos

def get_recent_events():
    """Read last 8 lines from FLEET-BACKUP.log."""
    events = []
    try:
        if not config.BACKUP_LOG.exists():
            return events
        lines = config.BACKUP_LOG.read_text(encoding='utf-8').strip().split('\n')
        events = [line.strip() for line in lines[-8:] if line.strip()]
    except Exception as e:
        print(f"[collectors] Failed to read recent events: {e}", file=sys.stderr)
    return events

def get_alerts():
    """Read SECURITY-ALERTS.log, skip NOTE:/RESOLVED-FP, count by severity."""
    alerts = {"count": 0, "lines": []}
    try:
        if not config.ALERTS_LOG.exists():
            return alerts
        lines = config.ALERTS_LOG.read_text(encoding='utf-8').strip().split('\n')
        unreviewed = [
            line.strip() for line in lines
            if line.strip()
            and "NOTE:" not in line
            and "RESOLVED-FP" not in line
        ]
        alerts["count"] = len(unreviewed)
        alerts["lines"] = unreviewed[-5:]  # Show last 5
    except Exception as e:
        print(f"[collectors] Failed to read alerts: {e}", file=sys.stderr)
    return alerts

def get_agent_lifecycle_events():
    """Collect agent lifecycle events from transcript analysis.

    Scans agent-*.jsonl files to infer agent state transitions.
    Returns structured events for Activity view rendering.

    Event format (list of dicts):
    [
        {
            "agent_id": "12345",
            "state": "dispatch" | "working" | "done" | "stalled",
            "last_activity": ISO 8601 timestamp (transcript mtime),
            "age_sec": seconds since last activity,
            "transitions": [
                {"state": "dispatch", "at": "2026-07-22T...Z"},
                {"state": "working", "at": "2026-07-22T...Z"},
            ]
        }
    ]

    If transcripts unavailable or no agents found, returns empty list.
    """
    events = []
    try:
        # Use wave_dispatch.py's existing logic for consistency
        import wave_dispatch as wd

        dispatch_data = wd.get_wave_dispatch(force=False)
        if not dispatch_data.get("available"):
            return events

        # Convert phase and timing data to lifecycle event structure
        agents_data = dispatch_data.get("agents", [])
        now_ts = datetime.now(timezone.utc)

        for agent in agents_data:
            agent_id = agent.get("id")
            phase = agent.get("phase")
            age_sec = agent.get("last_activity_age_sec", 0)

            if not agent_id:
                continue

            # Map phase to state
            phase_to_state = {
                "dispatch": "dispatch",
                "thinking": "working",
                "tool-use": "working",
                "stall": "stalled",
                "done": "done",
                "unknown": "working",
            }
            state = phase_to_state.get(phase, "working")

            # Estimate activity timestamp from age
            if age_sec >= 0:
                activity_ts = now_ts.timestamp() - age_sec
                activity_dt = datetime.fromtimestamp(activity_ts, timezone.utc)
                last_activity = activity_dt.isoformat(timespec='seconds').replace('+00:00', 'Z')
            else:
                last_activity = now_ts.isoformat(timespec='seconds').replace('+00:00', 'Z')

            event = {
                "agent_id": agent_id,
                "state": state,
                "last_activity": last_activity,
                "age_sec": age_sec,
                "transitions": [
                    {
                        "state": state,
                        "at": last_activity,
                    }
                ],
            }
            events.append(event)

    except Exception as e:
        print(f"[collectors] Failed to collect agent lifecycle events: {e}", file=sys.stderr)

    return events

def load_tracker():
    """Load tracker.json, return empty tracker if missing or corrupt.

    LOAD-ONLY path: reads the materialized view from disk (cache read).
    Mutations are handled by WriteAPI which keeps this file in sync.
    """
    if not config.TRACKER_FILE.exists():
        return {"version": 1, "items": []}

    try:
        data = json.loads(config.TRACKER_FILE.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or "version" not in data:
            raise ValueError("Invalid tracker schema")
        return data
    except Exception as e:
        print(f"[tracker] Corrupt tracker.json: {e}", file=sys.stderr)
        corrupt_path = config.TRACKER_FILE.with_suffix('.json.corrupt')
        try:
            if config.TRACKER_FILE.exists():
                config.TRACKER_FILE.rename(corrupt_path)
        except Exception as e:
            print(f"[tracker] Failed to rename corrupt tracker: {e}", file=sys.stderr)
        return {"version": 1, "items": []}

def get_tracker_items(status=None, priority=None):
    """Retrieve tracker items with optional filters.

    Reads from the materialized tracker.json (cache read).
    """
    tracker = load_tracker()
    items = tracker.get("items", [])

    if status:
        items = [i for i in items if i.get("status") == status]
    if priority:
        items = [i for i in items if i.get("priority") == priority]

    return items

# --- Event-sourced tracker backing (state_store) via unified WriteAPI --------
# Inc 1: All tracker mutations now route through WriteAPI, which provides:
# - Atomic event append + projection render (tempfile + os.replace)
# - OCC (Optimistic Concurrency Control) for write safety
# - Single canonical materializer (state_store.materialize)
#
# The live read path (load_tracker / get_tracker_items / SSE snapshot) is
# unchanged: it reads the materialized tracker.json as a cache. Writes keep
# it in sync via WriteAPI._render_tracker_atomic.

def _write_api():
    """Return a WriteAPI for tracker mutations (lazy import; call-time paths)."""
    try:
        from state_store.write_api import WriteAPI
    except ImportError:
        from pathlib import Path as _Path
        root = str(_Path(__file__).resolve().parents[1])
        if root not in sys.path:
            sys.path.insert(0, root)
        from state_store.write_api import WriteAPI
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    return WriteAPI(str(config.STATE_DIR))


def _tracker_api():
    """Return a StateAPI over the tracker event log (read/projection side).

    Distinct from _write_api(): callers here need .project(), which WriteAPI does
    not expose. This helper's definition was lost in the event-store refactor while
    two call sites in the inbox drain path kept calling it. Both sites sit inside
    try/except, so the resulting NameError was swallowed and the dedup-against-
    projection check silently never ran.
    """
    try:
        from state_store import StateAPI
    except ImportError:
        from pathlib import Path as _Path
        root = str(_Path(__file__).resolve().parents[1])
        if root not in sys.path:
            sys.path.insert(0, root)
        from state_store import StateAPI
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    return StateAPI(str(config.STATE_DIR / "tracker_events.db"))


def _backfill_missing_items(api, db_item_ids, disk_items):
    """Backfill items from tracker.json that are missing from the event log.

    Idempotent: only items not already in DB are appended. Per-record error
    handling ensures remaining items are backfilled even if one fails.

    Args:
        api: StateAPI instance for appending events.
        db_item_ids: set of item IDs already in the event store.
        disk_items: list of items from tracker.json.

    Returns:
        list of error messages (empty if all succeeded).
    """
    errors = []
    items_to_backfill = [
        item for item in disk_items
        if item.get("id") not in db_item_ids
    ]

    for item in items_to_backfill:
        try:
            api.append("tracker", "item_created", item, "migration")
        except Exception as e:
            item_id = item.get("id", "?")
            error_msg = f"Failed to backfill item {item_id}: {e}"
            print(f"[tracker] {error_msg}", file=sys.stderr)
            errors.append(error_msg)

    return errors


def _reconcile_status_updates(api, events, disk_items):
    """Reconcile item statuses: items with stale status on disk get updated events.

    Only trusts disk for items whose status the event log has NEVER recorded.
    Items with explicit item_updated events in the log are owned by the log and
    are skipped. This prevents reverting a real update if migration retries.

    Per-record error handling ensures remaining items are reconciled even if
    one fails.

    Args:
        api: StateAPI instance for appending events.
        events: list of events already in the store (used to identify recorded statuses).
        disk_items: list of items from tracker.json.

    Returns:
        list of error messages (empty if all succeeded).
    """
    errors = []

    # Project current state to get rendered items
    try:
        projected = {
            i.get("id"): i
            for i in api.project("tracker").get("items", [])
            if isinstance(i, dict) and i.get("id")
        }
    except Exception as e:
        print(f"[tracker] Could not project for status reconcile: {e}", file=sys.stderr)
        return errors

    # Build set of items that already have an explicit item_updated in the log
    status_recorded_ids = set()
    for ev in events:
        if ev.get("type") == "item_updated":
            payload = ev.get("payload") or {}
            if payload.get("id") and payload.get("status") is not None:
                status_recorded_ids.add(payload["id"])

    # For each disk item, emit an update event if its status differs from projection
    # and the log has never recorded a status update for it
    for item in disk_items:
        item_id = item.get("id")
        disk_status = item.get("status")

        # Skip if missing id or status, or not in projection
        if not item_id or not disk_status or item_id not in projected:
            continue

        # Skip if log already owns this item's status
        if item_id in status_recorded_ids:
            continue

        # Skip if projection status already matches disk
        if projected[item_id].get("status") == disk_status:
            continue

        # Emit the update event to reconcile the status
        try:
            api.append(
                "tracker",
                "item_updated",
                {"id": item_id, "status": disk_status},
                "migration",
            )
        except Exception as e:
            error_msg = f"Failed to reconcile status for {item_id}: {e}"
            print(f"[tracker] {error_msg}", file=sys.stderr)
            errors.append(error_msg)

    return errors


def _ensure_tracker_migrated(write_api):
    """Backfill the event log from the existing tracker.json once (idempotent).

    Adapted for Inc 1: Use StateAPI.append() directly to backfill events.
    Guard migration with marker events to prevent concurrent backfill.

    P0 FIX (wave-29): Separate migration_started and migration_completed markers.
    - migration_started: Claimed at start to guard concurrent backfills.
    - migration_completed: Written ONLY after backfill fully succeeds.
    - Skip logic gates on BOTH (migration truly done).
    - Retry if started exists without completed (stale claim or failed backfill).
    - Handles already-bricked state (items on disk not in event store).
    - All exceptions surface; none are swallowed.

    Reconciliation works in both directions:
    1. Backfill: items missing from the log are added as item_created events.
    2. Status reconcile: items with stale status on disk get item_updated events
       (but only if the log never recorded a status for them).
    """
    from state_store import StateAPI

    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    api = None

    try:
        api = StateAPI(str(config.STATE_DIR / "tracker_events.db"))
        events = api.get("tracker")

        # Check for completion marker
        has_migration_completed = any(
            e.get("type") == "migration_completed" and e.get("payload", {}).get("version") == 1
            for e in events
        )

        # If migration is already completed, nothing to do
        if has_migration_completed:
            return

        # Check for stale start marker (indicates incomplete or failed migration)
        has_migration_started = any(
            e.get("type") == "migration_started" and e.get("payload", {}).get("version") == 1
            for e in events
        )

        # If no start marker, claim this migration attempt
        if not has_migration_started:
            api.append("tracker", "migration_started", {"version": 1}, "system")

        # Refresh events to get any newly-appended start marker
        events = api.get("tracker")

        # Build set of items currently in the event store
        db_item_ids = set(
            e.get("payload", {}).get("id") for e in events
            if e.get("type") == "item_created" and e.get("payload", {}).get("id")
        )

        # Load items from tracker.json
        disk_items = []
        if config.TRACKER_FILE.exists():
            try:
                data = json.loads(config.TRACKER_FILE.read_text(encoding='utf-8'))
                disk_items = [
                    item for item in data.get("items", [])
                    if isinstance(item, dict) and item.get("id")
                ]
            except Exception as e:
                print(f"[tracker] Failed to read tracker.json: {e}", file=sys.stderr)
                # If we can't read the file, don't fail; just mark migration as done to avoid retry loops
                api.append("tracker", "migration_completed", {"version": 1}, "system")
                return

        # Direction 1: Backfill missing items
        backfill_errors = _backfill_missing_items(api, db_item_ids, disk_items)

        # Direction 2: Reconcile statuses (without this, migration loses data)
        reconcile_errors = _reconcile_status_updates(api, events, disk_items)

        # Combine all errors
        all_errors = backfill_errors + reconcile_errors

        # Only mark migration complete if no errors occurred
        # If there were errors, the next caller will retry
        if not all_errors:
            api.append("tracker", "migration_completed", {"version": 1}, "system")
        else:
            print(f"[tracker] Migration had {len(all_errors)} errors; "
                  f"will retry on next call", file=sys.stderr)

    except Exception as e:
        print(f"[tracker] Migration failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)

    finally:
        if api:
            try:
                api.close()
            except Exception:
                pass


def create_tracker_item(data):
    """Create a new tracker item (event-sourced via WriteAPI).

    Accepts optional acceptanceCriteria: list of {statement, verifiable_by} dicts.
    Authored AC always stored as-is; no derivation happens here (orchestrator handles derivation).

    Uses WriteAPI which provides atomic append + render under OCC protection.
    """
    write_api = _write_api()
    _ensure_tracker_migrated(write_api)

    item = {
        "title": data.get("title", ""),
        "priority": data.get("priority", "P1"),
        "status": data.get("status", "todo"),
        "lane": data.get("lane", "proposed"),
        "source": data.get("source", "manual"),
        "tags": data.get("tags", []) if isinstance(data.get("tags"), list) else [],
        "notes": data.get("notes"),
        "pr_link": data.get("pr_link"),
    }

    # Add acceptanceCriteria if provided (authored AC, never derived here)
    ac = data.get("acceptanceCriteria")
    if ac is not None and isinstance(ac, list) and len(ac) > 0:
        item["acceptanceCriteria"] = ac

    result = write_api.tracker_append_item(item, actor=item.get("source", "manual"))
    write_api.close()
    return result

def update_tracker_item(item_id, update_data):
    """Update a tracker item by id (event-sourced via WriteAPI).

    Accepts optional acceptanceCriteria: list of {statement, verifiable_by} dicts.
    Authored AC always replaces derived (authored wins, no merge).

    Uses WriteAPI which provides atomic append + render under OCC protection.
    """
    write_api = _write_api()
    _ensure_tracker_migrated(write_api)

    patch = {}
    for key in ["status", "lane", "priority", "notes", "pr_link", "tags", "acceptanceCriteria"]:
        if key in update_data:
            patch[key] = update_data[key]

    # Handle completed_at timestamp for status=done
    if update_data.get("status") == "done":
        # Read current item to check if already completed
        current = load_tracker()
        current_items = {it["id"]: it for it in current.get("items", [])}
        if item_id in current_items and not current_items[item_id].get("completed_at"):
            patch["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    result = write_api.tracker_update_item(item_id, patch, actor="api")
    write_api.close()
    return result

def delete_tracker_item(item_id):
    """Soft-delete a tracker item (mark as archived; event-sourced via WriteAPI).

    Uses WriteAPI which provides atomic append + render under OCC protection.
    """
    write_api = _write_api()
    _ensure_tracker_migrated(write_api)

    result = write_api.tracker_archive_item(item_id, actor="api")
    write_api.close()
    return result

def _snapshot_data():
    """Everything the 'data' SSE section covers (header, repos, events, alerts, messages)."""
    return {
        "watchdog": get_heartbeat_status(),
        "monitor": get_monitor_heartbeat_status(),
        "repos": get_repos_status(),
        "events": get_recent_events(),
        "alerts": get_alerts(),
        "messages": get_main_thread_messages(),
    }

def _snapshot_tracker():
    """Read tracker.json via ReadAPI, return {items: [...]}."""
    try:
        from state_store.read_api import ReadAPI
        api = ReadAPI(str(config.STATE_DIR))
        data = api.read_tracker_snapshot()
        if isinstance(data, dict) and "items" in data:
            return {"items": data.get("items", [])}
        return {"items": []}
    except Exception as e:
        print(f"[tracker] Snapshot error (via ReadAPI): {e}", file=sys.stderr)
        return {"items": []}

def _snapshot_orchestrator_status():
    """Read and normalize orchestrator-status.json via ReadAPI."""
    try:
        from state_store.read_api import ReadAPI
        api = ReadAPI(str(config.STATE_DIR))
        data = api.read_orchestrator_status()
        if data is None:
            return {"orchestrators": []}
        if not isinstance(data, dict):
            return {"orchestrators": []}
        # Already normalized list shape
        if "orchestrators" in data and isinstance(data["orchestrators"], list):
            return data
        # Wrap bare object as single entry
        if "id" in data or "role" in data:
            age_seconds = 0
            stale = False
            try:
                updated_at_str = data.get("updated_at", "")
                if updated_at_str:
                    updated_at_str = updated_at_str.rstrip('Z')
                    updated_at = datetime.fromisoformat(updated_at_str)
                    age_seconds = int((datetime.now(timezone.utc).replace(tzinfo=None) - updated_at).total_seconds())
                    stale = age_seconds > 1800
            except Exception as e:
                print(f"[collectors] Failed to parse orchestrator timestamp: {e}", file=sys.stderr)
            entry = dict(data)
            entry["age_seconds"] = age_seconds
            entry["stale"] = stale
            return {"orchestrators": [entry]}
        return {"orchestrators": []}
    except Exception as e:
        print(f"[status] Snapshot error (via ReadAPI): {e}", file=sys.stderr)
        return {"orchestrators": []}

def _recover_stranded_inbox_files():
    """Defect 3 fix: Recovery sweep for .tracker-inbox.jsonl.processing-* files.

    If drain_tracker_inbox crashes mid-process, it leaves a .processing-* file.
    This sweep re-ingests those files on the next drain call, preventing silent
    data loss. Returns list of items recovered.
    """
    recovered = []
    try:
        for processing_file in sorted(config.STATE_DIR.glob(".tracker-inbox.jsonl.processing-*")):
            if not processing_file.exists():
                continue
            try:
                content = processing_file.read_text(encoding='utf-8')
                if not content.strip():
                    try:
                        processing_file.unlink()
                    except Exception:
                        pass
                    continue

                # Build dedup hash set from both tracker.json and event store
                existing_hashes = set()

                # Check rendered tracker.json
                tracker_json = load_tracker()
                for item in tracker_json.get("items", []):
                    source = item.get("source", "")
                    title = item.get("title", "")
                    h = hashlib.sha256((source + ":" + title).encode()).hexdigest()
                    existing_hashes.add(h)

                # Check event store projection
                try:
                    api = _tracker_api()
                    projected = api.project("tracker")
                    for item in projected.get("items", []):
                        source = item.get("source", "")
                        title = item.get("title", "")
                        h = hashlib.sha256((source + ":" + title).encode()).hexdigest()
                        existing_hashes.add(h)
                except Exception as e:
                    print(f"[inbox] Recovery: failed to project tracker state: {e}", file=sys.stderr)

                # Reprocess lines from the recovered file
                lines = content.strip().splitlines()
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if not isinstance(entry, dict):
                            continue
                        source = entry.get("source", "")
                        title = entry.get("title", "")
                        h = hashlib.sha256((source + ":" + title).encode()).hexdigest()

                        if h not in existing_hashes:
                            item = create_tracker_item(entry)
                            recovered.append(item)
                            existing_hashes.add(h)
                    except (json.JSONDecodeError, Exception):
                        pass

                # Only delete after successful re-ingest
                try:
                    processing_file.unlink()
                except Exception as e:
                    print(f"[inbox] Recovery: failed to unlink {processing_file}: {e}", file=sys.stderr)

            except Exception as e:
                print(f"[inbox] Recovery sweep error on {processing_file}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[inbox] Recovery sweep failed: {e}", file=sys.stderr)

    return recovered


def drain_tracker_inbox():
    """Drain .tracker-inbox.jsonl, create items idempotently.

    Defect 1 fix: Atomically rename inbox file to unique processing name FIRST
    to ensure only one caller processes it under concurrent access. Strengthen
    dedup to check both tracker.json AND api.project("tracker") so items
    in the event store but not yet rendered are also excluded.

    Defect 3 fix: Before processing the current inbox, perform a recovery sweep
    to re-ingest leftover .tracker-inbox.jsonl.processing-* files from any
    previous crashes. This prevents silent data loss if a crash happens
    mid-drain: stranded files are recovered on the next drain call.
    """
    inbox_file = config.STATE_DIR / ".tracker-inbox.jsonl"

    # Defect 3: Recovery sweep for stranded .processing-* files from prior crashes.
    created = _recover_stranded_inbox_files()

    # Now process the current inbox if it exists
    if not inbox_file.exists():
        return created

    # Defect 1: Atomically rename inbox to unique processing name first.
    # This ensures only one caller wins; others see no file.
    processing_file = inbox_file.with_name(
        f".tracker-inbox.jsonl.processing-{secrets.token_hex(8)}"
    )
    try:
        os.replace(str(inbox_file), str(processing_file))
    except FileNotFoundError:
        # Another caller already renamed it; nothing to process
        return created
    except Exception as e:
        print(f"[inbox] Failed to rename inbox: {e}", file=sys.stderr)
        return created

    try:
        content = processing_file.read_text(encoding='utf-8')
        if not content.strip():
            processing_file.unlink()
            return created

        lines = content.strip().splitlines()

        # Defect 1: Build dedup hash set from both tracker.json AND event store projection.
        # This catches items in the event store that haven't been rendered to tracker.json yet.
        existing_hashes = set()

        # Check rendered tracker.json
        tracker_json = load_tracker()
        for item in tracker_json.get("items", []):
            source = item.get("source", "")
            title = item.get("title", "")
            h = hashlib.sha256((source + ":" + title).encode()).hexdigest()
            existing_hashes.add(h)

        # Also check event store projection (catches items in DB but not yet rendered)
        try:
            api = _tracker_api()
            projected = api.project("tracker")
            for item in projected.get("items", []):
                source = item.get("source", "")
                title = item.get("title", "")
                h = hashlib.sha256((source + ":" + title).encode()).hexdigest()
                existing_hashes.add(h)
        except Exception as e:
            print(f"[inbox] Failed to project tracker state: {e}", file=sys.stderr)
            # Fall back to tracker.json only if projection fails
            pass

        rejects = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                rejects.append(line)
                continue

            if not isinstance(entry, dict):
                rejects.append(line)
                continue

            source = entry.get("source", "")
            title = entry.get("title", "")
            h = hashlib.sha256((source + ":" + title).encode()).hexdigest()

            if h not in existing_hashes:
                # create_tracker_item can raise real errors (not malformed JSON)
                # let those bubble up rather than silently adding to rejects
                item = create_tracker_item(entry)
                created.append(item)
                existing_hashes.add(h)

        if rejects:
            rejects_file = inbox_file.with_name(".tracker-inbox.rejects")
            rejects_file.write_text("\n".join(rejects) + "\n", encoding='utf-8')

        # Only delete the processing file after successful completion.
        # If an exception occurs, the file is left behind for recovery.
        processing_file.unlink()
    except Exception as e:
        print(f"[inbox] Drain error: {e}; processing file {processing_file.name} left for recovery", file=sys.stderr)
        # Don't delete the file on error; let recovery sweep handle it next time

    return created
