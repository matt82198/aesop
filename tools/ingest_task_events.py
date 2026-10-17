#!/usr/bin/env python3
"""Tail-ingest the brain's task-events.jsonl into the state store.

INDEX: Idempotent tail-ingest of the brain's task-events.jsonl into the state store `task` stream (state_store WriteAPI/StateAPI, tracker_events.db); cursor file + event_id dedupe; fail-closed on malformed lines; --check asserts cursor == EOF.

Source: ~/.claude/state/task-events.jsonl, appended by the brain hook
~/.claude/hooks/task-event-projector.mjs (owned by another lane; this tool only
consumes the JSONL). One JSON object per line:
  {"ts", "session_id", "agent_id", "agent_type", "stop_reason", "summary", "context_used_pct"}

Behavior:
  - Each complete line becomes one event on stream "task", type "task_event",
    payload = the record + "event_id" + "kind". event_id = sha256 of
    "<byte offset>:" + raw line. The schema has no event id, and two
    byte-identical lines at distinct offsets are distinct events. Rows written
    before this key (event_id = sha256 of the raw line alone) still suppress
    replay, so a lost cursor cannot double-ingest old data.
  - kind = "internal" for harness-internal stops (see INTERNAL_SUMMARY_PATTERNS
    below: records with empty agent_type whose summary is user-voice or a
    slash-command echo); kind = "agent" otherwise.
  - Each agent-kind record also appends one lifecycle event to stream
    "agent_lifecycle" (payload agent_id, timestamp = record ts): agent_done, or
    agent_stalled when stop_reason contains "error" or is max_tokens/refusal.
    Deduped on a key derived from the task event_id, so a re-run (even with a
    lost cursor) adds no lifecycle row twice. Internal records get none.
  - Idempotent: a cursor file (byte offset) skips lines already consumed, and
    event_id dedupe against the stream prevents duplicates even if the cursor
    write was lost after the appends.
  - Malformed line, last in the file (nothing after its newline): fail closed.
    It is counted, the cursor is held before it, and the run exits 3. This is
    ambiguous (a slow writer may still be finishing it), so it is not
    quarantined. Once newer bytes follow it, it is quarantined on the next run.
  - Malformed line with bytes after it (a torn record glued to the next one,
    so it can never parse): quarantined. The raw line, byte offset, timestamp
    and parse error go to <source>.quarantine.jsonl, the cursor advances past
    it, and the run still exits 0. Quarantine rows are keyed by offset, so a
    lost cursor cannot quarantine the same line twice. A glued record is
    preserved only as raw text in the quarantine file, not ingested.
  - A trailing partial line (hook mid-write, no newline) is held for next run.
  - A JSONL that shrank below the cursor exits 3 (fail closed, nothing written).
  - Heartbeat: state/.task-ingest-heartbeat (epoch seconds) is written only on
    a clean run (exit 0, quarantined lines allowed), per the completed-cycle
    heartbeat contract. The receipt line on stdout reports quarantined=N.

Exit codes: 0 clean (quarantined lines included); 1 --check failed (pending
lines) or --status STALE; 2 usage/IO error; 3 held malformed last line or
truncated source.

--max-bytes N bounds one run to the complete lines that start within the next N
bytes of the JSONL (0 = unbounded). The cursor advances only over what was read,
so repeated bounded runs catch up without re-ingesting.

--status is read-only. It prints one INGEST line with the cursor, source size,
lag bytes and heartbeat age, and exits 1 with the word STALE when the heartbeat
is older than STALE_HEARTBEAT_SECONDS (900), the lag exceeds STALE_LAG_BYTES
(256 KiB), the heartbeat is missing, or the cursor is past the source end. A
missing source is NO-SOURCE, not stale (no brain hook on this box).

Usage:
  python tools/ingest_task_events.py [--jsonl PATH] [--state-dir DIR] [--max-bytes N]
  python tools/ingest_task_events.py --check [--jsonl PATH] [--state-dir DIR]
  python tools/ingest_task_events.py --status [--jsonl PATH] [--state-dir DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from state_store.api import StateAPI  # noqa: E402
from state_store.paths import resolve_db_path  # noqa: E402

STREAM = "task"
EVENT_TYPE = "task_event"
ACTOR = "task_ingest"
# Each lane task_event also appends one lifecycle event to LIFECYCLE_STREAM, folded
# by state_store.projections.project_agent_lifecycle (agent_done / agent_stalled).
LIFECYCLE_STREAM = "agent_lifecycle"
LIFECYCLE_ACTOR = "task_ingest"
# Non-"error" stop reasons that still mean the lane ended abnormally.
ABNORMAL_STOP_REASONS = frozenset({"max_tokens", "refusal"})
CURSOR_FILENAME = "task_events.cursor.json"
HEARTBEAT_FILENAME = ".task-ingest-heartbeat"
DEFAULT_JSONL = os.environ.get("AESOP_TASK_EVENTS_JSONL") or os.path.join(
    os.path.expanduser("~"), ".claude", "state", "task-events.jsonl"
)

EXIT_OK = 0
EXIT_CHECK_PENDING = 1
EXIT_USAGE = 2
EXIT_FAIL_CLOSED = 3

# Freshness thresholds for status(). The heartbeat is written on every clean run,
# and the watchdog runs the ingest every 150s (loop) or every few minutes (--once).
STALE_HEARTBEAT_SECONDS = 15 * 60
STALE_LAG_BYTES = 256 * 1024

# Harness-internal helper stops (prompt-suggestion echoes) carry agent_type ""
# AND a summary in user voice or a slash-command echo. Verified 2026-10-15 on
# the live JSONL: these 9 records are the only empty-type summaries matching.
# agent_type "" alone is NOT a sufficient signal (1273 of 1375 records have it,
# including real lane work), so the classifier requires both conditions.
INTERNAL_SUMMARY_PATTERNS = (
    re.compile(r"^/[A-Za-z0-9_:-]*$"),                      # "/mcp"
    re.compile(r"^The user\b", re.IGNORECASE),              # "The user has been frustrated..."
    re.compile(r"^chrome is connected now\b", re.IGNORECASE),
    re.compile(r"^ok retry\b", re.IGNORECASE),
)

REQUIRED_FIELDS = ("ts", "agent_id")


def classify_kind(record: dict) -> str:
    """Return "internal" or "agent" for a parsed task-events record."""
    agent_type = record.get("agent_type")
    if agent_type not in (None, ""):
        return "agent"
    summary = (record.get("summary") or "").strip()
    for pattern in INTERNAL_SUMMARY_PATTERNS:
        if pattern.search(summary):
            return "internal"
    return "agent"


def lifecycle_event_type(record: dict) -> str:
    """Return "agent_stalled" when the stop_reason signals an error, else "agent_done"."""
    stop_reason = record.get("stop_reason")
    if isinstance(stop_reason, str):
        lowered = stop_reason.strip().lower()
        if "error" in lowered or lowered in ABNORMAL_STOP_REASONS:
            return "agent_stalled"
    return "agent_done"


QUARANTINE_SUFFIX = ".quarantine.jsonl"


def _event_id(raw_line: bytes, offset: int) -> str:
    return hashlib.sha256(f"{offset}:".encode("ascii") + raw_line).hexdigest()


def _legacy_event_id(raw_line: bytes) -> str:
    return hashlib.sha256(raw_line).hexdigest()


def _lifecycle_event_id(task_event_id: str) -> str:
    """Dedupe key for the lifecycle row of one task event (derived, so replay-safe)."""
    return hashlib.sha256(f"{LIFECYCLE_STREAM}:{task_event_id}".encode("ascii")).hexdigest()


def _append_lifecycle(api, record: dict, task_event_id: str, seen_lifecycle: set, result: dict) -> None:
    """Append the agent lifecycle row for one agent-kind task event, at most once."""
    event_id = _lifecycle_event_id(task_event_id)
    if event_id in seen_lifecycle:
        return
    payload = {
        "agent_id": record["agent_id"],
        "timestamp": record["ts"],
        "stop_reason": record.get("stop_reason"),
        "task_event_id": task_event_id,
        "event_id": event_id,
    }
    api.append(LIFECYCLE_STREAM, lifecycle_event_type(record), payload, LIFECYCLE_ACTOR)
    seen_lifecycle.add(event_id)
    result["lifecycle"] += 1


def _quarantine_path(jsonl_path: Path) -> Path:
    return jsonl_path.with_name(jsonl_path.name + QUARANTINE_SUFFIX)


def _quarantined_offsets(path: Path) -> set:
    if not path.exists():
        return set()
    offsets = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            offsets.add(json.loads(line)["offset"])
    return offsets


def _append_quarantine(path: Path, rows: list) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _read_cursor(cursor_path: Path) -> int:
    if not cursor_path.exists():
        return 0
    data = json.loads(cursor_path.read_text(encoding="utf-8"))
    return int(data.get("offset", 0))


def _write_cursor(cursor_path: Path, offset: int, source: str) -> None:
    tmp = cursor_path.with_suffix(".json.tmp")
    body = {
        "offset": offset,
        "source": source,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, cursor_path)


def _scan(jsonl_path: Path, offset: int, max_bytes: int = 0):
    """Split the unconsumed tail into complete lines.

    Returns (complete, size) where complete is a list of
    (start_offset, end_offset, raw_bytes_without_newline, parse_error_or_None,
    record_or_None). A trailing partial line (no newline yet) is excluded.
    size is the true source size. max_bytes > 0 reads at most that many bytes
    past offset, so only lines that end inside the window are returned.
    """
    with open(jsonl_path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        fh.seek(offset)
        blob = fh.read(max_bytes) if max_bytes > 0 else fh.read()
    complete = []
    start = 0
    while True:
        nl = blob.find(b"\n", start)
        if nl == -1:
            break
        raw = blob[start:nl].rstrip(b"\r")
        record, error = _parse_line(raw)
        complete.append((offset + start, offset + nl + 1, raw, error, record))
        start = nl + 1
    return complete, size


def _parse_line(raw: bytes):
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"unparseable JSON: {exc}"
    if not isinstance(record, dict):
        return None, "record is not a JSON object"
    for field in REQUIRED_FIELDS:
        if not isinstance(record.get(field), str) or not record.get(field):
            return None, f"missing required field {field!r}"
    return record, None


def check(jsonl_path, state_dir) -> int:
    """Return 0 iff every complete line in the JSONL is consumed (cursor at EOF) and none are malformed."""
    jsonl_path = Path(jsonl_path)
    state_dir = Path(state_dir)
    if not jsonl_path.exists():
        print(f"task-events check: source missing: {jsonl_path}", file=sys.stderr)
        return EXIT_CHECK_PENDING
    offset = _read_cursor(state_dir / CURSOR_FILENAME)
    size = jsonl_path.stat().st_size
    if offset > size:
        print(f"task-events check: FAILED source shrank below cursor ({size} < {offset})", file=sys.stderr)
        return EXIT_CHECK_PENDING
    complete, _ = _scan(jsonl_path, offset)
    pending = len(complete)
    malformed = sum(1 for _, _, _, err, _ in complete if err)
    if pending == 0:
        print(f"task-events check: OK cursor={offset} source_bytes={size}")
        return EXIT_OK
    print(f"task-events check: PENDING lines={pending} malformed={malformed} cursor={offset} source_bytes={size}", file=sys.stderr)
    return EXIT_CHECK_PENDING


def status(jsonl_path, state_dir, now=None, stale_after_s=STALE_HEARTBEAT_SECONDS,
           lag_cap_bytes=STALE_LAG_BYTES) -> dict:
    """Read-only freshness report for the ingest. Writes nothing.

    STALE when any of: heartbeat missing or unreadable; heartbeat age > stale_after_s
    (age exactly equal to the threshold is fresh); lag (source bytes minus cursor)
    > lag_cap_bytes; cursor past the source end. A missing source is NO-SOURCE and
    not stale. Raises on an unreadable cursor file (callers fail closed).
    """
    jsonl_path = Path(jsonl_path)
    state_dir = Path(state_dir)
    now = time.time() if now is None else now
    if not jsonl_path.exists():
        return {
            "state": "NO-SOURCE", "stale": False, "source": str(jsonl_path), "source_bytes": None,
            "cursor": None, "lag_bytes": None, "heartbeat_age_s": None, "reasons": [],
            "line": f"INGEST: NO-SOURCE (no task-events source at {jsonl_path})",
        }
    size = jsonl_path.stat().st_size
    cursor = _read_cursor(state_dir / CURSOR_FILENAME)
    lag = size - cursor
    reasons = []
    if cursor > size:
        reasons.append(f"cursor {cursor} is past source end {size}")
    if lag > lag_cap_bytes:
        reasons.append(f"lag {lag} bytes exceeds cap {lag_cap_bytes}")
    heartbeat_age = None
    hb_path = state_dir / HEARTBEAT_FILENAME
    if hb_path.exists():
        try:
            heartbeat_age = int(now) - int(hb_path.read_text(encoding="utf-8").strip())
        except ValueError:
            heartbeat_age = None
    if heartbeat_age is None:
        reasons.append("heartbeat missing or unreadable")
    elif heartbeat_age > stale_after_s:
        reasons.append(f"heartbeat {heartbeat_age}s old exceeds {stale_after_s}s")
    stale = bool(reasons)
    age_text = "none" if heartbeat_age is None else str(heartbeat_age)
    line = (
        f"INGEST: {'STALE' if stale else 'fresh'} cursor={cursor} source_bytes={size} "
        f"lag_bytes={lag} heartbeat_age_s={age_text}"
    )
    if stale:
        line += " reasons=[" + "; ".join(reasons) + "]"
    return {
        "state": "STALE" if stale else "fresh", "stale": stale, "source": str(jsonl_path),
        "source_bytes": size, "cursor": cursor, "lag_bytes": lag,
        "heartbeat_age_s": heartbeat_age, "reasons": reasons, "line": line,
    }


def ingest(jsonl_path, state_dir, max_bytes: int = 0) -> dict:
    """Ingest unconsumed complete lines from ``jsonl_path`` into the task stream.

    max_bytes > 0 bounds this run to the lines that end within that many bytes
    of the cursor; a later run picks up the rest. Returns a result dict with
    keys: ingested, duplicates, malformed, cursor, exit_code, and optionally error.
    """
    jsonl_path = Path(jsonl_path)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    cursor_path = state_dir / CURSOR_FILENAME
    result = {"ingested": 0, "duplicates": 0, "malformed": 0, "quarantined": 0, "lifecycle": 0, "cursor": 0, "exit_code": EXIT_OK}

    if not jsonl_path.exists():
        # No brain hook on this box (or a CI runner): nothing to ingest is a clean
        # no-op, never a failure -- otherwise run-watchdog --once exits non-zero.
        return result

    offset = _read_cursor(cursor_path)
    size = jsonl_path.stat().st_size
    if offset > size:
        result.update(
            exit_code=EXIT_FAIL_CLOSED,
            error=f"source shrank below cursor ({size} < {offset}); refusing to ingest",
            cursor=offset,
        )
        return result

    complete, size = _scan(jsonl_path, offset, max_bytes)
    quarantine_path = _quarantine_path(jsonl_path)
    quarantined_offsets = _quarantined_offsets(quarantine_path)
    new_quarantine_rows = []
    api = StateAPI(resolve_db_path(state_dir, "tracker"))
    try:
        seen = {e["payload"].get("event_id") for e in api.get(STREAM)}
        seen_lifecycle = {e["payload"].get("event_id") for e in api.get(LIFECYCLE_STREAM)}
        cursor = offset
        contiguous = True
        for start, end, raw, error, record in complete:
            if error:
                if end == size:
                    # Last bytes in the file and newline-terminated with no newer
                    # data: ambiguous (a slow writer may still be finishing it).
                    # Hold the cursor; do not punish the writer by quarantining.
                    result["malformed"] += 1
                    contiguous = False
                    continue
                # Bytes follow the malformed line, so it can never become valid
                # (a torn record glued to the next one). Quarantine it and move on.
                if start not in quarantined_offsets:
                    quarantined_offsets.add(start)
                    new_quarantine_rows.append({
                        "offset": start,
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "error": error,
                        "raw": raw.decode("utf-8", errors="replace"),
                        "source": str(jsonl_path),
                    })
                    result["quarantined"] += 1
            else:
                event_id = _event_id(raw, start)
                if event_id in seen or _legacy_event_id(raw) in seen:
                    result["duplicates"] += 1
                else:
                    payload = dict(record)
                    payload["event_id"] = event_id
                    payload["kind"] = classify_kind(record)
                    payload.setdefault("agent_type", "")
                    api.append(STREAM, EVENT_TYPE, payload, ACTOR)
                    seen.add(event_id)
                    result["ingested"] += 1
                if classify_kind(record) == "agent":
                    # Checked on duplicates too: a crash between the task append and
                    # the lifecycle append must not leave the lane without its row.
                    _append_lifecycle(api, record, event_id, seen_lifecycle, result)
            if contiguous:
                cursor = end
        if new_quarantine_rows:
            _append_quarantine(quarantine_path, new_quarantine_rows)
        result["cursor"] = cursor
        _write_cursor(cursor_path, cursor, str(jsonl_path))
    finally:
        api.close()

    if result["malformed"]:
        result.update(
            exit_code=EXIT_FAIL_CLOSED,
            error=f"{result['malformed']} malformed line(s) in {jsonl_path}; cursor held at {result['cursor']}",
        )
        return result

    _write_heartbeat(state_dir)
    return result


def _write_heartbeat(state_dir: Path) -> None:
    (state_dir / HEARTBEAT_FILENAME).write_text(str(int(time.time())) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ingest task-events.jsonl into the state store task stream.")
    parser.add_argument("--jsonl", default=DEFAULT_JSONL, help="path to task-events.jsonl")
    parser.add_argument("--state-dir", default=None, help="state directory (default: AESOP_STATE_ROOT or ./state)")
    parser.add_argument("--check", action="store_true", help="assert cursor == EOF; no writes")
    parser.add_argument("--status", action="store_true", help="print the INGEST freshness line; exit 1 when STALE; no writes")
    parser.add_argument("--max-bytes", type=int, default=0, help="bound this run to N bytes of JSONL (0 = unbounded)")
    args = parser.parse_args(argv)
    if args.max_bytes < 0:
        print("task-events ingest: --max-bytes must be >= 0", file=sys.stderr)
        return EXIT_USAGE

    state_dir = args.state_dir or os.environ.get("AESOP_STATE_ROOT") or str(REPO_ROOT / "state")
    if args.check:
        try:
            return check(args.jsonl, state_dir)
        except Exception as exc:
            print(f"task-events check: FAILED: internal error: {exc}", file=sys.stderr)
            return EXIT_CHECK_PENDING

    if args.status:
        try:
            st = status(args.jsonl, state_dir)
        except Exception as exc:
            print(f"INGEST: STALE (status unreadable: {exc})")
            return EXIT_CHECK_PENDING
        print(st["line"])
        return EXIT_CHECK_PENDING if st["stale"] else EXIT_OK

    try:
        result = ingest(args.jsonl, state_dir, args.max_bytes)
    except Exception as exc:  # fail closed: any internal error is a reported failure
        print(f"task-events ingest: FAILED: internal error: {exc}", file=sys.stderr)
        return EXIT_FAIL_CLOSED
    print(
        f"task-events ingest: ingested={result['ingested']} duplicates={result['duplicates']} "
        f"malformed={result['malformed']} quarantined={result['quarantined']} cursor={result['cursor']}"
    )
    if result.get("error"):
        print(f"task-events ingest: FAILED: {result['error']}", file=sys.stderr)
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
