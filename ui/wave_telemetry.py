#!/usr/bin/env python3
"""
Wave telemetry collector — current wave phase, cost metrics, and blockers.

Reads state at CALL TIME (not import time) to ensure test isolation.
State sources (via read_api facade):
  - orchestrator-status: current phase and activity (preferred, <24h)
  - STATE.md: current phase and wave info (fallback if status file missing/stale)
  - AUDIT-BACKLOG.md: top blocker via parse
  - ledger: cost data (re-uses cost.py logic)
"""
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import config
import cost

# serve.py runs with ui/ as the import root; state_store is a repo-root
# package, so the repo root must be on sys.path before this import.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from state_store.read_api import ReadAPI  # noqa: E402


def _read_orchestrator_status():
    """Read orchestrator-status.json if fresh (<24h old) using the read_api.

    Returns:
        tuple: (phase_str, activity_str, source_str) or (None, None, None) if missing/stale/malformed
    """
    try:
        # Use read_api to read orchestrator status
        api = ReadAPI(str(config.STATE_DIR))
        status = api.read_orchestrator_status()
        if status is None:
            return None, None, None

        # Check freshness (24h threshold)
        updated_at_str = status.get("updated_at")
        if not updated_at_str:
            return None, None, None

        # Parse ISO format timestamp (handle both "Z" and "+00:00" suffixes)
        updated_at_str_normalized = updated_at_str.replace("Z", "+00:00")
        try:
            updated_at = datetime.fromisoformat(updated_at_str_normalized)
        except ValueError:
            return None, None, None

        # Check if fresh (<24h) and not in the future
        now = datetime.now(timezone.utc)
        age = now - updated_at
        # Treat ANY future-dated timestamp as NOT fresh (fail-closed)
        if age > timedelta(hours=24) or age < timedelta(0):
            return None, None, None

        # Extract phase and activity
        phase = status.get("phase", "").lower()
        activity = status.get("activity", "").lower()

        if not phase:
            return None, None, None

        return phase, activity, "orchestrator-status"
    except Exception as e:
        print(f"[wave_telemetry] Error reading orchestrator-status.json: {e}", file=sys.stderr)
        return None, None, None


def _parse_state_md_phase():
    """Extract current phase and wave from STATE.md.

    Looks for lines like:
      ## Phase: `rc-1-published-source-available` (2026-07-17, current)
    or
      ## Phase: `wave-rc.2` (2026-07-17, current)

    Returns:
        dict: {"wave": str, "phase": str} or {"wave": "unknown", "phase": "unknown"}
    """
    try:
        state_md = config.AESOP_ROOT / "STATE.md"
        if not state_md.exists():
            return {"wave": "unknown", "phase": "unknown"}

        content = state_md.read_text(encoding='utf-8')

        # Look for ## Phase: `...` (..., current)
        # Extract the phase name between backticks and the wave indicator
        match = re.search(r'## Phase:\s*`([^`]+)`', content)
        if not match:
            return {"wave": "unknown", "phase": "unknown"}

        phase = match.group(1)

        # Extract wave name from phase (e.g., "wave-rc.2" from "rc-1-published-source-available")
        # Match patterns like "wave-26", "wave-rc.2", "rc-1", etc.
        # Use (?:\.\w+)* to capture dot-separated identifiers (e.g., "wave-rc.2")
        wave_match = re.search(r'(wave[-.]?\w+(?:\.\w+)*|rc[-.]?\w+(?:\.\w+)*)', phase, re.IGNORECASE)
        if wave_match:
            wave_str = wave_match.group(0)  # e.g., "wave-26" or "rc-1"
        else:
            wave_str = phase  # Fallback to phase itself

        return {
            "wave": wave_str.lower(),
            "phase": phase.lower()
        }
    except Exception as e:
        print(f"[wave_telemetry] Error parsing STATE.md: {e}", file=sys.stderr)
        return {"wave": "unknown", "phase": "unknown"}


def _parse_top_blocker():
    """Extract top blocker from AUDIT-BACKLOG.md.

    Looks for the first P0 item with status 🔵 (inflight) or ⬜ (todo).
    Returns the title if found, otherwise looks for orchestrator-status.

    Returns:
        str: blocker title or reason, e.g., "CI test flakes", "unknown"
    """
    try:
        backlog_file = config.AUDIT_BACKLOG_FILE
        if not backlog_file.exists():
            return "unknown"

        content = backlog_file.read_text(encoding='utf-8')
        lines = content.split('\n')

        in_p0 = False
        for line in lines:
            line_stripped = line.strip()

            # Check if we entered P0 section
            if re.match(r'^##\s*P0\b', line_stripped):
                in_p0 = True
                continue

            # If we entered a different tier, stop
            if line_stripped.startswith('## ') and not re.match(r'^##\s*P0\b', line_stripped):
                in_p0 = False
                continue

            if in_p0 and line_stripped.startswith('- '):
                # Check for inflight (🔵) or todo (⬜) status
                if '🔵' in line_stripped or '⬜' in line_stripped:
                    # Extract title after status
                    # Pattern: - 🔵 **[tag] Title**
                    match = re.search(r'(?:🔵|⬜)\s+\*\*\[([^\]]+)\]\s+(.+?)\*\*', line_stripped)
                    if match:
                        title = match.group(2)
                        return title

        return "unknown"
    except Exception as e:
        print(f"[wave_telemetry] Error parsing blocker: {e}", file=sys.stderr)
        return "unknown"


def _get_wave_cost_metrics():
    """Get cost metrics for the current wave.

    Uses cost.py to parse the ledger and return this-wave totals.
    For now, returns a simplified snapshot of per-model tokens and OK rate.

    Returns:
        dict: {
            "tokens_used": int,
            "top_model": str,
            "ok_rate": float (0.0-1.0)
        }
    """
    try:
        summary = cost.get_cost_summary()

        if "error" in summary:
            return {
                "tokens_used": 0,
                "top_model": "unknown",
                "ok_rate": 0.0
            }

        # Sum total tokens across all models
        total_tokens = 0
        top_model = "unknown"
        top_model_tokens = 0

        for model, stats in summary.get("models", {}).items():
            tokens = stats.get("tokens_in", 0) + stats.get("tokens_out", 0)
            total_tokens += tokens
            if tokens > top_model_tokens:
                # Extract model name (e.g., "haiku" from "claude-haiku-4-5" or "claude-haiku-4-5-20251001")
                model_lower = model.lower()
                if "haiku" in model_lower:
                    top_model = "haiku"
                elif "sonnet" in model_lower:
                    top_model = "sonnet"
                elif "opus" in model_lower:
                    top_model = "opus"
                else:
                    # Fallback to last part of model id
                    top_model = model.split("-")[-1]
                top_model_tokens = tokens

        scorecard = summary.get("overall_scorecard", {})
        ok_rate = scorecard.get("ok_rate", 0.0)

        return {
            "tokens_used": total_tokens,
            "top_model": top_model,
            "ok_rate": ok_rate
        }
    except Exception as e:
        print(f"[wave_telemetry] Error computing cost metrics: {e}", file=sys.stderr)
        return {
            "tokens_used": 0,
            "top_model": "unknown",
            "ok_rate": 0.0
        }


def _get_wave_start_time():
    """Get the wave start time from orchestrator-status.json or STATE.md.

    Returns:
        datetime or None: wave start time in UTC, or None if not available
    """
    try:
        # Use read_api to read orchestrator status
        api = ReadAPI(str(config.STATE_DIR))
        status = api.read_orchestrator_status()
        if status is not None:
            # Check if there's a wave_start_time or started_at field
            start_time_str = status.get("wave_start_time") or status.get("started_at")
            if start_time_str:
                start_time_str_normalized = start_time_str.replace("Z", "+00:00")
                return datetime.fromisoformat(start_time_str_normalized)
    except Exception:
        pass
    return None


def _calculate_burn_rate_fields(total_tokens: int) -> dict:
    """Calculate burn-rate and projection fields for live wave cost.

    Args:
        total_tokens: total tokens burned in this wave so far

    Returns:
        dict: {
            "tokens_burned_per_min": float (tokens/min),
            "projected_total_tokens": int (at current rate),
            "cost_ceiling_exceeded": bool
        }
    """
    try:
        # Try to get wave start time for burn-rate calculation
        wave_start = _get_wave_start_time()
        if not wave_start:
            # Fallback: estimate from cost ledger timestamps if available
            return {
                "tokens_burned_per_min": 0.0,
                "projected_total_tokens": 0,
                "cost_ceiling_exceeded": False
            }

        now = datetime.now(timezone.utc)
        elapsed_seconds = (now - wave_start).total_seconds()
        if elapsed_seconds < 1:
            return {
                "tokens_burned_per_min": 0.0,
                "projected_total_tokens": 0,
                "cost_ceiling_exceeded": False
            }

        elapsed_minutes = elapsed_seconds / 60.0
        burn_rate = total_tokens / elapsed_minutes if elapsed_minutes > 0 else 0.0

        # Get cost ceiling from config (typical: 2M tokens = $40 budget estimate)
        cost_ceiling = getattr(config, 'COST_CEILING_TOKENS', 2_000_000)

        # Estimate wave duration (typical: 8-30 min, assume 20 min average for projection)
        avg_wave_duration_min = 20
        estimated_remaining_min = max(0, avg_wave_duration_min - elapsed_minutes)
        projected_additional = burn_rate * estimated_remaining_min
        projected_total = int(total_tokens + projected_additional)

        ceiling_exceeded = total_tokens > cost_ceiling

        return {
            "tokens_burned_per_min": round(burn_rate, 1),
            "projected_total_tokens": projected_total,
            "cost_ceiling_exceeded": ceiling_exceeded
        }
    except Exception as e:
        print(f"[wave_telemetry] Error calculating burn-rate: {e}", file=sys.stderr)
        return {
            "tokens_burned_per_min": 0.0,
            "projected_total_tokens": 0,
            "cost_ceiling_exceeded": False
        }


def get_wave_telemetry():
    """Get consolidated wave telemetry snapshot.

    Prefers state/orchestrator-status.json (if fresh <24h) over STATE.md regex parsing.
    Reads all state at call time (not import time) to ensure test isolation.

    Returns:
        dict: {
            "wave": str,
            "phase": str,
            "blocker": str,
            "tokens_used": int,
            "top_model": str,
            "ok_rate": float,
            "source": "orchestrator-status" | "state-md",
            "tokens_burned_per_min": float (NEW: burn rate),
            "projected_total_tokens": int (NEW: projection),
            "cost_ceiling_exceeded": bool (NEW: alert flag)
        }
    """
    try:
        # Try orchestrator-status.json first (fresh <24h)
        orch_phase, orch_activity, source = _read_orchestrator_status()

        if orch_phase:
            # Fresh orchestrator-status.json found; use it
            # Extract wave identifier from phase (e.g., "wave-26" from "wave-26-verify", "wave-rc.2" from "wave-rc.2: build")
            # Use (?:\.\w+)* to capture dot-separated identifiers (e.g., "wave-rc.2")
            wave_match = re.search(r'(wave[-.]?\w+(?:\.\w+)*|rc[-.]?\w+(?:\.\w+)*)', orch_phase, re.IGNORECASE)
            if wave_match:
                wave_str = wave_match.group(0)  # e.g., "wave-26", "wave-rc", or "rc-1"
            else:
                wave_str = orch_phase  # Fallback to phase itself
            phase_info = {
                "wave": wave_str.lower(),
                "phase": orch_phase
            }
            source_field = source
        else:
            # Fall back to STATE.md
            phase_info = _parse_state_md_phase()
            source_field = "state-md"

        blocker = _parse_top_blocker()
        cost_metrics = _get_wave_cost_metrics()

        # Calculate burn-rate fields
        burn_rate_fields = _calculate_burn_rate_fields(cost_metrics["tokens_used"])

        return {
            "wave": phase_info["wave"],
            "phase": phase_info["phase"],
            "blocker": blocker,
            "tokens_used": cost_metrics["tokens_used"],
            "top_model": cost_metrics["top_model"],
            "ok_rate": cost_metrics["ok_rate"],
            "source": source_field,
            "tokens_burned_per_min": burn_rate_fields["tokens_burned_per_min"],
            "projected_total_tokens": burn_rate_fields["projected_total_tokens"],
            "cost_ceiling_exceeded": burn_rate_fields["cost_ceiling_exceeded"]
        }
    except Exception as e:
        print(f"[wave_telemetry] Uncaught error: {e}", file=sys.stderr)
        return {
            "wave": "unknown",
            "phase": "unknown",
            "blocker": "error",
            "tokens_used": 0,
            "top_model": "unknown",
            "ok_rate": 0.0,
            "source": "error",
            "tokens_burned_per_min": 0.0,
            "projected_total_tokens": 0,
            "cost_ceiling_exceeded": False
        }
