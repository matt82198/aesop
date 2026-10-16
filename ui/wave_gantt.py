#!/usr/bin/env python3
"""
Wave Gantt timeline data — per-agent phase spans from ledger/journal + transcript mtimes.

Reads agent dispatch data (via wave_dispatch.py) and enriches with timing info
from the ledger to create Gantt-style bars (agent rows, phase spans as bars).

Timestamp contract: every emitted timestamp is RFC 3339 in the single `...Z` form
(UTC, seconds precision, matching wave_dispatch). A duration is only reported when
both its real start and end are known; otherwise it is None (unknown). Idle age
(last_activity_age_sec) is never substituted for a duration.

Returns timeline data for visualization in the Activity view.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import wave_dispatch


def _now_rfc3339() -> str:
    """Current UTC time as RFC 3339 'Z' (seconds precision)."""
    return _to_rfc3339(datetime.now(timezone.utc))


def _to_rfc3339(dt: datetime) -> str:
    """Format an aware datetime as RFC 3339 'Z' (seconds precision, UTC)."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_ts(value) -> Optional[datetime]:
    """Parse an RFC 3339 timestamp to an aware UTC datetime, or None if unparseable."""
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _agent_phase(agent: Dict, snapshot_at: Optional[datetime]) -> Dict:
    """Build the single phase span for one agent from its dispatch row.

    start: the real started_at, or None when the dispatch row does not carry one.
    end: the real last_activity, or the snapshot time minus the measured idle age.
    duration_sec: end - start when both are known and non-negative, else None.
    """
    start = _parse_ts(agent.get("started_at"))
    end = _parse_ts(agent.get("last_activity"))
    if end is None and snapshot_at is not None:
        age = agent.get("last_activity_age_sec")
        if isinstance(age, int) and not isinstance(age, bool) and age >= 0:
            end = snapshot_at - timedelta(seconds=age)

    duration = None
    if start is not None and end is not None:
        span = int((end - start).total_seconds())
        if span >= 0:
            duration = span

    return {
        "phase": agent.get("phase", "unknown"),
        "start": _to_rfc3339(start) if start is not None else None,
        "end": _to_rfc3339(end) if end is not None else None,
        "duration_sec": duration,
        "token_estimate": agent.get("token_estimate", 0),
    }


def _total_duration(phases: List[Dict]) -> Optional[int]:
    """Span from the earliest phase start to the latest phase end, or None if unknown."""
    if not phases:
        return None
    starts = [_parse_ts(p.get("start")) for p in phases]
    ends = [_parse_ts(p.get("end")) for p in phases]
    if any(s is None for s in starts) or any(e is None for e in ends):
        return None
    span = int((max(ends) - min(starts)).total_seconds())
    return span if span >= 0 else None


def get_wave_gantt() -> Dict:
    """Get Gantt timeline data for current wave.

    Returns per-agent rows with phase timing spans suitable for Gantt visualization.

    Returns:
        dict: {
            "available": bool,
            "wave_phase": str,
            "agents": [
                {
                    "id": "agent-123",
                    "phases": [
                        {"phase": "thinking", "start": iso|None, "end": iso|None,
                         "duration_sec": int|None, "token_estimate": int},
                    ],
                    "total_duration_sec": int|None,   # None = unknown
                    "status": "running|done|error"
                },
                ...
            ],
            "at": "2026-07-21T12:34:56Z"
        }
    """
    try:
        dispatch_data = wave_dispatch.get_wave_dispatch()
        if not dispatch_data or not dispatch_data.get("available"):
            return {
                "available": False,
                "agents": [],
                "error": "No active workflow",
                "at": _now_rfc3339(),
            }

        snapshot_at = _parse_ts(dispatch_data.get("at")) or datetime.now(timezone.utc)

        agents = []
        for agent in dispatch_data.get("agents", []):
            phase = _agent_phase(agent, snapshot_at)
            phases = [phase]
            agents.append({
                "id": agent.get("id", "unknown"),
                "phases": phases,
                "total_duration_sec": _total_duration(phases),
                "status": _infer_status(agent.get("phase", "unknown"), agent.get("warnings", [])),
            })

        return {
            "available": True,
            "wave_phase": dispatch_data.get("wave_phase", "unknown"),
            "agents": agents,
            "at": _to_rfc3339(snapshot_at) if dispatch_data.get("at") else _now_rfc3339(),
        }
    except Exception as e:
        print(f"[wave_gantt] Uncaught error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "available": False,
            "agents": [],
            "error": str(e),
            "at": _now_rfc3339(),
        }


def _infer_status(phase: str, warnings: List[str]) -> str:
    """Infer agent status from phase and warnings."""
    if "stall" in phase.lower():
        return "stalled"
    if warnings and any("inactive" in w.lower() for w in warnings):
        return "inactive"
    if phase.lower() == "done":
        return "done"
    return "running"
