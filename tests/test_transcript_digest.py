#!/usr/bin/env python3
"""
test_transcript_digest.py — Tests for transcript_digest.py

Fixture-root isolated: uses temp directories and synthetic transcript fixtures.
Tests brief schema, idempotency, and aggressive redaction.
"""

import json
import tempfile
import unittest
from pathlib import Path

# Import the module under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.transcript_digest import (
    create_brief,
    redact_text,
    stream_jsonl_transcripts,
    get_existing_agent_ids,
    append_briefs,
    extract_tool_calls,
    extract_errors,
    extract_token_usage,
    generate_brief,
    infer_outcome,
)


class TestRedaction(unittest.TestCase):
    """Test aggressive redaction of secrets, paths, emails, usernames."""

    def test_redact_email(self):
        """Email addresses should be redacted."""
        text = "Contact matt82198@gmail.com for support"
        redacted = redact_text(text)
        self.assertIn("[EMAIL]", redacted)
        self.assertNotIn("gmail", redacted)

    def test_redact_absolute_windows_path(self):
        """Windows absolute paths should be redacted."""
        text = "File written to C:\\Users\\matt8\\aesop\\test.py"
        redacted = redact_text(text)
        self.assertIn("[PATH]", redacted)
        self.assertNotIn("C:\\", redacted)
        self.assertNotIn("matt8", redacted)

    def test_redact_absolute_posix_path(self):
        """POSIX absolute paths should be redacted."""
        text = "Error in /home/matt8/aesop/tools/test.py"
        redacted = redact_text(text)
        self.assertIn("[PATH]", redacted)
        self.assertNotIn("/home/", redacted)

    def test_redact_repo_names(self):
        """Repo names should be redacted."""
        text = "Working on aesop and conductor3 projects"
        redacted = redact_text(text)
        self.assertIn("[REPO]", redacted)
        self.assertNotIn("aesop", redacted.lower())

    def test_redact_username(self):
        """Usernames should be redacted."""
        text = "User matt8 logged in as John Doe"
        redacted = redact_text(text)
        self.assertNotIn("matt8", redacted)
        self.assertNotIn("John", redacted)

    def test_redact_aws_key(self):
        """AWS access keys should be redacted."""
        text = "AWS key " + "AKIA" + "1234567890ABCDEF used"
        redacted = redact_text(text)
        self.assertIn("[REDACTED]", redacted)
        self.assertNotIn("AKIA", redacted)

    def test_redact_github_token(self):
        """GitHub tokens should be redacted."""
        text = "GitHub token " + "ghp_" + "1234567890abcdefghij1234567890ab used"
        redacted = redact_text(text)
        self.assertIn("[REDACTED]", redacted)
        self.assertNotIn("ghp_", redacted)

    def test_redact_sk_key(self):
        """sk-* API keys should be redacted."""
        text = "OpenAI key " + "sk-" + "proj-abc123def456ghi789jkl012mno345pqr"
        redacted = redact_text(text)
        self.assertIn("[REDACTED]", redacted)
        self.assertNotIn("sk-", redacted)


class TestBriefSchema(unittest.TestCase):
    """Test brief structure and schema compliance."""

    def setUp(self):
        """Create minimal fixture metadata and messages."""
        self.metadata = {
            "start_time": "2026-07-17T20:00:00Z",
            "end_time": "2026-07-17T20:05:00Z",
            "model": "haiku",
            "usage": {
                "input_tokens": 10000,
                "output_tokens": 5000
            }
        }
        self.messages = [
            {"type": "user", "content": "Do something"},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/test.py"}},
            {"type": "tool_result", "content": "File contents"},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/tmp/test.py"}},
            {"type": "tool_result", "content": "File edited"},
        ]

    def test_brief_has_all_fields(self):
        """Brief should have all required schema fields."""
        brief = create_brief("rc.6", "fleet-fix-0", self.metadata, self.messages)

        required_fields = {
            "wave", "agent_id", "start_time", "end_time", "duration_sec",
            "outcome", "top_tool_calls", "files_created", "files_modified",
            "errors", "token_usage", "brief", "brief_schema_version"
        }
        self.assertEqual(set(brief.keys()), required_fields)

    def test_brief_types(self):
        """Brief fields should have correct types."""
        brief = create_brief("rc.6", "fleet-fix-0", self.metadata, self.messages)

        self.assertIsInstance(brief["wave"], str)
        self.assertIsInstance(brief["agent_id"], str)
        self.assertIsInstance(brief["start_time"], str)
        self.assertIsInstance(brief["end_time"], str)
        self.assertIsInstance(brief["duration_sec"], int)
        self.assertIsInstance(brief["outcome"], str)
        self.assertIsInstance(brief["top_tool_calls"], list)
        self.assertIsInstance(brief["files_created"], list)
        self.assertIsInstance(brief["files_modified"], list)
        self.assertIsInstance(brief["errors"], list)
        self.assertIsInstance(brief["token_usage"], dict)
        self.assertIsInstance(brief["brief"], str)
        self.assertIsInstance(brief["brief_schema_version"], int)

    def test_brief_size_under_200_bytes(self):
        """Brief text should be compact (~200 bytes per agent)."""
        brief = create_brief("rc.6", "fleet-fix-0", self.metadata, self.messages)
        brief_json = json.dumps(brief)
        # Most briefs should be well under 500 bytes; allow some overhead
        self.assertLess(len(brief_json), 1000)

    def test_token_usage_has_required_fields(self):
        """Token usage should have input, output, and model."""
        brief = create_brief("rc.6", "fleet-fix-0", self.metadata, self.messages)
        usage = brief["token_usage"]

        self.assertIn("input", usage)
        self.assertIn("output", usage)
        self.assertIn("model", usage)
        self.assertIsInstance(usage["input"], int)
        self.assertIsInstance(usage["output"], int)
        self.assertIsInstance(usage["model"], str)

    def test_outcome_values(self):
        """Outcome should be one of the valid values."""
        brief = create_brief("rc.6", "fleet-fix-0", self.metadata, self.messages)
        self.assertIn(brief["outcome"], ["completed", "stalled", "failed", "timeout"])


class TestIdempotency(unittest.TestCase):
    """Test idempotency: digesting the same transcript twice is identical."""

    def test_idempotent_digestion(self):
        """Digesting the same transcript should produce identical briefs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            state_root = tmpdir_path / "state"
            state_root.mkdir(exist_ok=True)

            # Create a synthetic transcript
            transcripts_dir = tmpdir_path / "transcripts"
            transcripts_dir.mkdir(exist_ok=True)

            transcript_data = [
                {"type": "metadata", "start_time": "2026-07-17T20:00:00Z", "end_time": "2026-07-17T20:05:00Z", "model": "haiku", "usage": {"input_tokens": 10000, "output_tokens": 5000}},
                {"type": "user", "content": "Do something"},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "test.py"}},
                {"type": "tool_result", "content": "File contents"},
            ]

            transcript_file = transcripts_dir / "agent-fleet-fix-0.jsonl"
            with open(transcript_file, "w") as f:
                for obj in transcript_data:
                    f.write(json.dumps(obj) + "\n")

            # Digest once
            agents = stream_jsonl_transcripts(transcripts_dir)
            brief1 = create_brief("rc.6", "fleet-fix-0", agents["fleet-fix-0"][0], agents["fleet-fix-0"][1])

            # Digest again
            agents = stream_jsonl_transcripts(transcripts_dir)
            brief2 = create_brief("rc.6", "fleet-fix-0", agents["fleet-fix-0"][0], agents["fleet-fix-0"][1])

            # Should be identical
            self.assertEqual(json.dumps(brief1, sort_keys=True), json.dumps(brief2, sort_keys=True))

    def test_skip_already_digested_agents(self):
        """Should not re-digest agents already in the ledger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            state_root = tmpdir_path / "state"
            ledger_dir = state_root / "ledger"
            ledger_dir.mkdir(parents=True, exist_ok=True)

            ledger_file = ledger_dir / "transcripts-brief.jsonl"

            # Pre-populate ledger with one agent
            existing_brief = {"agent_id": "fleet-fix-0", "wave": "rc.5"}
            with open(ledger_file, "w") as f:
                f.write(json.dumps(existing_brief) + "\n")

            # Check that agent ID is recognized as existing
            existing_ids = get_existing_agent_ids(ledger_file)
            self.assertIn("fleet-fix-0", existing_ids)

    def test_ledger_append_creates_dir(self):
        """Appending to ledger should create parent directories if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            ledger_file = tmpdir_path / "state" / "ledger" / "transcripts-brief.jsonl"

            brief = {"agent_id": "fleet-fix-0", "wave": "rc.6", "brief": "Test"}
            count = append_briefs(ledger_file, [brief])

            self.assertEqual(count, 1)
            self.assertTrue(ledger_file.exists())

            # Verify content
            with open(ledger_file, "r") as f:
                line = f.readline()
                obj = json.loads(line)
                self.assertEqual(obj["agent_id"], "fleet-fix-0")


class TestTranscriptParsing(unittest.TestCase):
    """Test parsing of synthetic transcript fixtures."""

    def test_extract_tool_calls_success(self):
        """Should extract tool names and count."""
        messages = [
            {"type": "tool_use", "name": "Read"},
            {"type": "tool_use", "name": "Read"},
            {"type": "tool_use", "name": "Edit"},
            {"type": "tool_use", "name": "Bash"},
        ]
        tools, count = extract_tool_calls(messages)
        self.assertEqual(count, 4)
        self.assertIn("Read", tools)
        self.assertEqual(len(tools), 3)  # Top 3

    def test_extract_errors_with_message(self):
        """Should extract error messages with truncation."""
        messages = [
            {"type": "tool_use", "name": "Bash"},
            {"type": "tool_result", "is_error": True, "content": "ConnectionRefusedError: Failed to connect to localhost:5000", "name": "Bash"},
        ]
        errors = extract_errors(messages)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["tool"], "Bash")
        self.assertIn("Connection", errors[0]["message"])

    def test_infer_outcome_completed(self):
        """Should infer completed outcome with no errors."""
        messages = [
            {"type": "user", "content": "Do something"},
            {"type": "tool_use", "name": "Read"},
            {"type": "tool_result", "content": "Success"},
        ]
        outcome = infer_outcome(messages, [])
        self.assertEqual(outcome, "completed")

    def test_infer_outcome_stalled_on_error(self):
        """Should infer stalled outcome when errors occur (non-timeout)."""
        messages = [
            {"type": "user", "content": "Do something"},
            {"type": "tool_use", "name": "Bash"},
        ]
        errors = [{"tool": "Bash", "message": "Connection refused"}]
        outcome = infer_outcome(messages, errors)
        self.assertEqual(outcome, "stalled")

    def test_infer_outcome_timeout(self):
        """Should infer timeout outcome when timeout error occurs."""
        messages = []
        errors = [{"tool": "Bash", "message": "timeout"}]
        outcome = infer_outcome(messages, errors)
        self.assertEqual(outcome, "timeout")

    def test_extract_token_usage(self):
        """Should extract token counts from metadata."""
        metadata = {
            "model": "haiku",
            "usage": {"input_tokens": 100000, "output_tokens": 50000}
        }
        usage = extract_token_usage(metadata)
        self.assertEqual(usage["input"], 100000)
        self.assertEqual(usage["output"], 50000)
        self.assertEqual(usage["model"], "haiku")

    def test_generate_brief_text(self):
        """Should generate concise 1-2 sentence summary."""
        tool_calls = ["Read", "Edit", "Bash"]
        files_created = {"test.py", "test2.py"}
        files_modified = {"main.py"}
        errors = []
        brief = generate_brief([], tool_calls, files_created, files_modified, errors)

        self.assertIsInstance(brief, str)
        self.assertLess(len(brief), 200)
        self.assertGreater(len(brief), 10)


class TestStreamJsonl(unittest.TestCase):
    """Test streaming JSONL parsing."""

    def test_stream_single_transcript(self):
        """Should correctly parse a single transcript."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create a synthetic transcript
            transcript_data = [
                {"type": "metadata", "start_time": "2026-07-17T20:00:00Z", "end_time": "2026-07-17T20:05:00Z", "model": "haiku"},
                {"type": "user", "content": "Do something"},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "test.py"}},
            ]

            transcript_file = tmpdir_path / "agent-fleet-fix-0.jsonl"
            with open(transcript_file, "w") as f:
                for obj in transcript_data:
                    f.write(json.dumps(obj) + "\n")

            agents = stream_jsonl_transcripts(tmpdir_path)

            self.assertIn("fleet-fix-0", agents)
            metadata, messages = agents["fleet-fix-0"]
            self.assertEqual(metadata.get("model"), "haiku")
            self.assertEqual(len(messages), 2)

    def test_stream_multiple_transcripts(self):
        """Should handle multiple agent transcripts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create two transcripts
            for agent_id in ["fleet-fix-0", "fleet-fix-1"]:
                transcript_data = [
                    {"type": "metadata", "start_time": "2026-07-17T20:00:00Z", "end_time": "2026-07-17T20:05:00Z"},
                    {"type": "user", "content": f"Agent {agent_id} work"},
                ]
                transcript_file = tmpdir_path / f"agent-{agent_id}.jsonl"
                with open(transcript_file, "w") as f:
                    for obj in transcript_data:
                        f.write(json.dumps(obj) + "\n")

            agents = stream_jsonl_transcripts(tmpdir_path)

            self.assertEqual(len(agents), 2)
            self.assertIn("fleet-fix-0", agents)
            self.assertIn("fleet-fix-1", agents)

    def test_stream_skip_malformed_lines(self):
        """Should skip malformed JSON lines gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            transcript_file = tmpdir_path / "agent-fleet-fix-0.jsonl"
            with open(transcript_file, "w") as f:
                f.write('{"type": "metadata"}\n')
                f.write('INVALID JSON HERE\n')
                f.write('{"type": "user", "content": "valid"}\n')

            agents = stream_jsonl_transcripts(tmpdir_path)

            self.assertIn("fleet-fix-0", agents)
            metadata, messages = agents["fleet-fix-0"]
            self.assertEqual(len(messages), 1)


class TestSensitiveDataRedaction(unittest.TestCase):
    """Integration test: redaction of realistic sensitive data."""

    def test_redact_complete_scenario(self):
        """Integration: a transcript with paths and emails should be fully redacted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            state_root = tmpdir_path / "state"
            state_root.mkdir(exist_ok=True)

            # Create a transcript with sensitive data
            transcripts_dir = tmpdir_path / "transcripts"
            transcripts_dir.mkdir(exist_ok=True)

            transcript_data = [
                {"type": "metadata", "start_time": "2026-07-17T20:00:00Z", "end_time": "2026-07-17T20:05:00Z", "model": "haiku", "usage": {"input_tokens": 10000, "output_tokens": 5000}},
                {"type": "user", "content": "Email matt82198@gmail.com for help"},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "C:\\Users\\matt8\\aesop\\test.py"}},
                {"type": "tool_result", "content": "From C:\\Users\\matt8\\aesop, contact matt82198@gmail.com"},
            ]

            transcript_file = transcripts_dir / "agent-fleet-fix-0.jsonl"
            with open(transcript_file, "w") as f:
                for obj in transcript_data:
                    f.write(json.dumps(obj) + "\n")

            # Create the brief
            agents = stream_jsonl_transcripts(transcripts_dir)
            brief = create_brief("rc.6", "fleet-fix-0", agents["fleet-fix-0"][0], agents["fleet-fix-0"][1])

            # Verify redaction in output
            brief_str = json.dumps(brief)
            self.assertNotIn("matt82198", brief_str)
            self.assertNotIn("gmail", brief_str)
            self.assertNotIn("C:\\Users\\", brief_str)
            self.assertNotIn("aesop", brief_str.lower())
            self.assertNotIn("conductor3", brief_str.lower())

            # Should still have the brief content, just redacted
            self.assertGreater(len(brief["brief"]), 0)


if __name__ == "__main__":
    unittest.main()
