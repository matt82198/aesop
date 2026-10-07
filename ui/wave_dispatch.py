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


def _infer_agent_phase_from_transcript(transcript_path):
    """Infer agent phase from transcript tail.

    Reads the last ~10 lines of an NDJSON transcript to infer phase state.
    Returns one of: 'dispatch', 'thinking', 'tool-use', 'stall', 'done'.

    Args:
        transcript_path: Path to agent-*.jsonl file

    Returns:
        str: phase label, or 'unknown' if inference fails
    """
    try:
        if not transcript_path.exists():
            return "unknown"

        # Read last ~10 lines to infer phase
        with open(transcript_path, 'rb') as f:
            # Seek to end and read backwards to get tail
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return "dispatch"  # Empty file, likely just dispatched

            # Read last ~2KB to capture ~10 lines
            read_size = min(2048, file_size)
            f.seek(max(0, file_size - read_size))
            tail = f.read().decode('utf-8', errors='replace')

        # Count message types to infer phase
        lines = tail.strip().split('\n')
        message_types = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if isinstance(entry, dict) and 'type' in entry:
                    message_types.append(entry['type'].lower())
            except (json.JSONDecodeError, ValueError):
                pass

        # Phase inference logic:
        # - If last message is user: dispatch
        # - If last message is assistant: thinking or tool-use
        # - If we see [tool_use: ...]: tool-use
        # - If we see [error] or timestamp recent: stall
        # - If multiple completions: done
        if not message_types:
            return "unknown"

        last_type = message_types[-1] if message_types else "unknown"

        # Simple heuristic: check for tool_use markers in tail
        tail_lower = tail.lower()
        if '[tool_use:' in tail_lower or 'tool_use' in last_type:
            return "tool-use"
        elif '[error]' in tail_lower or 'error' in tail_lower:
            return "stall"
        elif last_type == 'assistant':
            # Check if we have many completions (indicates done)
            completion_count = tail.count('"type": "assistant"') + tail.count('"type":"assistant"')
            if completion_count >= 3:
                return "done"
            return "thinking"
        elif last_type == 'user':
            return "dispatch"
        else:
            return "thinking"
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
