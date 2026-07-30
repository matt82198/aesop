#!/usr/bin/env python3
"""Aesop UI — SSE client registry + broadcast + background collector loop (wave-9 split)."""
import hashlib
import json
import queue
import sys
import threading
import time

import config
import cost
from collectors import (parse_audit_backlog, _snapshot_data, _snapshot_tracker,
                        _snapshot_orchestrator_status, drain_tracker_inbox)
from agents import get_fleet_agents, _transcripts_fingerprint, sanitize_agents_for_broadcast


HEARTBEAT_EVENT_NAME = 'heartbeat'
HEARTBEAT_INTERVAL = 15  # seconds; emit every 15s to detect collector thread death

_sse_lock = threading.Lock()

_sse_clients = []  # list[queue.Queue]

_dropped_counts = {}  # dict[queue.Queue, int] — track dropped events per client

_latest_lock = threading.Lock()

_latest_snapshots = {"data": None, "backlog": None, "agents": None,
                     "tracker": None, "status": None, "cost": None}  # name -> json str

_collector_lock = threading.Lock()

_collector_started = False

_collector_stop_event = threading.Event()

# Track collector health for staleness detection (wave-31 reliability)
_collector_health_lock = threading.Lock()
_collector_health = {
    "last_successful_cycle": None,  # epoch seconds of last full successful cycle
    "per_source_errors": {},  # dict[source_name, list[error_info]]
}


def reset_state():
    """Reset collector/SSE singleton state for a fresh serve import.

    The sse module object is cached in sys.modules, so per-test re-imports of
    serve would otherwise share one collector thread + snapshot dict (a prior
    test's thread keeps polling its own dir; later tests never see their state).
    serve.py calls this at import to restore the per-import isolation the
    pre-split monolith had. In production serve is imported once, so this is a
    harmless no-op before the collector ever starts.
    """
    global _collector_started, _collector_stop_event, _collector_health
    with _collector_lock:
        _collector_stop_event.set()        # stop a thread left over from a prior import
        _collector_stop_event = threading.Event()
        _collector_started = False
    with _latest_lock:
        for k in list(_latest_snapshots):
            _latest_snapshots[k] = None
    with _sse_lock:
        _sse_clients.clear()
        _dropped_counts.clear()
    with _collector_health_lock:
        _collector_health["last_successful_cycle"] = None
        _collector_health["per_source_errors"] = {}


def register_sse_client():
    """Register a new SSE client queue. Returns the queue to read events from, or None if cap exceeded."""
    with _sse_lock:
        if len(_sse_clients) >= config.SSE_MAX_CLIENTS:
            return None  # Caller will return HTTP 503
        q = queue.Queue(maxsize=config.SSE_QUEUE_MAXSIZE)
        _sse_clients.append(q)
    return q

def unregister_sse_client(q):
    """Remove a disconnected SSE client's queue."""
    with _sse_lock:
        if q in _sse_clients:
            _sse_clients.remove(q)
        _dropped_counts.pop(q, None)  # Clean up dropped count for this client

def broadcast_sse(event_name, payload):
    """Push (event_name, payload) onto every currently-registered client queue.

    If a client queue is full, drop the oldest event to make room (bounded backpressure).
    This prevents one slow client from blocking the broadcast.

    Tracks dropped events: when a client's queue overflows, we increment the dropped counter
    and attach a "dropped": N field to the event being queued, so the frontend can detect
    that it missed updates.
    """
    with _sse_lock:
        clients = list(_sse_clients)
    for q in clients:
        try:
            q.put_nowait((event_name, payload))
        except queue.Full:
            # Queue is full: drop oldest, track the drop, and add dropped field to new event
            with _sse_lock:
                _dropped_counts[q] = _dropped_counts.get(q, 0) + 1
                dropped = _dropped_counts[q]

            # Try to parse payload and add dropped field
            effective_payload = payload
            try:
                data = json.loads(payload)
                data["dropped"] = dropped
                effective_payload = json.dumps(data, default=str, sort_keys=True)
            except (json.JSONDecodeError, TypeError):
                # If payload is not JSON, can't attach dropped count; use original
                pass

            try:
                q.get_nowait()  # Remove oldest event
                q.put_nowait((event_name, effective_payload))  # Add new event with dropped field
                # Reset the dropped counter after successful queue
                with _sse_lock:
                    _dropped_counts[q] = 0
            except Exception as e:
                print(f"[collector_loop] Exception: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        except Exception:
            pass

def _maybe_emit(name, snapshot, last_hashes):
    """Hash-gate: only store + broadcast a section if its content actually changed."""
    payload = json.dumps(snapshot, default=str, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if last_hashes.get(name) == digest:
        return
    last_hashes[name] = digest
    with _latest_lock:
        _latest_snapshots[name] = payload
    broadcast_sse(name, payload)

def collector_loop(stop_event):
    """Background loop: poll cheap sources, gate expensive ones, broadcast on change."""
    global _collector_health
    last_hashes = {}
    last_backlog_mtime = object()  # sentinel guaranteed != any real mtime/None
    last_agents_fingerprint = None
    cached_backlog_snapshot = {"tiers": []}
    cached_agents_snapshot = []
    last_tracker_mtime = object()
    last_tracker_size = object()
    last_status_mtime = object()
    last_status_size = object()
    last_cost_mtime = object()
    last_cost_size = object()
    cached_tracker_snapshot = {'items': []}
    cached_status_snapshot = {'orchestrators': []}
    cached_cost_snapshot = {}
    # Wave-19: Gate data section sources on mtime to avoid expensive reads every tick
    last_backup_log_mtime = object()  # sentinel
    last_backup_log_size = object()
    last_alerts_log_mtime = object()  # sentinel
    last_alerts_log_size = object()
    cached_data_snapshot = {}
    # Wave-20: heartbeat emission to detect collector thread death
    last_heartbeat_time = 0.0

    def _record_error(source_name, error):
        """Record an error for a source and update collector health."""
        with _collector_health_lock:
            if source_name not in _collector_health["per_source_errors"]:
                _collector_health["per_source_errors"][source_name] = []
            error_list = _collector_health["per_source_errors"][source_name]
            error_info = f"{type(error).__name__}:{str(error)[:50]}"
            if error_info not in error_list:
                error_list.append(error_info)
            if len(error_list) > 5:
                error_list.pop(0)

    def _record_success(source_name):
        """Clear errors for a source upon successful collection."""
        with _collector_health_lock:
            _collector_health["per_source_errors"][source_name] = []

    while not stop_event.is_set():
        try:
            current_time = time.time()

            # Wave-20: Emit heartbeat every 15s to signal collector thread is alive
            if current_time - last_heartbeat_time >= HEARTBEAT_INTERVAL:
                last_heartbeat_time = current_time
                heartbeat_payload = json.dumps({"timestamp": int(current_time * 1000)}, default=str)
                broadcast_sse(HEARTBEAT_EVENT_NAME, heartbeat_payload)

            # Wave-19: Gate data section on mtimes+sizes to avoid expensive file reads every tick.
            # Only regenerate the snapshot if one of the underlying log files changed.
            try:
                backup_log_stat = config.BACKUP_LOG.stat() if config.BACKUP_LOG and config.BACKUP_LOG.exists() else None
                backup_log_mtime = backup_log_stat.st_mtime if backup_log_stat else None
                backup_log_size = backup_log_stat.st_size if backup_log_stat else None
            except OSError:
                backup_log_mtime = None
                backup_log_size = None
            try:
                alerts_log_stat = config.ALERTS_LOG.stat() if config.ALERTS_LOG and config.ALERTS_LOG.exists() else None
                alerts_log_mtime = alerts_log_stat.st_mtime if alerts_log_stat else None
                alerts_log_size = alerts_log_stat.st_size if alerts_log_stat else None
            except OSError:
                alerts_log_mtime = None
                alerts_log_size = None

            if ((backup_log_mtime, backup_log_size) != (last_backup_log_mtime, last_backup_log_size) or
                (alerts_log_mtime, alerts_log_size) != (last_alerts_log_mtime, last_alerts_log_size)):
                last_backup_log_mtime = backup_log_mtime
                last_backup_log_size = backup_log_size
                last_alerts_log_mtime = alerts_log_mtime
                last_alerts_log_size = alerts_log_size
                try:
                    cached_data_snapshot = _snapshot_data()
                    _record_success('data')
                except Exception as e:
                    _record_error('data', e)
                    print(f"[collector] data snapshot error: {type(e).__name__}: {e}", file=sys.stderr, flush=True)

            _maybe_emit("data", cached_data_snapshot, last_hashes)

            try:
                backlog_mtime = config.AUDIT_BACKLOG_FILE.stat().st_mtime if config.AUDIT_BACKLOG_FILE.exists() else None
            except OSError:
                backlog_mtime = None
            if backlog_mtime != last_backlog_mtime:
                last_backlog_mtime = backlog_mtime
                try:
                    cached_backlog_snapshot = parse_audit_backlog()
                    _record_success('backlog')
                except Exception as e:
                    _record_error('backlog', e)
                    print(f"[collector] backlog snapshot error: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            _maybe_emit("backlog", cached_backlog_snapshot, last_hashes)

            fingerprint = _transcripts_fingerprint()
            if fingerprint != last_agents_fingerprint:
                last_agents_fingerprint = fingerprint
                try:
                    agents = get_fleet_agents()
                    # Wave-19: strip large prompt fields before broadcast
                    cached_agents_snapshot = sanitize_agents_for_broadcast(agents)
                    _record_success('agents')
                except Exception as e:
                    _record_error('agents', e)
                    print(f"[collector] agents snapshot error: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            _maybe_emit("agents", cached_agents_snapshot, last_hashes)

            # Emit tracker section (mtime+size-gated)
            try:
                tracker_stat = (config.STATE_DIR / "tracker.json").stat() if (config.STATE_DIR / "tracker.json").exists() else None
                tracker_mtime = tracker_stat.st_mtime if tracker_stat else None
                tracker_size = tracker_stat.st_size if tracker_stat else None
            except OSError:
                tracker_mtime = None
                tracker_size = None
            if (tracker_mtime, tracker_size) != (last_tracker_mtime, last_tracker_size):
                last_tracker_mtime = tracker_mtime
                last_tracker_size = tracker_size
                try:
                    cached_tracker_snapshot = _snapshot_tracker()
                    _record_success('tracker')
                except Exception as e:
                    _record_error('tracker', e)
                    print(f"[collector] tracker snapshot error: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            _maybe_emit("tracker", cached_tracker_snapshot, last_hashes)

            # Emit status section (mtime+size-gated)
            try:
                status_stat = (config.STATE_DIR / "orchestrator-status.json").stat() if (config.STATE_DIR / "orchestrator-status.json").exists() else None
                status_mtime = status_stat.st_mtime if status_stat else None
                status_size = status_stat.st_size if status_stat else None
            except OSError:
                status_mtime = None
                status_size = None
            if (status_mtime, status_size) != (last_status_mtime, last_status_size):
                last_status_mtime = status_mtime
                last_status_size = status_size
                try:
                    cached_status_snapshot = _snapshot_orchestrator_status()
                    _record_success('status')
                except Exception as e:
                    _record_error('status', e)
                    print(f"[collector] status snapshot error: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            _maybe_emit("status", cached_status_snapshot, last_hashes)

            # Emit cost section (mtime+size-gated on the outcomes ledger)
            try:
                cost_stat = config.LEDGER_FILE.stat() if config.LEDGER_FILE and config.LEDGER_FILE.exists() else None
                cost_mtime = cost_stat.st_mtime if cost_stat else None
                cost_size = cost_stat.st_size if cost_stat else None
            except OSError:
                cost_mtime = None
                cost_size = None
            if (cost_mtime, cost_size) != (last_cost_mtime, last_cost_size):
                last_cost_mtime = cost_mtime
                last_cost_size = cost_size
                try:
                    cached_cost_snapshot = cost.get_cost_summary()
                    _record_success('cost')
                except Exception as e:
                    _record_error('cost', e)
                    print(f"[collector] cost snapshot error: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            _maybe_emit("cost", cached_cost_snapshot, last_hashes)

            # Emit collector health snapshot
            with _collector_health_lock:
                _collector_health["last_successful_cycle"] = current_time
            health_snapshot = dict(_collector_health)
            _maybe_emit("collector_health", health_snapshot, last_hashes)

            # Drain inbox
            try:
                drain_tracker_inbox()
            except Exception as e:
                print(f"[collector] Inbox drain error: {e}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[collector_loop] Exception: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        stop_event.wait(config.COLLECTOR_INTERVAL)

def start_collector_thread():
    """Idempotently start the background collector daemon thread (safe to call from
    multiple request handlers / run_server — only the first call actually starts it)."""
    global _collector_started
    with _collector_lock:
        if _collector_started:
            return
        _collector_started = True
        t = threading.Thread(target=collector_loop, args=(_collector_stop_event,), daemon=True)
        t.start()
