"""Wave dispatch phase inference: phase comes from structured transcript fields only.

Contract under test (ui/wave_dispatch.py::_infer_agent_phase_from_transcript):
  - 'stall' and 'done' are derived from structured transcript fields (entry type,
    message.stop_reason, content-block types, a structured API-error marker), never
    from a free-text substring such as 'error' appearing anywhere in the tail.
  - When the structured signal is absent the phase is 'unknown', never a guess.

Run: python -I tests/test_wave_dispatch.py
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

import wave_dispatch  # noqa: E402


def _user_prompt(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _tool_result(text):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": text}]}}


def _assistant_text(text, stop_reason=None):
    return {"type": "assistant", "message": {"role": "assistant", "stop_reason": stop_reason,
            "content": [{"type": "text", "text": text}]}}


def _assistant_tool_use(name="Bash"):
    return {"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": "t1", "name": name, "input": {}}]}}


class PhaseFromStructuredFieldsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="aesop-wave-dispatch-phase-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _transcript(self, records, raw_lines=None):
        path = self.root / "agent-fixture.jsonl"
        if raw_lines is not None:
            body = "\n".join(raw_lines) + "\n"
        else:
            body = "\n".join(json.dumps(r) for r in records) + "\n"
        path.write_text(body, encoding="utf-8")
        return path

    def test_healthy_agent_with_zero_errors_in_tail_is_not_stall(self):
        """Repro 1: a healthy agent whose tail says '0 errors' must not read as stall."""
        path = self._transcript([
            _user_prompt("run the suite"),
            _assistant_tool_use("Bash"),
            _tool_result("ran 412 tests, 0 errors, 0 failures"),
            _assistant_tool_use("Bash"),
        ])
        phase = wave_dispatch._infer_agent_phase_from_transcript(path)
        self.assertEqual(phase, "tool-use")
        self.assertNotEqual(phase, "stall")

    def test_working_agent_with_three_text_only_turns_is_not_done(self):
        """Repro 2: three text-only assistant turns, no end_turn, is still working."""
        path = self._transcript([
            _user_prompt("review the module"),
            _assistant_text("Reading the file first."),
            _assistant_text("Found the first issue."),
            _assistant_text("Checking the second case."),
        ])
        phase = wave_dispatch._infer_agent_phase_from_transcript(path)
        self.assertEqual(phase, "thinking")
        self.assertNotEqual(phase, "done")

    def test_end_turn_final_assistant_turn_is_done(self):
        path = self._transcript([
            _user_prompt("review the module"),
            _assistant_tool_use("Read"),
            _tool_result("file body"),
            _assistant_text("Review complete, no defects.", stop_reason="end_turn"),
        ])
        self.assertEqual(wave_dispatch._infer_agent_phase_from_transcript(path), "done")

    def test_structured_api_error_marker_is_stall(self):
        path = self._transcript([
            _user_prompt("run the suite"),
            _assistant_tool_use("Bash"),
            _tool_result("ok"),
            {"type": "assistant", "isApiErrorMessage": True,
             "message": {"role": "assistant", "stop_reason": None,
                         "content": [{"type": "text", "text": "API Error"}]}},
        ])
        self.assertEqual(wave_dispatch._infer_agent_phase_from_transcript(path), "stall")

    def test_error_word_in_text_without_structured_marker_is_not_stall(self):
        path = self._transcript([
            _user_prompt("check the logs"),
            _assistant_text("[error] this line is only prose, not an error record"),
        ])
        phase = wave_dispatch._infer_agent_phase_from_transcript(path)
        self.assertNotEqual(phase, "stall")

    def test_free_text_records_without_structure_are_unknown(self):
        """No message/content structure at all: the phase is unknown, not a guess."""
        path = self._transcript(None, raw_lines=[
            json.dumps({"type": "user", "text": "User dispatch"}),
            json.dumps({"type": "assistant", "text": "Assistant completion"}),
            json.dumps({"type": "assistant", "text": "Another completion"}),
        ])
        self.assertEqual(wave_dispatch._infer_agent_phase_from_transcript(path), "unknown")

    def test_unparseable_tail_is_unknown(self):
        path = self._transcript(None, raw_lines=["not json at all", "{truncated"])
        self.assertEqual(wave_dispatch._infer_agent_phase_from_transcript(path), "unknown")

    def test_plain_user_prompt_is_dispatch_and_tool_result_is_thinking(self):
        dispatch = self._transcript([_user_prompt("start")])
        self.assertEqual(wave_dispatch._infer_agent_phase_from_transcript(dispatch), "dispatch")
        result = self._transcript([_assistant_tool_use("Read"), _tool_result("body")])
        self.assertEqual(wave_dispatch._infer_agent_phase_from_transcript(result), "thinking")

    def test_long_final_record_is_read_whole_not_truncated_to_unknown(self):
        """A final tool_use record larger than the old 2KB tail must still be seen."""
        big_input = {"command": "x" * 8000}
        record = {"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use",
                  "content": [{"type": "tool_use", "id": "t9", "name": "Write", "input": big_input}]}}
        path = self._transcript([_user_prompt("write it"), record])
        self.assertEqual(wave_dispatch._infer_agent_phase_from_transcript(path), "tool-use")


if __name__ == "__main__":
    unittest.main()
