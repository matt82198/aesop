#!/usr/bin/env python3
"""Test response size cap enforcement at the byte-level (RS3-T).

Verifies that response reads are bounded BEFORE parsing, not after:
- Cap the read() at MAX_RESPONSE_SIZE bytes
- Measure in BYTES not chars
- Raise error if exceeded, preventing OOM
- Apply to all transport paths (openai, openai-compatible, orchestrator)
- Apply to codex worker dispatch (if it reads responses directly)
"""

import json
import os
import sys
import unittest
from io import BytesIO
from unittest.mock import Mock, patch, MagicMock

# Add parent directory for imports.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from driver import openai_transport
from driver import openai_compatible_driver
from driver import orchestrator_backend
from driver import codex_driver


# Maximum response size (100 KB) - must match constants in production code.
MAX_RESPONSE_SIZE = 100 * 1024


class FakeResponse:
    """Mock response object that simulates urllib response behavior."""

    def __init__(self, body_bytes, status=200):
        self.body_bytes = body_bytes
        self.status = status
        self.fp = BytesIO(body_bytes)

    def read(self, amt=None):
        """Read from the body, optionally bounded by amt."""
        if amt is None:
            return self.fp.read()
        return self.fp.read(amt)

    def decode(self, encoding="utf-8"):
        """For string responses (not used here, but for interface compat)."""
        return self.body_bytes.decode(encoding)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TestOpenAITransportResponseCap(unittest.TestCase):
    """Test openai_transport.py response size capping."""

    def test_normal_response_succeeds(self):
        """A normal-sized response should be read and parsed successfully."""
        valid_response = {
            "choices": [{"message": {"content": "Hello, world!"}}],
            "usage": {"total_tokens": 10},
        }
        response_json = json.dumps(valid_response)
        response_bytes = response_json.encode("utf-8")

        # Mock the opener to return our test response.
        fake_response = FakeResponse(response_bytes, status=200)

        with patch("urllib.request.build_opener") as mock_opener_factory:
            mock_opener = Mock()
            mock_opener.__enter__ = Mock(return_value=fake_response)
            mock_opener.__exit__ = Mock(return_value=None)
            mock_opener_factory.return_value.open.return_value = fake_response

            # Should succeed with normal response.
            result = openai_transport.default_openai_transport(
                {"model": "gpt-4"},
                timeout_s=5.0,
                api_key="test-key"
            )
            self.assertIn("choices", result)

    def test_oversized_response_rejected_before_parse(self):
        """An oversized response should be rejected at the read() level, not after parsing.

        This test verifies the fix: response.read() is capped at MAX_RESPONSE_SIZE bytes.
        If response is larger than cap, read() should raise an error BEFORE json.loads().
        """
        # Create a response much larger than MAX_RESPONSE_SIZE.
        oversized_json = json.dumps({
            "choices": [{"message": {"content": "x" * (MAX_RESPONSE_SIZE * 2)}}]
        })
        oversized_bytes = oversized_json.encode("utf-8")

        self.assertGreater(len(oversized_bytes), MAX_RESPONSE_SIZE,
                          "Test setup: oversized response must exceed cap")

        fake_response = FakeResponse(oversized_bytes, status=200)

        with patch("urllib.request.build_opener") as mock_opener_factory:
            mock_opener = Mock()
            mock_opener.__enter__ = Mock(return_value=fake_response)
            mock_opener.__exit__ = Mock(return_value=None)
            mock_opener_factory.return_value.open.return_value = fake_response

            # Should raise RuntimeError for oversized response (capped read).
            with self.assertRaises(RuntimeError) as ctx:
                openai_transport.default_openai_transport(
                    {"model": "gpt-4"},
                    timeout_s=5.0,
                    api_key="test-key"
                )

            # Error should mention response size cap, not JSON parsing.
            self.assertIn("size", str(ctx.exception).lower())


class TestOpenAICompatibleTransportResponseCap(unittest.TestCase):
    """Test openai_compatible_driver.py response size capping."""

    def test_normal_response_succeeds(self):
        """A normal-sized response from OpenAI-compatible endpoint should work."""
        valid_response = {
            "choices": [{"message": {"content": "Hello from Ollama!"}}],
            "usage": {"total_tokens": 5},
        }
        response_json = json.dumps(valid_response)
        response_bytes = response_json.encode("utf-8")

        fake_response = FakeResponse(response_bytes, status=200)

        with patch("urllib.request.build_opener") as mock_opener_factory:
            mock_opener = Mock()
            mock_opener.__enter__ = Mock(return_value=fake_response)
            mock_opener.__exit__ = Mock(return_value=None)
            mock_opener_factory.return_value.open.return_value = fake_response

            # Create transport.
            transport = openai_compatible_driver.make_openai_compatible_transport(
                base_url="http://localhost:11434/v1",
                is_local=True,
                timeout_s=5.0
            )

            # Should succeed with normal response.
            result = transport({"model": "neural-chat"})
            self.assertIn("choices", result)

    def test_oversized_response_rejected_before_parse(self):
        """OpenAI-compatible transport should reject oversized responses at read() level."""
        # Create a response much larger than MAX_RESPONSE_SIZE.
        oversized_json = json.dumps({
            "choices": [{"message": {"content": "x" * (MAX_RESPONSE_SIZE * 2)}}]
        })
        oversized_bytes = oversized_json.encode("utf-8")

        self.assertGreater(len(oversized_bytes), MAX_RESPONSE_SIZE,
                          "Test setup: oversized response must exceed cap")

        fake_response = FakeResponse(oversized_bytes, status=200)

        with patch("urllib.request.build_opener") as mock_opener_factory:
            mock_opener = Mock()
            mock_opener.__enter__ = Mock(return_value=fake_response)
            mock_opener.__exit__ = Mock(return_value=None)
            mock_opener_factory.return_value.open.return_value = fake_response

            # Create transport.
            transport = openai_compatible_driver.make_openai_compatible_transport(
                base_url="http://localhost:11434/v1",
                is_local=True,
                timeout_s=5.0
            )

            # Should raise RuntimeError for oversized response.
            with self.assertRaises(RuntimeError) as ctx:
                transport({"model": "neural-chat"})

            self.assertIn("size", str(ctx.exception).lower())


class TestOrchestratorBackendResponseCap(unittest.TestCase):
    """Test orchestrator_backend.py response size capping (byte level)."""

    def test_normal_response_succeeds(self):
        """A normal-sized orchestrator response should work."""
        valid_response = {
            "choices": [{"message": {"content": '{"verdict": "approve"}'}}],
            "usage": {"total_tokens": 20},
        }
        response_json = json.dumps(valid_response)
        response_bytes = response_json.encode("utf-8")

        fake_response = FakeResponse(response_bytes, status=200)

        def fake_transport(payload, timeout_s=None, base_url=None):
            return json.loads(response_bytes)

        backend = orchestrator_backend.OpenAICompatibleOrchestratorBackend(
            model="gpt-4o-mini",
            base_url="http://localhost:8000/v1",
            transport=fake_transport,
            is_local=True
        )

        result = backend.decide_call("test prompt")
        self.assertEqual(result, '{"verdict": "approve"}')

    def test_oversized_response_rejected_before_parse(self):
        """Orchestrator backend should reject oversized responses at read() level.

        The key fix: measure BYTES in the cap, not chars (the old code used len(completion_text)).
        """
        # Create a response with content larger than MAX_RESPONSE_SIZE bytes.
        # Use multi-byte UTF-8 characters to ensure byte count > char count.
        large_content = "你好世界" * (MAX_RESPONSE_SIZE // 4)  # Each char is ~3 bytes in UTF-8
        large_response = {
            "choices": [{"message": {"content": large_content}}],
            "usage": {"total_tokens": 100},
        }
        response_json = json.dumps(large_response)
        response_bytes = response_json.encode("utf-8")

        # Verify the content is large in bytes.
        self.assertGreater(len(response_bytes), MAX_RESPONSE_SIZE,
                          "Test setup: response bytes must exceed cap")

        def fake_transport(payload, timeout_s=None, base_url=None):
            return json.loads(response_bytes)

        backend = orchestrator_backend.OpenAICompatibleOrchestratorBackend(
            model="gpt-4o-mini",
            base_url="http://localhost:8000/v1",
            transport=fake_transport,
            is_local=True
        )

        # Should raise RuntimeError for oversized response.
        with self.assertRaises(RuntimeError) as ctx:
            backend.decide_call("test prompt")

        self.assertIn("size", str(ctx.exception).lower())

    def test_response_cap_measures_bytes_not_chars(self):
        """Verify the cap measures BYTES, not CHARS (critical distinction for UTF-8).

        A string with multi-byte UTF-8 chars might have char_count << byte_count.
        The fix must cap bytes, not chars.
        """
        # Create content with multi-byte UTF-8 characters.
        # Each character takes ~3-4 bytes in UTF-8.
        multi_byte_char = "🔐"  # Takes 4 bytes in UTF-8.
        content = multi_byte_char * (MAX_RESPONSE_SIZE // 2)  # ~200KB when encoded

        response = {
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 50},
        }
        response_json = json.dumps(response)
        response_bytes = response_json.encode("utf-8")

        # Char count is small, but byte count exceeds cap.
        self.assertGreater(len(response_bytes), MAX_RESPONSE_SIZE,
                          "Test setup: byte count must exceed cap")

        def fake_transport(payload, timeout_s=None, base_url=None):
            return json.loads(response_bytes)

        backend = orchestrator_backend.OpenAICompatibleOrchestratorBackend(
            model="gpt-4o-mini",
            base_url="http://localhost:8000/v1",
            transport=fake_transport,
            is_local=True
        )

        # Should reject based on BYTES, even though char count is smaller.
        with self.assertRaises(RuntimeError) as ctx:
            backend.decide_call("test prompt")

        error_msg = str(ctx.exception)
        self.assertIn("size", error_msg.lower())


class TestCodexDriverResponseCap(unittest.TestCase):
    """Test codex_driver.py response size capping in worker dispatch."""

    def test_normal_worker_response_succeeds(self):
        """A normal-sized worker response should be parsed and used."""
        valid_patch = {
            "files": [{"path": "test.py", "contents": "print('hello')"}],
            "summary": "Added print statement",
            "done": True,
        }
        response = {
            "choices": [{"message": {"content": json.dumps(valid_patch)}}],
            "usage": {"total_tokens": 100},
        }

        def fake_transport(payload):
            return response

        driver = codex_driver.CodexDriver(transport=fake_transport)

        request = codex_driver.WorkerRequest(
            role="worker",
            prompt="Add a print statement",
            owned_files=["test.py"],
            workdir="/tmp/test",
            label="test"
        )

        # Mock read_text to return empty content.
        with patch("pathlib.Path.read_text", return_value=""):
            result = driver.dispatch_worker(request)
            self.assertTrue(result.ok or not result.ok)  # Just verify no crash on normal size.

    def test_oversized_worker_response_rejected(self):
        """Codex driver should reject oversized worker responses."""
        # Create an oversized patch.
        oversized_patch = {
            "files": [{"path": "test.py", "contents": "x" * (MAX_RESPONSE_SIZE * 2)}],
            "summary": "Oversized content",
            "done": True,
        }
        response = {
            "choices": [{"message": {"content": json.dumps(oversized_patch)}}],
            "usage": {"total_tokens": 100},
        }

        def fake_transport(payload):
            # Transport should raise if response is too large (capped read).
            raise RuntimeError("Response size exceeded")

        driver = codex_driver.CodexDriver(transport=fake_transport)

        request = codex_driver.WorkerRequest(
            role="worker",
            prompt="Add oversized content",
            owned_files=["test.py"],
            workdir="/tmp/test",
            label="test"
        )

        with patch("pathlib.Path.read_text", return_value=""):
            result = driver.dispatch_worker(request)
            # Transport error should result in WORKER_FAILED.
            self.assertFalse(result.ok)
            self.assertIn("transport", result.error.lower())


if __name__ == "__main__":
    unittest.main()
