#!/usr/bin/env python3
"""Tests for ui/wave_gantt.py — RFC 3339 timestamps and real (not idle-age) durations.

Drives get_wave_gantt() with wave_dispatch.get_wave_dispatch patched to return the
exact live-agent shape (id, phase, last_activity_age_sec, token_estimate, warnings;
no started_at / last_activity keys) plus richer fixtures where real timing exists.
"""
import re
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

import wave_dispatch
import wave_gantt

# Strict RFC 3339 date-time: YYYY-MM-DDTHH:MM:SS[.frac](Z|+HH:MM|-HH:MM)
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)
SNAPSHOT_AT = "2026-10-15T12:00:00Z"


def _parse_rfc3339(value):
    """Return a timezone-aware datetime for a strict RFC 3339 string, else raise."""
    if not isinstance(value, str) or not RFC3339_RE.match(value):
        raise ValueError(f"not RFC 3339: {value!r}")
    return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)


def _collect_timestamps(payload):
    """Every timestamp-valued field in a get_wave_gantt() payload (None values skipped)."""
    stamps = []
    if payload.get("at") is not None:
        stamps.append(("at", payload["at"]))
    for agent in payload.get("agents", []):
        for phase in agent.get("phases", []):
            for key in ("start", "end"):
                if phase.get(key) is not None:
                    stamps.append((f"{agent['id']}.{key}", phase[key]))
    return stamps


class TestWaveGanttTimestamps(unittest.TestCase):
    """Every emitted timestamp is one RFC 3339 form and parses strictly."""

    def _run_with_dispatch(self, dispatch):
        with patch.object(wave_dispatch, "get_wave_dispatch", return_value=dispatch):
            return wave_gantt.get_wave_gantt()

    def test_live_agent_timestamps_parse_strictly(self):
        """Live-shape agent (no started_at/last_activity) emits only valid RFC 3339."""
        dispatch = {
            "available": True,
            "wave_phase": "build",
            "agents": [{
                "id": "abc123",
                "phase": "thinking",
                "last_activity_age_sec": 42,
                "token_estimate": 900,
            }],
            "at": SNAPSHOT_AT,
        }
        payload = self._run_with_dispatch(dispatch)
        stamps = _collect_timestamps(payload)
        self.assertTrue(stamps, "payload emitted no timestamps at all")
        for label, value in stamps:
            with self.subTest(field=label):
                _parse_rfc3339(value)  # raises on '+00:00Z' or any non-RFC3339 form
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                self.assertNotIn("+00:00Z", value)

    def test_unavailable_payload_at_is_rfc3339(self):
        """The no-workflow path also emits a strict RFC 3339 'at'."""
        with patch.object(wave_dispatch, "get_wave_dispatch", return_value={"available": False}):
            payload = wave_gantt.get_wave_gantt()
        self.assertFalse(payload["available"])
        _parse_rfc3339(payload["at"])
        self.assertNotIn("+00:00Z", payload["at"])

    def test_timestamps_share_one_form(self):
        """Emitted timestamps use one RFC 3339 form (all 'Z' here, never mixed with '+00:00')."""
        dispatch = {
            "available": True,
            "wave_phase": "build",
            "agents": [{
                "id": "a1",
                "phase": "tool-use",
                "last_activity_age_sec": 10,
                "token_estimate": 1,
                "started_at": "2026-10-15T11:00:00Z",
                "last_activity": "2026-10-15T11:30:00Z",
            }],
            "at": SNAPSHOT_AT,
        }
        payload = self._run_with_dispatch(dispatch)
        forms = {"Z" if v.endswith("Z") else v[-6:] for _, v in _collect_timestamps(payload)}
        self.assertEqual(len(forms), 1, f"mixed timestamp forms: {forms}")


class TestWaveGanttDuration(unittest.TestCase):
    """Duration is end minus start; unknown is reported as unknown, never idle age."""

    def _run_with_dispatch(self, dispatch):
        with patch.object(wave_dispatch, "get_wave_dispatch", return_value=dispatch):
            return wave_gantt.get_wave_gantt()

    def test_duration_equals_end_minus_start(self):
        """With real start and last_activity, total and phase durations are end - start."""
        dispatch = {
            "available": True,
            "wave_phase": "build",
            "agents": [{
                "id": "a2",
                "phase": "thinking",
                "last_activity_age_sec": 5,
                "token_estimate": 1,
                "started_at": "2026-10-15T11:00:00Z",
                "last_activity": "2026-10-15T11:30:00Z",
            }],
            "at": SNAPSHOT_AT,
        }
        agent = self._run_with_dispatch(dispatch)["agents"][0]
        self.assertEqual(agent["total_duration_sec"], 1800)
        self.assertNotEqual(agent["total_duration_sec"], 5)  # not the idle age
        self.assertEqual(agent["phases"][0]["duration_sec"], 1800)

    def test_unknown_start_reports_unknown_not_idle_age(self):
        """Without a real start, total duration is None, not last_activity_age_sec."""
        dispatch = {
            "available": True,
            "wave_phase": "build",
            "agents": [{
                "id": "a3",
                "phase": "thinking",
                "last_activity_age_sec": 120,
                "token_estimate": 1,
            }],
            "at": SNAPSHOT_AT,
        }
        agent = self._run_with_dispatch(dispatch)["agents"][0]
        self.assertIsNone(agent["total_duration_sec"])
        self.assertIsNone(agent["phases"][0]["duration_sec"])
        self.assertNotEqual(agent["total_duration_sec"], 120)

    def test_end_derived_from_snapshot_age_is_real(self):
        """Without last_activity, end is the snapshot time minus the measured idle age."""
        dispatch = {
            "available": True,
            "wave_phase": "build",
            "agents": [{
                "id": "a4",
                "phase": "thinking",
                "last_activity_age_sec": 120,
                "token_estimate": 1,
            }],
            "at": SNAPSHOT_AT,
        }
        phase = self._run_with_dispatch(dispatch)["agents"][0]["phases"][0]
        self.assertEqual(phase["end"], "2026-10-15T11:58:00Z")
        _parse_rfc3339(phase["end"])


if __name__ == "__main__":
    unittest.main()
