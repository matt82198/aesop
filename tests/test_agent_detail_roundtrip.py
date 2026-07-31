"""Roundtrip test: agent IDs from /api/agents must resolve via GET /agent?id=<id>

This test reproduces the wave-14 dashboard bug where expanding an agent row
fetches GET /agent?id=<id> for every currently-running agent and gets 404
"transcript not found" for EVERY agent, even though GET /api/agents successfully
listed them with 13-char truncated IDs.

Root cause: dash-extra.mjs scans for agent-*.jsonl files and emits 13-char
truncated IDs, but agents.py searches for */*.output files (wrong extension).
The fix: agents.py must search for agent-{id}*.jsonl to match what dash-extra
actually found.

Run: python -m unittest tests.test_agent_detail_roundtrip
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

import config
import agents

ENV_KEYS = ("AESOP_ROOT", "AESOP_STATE_ROOT", "AESOP_TRANSCRIPTS_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


class AgentDetailRoundtripTest(unittest.TestCase):
    """Test that agent IDs emitted by get_fleet_agents() round-trip to
    extract_agent_dispatch_prompt()."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-roundtrip-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)
        self.transcripts_root = self.fixture_root / "transcripts"
        self.transcripts_root.mkdir()
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_ROOT"] = str(self.fixture_root)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.transcripts_root)
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"
        config.reload()

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # setUp called config.reload() to pick up the fixture paths; restoring the env
        # without reloading leaves the module pointing at the temp dir removed below,
        # so any later test reading config sees an empty state root. Reload to undo it.
        config.reload()
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def test_jsonl_agent_transcript_roundtrip(self):
        """Create a real agent-*.jsonl transcript as Claude Code generates.

        The agent ID from the filename is truncated to 13 chars by dash-extra.mjs.
        GET /api/agents emits this truncated ID. When the user clicks to expand,
        GET /agent?id=<truncated-id> must resolve back to the transcript file.
        """
        # Create a fixture transcript matching real Claude agent output:
        # agent-<full-id>.jsonl with NDJSON format
        full_id = "a7815f98a4a97c1d"  # 16 chars, truncates to "a7815f98a4a97" (13 chars)
        truncated_id = full_id[:13]

        # Create the transcript file in the same structure as real ones
        transcripts_subdir = self.transcripts_root / "subagents"
        transcripts_subdir.mkdir()
        transcript_file = transcripts_subdir / f"agent-{full_id}.jsonl"

        # Write NDJSON lines matching real Claude agent transcripts
        lines = [
            json.dumps({
                "type": "user",
                "parentUuid": None,
                "message": {"content": "Test dispatch prompt for agent {0}".format(full_id)}
            }),
            json.dumps({
                "type": "assistant",
                "model": "claude-haiku-4-5",
                "message": {"content": "ok"},
                "usage": {"input_tokens": 100, "output_tokens": 50}
            }),
        ]
        transcript_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Verify the file exists
        self.assertTrue(transcript_file.exists(), f"Transcript file not created at {transcript_file}")

        # Now test the roundtrip: the truncated ID must resolve to this transcript
        result = agents.extract_agent_dispatch_prompt(truncated_id)

        # Should succeed (no error), not 404
        self.assertNotIn("error", result, f"Expected success, got: {result.get('error', '')}")
        self.assertIn("dispatch_prompt", result)
        self.assertIn("Test dispatch prompt", result["dispatch_prompt"])
        self.assertEqual(result["dispatcher"], "main thread")
        self.assertEqual(result["model"], "claude-haiku-4-5")

    def test_multiple_agents_each_resolve_correctly(self):
        """When multiple agents are active, each emitted ID must resolve uniquely."""
        transcripts_subdir = self.transcripts_root / "subagents"
        transcripts_subdir.mkdir()

        # Create three agents with different prompts
        agents_data = [
            ("abc1234567890xyz", "Dispatch prompt for agent 1"),
            ("def9876543210uvw", "Dispatch prompt for agent 2"),
            ("ghi0123456789tsr", "Dispatch prompt for agent 3"),
        ]

        for full_id, prompt in agents_data:
            transcript_file = transcripts_subdir / f"agent-{full_id}.jsonl"
            lines = [
                json.dumps({
                    "type": "user",
                    "parentUuid": None,
                    "message": {"content": prompt}
                }),
            ]
            transcript_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Each truncated ID must resolve to its corresponding transcript
        for full_id, expected_prompt in agents_data:
            truncated_id = full_id[:13]
            result = agents.extract_agent_dispatch_prompt(truncated_id)
            self.assertNotIn("error", result, f"Failed for {truncated_id}: {result.get('error', '')}")
            self.assertIn(expected_prompt, result["dispatch_prompt"])


if __name__ == "__main__":
    unittest.main()
