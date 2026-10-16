#!/usr/bin/env python3
"""
Wave dispatch collector — live per-agent phase and activity visibility.

Surfaces what a wave's workers are doing RIGHT NOW: per-agent phase (dispatch/thinking/tool-use/stall/done),
last-activity age, and token burn estimates.

Data sources:
  - Agent transcripts: recursively scanned from {transcripts_root}/**/agent-*.jsonl (mtime + file size).
    Real layout: ~/.claude/projects/*/subagents/agent-*.jsonl or {session}/subagents/agent-*.jsonl.
    Filters to recent files only (<30 min) to avoid hundreds of stale entries.
  - Orchestrator status: state/orchestrator-status.json (phase info)
  - Workflow journal: state/workflow.journal.jsonl (per-agent state transitions, optional)

Degrades to {available:false} when no active workflow.

Performance: includes ~5s in-process cache to avoid re-reading transcripts on rapid polls.
"""
import json
import sys
import time
from datetime import datetime, timezone

import config


# Conservative token estimate: 4.5 bytes per token (empirical average)
BYTES_PER_TOKEN = 4.5

# Module-level cache: (expires_at_epoch, payload_dict). Avoids re-scanning transcripts
# on rapid polls (typical dashboard: 2-3s interval). TTL matches wave_prs.py pattern.
# Cache is invalidated if config paths change (important for test isolation).
_CACHE_TTL_SECONDS = 5.0
_cache = {"expires": 0.0, "payload": None, "transcripts_root": None}


# Bytes read from the end of a transcript. Large enough to hold a final record that
# carries a big tool_use input; the previous 2KB tail cut such records in half.
_TAIL_BYTES = 64 * 1024


def _read_tail_records(transcript_path):
    """Parse the dict records at the end of an NDJSON transcript, oldest first.

    Returns None for an empty file, else (records, last_line_parsed). Unparseable
    lines are skipped; a partial first line (the seek landed mid-record) is dropped.
    last_line_parsed is False when the final non-empty line did not parse, which
    means the newest record is still being written and its state is unknowable.
    """
    with open(transcript_path, 'rb') as f:
        f.seek(0, 2)
        file_size = f.tell()
        if file_size == 0:
            return None
        read_size = min(_TAIL_BYTES, file_size)
        f.seek(file_size - read_size)
        raw = f.read()
    lines = raw.decode('utf-8', errors='replace').split('\n')
    if read_size < file_size:
        lines = lines[1:]
    records = []
    last_line_parsed = True
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            last_line_parsed = False
            continue
        last_line_parsed = isinstance(entry, dict)
        if isinstance(entry, dict):
            records.append(entry)
    return records, last_line_parsed


def _content_blocks(message):
    """Return the structured content-block list of a message, or []."""
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)]


def _infer_agent_phase_from_transcript(transcript_path):
    """Infer agent phase from the structured fields of the transcript tail.

    Decisions use only structured fields: record type, message.stop_reason, content
    block types, and a structured API-error marker. Free text is never inspected,
    so prose such as '0 errors' or '[error]' cannot change the phase.

    Returns one of: 'dispatch', 'thinking', 'tool-use', 'stall', 'done', 'unknown'.
    'unknown' means the structured signal is absent; it is never a guess.

    Args:
        transcript_path: Path to agent-*.jsonl file

    Returns:
        str: phase label
    """
    try:
        if not transcript_path.exists():
            return "unknown"

        tail = _read_tail_records(transcript_path)
        if tail is None:
            return "dispatch"  # Empty file: nothing emitted yet, so the agent was just dispatched
        records, last_line_parsed = tail
        if not records or not last_line_parsed:
            return "unknown"

        # Structured API-error marker on the newest record -> stall
        newest = records[-1]
        if newest.get("isApiErrorMessage") is True or newest.get("type") == "error":
            return "stall"

        conversational = [r for r in records if r.get("type") in ("user", "assistant")]
        if not conversational:
            return "unknown"
        latest = conversational[-1]
        message = latest.get("message")
        if not isinstance(message, dict):
            return "unknown"
        block_types = {b.get("type") for b in _content_blocks(message)}

        if latest.get("type") == "assistant":
            stop_reason = message.get("stop_reason")
            if "tool_use" in block_types or stop_reason == "tool_use":
                return "tool-use"
            if stop_reason == "end_turn":
                return "done"
            if stop_reason is None:
                return "thinking"  # Turn still streaming, no stop reason written yet
            return "unknown"

        # latest is a user record
        if "tool_result" in block_types:
            return "thinking"  # Tool output delivered, model has not replied yet
        return "dispatch"  # Plain prompt with no reply yet
    except Exception as e:
        print(f"[wave_dispatch] Error inferring phase from {transcript_path}: {e}", file=sys.stderr)
        return "unknown"


def _estimate_tokens_from_file_size(file_path):
    """Estimate tokens from file size.

    Uses conservative ratio: size (bytes) / 4.5 bytes-per-token.

    Args:
        file_path: Path to transcript file

    Returns:
        int: estimated token count, or 0 if file missing
    """
    try:
        if not file_path.exists():
            return 0
        size = file_path.stat().st_size
        return max(0, int(size / BYTES_PER_TOKEN))
    except Exception:
        return 0


def _get_last_activity_age_sec(file_path):
    """Get age of last file modification in seconds.

    Args:
        file_path: Path to transcript file

    Returns:
        int: seconds since last mtime, or -1 if file missing
    """
    try:
        if not file_path.exists():
            return -1
        mtime = file_path.stat().st_mtime
        now = time.time()
        age_sec = int(now - mtime)
        return max(0, age_sec)
    except Exception:
        return -1


def get_wave_dispatch(force=False):
    """Get consolidated wave dispatch snapshot.

    Reads agent transcripts and orchestrator status at call time (not import time)
    to ensure test isolation. Returns per-agent phase, activity age, and token burn.

    Caches result for ~5s to avoid re-reading transcripts on rapid dashboard polls.
    Pass force=True to bypass cache (mainly for testing).

    Returns:
        dict: {
            "available": bool,
            "wave_phase": str or None,
            "agents": [
                {
                    "id": str,
                    "phase": str,
                    "last_activity_age_sec": int,
                    "token_estimate": int,
                    "warnings": [str] (optional)
                }
            ],
            "at": str (ISO 8601)
        }
    """
    # Cache check: if valid cached payload exists and config hasn't changed, return it
    # (Invalidate cache if transcripts_root changed, important for test isolation)
    now_epoch = time.time()
    current_transcripts_root = str(config.TRANSCRIPTS_ROOT)
    cached_transcripts_root = _cache.get("transcripts_root")

    if (not force and
        _cache["payload"] is not None and
        now_epoch < _cache["expires"] and
        current_transcripts_root == cached_transcripts_root):
        return _cache["payload"]

    try:
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat(timespec='seconds').replace('+00:00', 'Z')

        # Try to read orchestrator status to get wave phase
        wave_phase = None
        try:
            orch_status_file = config.ORCH_STATUS_FILE
            if orch_status_file.exists():
                content = orch_status_file.read_text(encoding='utf-8')
                orch_data = json.loads(content)
                wave_phase = orch_data.get("phase")
        except Exception as e:
            print(f"[wave_dispatch] Error reading orchestrator status: {e}", file=sys.stderr)

        # Find active agent transcripts
        agents = []
        transcripts_root = config.TRANSCRIPTS_ROOT

        try:
            # Scan recursively for agent-*.jsonl files matching real Claude Code layout:
            # ~/.claude/projects/*/subagents/agent-*.jsonl or {session}/subagents/agent-*.jsonl
            # Uses same pattern as agents.py to ensure consistency (ui/agents.py:482)
            if transcripts_root.exists():
                for transcript_file in transcripts_root.glob("**/agent-*.jsonl"):
                    # Filter to recent files only (<30 min) to avoid hundreds of stale entries
                    age_sec = _get_last_activity_age_sec(transcript_file)
                    if age_sec < 0:  # File missing or error reading
                        continue
                    if age_sec > 1800:  # 30 minutes; skip stale transcripts
                        continue

                    agent_id = transcript_file.stem  # e.g., "agent-12345"
                    if agent_id.startswith("agent-"):
                        # Remove "agent-" prefix for display
                        display_id = agent_id[6:]  # "12345"

                        phase = _infer_agent_phase_from_transcript(transcript_file)
                        tokens = _estimate_tokens_from_file_size(transcript_file)

                        # Build warnings
                        warnings = []
                        if age_sec > 300:  # 5 minutes
                            warnings.append("inactive >5min")
                        if age_sec > 600:  # 10 minutes
                            warnings.append("stalled >10min")

                        agent_entry = {
                            "id": display_id,
                            "phase": phase,
                            "last_activity_age_sec": age_sec,
                            "token_estimate": tokens,
                        }
                        if warnings:
                            agent_entry["warnings"] = warnings

                        agents.append(agent_entry)
        except Exception as e:
            print(f"[wave_dispatch] Error scanning transcripts: {e}", file=sys.stderr)

        # Determine availability: if no agents found, mark as unavailable
        available = len(agents) > 0

        payload = {
            "available": available,
            "wave_phase": wave_phase,
            "agents": agents,
            "at": timestamp
        }

        # Cache this payload for ~5s to avoid re-reading transcripts on rapid polls
        _cache["payload"] = payload
        _cache["expires"] = now_epoch + _CACHE_TTL_SECONDS
        _cache["transcripts_root"] = str(config.TRANSCRIPTS_ROOT)

        return payload
    except Exception as e:
        print(f"[wave_dispatch] Uncaught error: {e}", file=sys.stderr)
        payload = {
            "available": False,
            "wave_phase": None,
            "agents": [],
            "error": "Internal error",
            "at": datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
        }

        # Cache error response too (so we don't retry immediately)
        _cache["payload"] = payload
        _cache["expires"] = now_epoch + _CACHE_TTL_SECONDS
        _cache["transcripts_root"] = str(config.TRANSCRIPTS_ROOT)

        return payload
