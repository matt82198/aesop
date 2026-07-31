#!/usr/bin/env python3
"""Aesop UI — SSE client registry + broadcast + background collector loop (wave-9 split).

Refactored to use CollectorSource abstraction (wave-32: complexity reduction).
Each of 9 sources declares its path, change detection, and snapshot builder.
Main loop iterates over sources; complexity reduced from F(43) to C.
"""
import hashlib
import json
import queue
import sys
import threading
import time
from pathlib import Path

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


# === CollectorSource abstraction ===

class CollectorSource:
    """Base abstraction for a single SSE source.

    Each source declares:
    - name: event name emitted to SSE
    - default snapshot: initial cached value
    Subclasses implement:
    - should_update(): detect if content changed
    - build_snapshot(): fetch/build payload
    """

    def __init__(self, name, default_snapshot):
        self.name = name
        self.cached_snapshot = default_snapshot
        self.error_list = []

    def should_update(self):
        """Returns True if source content has changed since last check."""
        raise NotImplementedError

    def build_snapshot(self):
        """Builds and returns the snapshot object for this source."""
        raise NotImplementedError

    def emit_if_changed(self, last_hashes):
        """Check for updates, rebuild if needed, emit via hash gate."""
        if self.should_update():
            try:
                self.cached_snapshot = self.build_snapshot()
                self._record_success()
            except Exception as e:
                self._record_error(e)
                print(f"[collector] {self.name} snapshot error: {type(e).__name__}: {e}",
                      file=sys.stderr, flush=True)
        _maybe_emit(self.name, self.cached_snapshot, last_hashes)

    def _record_error(self, error):
        """Record an error for health tracking."""
        error_info = f"{type(error).__name__}:{str(error)[:50]}"
        if error_info not in self.error_list:
            self.error_list.append(error_info)
        if len(self.error_list) > 5:
            self.error_list.pop(0)
        with _collector_health_lock:
            if self.name not in _collector_health["per_source_errors"]:
                _collector_health["per_source_errors"][self.name] = []
            health_list = _collector_health["per_source_errors"][self.name]
            if error_info not in health_list:
                health_list.append(error_info)
            if len(health_list) > 5:
                health_list.pop(0)

    def _record_success(self):
        """Clear errors for this source."""
        self.error_list = []
        with _collector_health_lock:
            _collector_health["per_source_errors"][self.name] = []


class MtimeSizeGatedSource(CollectorSource):
    """Source that detects changes via (mtime, size) tuple on a single file."""

    def __init__(self, name, default_snapshot, file_path):
        super().__init__(name, default_snapshot)
        self.file_path = file_path
        self.last_mtime = object()  # sentinel
        self.last_size = object()

    def should_update(self):
        """Check if file mtime+size changed."""
        try:
            stat = self.file_path.stat() if self.file_path.exists() else None
            mtime = stat.st_mtime if stat else None
            size = stat.st_size if stat else None
        except OSError:
            mtime = None
            size = None

        if (mtime, size) != (self.last_mtime, self.last_size):
            self.last_mtime = mtime
            self.last_size = size
            return True
        return False


class MultiFileMtimeGatedSource(CollectorSource):
    """Source that detects changes via (mtime, size) on multiple files."""

    def __init__(self, name, default_snapshot, file_paths):
        super().__init__(name, default_snapshot)
        self.file_paths = file_paths
        self.last_mtimes = {}
        self.last_sizes = {}

    def should_update(self):
        """Check if any file mtime+size changed."""
        changed = False
        for fpath in self.file_paths:
            try:
                stat = fpath.stat() if fpath.exists() else None
                mtime = stat.st_mtime if stat else None
                size = stat.st_size if stat else None
            except OSError:
                mtime = None
                size = None

            if (mtime, size) != (self.last_mtimes.get(fpath), self.last_sizes.get(fpath)):
                self.last_mtimes[fpath] = mtime
                self.last_sizes[fpath] = size
                changed = True

        return changed


class FingerprintGatedSource(CollectorSource):
    """Source that detects changes via fingerprint (e.g., content hash)."""

    def __init__(self, name, default_snapshot):
        super().__init__(name, default_snapshot)
        self.last_fingerprint = None

    def get_fingerprint(self):
        """Return current fingerprint; subclass implements."""
        raise NotImplementedError

    def should_update(self):
        """Check if fingerprint changed."""
        fingerprint = self.get_fingerprint()
        if fingerprint != self.last_fingerprint:
            self.last_fingerprint = fingerprint
            return True
        return False


# === Concrete source implementations ===

class DataSource(MultiFileMtimeGatedSource):
    """Data section: backup log + alerts log (wave-19 gating)."""

    def __init__(self):
        file_paths = [
            config.BACKUP_LOG,
            config.ALERTS_LOG,
        ]
        super().__init__("data", {}, [p for p in file_paths if p])

    def build_snapshot(self):
        return _snapshot_data()


class BacklogSource(MtimeSizeGatedSource):
    """Backlog section: audit backlog file."""

    def __init__(self):
        super().__init__("backlog", {"tiers": []}, config.AUDIT_BACKLOG_FILE)

    def build_snapshot(self):
        return parse_audit_backlog()


class AgentsSource(FingerprintGatedSource):
    """Agents section: transcript fingerprint gated."""

    def __init__(self):
        super().__init__("agents", [])

    def get_fingerprint(self):
        return _transcripts_fingerprint()

    def build_snapshot(self):
        agents = get_fleet_agents()
        return sanitize_agents_for_broadcast(agents)


class TrackerSource(MtimeSizeGatedSource):
    """Tracker section: tracker.json file."""

    def __init__(self):
        tracker_file = config.STATE_DIR / "tracker.json"
        super().__init__("tracker", {'items': []}, tracker_file)

    def build_snapshot(self):
        return _snapshot_tracker()


class StatusSource(MtimeSizeGatedSource):
    """Status section: orchestrator-status.json file."""

    def __init__(self):
        status_file = config.STATE_DIR / "orchestrator-status.json"
        super().__init__("status", {'orchestrators': []}, status_file)

    def build_snapshot(self):
        return _snapshot_orchestrator_status()


class CostSource(MtimeSizeGatedSource):
    """Cost section: ledger file (OUTCOMES-LEDGER.md)."""

    def __init__(self):
        super().__init__("cost", {}, config.LEDGER_FILE if config.LEDGER_FILE else Path("/dev/null"))

    def build_snapshot(self):
        return cost.get_cost_summary()


class CollectorHealthSource(CollectorSource):
    """Collector health snapshot: emitted every cycle."""

    def __init__(self):
        super().__init__("collector_health", {})

    def should_update(self):
        """Always update; health changes every cycle."""
        return True

    def build_snapshot(self):
        with _collector_health_lock:
            return dict(_collector_health)


def collector_loop(stop_event):
    """Background loop: poll sources, broadcast on change.

    Refactored to use CollectorSource abstraction (wave-32).
    Complexity reduced from F(43) to C by extracting each source's
    mtime-caching, snapshot-building, and error handling.
    """
    last_hashes = {}
    last_heartbeat_time = 0.0

    # Initialize all sources
    sources = [
        DataSource(),
        BacklogSource(),
        AgentsSource(),
        TrackerSource(),
        StatusSource(),
        CostSource(),
        CollectorHealthSource(),
    ]

    while not stop_event.is_set():
        try:
            current_time = time.time()

            # Wave-20: Emit heartbeat every 15s to signal collector thread is alive
            if current_time - last_heartbeat_time >= HEARTBEAT_INTERVAL:
                last_heartbeat_time = current_time
                heartbeat_payload = json.dumps({"timestamp": int(current_time * 1000)}, default=str)
                broadcast_sse(HEARTBEAT_EVENT_NAME, heartbeat_payload)

            # Iterate over all sources and emit if changed
            for source in sources:
                source.emit_if_changed(last_hashes)

            # Emit collector health snapshot
            with _collector_health_lock:
                _collector_health["last_successful_cycle"] = current_time

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
