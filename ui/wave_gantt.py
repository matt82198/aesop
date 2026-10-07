#!/usr/bin/env python3
"""
Wave Gantt timeline data — per-agent phase spans from ledger/journal + transcript mtimes.

Reads agent dispatch data (via wave_dispatch.py) and enriches with timing info
from the ledger to create Gantt-style bars (agent rows, phase spans as bars).

Returns timeline data for visualization in the Activity view.
"""
from datetime import datetime, timezone
from typing import Dict, List

import wave_dispatch


def _parse_agent_phases_from_transcript(agent_id: str) -> List[Dict]:
    """Extract phase timing info from agent transcript (if available).

    Looks for transcript_mtime events in state_store that track phase changes.
    Falls back to dispatch inference if not available.

    Returns:
        list: [{"phase": "dispatch", "start": iso_timestamp, "end": iso_timestamp, ...}, ...]
    """
    try:
        # For now, use dispatch data as single span
        # Future: parse state_store for detailed phase tracking
        dispatch_data = wave_dispatch.get_wave_dispatch()
        if not dispatch_data or not dispatch_data.get("available"):
            return []

        # Find this agent in dispatch data
        for agent in dispatch_data.get("agents", []):
            if agent.get("id") == agent_id:
                # Create a single span from start to now
                now = datetime.now(timezone.utc).isoformat() + "Z"
                return [
                    {
                        "phase": agent.get("phase", "unknown"),
                        "start": agent.get("started_at") or now,
                        "end": agent.get("last_activity") or now,
                        "duration_sec": agent.get("last_activity_age_sec", 0),
                        "token_estimate": agent.get("token_estimate", 0),
                    }
                ]
        return []
    except Exception as e:
        print(f"[wave_gantt] Error parsing phases: {e}")
        return []


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
                        {"phase": "dispatch", "start": iso, "end": iso, "duration_sec": 10},
                        {"phase": "thinking", "start": iso, "end": iso, "duration_sec": 25},
                        ...
                    ],
                    "total_duration_sec": 120,
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
                "at": datetime.now(timezone.utc).isoformat() + "Z"
            }

        agents = []
        for agent in dispatch_data.get("agents", []):
            agent_id = agent.get("id", "unknown")
            phases = _parse_agent_phases_from_transcript(agent_id)

            # Calculate total duration
            total_sec = 0
            if phases:
                # Use the span from earliest start to latest end
                starts = [p.get("start") for p in phases if p.get("start")]
                ends = [p.get("end") for p in phases if p.get("end")]
                if starts and ends:
                    try:
                        earliest = min(datetime.fromisoformat(s.replace("Z", "+00:00")) for s in starts)
                        latest = max(datetime.fromisoformat(e.replace("Z", "+00:00")) for e in ends)
                        total_sec = int((latest - earliest).total_seconds())
                    except (ValueError, TypeError):
                        total_sec = sum(p.get("duration_sec", 0) for p in phases)
            else:
                # Fallback: use last_activity_age_sec
                total_sec = agent.get("last_activity_age_sec", 0)

            agents.append({
                "id": agent_id,
                "phases": phases or [
                    {
                        "phase": agent.get("phase", "unknown"),
                        "start": agent.get("started_at") or datetime.now(timezone.utc).isoformat() + "Z",
                        "end": agent.get("last_activity") or datetime.now(timezone.utc).isoformat() + "Z",
                        "duration_sec": agent.get("last_activity_age_sec", 0),
                    }
                ],
                "total_duration_sec": total_sec,
                "status": _infer_status(agent.get("phase", "unknown"), agent.get("warnings", [])),
            })

        return {
            "available": True,
            "wave_phase": dispatch_data.get("wave_phase", "unknown"),
            "agents": agents,
            "at": dispatch_data.get("at", datetime.now(timezone.utc).isoformat() + "Z"),
        }
    except Exception as e:
        print(f"[wave_gantt] Uncaught error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "available": False,
            "agents": [],
            "error": str(e),
            "at": datetime.now(timezone.utc).isoformat() + "Z"
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
