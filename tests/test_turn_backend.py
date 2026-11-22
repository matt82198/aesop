#!/usr/bin/env python3
"""Tests for driver/turn_backend.py (TUI driver plan, phase P1).

Offline only: every outcome is driven through FakeTransport, so no socket
opens. Covers ok (0), usage/base-url guard (2), unreachable (3), HTTP error
(4), timeout (5), refusal (6, zero transport calls), malformed replies (4),
and the explicit proxy-ignoring option of the shared OpenAI-compatible transport.

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import os
import socket
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import activity_feed_isolation  # noqa: E402,F401  (pins AESOP_ACTIVITY_FEED; see that module)
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

import turn_backend as tb  # noqa: E402
from openai_compatible_driver import make_openai_compatible_transport  # noqa: E402

OK_REPLY = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
LOCAL_BASE = "http://127.0.0.1:11434/v1"


class FakeTransport:
    """Records every payload; returns a canned reply or raises a canned error."""

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)
        if self.error is not None:
            raise self.error
        return self.reply


def wrapped(cause):
    """Mimic make_openai_compatible_transport: a RuntimeError chained from the real cause."""
    err = RuntimeError("OpenAI-compatible API request failed: simulated")
    err.__cause__ = cause
    return err


class DefaultProviderTests(unittest.TestCase):
    """The default provider is the local Ollama endpoint with the qwen3-coder model."""

    def test_default_provider_is_local_ollama_qwen(self):
        provider = tb.default_provider()
        self.assertEqual(provider.name, "ollama")
        self.assertEqual(provider.base_url, LOCAL_BASE)
        self.assertEqual(provider.model, "qwen3-coder:30b")
        self.assertTrue(provider.is_local)


class RunTurnOutcomeTests(unittest.TestCase):
    """Each transport outcome maps to its documented exit code."""

    def setUp(self):
        self.provider = tb.default_provider()
        self.messages = [{"role": "user", "content": "reply with exactly: ok"}]

    def test_ok_reply_maps_text_and_sends_messages(self):
        fake = FakeTransport(reply=OK_REPLY)
        result = tb.run_turn(self.provider, self.messages, transport=fake)
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "ok")
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.model, "qwen3-coder:30b")
        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(result.error)
        self.assertIsInstance(result.elapsed_ms, int)
        self.assertGreaterEqual(result.elapsed_ms, 0)
        self.assertEqual(len(fake.payloads), 1)
        self.assertEqual(fake.payloads[0]["model"], "qwen3-coder:30b")
        self.assertEqual(fake.payloads[0]["messages"], self.messages)

    def test_unreachable_wrapped_urlerror_is_exit_3(self):
        cause = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        result = tb.run_turn(self.provider, self.messages, transport=FakeTransport(error=wrapped(cause)))
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 3)
        self.assertTrue(result.error)

    def test_bare_connection_error_is_exit_3(self):
        result = tb.run_turn(self.provider, self.messages, transport=FakeTransport(error=ConnectionRefusedError("no")))
        self.assertEqual(result.exit_code, 3)

    def test_http_error_is_exit_4(self):
        cause = urllib.error.HTTPError(LOCAL_BASE + "/chat/completions", 404, "Not Found", None, None)
        result = tb.run_turn(self.provider, self.messages, transport=FakeTransport(error=wrapped(cause)))
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 4)

    def test_timeout_wrapped_urlerror_is_exit_5(self):
        cause = urllib.error.URLError(socket.timeout("timed out"))
        result = tb.run_turn(self.provider, self.messages, transport=FakeTransport(error=wrapped(cause)))
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 5)

    def test_raw_timeout_error_is_exit_5(self):
        result = tb.run_turn(self.provider, self.messages, transport=FakeTransport(error=TimeoutError("read timed out")))
        self.assertEqual(result.exit_code, 5)

    def test_unexpected_transport_failure_fails_closed_as_exit_4(self):
        result = tb.run_turn(self.provider, self.messages, transport=FakeTransport(error=RuntimeError("size limit")))
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 4)

    def test_malformed_replies_are_exit_4(self):
        bad_replies = [
            {},
            {"choices": []},
            {"choices": [{"message": {"content": None}}]},
            {"choices": [{"message": {"content": "   "}}]},
            "not a dict",
        ]
        for reply in bad_replies:
            with self.subTest(reply=reply):
                result = tb.run_turn(self.provider, self.messages, transport=FakeTransport(reply=reply))
                self.assertFalse(result.ok)
                self.assertEqual(result.exit_code, 4)


class RefusalTests(unittest.TestCase):
    """Anthropic and Claude ids are refused before any transport call (P4-lite)."""

    def setUp(self):
        self.messages = [{"role": "user", "content": "hi"}]

    def test_claude_model_id_refused_with_zero_transport_calls(self):
        for model in ("claude-3-haiku", "Claude-Opus-5"):
            with self.subTest(model=model):
                fake = FakeTransport(reply=OK_REPLY)
                provider = tb.TurnProvider("ollama", LOCAL_BASE, model)
                result = tb.run_turn(provider, self.messages, transport=fake)
                self.assertEqual(result.exit_code, 6)
                self.assertFalse(result.ok)
                self.assertEqual(fake.payloads, [])

    def test_anthropic_provider_name_refused_with_zero_transport_calls(self):
        fake = FakeTransport(reply=OK_REPLY)
        provider = tb.TurnProvider("anthropic", LOCAL_BASE, "some-model")
        result = tb.run_turn(provider, self.messages, transport=fake)
        self.assertEqual(result.exit_code, 6)
        self.assertEqual(fake.payloads, [])

    def test_anthropic_host_refused_before_transport_is_built(self):
        provider = tb.TurnProvider("gateway", "https://api.anthropic.com/v1", "gpt-x")
        with mock.patch.object(tb, "make_openai_compatible_transport") as factory:
            result = tb.run_turn(provider, self.messages)
        self.assertEqual(result.exit_code, 6)
        factory.assert_not_called()

    def test_claude_ai_host_refused(self):
        self.assertIsNotNone(tb.refusal_reason("gateway", "gpt-x", "https://claude.ai/api/v1"))

    def test_local_ollama_is_not_refused(self):
        self.assertIsNone(tb.refusal_reason("ollama", "qwen3-coder:30b", LOCAL_BASE))


class BaseUrlGuardTests(unittest.TestCase):
    """A private-range base_url is rejected (exit 2) before any transport call."""

    def test_private_range_base_url_is_usage_exit_2(self):
        fake = FakeTransport(reply=OK_REPLY)
        provider = tb.TurnProvider("ollama", "http://10.0.0.5:11434/v1", "qwen3-coder:30b")
        result = tb.run_turn(provider, [{"role": "user", "content": "hi"}], transport=fake)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(fake.payloads, [])


class ProxyIgnoreTests(unittest.TestCase):
    """Proxy env vars are ignored explicitly; the shared transport gets a no-proxy handler."""

    def test_transport_with_ignore_env_proxy_installs_empty_proxy_handler(self):
        captured = {}

        def fake_build_opener(*handlers):
            captured["handlers"] = handlers
            opener = mock.Mock()
            opener.open.side_effect = RuntimeError("stop before network")
            return opener

        transport = make_openai_compatible_transport(
            LOCAL_BASE,
            api_key_env="AESOP_TURN_API_KEY",
            timeout_s=5,
            is_local=True,
            ignore_env_proxy=True,
        )
        env = {"HTTP_PROXY": "http://proxy.invalid:3128", "http_proxy": "http://proxy.invalid:3128"}
        with mock.patch.dict(os.environ, env), mock.patch.object(
            urllib.request, "build_opener", side_effect=fake_build_opener
        ):
            with self.assertRaises(RuntimeError):
                transport({"model": "m", "messages": []})
        proxy_handlers = [h for h in captured["handlers"] if isinstance(h, urllib.request.ProxyHandler)]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})

    def test_run_turn_builds_transport_with_proxy_ignored(self):
        with mock.patch.object(tb, "make_openai_compatible_transport", return_value=FakeTransport(reply=OK_REPLY)) as factory:
            result = tb.run_turn(tb.default_provider(), [{"role": "user", "content": "hi"}], timeout_s=9)
        self.assertTrue(result.ok)
        kwargs = factory.call_args.kwargs
        self.assertTrue(kwargs["ignore_env_proxy"])
        self.assertTrue(kwargs["is_local"])
        self.assertEqual(kwargs["timeout_s"], 9)


if __name__ == "__main__":
    unittest.main()
