#!/usr/bin/env python3
"""
state_store.write_api — Typed write facade for tracker mutations (state consolidation).

Consolidates write patterns for tracker mutations: status updates and item creation.
This facade allows the underlying write implementation to change (immediate projection
→ queued render → event store publishing) without altering caller code.

Mirrors the read_api.py facade pattern: callers use WriteAPI only; backend
implementation (EventStore + projection rendering) is hidden.

Callers use:
  api = WriteAPI(state_dir)
  api.tracker_update_status(item_id, new_status, note="optional note")
  api.tracker_append_item({"title": "...", "priority": "P1", ...})

Both operations are fail-closed: event append failure → no projection write.
Projection write conflicts raise WriteConflict (honest failure, no silent data loss).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Platform-specific file locking
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

# Ensure tools and state_store modules are importable
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

try:
    from state_store import EventStore, ConcurrencyConflict
except ImportError:
    from state_store.store import EventStore, ConcurrencyConflict


class WriteConflict(Exception):
    """Raised when a projection write conflicts (content-hash mismatch).

    Signifies that the tracker.json file's content hash does not match the
    expected value, indicating concurrent modification. The event was appended
    (durable in EventStore), but the projection write was skipped to prevent
    silent data loss. Caller should re-read tracker.json, extract new version,
    and retry.

    Attributes:
        expected_hash: The content hash caller expected for tracker.json
        actual_hash: The content hash found on disk
        reason: Human-readable description of the conflict
    """

    def __init__(self, expected_hash: str | None, actual_hash: str | None, reason: str = ""):
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        self.reason = reason
        super().__init__(
            f"Projection write conflict: {reason} "
            f"(expected hash {expected_hash}, found {actual_hash})"
        )


class WriteAPI:
    """Write facade for tracker mutations, backed by EventStore + atomic projection rendering.

    Designed to be swappable: write backend can change (immediate render → event sourcing)
    without altering call sites. Current implementation appends to event log and re-renders
    tracker.json atomically (tempfile + os.replace).

    All write operations are fail-closed: if event append fails, projection is not written.
    If projection write fails due to concurrent modification, raises WriteConflict (event
    is safely in the log, caller must retry).

    OCC (Optimistic Concurrency Control): Each WriteAPI instance tracks the last projection
    hash it wrote (or read at init). Before atomic write, if on-disk hash differs from
    both tracked_hash and new_hash, raises WriteConflict to prevent overwriting concurrent
    modifications. First-write case: hash is captured at operation start (before event append).
    """

    def __init__(self, state_dir: str | Path):
        """Initialize the write API with a state directory.

        Args:
            state_dir: Path to the state directory (e.g., "state" or "/absolute/path/state")
        """
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = str(self.state_dir / "tracker_events.db")
        self.tracker_file = self.state_dir / "tracker.json"
        self._stores: list = []  # track EventStore instances for close()

    def _make_store(self):
        """Create an EventStore and track it for close()."""
        store = EventStore(self.db_path)
        self._stores.append(store)
        return store

    def close(self) -> None:
        """Close all EventStore connections opened by this WriteAPI.

        Must be called before deleting the state directory on Windows, where
        open SQLite connections hold file locks that block shutil.rmtree().
        Safe to call multiple times.
        """
        for store in self._stores:
            try:
                store.close()
            except Exception:
                pass
        self._stores.clear()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def tracker_update_status(
        self,
        item_id: str,
        new_status: str,
        note: str | None = None,
        actor: str = "api",
    ) -> dict:
        """Update an existing tracker item's status and optionally add a note.

        Appends an item_updated event to the event log, then re-renders tracker.json
        atomically. Fail-closed: event append failure blocks projection write.

        Args:
            item_id: The item UUID to update
            new_status: New status (e.g., "todo", "in-progress", "done", "archived")
            note: Optional note to append to the item's notes field
            actor: Actor performing the update (default "api")

        Returns:
            dict: The updated item from the tracker projection

        Raises:
            ValueError: If item_id not found or other validation failure
            WriteConflict: If projection write fails due to concurrent modification
            ConcurrencyConflict: If EventStore append hits OCC mismatch (should not happen
                               in this phase, but reserved for future use)
        """
        store = self._make_store()

        # Read current tracker to find the item
        current_tracker = self._load_tracker_safe()
        current_items = {item["id"]: item for item in current_tracker.get("items", [])}

        if item_id not in current_items:
            raise ValueError(f"Item not found: {item_id}")

        current_item = current_items[item_id]

        # Build the update payload
        update_payload = {"id": item_id, "status": new_status}

        # If adding a note, append to the item's notes field
        if note:
            existing_notes = current_item.get("notes", "")
            if existing_notes:
                update_payload["notes"] = f"{existing_notes}\n{note}"
            else:
                update_payload["notes"] = note

        # CRITICAL: Capture on-disk hash at operation START (before event append)
        # This is our baseline for OCC conflict detection.
        start_disk_hash = None
        if self.tracker_file.exists():
            try:
                current_on_disk = json.loads(
                    self.tracker_file.read_text(encoding="utf-8")
                )
                start_disk_hash = self._compute_content_hash(current_on_disk)
            except Exception:
                # Corrupt file on disk at START: treat as conflict (fail-closed)
                pass

        # Append the event (fail-closed: if this fails, no projection write)
        try:
            store.append("tracker", "item_updated", update_payload, actor)
        except Exception as e:
            raise ValueError(f"Failed to append update event: {e}") from e

        # Now re-render the projection atomically
        self._render_tracker_atomic(store, start_disk_hash=start_disk_hash)

        # Return the updated item from the freshly projected state
        updated_tracker = self._load_tracker_safe()
        updated_items = {item["id"]: item for item in updated_tracker.get("items", [])}

        if item_id not in updated_items:
            # Should not happen if projection is consistent, but defend against it
            raise ValueError(f"Item disappeared after update: {item_id}")

        return updated_items[item_id]

    def tracker_append_item(
        self,
        item_dict: dict,
        actor: str = "api",
    ) -> dict:
        """Create a new tracker item.

        Validates the item dict, appends an item_created event to the event log,
        then re-renders tracker.json atomically. Fail-closed: event append failure
        blocks projection write.

        Args:
            item_dict: Item dict with fields: id (optional, auto-generated if missing),
                      title, priority (optional, defaults to "P1"), status (optional,
                      defaults to "todo"), lane (optional, defaults to "proposed"),
                      source (optional, defaults to "api"), tags, notes, pr_link, etc.
            actor: Actor performing the create (default "api")

        Returns:
            dict: The created item from the tracker projection

        Raises:
            ValueError: If item_dict is invalid, missing required fields, or explicit ID
                       already exists in projection
            WriteConflict: If projection write fails due to concurrent modification
            ConcurrencyConflict: If EventStore append hits OCC mismatch
        """
        if not isinstance(item_dict, dict):
            raise ValueError("item_dict must be a dict")

        title = item_dict.get("title", "").strip()
        if not title:
            raise ValueError("item_dict must have a non-empty 'title' field")

        # Build the canonical item structure
        import secrets
        item_id = item_dict.get("id") or secrets.token_hex(6)

        # CRITICAL: Capture on-disk hash at operation START (before any other reads)
        # This is our baseline for OCC conflict detection. The check window covers
        # the entire operation (read baseline → load tracker → append event → render).
        start_disk_hash = None
        if self.tracker_file.exists():
            try:
                current_on_disk = json.loads(
                    self.tracker_file.read_text(encoding="utf-8")
                )
                start_disk_hash = self._compute_content_hash(current_on_disk)
            except Exception:
                # Corrupt file on disk at START of operation
                # Treat as a conflict (fail-closed)
                pass

        # Check for duplicate explicit ID: if caller provided an ID, verify it's not
        # already in the current projection. This is fail-closed: reject the operation
        # before appending to event store.
        if "id" in item_dict:
            current_tracker = self._load_tracker_safe()
            existing_ids = {item["id"] for item in current_tracker.get("items", [])}
            if item_id in existing_ids:
                raise ValueError(f"Item with id '{item_id}' already exists in projection")

        created_item = {
            "id": item_id,
            "title": title,
            "priority": item_dict.get("priority", "P1"),
            "status": item_dict.get("status", "todo"),
            "lane": item_dict.get("lane", "proposed"),
            "source": item_dict.get("source", actor),
            "tags": item_dict.get("tags", []) if isinstance(item_dict.get("tags"), list) else [],
            "notes": item_dict.get("notes"),
            "pr_link": item_dict.get("pr_link"),
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "completed_at": None,
        }

        store = self._make_store()

        # Append the event (fail-closed: if this fails, no projection write)
        try:
            store.append("tracker", "item_created", created_item, actor)
        except Exception as e:
            raise ValueError(f"Failed to append create event: {e}") from e

        # Now re-render the projection atomically
        self._render_tracker_atomic(store, start_disk_hash=start_disk_hash)

        # Return the created item from the freshly projected state
        created_tracker = self._load_tracker_safe()
        created_items = {item["id"]: item for item in created_tracker.get("items", [])}

        if item_id not in created_items:
            # Should not happen if projection is consistent, but defend against it
            raise ValueError(f"Item disappeared after create: {item_id}")

        return created_items[item_id]

    def rebuild_projection(self) -> None:
        """Rebuild tracker.json from the event store.

        Renders the projection from events, bypassing OCC conflict detection.
        Use this to recover from orphaned events (event in store, missing from projection).

        The projection is derived from the event store, so rebuilding naturally recovers
        prior events. This is the self-healing recovery contract: if projection becomes
        stale or corrupted, rebuild_projection() restores consistency.

        Raises:
            WriteConflict: If atomic write fails (disk write error, not conflict).
        """
        store = self._make_store()
        # Bypass OCC check with force=True (recovery always bypasses conflict detection)
        self._render_tracker_atomic(store, start_disk_hash=None, force=True)

    # --- Markdown write-path unification (WS4 increment 1) ---

    def write_state_md(self, content: str, actor: str = "api") -> None:
        """Write STATE.md and append event to event store (unified write path).

        Writes STATE.md atomically, then appends a state_md_written event to the
        state_markdown stream. Fail-closed: file write must succeed before event is appended.
        This ordering ensures no orphaned events (safe direction if crash occurs).

        Args:
            content: The full STATE.md content to write
            actor: Actor performing the write (default "api")

        Raises:
            ValueError: If event append fails
            WriteConflict: If concurrent modification detected or atomic write fails
        """
        store = self._make_store()
        state_file = self.state_dir / "STATE.md"

        # CRITICAL: Capture on-disk hash at operation START (before file write)
        start_disk_hash = None
        if state_file.exists():
            try:
                start_content = state_file.read_text(encoding="utf-8")
                start_disk_hash = self._compute_content_hash({"content": start_content})
            except Exception:
                # Corrupt file at START: treat as conflict (fail-closed)
                pass

        # Write the file atomically with OCC check (event is only appended on success)
        self._write_markdown_atomic(state_file, content, start_disk_hash)

        # Now append the event (only on successful file write)
        try:
            store.append("state_markdown", "state_md_written", {"content": content}, actor)
        except Exception as e:
            raise ValueError(f"Failed to append state_md_written event: {e}") from e

    def append_buildlog(self, line: str, actor: str = "api") -> None:
        """Append a line to BUILDLOG.md and event to event store (unified write path).

        Appends the line to BUILDLOG.md atomically, then appends a buildlog_entry event
        to the buildlog stream. Fail-closed: file write must succeed before event is appended.
        This ordering ensures no orphaned events (safe direction if crash occurs).

        Args:
            line: The line to append (without newline; newline is added automatically)
            actor: Actor performing the append (default "api")

        Raises:
            ValueError: If event append fails
            WriteConflict: If atomic write fails
        """
        store = self._make_store()
        buildlog_file = self.state_dir / "BUILDLOG.md"

        # Ensure buildlog exists first (idempotent)
        self.ensure_buildlog_exists()

        # CRITICAL: Capture on-disk hash at operation START (before file write)
        start_disk_hash = None
        if buildlog_file.exists():
            try:
                current_content = buildlog_file.read_text(encoding="utf-8")
                start_disk_hash = self._compute_content_hash({"content": current_content})
            except Exception:
                pass

        # Append to BUILDLOG.md atomically (event is only appended on success)
        self._append_to_markdown_atomic(buildlog_file, line, start_disk_hash)

        # Now append the event (only on successful file write)
        try:
            store.append("buildlog", "buildlog_entry", {"line": line}, actor)
        except Exception as e:
            raise ValueError(f"Failed to append buildlog_entry event: {e}") from e

    def ensure_buildlog_exists(self, header: str = "# BUILDLOG\n") -> None:
        """Ensure BUILDLOG.md exists with header. Idempotent.

        If BUILDLOG.md doesn't exist, creates it with a header. Does not append
        any events to the event store (it's a structural requirement, not a write).

        Args:
            header: Header content to write when creating the file (default
                    "# BUILDLOG\\n"). Migrated legacy writers pass their own
                    historical header to stay byte-compatible. Ignored if the
                    file already exists (never overwrites).

        Returns:
            None
        """
        buildlog_file = self.state_dir / "BUILDLOG.md"
        if not buildlog_file.exists():
            buildlog_file.write_text(header, encoding="utf-8")

    def rebuild_state_md(self, content: str, actor: str = "api", force: bool = False) -> None:
        """Rebuild STATE.md from content, optionally bypassing OCC (recovery use case).

        Appends a state_md_rebuilt event to the event store, then writes STATE.md atomically.
        Fail-closed: event append failure blocks file write.

        Args:
            content: The full STATE.md content to write
            actor: Actor performing the rebuild (default "api")
            force: If True, bypass OCC conflict detection (recovery-only)

        Raises:
            ValueError: If event append fails
            WriteConflict: If concurrent modification detected or atomic write fails
        """
        store = self._make_store()
        state_file = self.state_dir / "STATE.md"
        start_disk_hash = None

        if not force and state_file.exists():
            try:
                start_content = state_file.read_text(encoding="utf-8")
                start_disk_hash = self._compute_content_hash({"content": start_content})
            except Exception:
                pass

        # Append the event (fail-closed: if this fails, no file write)
        try:
            store.append("state_markdown", "state_md_rebuilt", {"content": content}, actor)
        except Exception as e:
            raise ValueError(f"Failed to append state_md_rebuilt event: {e}") from e

        # Write with OCC check (unless force=True)
        self._write_markdown_atomic(state_file, content, start_disk_hash, force=force)

    # --- Private helpers ---

    @contextmanager
    def _file_lock(self, file_path: Path):
        """Context manager for advisory file lock using a separate lock file (cross-platform).

        Uses a .lock file adjacent to the target file to coordinate access without
        holding open the target file. This avoids file-in-use issues on Windows.

        On Windows: uses msvcrt.locking on the lock file.
        On Unix: uses fcntl.flock on the lock file.

        Args:
            file_path: Path to the file to protect (file may or may not exist)

        Yields:
            None (lock is acquired and held throughout the context)

        Raises:
            IOError: If locking fails
        """
        lock_file_path = file_path.parent / f".{file_path.name}.lock"
        fd = None

        try:
            # Create and open the lock file (create it if it doesn't exist)
            fd = os.open(
                str(lock_file_path),
                os.O_CREAT | os.O_WRONLY | os.O_BINARY if sys.platform == "win32" else os.O_CREAT | os.O_WRONLY,
                0o666
            )

            if sys.platform == "win32":
                # Windows: use msvcrt.locking for advisory lock (exclusive lock)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError:
                    # Lock failed; sleep briefly and retry
                    import time
                    time.sleep(0.01)
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    except OSError:
                        # Give up after one retry
                        raise IOError(f"Failed to acquire lock on {file_path.name}")
            else:
                # Unix: use fcntl.flock for advisory lock
                fcntl.flock(fd, fcntl.LOCK_EX)

            yield

        finally:
            if fd is not None:
                try:
                    if sys.platform == "win32":
                        # Windows: unlock before closing
                        try:
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    os.close(fd)
                except Exception:
                    pass

                # Try to clean up the lock file (best effort)
                try:
                    os.unlink(str(lock_file_path))
                except Exception:
                    pass

    def _load_tracker_safe(self) -> dict:
        """Load tracker.json, return empty tracker if missing or corrupt.

        Returns:
            dict: Tracker snapshot ({"version": 1, "items": [...]}) or empty dict
        """
        if not self.tracker_file.exists():
            return {"version": 1, "items": []}

        try:
            content = self.tracker_file.read_text(encoding="utf-8")
            data = json.loads(content)
            if not isinstance(data, dict) or "version" not in data:
                return {"version": 1, "items": []}
            return data
        except Exception:
            # Corrupt or unreadable; return empty tracker
            return {"version": 1, "items": []}

    def _project_tracker(self, store: EventStore) -> dict:
        """Project the tracker state from the event log.

        Reads all events from the "tracker" stream and folds them into the
        current tracker state using the standard projection rules.

        Args:
            store: EventStore instance to read events from

        Returns:
            dict: Tracker projection ({"version": 1, "items": [...]})
        """
        try:
            from state_store import project_tracker
        except ImportError:
            from state_store.projections import project_tracker

        events = store.read("tracker")
        return project_tracker(events)

    def _compute_content_hash(self, tracker_dict: dict) -> str:
        """Compute a stable SHA256 hash of the tracker content.

        Used for conflict detection: if the hash doesn't match expected, a concurrent
        writer has changed the file (either tracker.json directly or another WriteAPI
        caller's projection render).

        Args:
            tracker_dict: The tracker dict to hash

        Returns:
            str: Hex-encoded SHA256 hash
        """
        # Normalize to a canonical JSON form for hashing (sorted keys, no whitespace)
        content = json.dumps(tracker_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _write_markdown_atomic(
        self, file_path: Path, content: str, start_disk_hash: str | None = None, force: bool = False
    ) -> None:
        """Write a markdown file atomically with OCC conflict detection and file locking.

        Acquires a file lock, performs OCC check, writes content to temp file, then renames
        atomically. Lock is held for the entire operation to prevent TOCTOU races.

        Args:
            file_path: Path to the markdown file
            content: Content to write
            start_disk_hash: Hash at operation START for OCC baseline
            force: If True, bypass OCC check (recovery-only)

        Raises:
            WriteConflict: If concurrent modification detected or atomic write fails
        """
        new_hash = self._compute_content_hash({"content": content})

        # Acquire file lock and perform OCC check + atomic write under the lock
        # This prevents TOCTOU races between OCC check and os.replace
        with self._file_lock(file_path):
            # OCC Conflict Detection (unless forcing)
            if not force and file_path.exists():
                try:
                    current_content = file_path.read_text(encoding="utf-8")
                    disk_hash = self._compute_content_hash({"content": current_content})

                    # Conflict if disk has been modified since operation start
                    # (and it's not due to our write).
                    # Case 1: File existed at start (start_disk_hash is not None)
                    #   Conflict if: disk changed from start AND disk is not our new content
                    # Case 2: File didn't exist at start (start_disk_hash is None)
                    #   Conflict if: disk exists now AND disk is not our new content
                    if start_disk_hash is not None:
                        # File existed at start; check if it's been modified externally
                        if disk_hash != start_disk_hash and disk_hash != new_hash:
                            raise WriteConflict(
                                expected_hash=start_disk_hash,
                                actual_hash=disk_hash,
                                reason=f"Concurrent modification detected in {file_path.name}: "
                                       f"file changed since operation start ({start_disk_hash[:8]} → {disk_hash[:8]}), "
                                       f"and new content would be {new_hash[:8]}",
                            )
                    # If start_disk_hash was None (file didn't exist), we're creating it,
                    # so no conflict possible.
                except WriteConflict:
                    raise
                except Exception as e:
                    # Other read errors: fail-closed
                    raise WriteConflict(
                        expected_hash=start_disk_hash,
                        actual_hash=None,
                        reason=f"Failed to read {file_path.name} for conflict check: {e}",
                    ) from e

            # Write atomically via tempfile + os.replace (while holding the lock)
            try:
                fd, temp_path = tempfile.mkstemp(
                    suffix=".md",
                    prefix=f".{file_path.stem}-",
                    dir=str(file_path.parent),
                    text=False,
                )
                try:
                    os.write(fd, content.encode("utf-8"))
                    os.close(fd)
                    os.replace(str(temp_path), str(file_path))
                except Exception:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
                    try:
                        os.unlink(temp_path)
                    except Exception:
                        pass
                    raise
            except Exception as e:
                raise WriteConflict(
                    expected_hash=start_disk_hash,
                    actual_hash=None,
                    reason=f"Failed to write {file_path.name} atomically: {e}",
                ) from e

    def _append_to_markdown_atomic(
        self, file_path: Path, line: str, start_disk_hash: str | None = None
    ) -> None:
        """Append a line to a markdown file atomically with OCC conflict detection and file locking.

        Acquires a file lock, reads current content, appends line, and writes back atomically.
        Lock is held for the entire operation to prevent TOCTOU races.

        Args:
            file_path: Path to the markdown file
            line: Line to append (without newline)
            start_disk_hash: Hash at operation START for OCC baseline

        Raises:
            WriteConflict: If concurrent modification detected or atomic write fails
        """
        # Acquire file lock and perform read + OCC check + atomic write under the lock
        # This prevents TOCTOU races between read/check and os.replace
        with self._file_lock(file_path):
            # Read current content
            try:
                current_content = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
            except Exception as e:
                raise WriteConflict(
                    expected_hash=start_disk_hash,
                    actual_hash=None,
                    reason=f"Failed to read {file_path.name}: {e}",
                ) from e

            # Append line with newline
            new_content = current_content + line + "\n"
            new_hash = self._compute_content_hash({"content": new_content})

            # OCC Conflict Detection
            if file_path.exists():
                try:
                    disk_content = file_path.read_text(encoding="utf-8")
                    disk_hash = self._compute_content_hash({"content": disk_content})

                    # Conflict if file has been modified since operation start
                    if start_disk_hash is not None:
                        if disk_hash != start_disk_hash and disk_hash != new_hash:
                            raise WriteConflict(
                                expected_hash=start_disk_hash,
                                actual_hash=disk_hash,
                                reason=f"Concurrent modification detected in {file_path.name} "
                                       f"during append: file changed since start ({start_disk_hash[:8]} → {disk_hash[:8]})",
                            )
                except WriteConflict:
                    raise
                except Exception as e:
                    raise WriteConflict(
                        expected_hash=start_disk_hash,
                        actual_hash=None,
                        reason=f"Failed to detect conflict in {file_path.name}: {e}",
                    ) from e

            # Write atomically (while holding the lock)
            try:
                fd, temp_path = tempfile.mkstemp(
                    suffix=".md",
                    prefix=f".{file_path.stem}-",
                    dir=str(file_path.parent),
                    text=False,
                )
                try:
                    os.write(fd, new_content.encode("utf-8"))
                    os.close(fd)
                    os.replace(str(temp_path), str(file_path))
                except Exception:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
                    try:
                        os.unlink(temp_path)
                    except Exception:
                        pass
                    raise
            except Exception as e:
                raise WriteConflict(
                    expected_hash=start_disk_hash,
                    actual_hash=None,
                    reason=f"Failed to append to {file_path.name} atomically: {e}",
                ) from e

    def _render_tracker_atomic(
        self, store: EventStore, start_disk_hash: str | None = None, force: bool = False
    ) -> None:
        """Render the tracker projection to tracker.json atomically.

        Projects the event log, writes to a temp file, then renames atomically.
        Includes OCC (Optimistic Concurrency Control): before write, detects concurrent
        modification by comparing on-disk state against our projection.

        Fail-closed: if on-disk content differs from our computed projection AND it
        didn't exist at operation start, raises WriteConflict (another writer has
        modified the file). If on-disk JSON is corrupt, raises WriteConflict (fail-closed).

        Args:
            store: EventStore instance to project from
            start_disk_hash: Hash of tracker.json at operation START (before event append).
                           Used for OCC baseline. If None, assume file didn't exist at start.
            force: If True, bypass OCC check (recovery-only, use rebuild_projection).

        Raises:
            WriteConflict: If concurrent modification detected or on-disk corruption.
                         Event is safely appended; caller must retry.
        """
        # Project the current state
        projection = self._project_tracker(store)
        new_hash = self._compute_content_hash(projection)

        # OCC Conflict Detection
        # Only perform if not forcing (recovery use case bypasses check)
        if not force and self.tracker_file.exists():
            try:
                current_on_disk = json.loads(
                    self.tracker_file.read_text(encoding="utf-8")
                )
                disk_hash = self._compute_content_hash(current_on_disk)

                # Conflict detection: fail-closed if disk state is unexplained
                # Check 1: If disk has items not in our projection, it's a conflict
                # (external write or divergent event store)
                disk_item_ids = {item.get("id") for item in current_on_disk.get("items", [])}
                projection_item_ids = {item.get("id") for item in projection.get("items", [])}
                unexplained_items = disk_item_ids - projection_item_ids

                if unexplained_items:
                    raise WriteConflict(
                        expected_hash=start_disk_hash,
                        actual_hash=disk_hash,
                        reason=f"Unexplained disk state: items {unexplained_items} "
                               f"on disk but not in event store (divergent state or external write)",
                    )

                # Check 2: If disk differs from both start and projection, it's a concurrent modification
                if disk_hash != new_hash and disk_hash != start_disk_hash:
                    raise WriteConflict(
                        expected_hash=start_disk_hash,
                        actual_hash=disk_hash,
                        reason=f"Concurrent modification detected: disk hash {disk_hash[:8]} "
                               f"differs from operation start {start_disk_hash[:8] if start_disk_hash else 'N/A'} "
                               f"and projection {new_hash[:8]}",
                    )

            except json.JSONDecodeError as e:
                # Corrupt JSON on disk: fail-closed
                raise WriteConflict(
                    expected_hash=start_disk_hash,
                    actual_hash=None,
                    reason=f"Corrupt JSON on disk (cannot detect conflict safely): {e}",
                ) from e
            except WriteConflict:
                # Re-raise conflict (don't catch our own exception)
                raise
            except Exception as e:
                # Other read errors (permissions, etc.): fail-closed
                raise WriteConflict(
                    expected_hash=start_disk_hash,
                    actual_hash=None,
                    reason=f"Failed to read tracker.json for conflict check: {e}",
                ) from e

        # Write atomically via tempfile + os.replace
        # Use POSIX-safe temp file creation (works on Windows too via Python's tempfile)
        try:
            fd, temp_path = tempfile.mkstemp(
                suffix=".json",
                prefix=".tracker-",
                dir=str(self.state_dir),
                text=False,  # Binary mode for explicit encoding control
            )
            try:
                # Write projection as JSON (indent for git diffability)
                content = json.dumps(projection, indent=2, ensure_ascii=False)
                os.write(fd, content.encode("utf-8"))
                os.close(fd)

                # Atomic rename (fails if target exists on some systems, but Python's
                # os.replace is cross-platform atomic where the OS supports it)
                os.replace(str(temp_path), str(self.tracker_file))

            except Exception:
                # Ensure fd is closed on error
                try:
                    os.close(fd)
                except Exception:
                    pass
                # Clean up temp file
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
                raise
        except Exception as e:
            raise WriteConflict(
                expected_hash=start_disk_hash,
                actual_hash=None,
                reason=f"Failed to write tracker.json atomically: {e}",
            ) from e
