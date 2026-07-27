#!/usr/bin/env python3
"""Tests for driver/anthropic_driver.py with forced tool-call channel.

Tests verify:
  1. Tool_use responses are parsed correctly
  2. Tool input is validated against WORKER_PATCH_SCHEMA
  3. Refusals (stop_reason=refusal) handled gracefully -> error status
  4. Files are written from tool input (ownership checked)
  5. Multi-turn repair loop threads tool_use blocks correctly
  6. Cumulative token accounting works

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

# Add driver to path.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from agent_driver import WorkerRequest, WORKER_DONE, WORKER_FAILED
from anthropic_driver import AnthropicDriver, _build_submit_work_tool


class FakeAnthropicToolTransport:
    """Fake Anthropic transport returning tool_use responses."""

    def __init__(self, response=None, fail=False, refusal=False):
        """Initialize with a response.

        Args:
            response: complete response dict (with content, usage)
            fail: if True, raise RuntimeError
            refusal: if True, return refusal response (stop_reason=refusal)
        """
        self.fail = fail
        self.refusal = refusal
        self.response = response
        self.call_count = 0
        self.requests = []

    def __call__(self, payload):
        self.requests.append(payload)
        self.call_count += 1

        if self.fail:
            raise RuntimeError("API error")

        if self.refusal:
            return {
                "stop_reason": "refusal",
                "content": [],
                "usage": {"input_tokens": 100, "output_tokens": 0},
            }

        return self.response or {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "submit_work",
                    "input": {
                        "files": [{"path": "main.py", "contents": "# fixed"}],
                        "summary": "Fixed",
                        "done": True,
                    },
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }


class TestToolUseDispatch(unittest.TestCase):
    """Test tool_use dispatch channel."""

    def test_tool_use_response_parsed_correctly(self):
        """Tool_use block is parsed into structured result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            (sandbox / "main.py").write_text("x = 1")

            transport = FakeAnthropicToolTransport()
            driver = AnthropicDriver(transport=transport)

            request = WorkerRequest(
                prompt="Fix the code",
                owned_files=("main.py",),
                workdir=str(sandbox),
                label="test",
            )

            result = driver.dispatch_worker(request)

            self.assertTrue(result.ok)
            self.assertEqual(result.status, WORKER_DONE)
            self.assertIn("main.py", result.files_written)
            self.assertEqual(result.structured["summary"], "Fixed")

    def test_refusal_response_handled_gracefully(self):
        """Refusal (stop_reason=refusal) -> error status, no exception."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            (sandbox / "main.py").write_text("x = 1")

            transport = FakeAnthropicToolTransport(refusal=True)
            driver = AnthropicDriver(transport=transport)

            request = WorkerRequest(
                prompt="Fix the code",
                owned_files=("main.py",),
                workdir=str(sandbox),
                label="test",
            )

            result = driver.dispatch_worker(request)

            self.assertFalse(result.ok)
            self.assertEqual(result.status, WORKER_FAILED)
            self.assertIn("refused", result.error.lower())

    def test_missing_tool_use_block_error(self):
        """Response without tool_use block -> error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            (sandbox / "main.py").write_text("x = 1")

            # Response with no tool_use (e.g., just text).
            response = {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Done"}],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
            transport = FakeAnthropicToolTransport(response=response)
            driver = AnthropicDriver(transport=transport)

            request = WorkerRequest(
                prompt="Fix the code",
                owned_files=("main.py",),
                workdir=str(sandbox),
                label="test",
            )

            result = driver.dispatch_worker(request)

            self.assertFalse(result.ok)
            self.assertIn("tool_use", result.error)

    def test_tool_input_validation_fails(self):
        """Invalid tool input (missing required fields) -> error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            (sandbox / "main.py").write_text("x = 1")

            # Tool input missing "done" field.
            response = {
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "submit_work",
                        "input": {
                            "files": [],
                            "summary": "Fixed",
                            # "done" is missing!
                        },
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
            transport = FakeAnthropicToolTransport(response=response)
            driver = AnthropicDriver(transport=transport)

            request = WorkerRequest(
                prompt="Fix the code",
                owned_files=("main.py",),
                workdir=str(sandbox),
                label="test",
            )

            result = driver.dispatch_worker(request)

            self.assertFalse(result.ok)
            self.assertIn("validation failed", result.error.lower())

    def test_ownership_check_enforced(self):
        """Files outside owned_files rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            (sandbox / "allowed.py").write_text("x = 1")
            (sandbox / "forbidden.py").write_text("y = 2")

            # Tool response tries to modify forbidden.py.
            response = {
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "submit_work",
                        "input": {
                            "files": [
                                {"path": "allowed.py", "contents": "# ok"},
                                {"path": "forbidden.py", "contents": "# NOT OK"},
                            ],
                            "summary": "Fixed",
                            "done": True,
                        },
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
            transport = FakeAnthropicToolTransport(response=response)
            driver = AnthropicDriver(transport=transport)

            request = WorkerRequest(
                prompt="Fix allowed.py",
                owned_files=("allowed.py",),
                workdir=str(sandbox),
                label="test",
            )

            result = driver.dispatch_worker(request)

            self.assertFalse(result.ok)
            self.assertIn("forbidden.py", result.error)

    def test_cumulative_token_accounting(self):
        """Tokens are accumulated across multiple calls."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            (sandbox / "main.py").write_text("x = 1")

            transport = FakeAnthropicToolTransport()
            driver = AnthropicDriver(transport=transport)

            # First call.
            request1 = WorkerRequest(
                prompt="Fix",
                owned_files=("main.py",),
                workdir=str(sandbox),
                label="test1",
            )
            result1 = driver.dispatch_worker(request1)
            tokens_after_first = driver.get_tokens_spent()

            # Second call (same driver).
            request2 = WorkerRequest(
                prompt="Fix again",
                owned_files=("main.py",),
                workdir=str(sandbox),
                label="test2",
            )
            result2 = driver.dispatch_worker(request2)
            tokens_after_second = driver.get_tokens_spent()

            # Tokens should accumulate (each call: 100 input + 50 output = 150).
            self.assertIsNotNone(tokens_after_first)
            self.assertIsNotNone(tokens_after_second)
            self.assertGreater(tokens_after_second, tokens_after_first)


class TestRepairLoopThreading(unittest.TestCase):
    """Test multi-turn repair loop with tool_use."""

    def test_repair_request_contains_prior_failure_output(self):
        """Second dispatch sees first's failure in the prompt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            (sandbox / "main.py").write_text("x = 1")

            # First call returns "good" tool_use that passes the test.
            # In a real repair loop, the orchestrator would run the test,
            # see failure, then build a new request with the failure appended.

            # Here we just verify the transport is called with the right payload structure.
            transport = FakeAnthropicToolTransport()
            driver = AnthropicDriver(transport=transport)

            # First request (initial).
            request1 = WorkerRequest(
                prompt="Fix the bug",
                owned_files=("main.py",),
                workdir=str(sandbox),
                label="test",
            )
            result1 = driver.dispatch_worker(request1)

            # Verify first request was sent.
            self.assertEqual(transport.call_count, 1)
            first_payload = transport.requests[0]
            self.assertIn("Fix the bug", first_payload["messages"][0]["content"])

            # Now simulate a repair attempt (orchestrator appends failure output to prompt).
            repair_prompt = "Fix the bug\n\nTest failed with exit code 1.\nError: AssertionError: x != 2"
            request2 = WorkerRequest(
                prompt=repair_prompt,
                owned_files=("main.py",),
                workdir=str(sandbox),
                label="test",
            )
            result2 = driver.dispatch_worker(request2)

            # Verify second request contains failure output.
            self.assertEqual(transport.call_count, 2)
            second_payload = transport.requests[1]
            self.assertIn("Test failed with exit code 1", second_payload["messages"][0]["content"])
            self.assertIn("AssertionError", second_payload["messages"][0]["content"])

    def test_tool_definition_always_present(self):
        """Every dispatch includes the submit_work tool definition."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir)
            (sandbox / "main.py").write_text("x = 1")

            transport = FakeAnthropicToolTransport()
            driver = AnthropicDriver(transport=transport)

            # Multiple requests.
            for i in range(2):
                request = WorkerRequest(
                    prompt=f"Task {i}",
                    owned_files=("main.py",),
                    workdir=str(sandbox),
                    label="test",
                )
                driver.dispatch_worker(request)

            # Verify each call has tools and tool_choice.
            for payload in transport.requests:
                self.assertIn("tools", payload)
                self.assertIn("tool_choice", payload)
                self.assertEqual(
                    payload["tool_choice"]["name"],
                    "submit_work",
                    "tool_choice should force submit_work",
                )
                self.assertTrue(
                    any(t["name"] == "submit_work" for t in payload["tools"]),
                    "submit_work tool should be in tools list",
                )


class TestSubmitWorkToolSchema(unittest.TestCase):
    """Test the submit_work tool definition."""

    def test_tool_schema_matches_patch_schema(self):
        """Tool input_schema matches WORKER_PATCH_SCHEMA structure."""
        tool = _build_submit_work_tool()

        # Extract the input_schema.
        input_schema = tool["input_schema"]

        # Verify required fields.
        self.assertEqual(
            set(input_schema["required"]),
            {"files", "summary", "done"},
        )

        # Verify files property.
        files_prop = input_schema["properties"]["files"]
        self.assertEqual(files_prop["type"], "array")
        self.assertEqual(
            set(files_prop["items"]["required"]),
            {"path", "contents"},
        )

        # Verify summary and done.
        self.assertEqual(input_schema["properties"]["summary"]["type"], "string")
        self.assertEqual(input_schema["properties"]["done"]["type"], "boolean")


if __name__ == "__main__":
    unittest.main()
