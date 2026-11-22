#!/usr/bin/env python3
"""Tests for driver/turn.py: `python -m driver.turn` (TUI driver plan, phase P1).

Offline only: main() is driven with an injected FakeTransport, injected stdin
and injected stdout/stderr. Exit codes match `aesop ask`:
0 ok, 2 usage, 3 unreachable, 4 provider/model error, 5 timeout, 6 refused.

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import activity_feed_isolation  # noqa: E402,F401  (pins AESOP_ACTIVITY_FEED; see that module)
import unittest
import urllib.error
import socket
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

import turn as turn_cli  # noqa: E402

OK_REPLY = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
MANGO_REPLY = {"choices": [{"message": {"role": "assistant", "content": "mango"}}]}


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


class PipedStdin(io.StringIO):
    """A non-TTY stdin carrying the given text."""

    def isatty(self):
        return False


class TtyStdin:
    """A TTY stdin: the CLI must never read it."""

    def isatty(self):
        return True

    def read(self, *args):
        raise AssertionError("TTY stdin must not be read")


def run_cli(argv, *, stdin_text="", tty=False, transport=None):
    out, err = io.StringIO(), io.StringIO()
    stdin = TtyStdin() if tty else PipedStdin(stdin_text)
    code = turn_cli.main(argv, stdin=stdin, stdout=out, stderr=err, transport=transport)
    return code, out.getvalue(), err.getvalue()


def user_content(fake):
    return fake.payloads[0]["messages"][-1]["content"]


class OkPathTests(unittest.TestCase):
    """Exit 0 paths: plain text, JSON object, stdin context, system prompt."""

    def test_json_ok_prints_one_object_and_exits_zero(self):
        fake = FakeTransport(reply=OK_REPLY)
        code, out, _ = run_cli(["--json", "reply with exactly: ok"], transport=fake)
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 1)
        obj = json.loads(lines[0])
        self.assertIs(obj["ok"], True)
        self.assertEqual(obj["provider"], "ollama")
        self.assertEqual(obj["model"], "qwen3-coder:30b")
        self.assertEqual(obj["text"], "ok")
        self.assertIsInstance(obj["elapsed_ms"], int)
        self.assertNotIn("error", obj)

    def test_plain_ok_prints_text_only(self):
        code, out, err = run_cli(["reply with exactly: ok"], transport=FakeTransport(reply=OK_REPLY))
        self.assertEqual(code, 0)
        self.assertEqual(out, "ok\n")
        self.assertEqual(err, "")

    def test_piped_stdin_is_appended_as_context(self):
        fake = FakeTransport(reply=MANGO_REPLY)
        code, out, _ = run_cli(
            ["what is the secret word? one word"],
            stdin_text="the secret word is mango\n",
            transport=fake,
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "mango\n")
        content = user_content(fake)
        self.assertIn("the secret word is mango", content)
        self.assertIn("what is the secret word? one word", content)
        self.assertLess(content.index("the secret word is mango"), content.index("what is the secret word?"))

    def test_empty_piped_stdin_adds_no_context(self):
        fake = FakeTransport(reply=OK_REPLY)
        code, _, _ = run_cli(["reply with exactly: ok"], stdin_text="", transport=fake)
        self.assertEqual(code, 0)
        self.assertEqual(user_content(fake), "reply with exactly: ok")

    def test_tty_stdin_is_never_read(self):
        fake = FakeTransport(reply=OK_REPLY)
        code, _, _ = run_cli(["hi"], tty=True, transport=fake)
        self.assertEqual(code, 0)
        self.assertEqual(user_content(fake), "hi")

    def test_system_prompt_is_first_message(self):
        fake = FakeTransport(reply=OK_REPLY)
        code, _, _ = run_cli(["--system", "be brief", "hi"], transport=fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.payloads[0]["messages"][0], {"role": "system", "content": "be brief"})

    def test_bare_model_id_uses_default_provider(self):
        fake = FakeTransport(reply=OK_REPLY)
        code, _, _ = run_cli(["--model", "qwen3-coder:30b", "hi"], transport=fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.payloads[0]["model"], "qwen3-coder:30b")


class UsageTests(unittest.TestCase):
    """Exit 2: usage errors, no transport call."""

    def test_missing_prompt_is_usage_exit_2(self):
        fake = FakeTransport(reply=OK_REPLY)
        code, out, err = run_cli([], transport=fake)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("usage", err)
        self.assertEqual(fake.payloads, [])

    def test_unknown_option_is_usage_exit_2(self):
        fake = FakeTransport(reply=OK_REPLY)
        code, _, _ = run_cli(["--bogus", "hi"], transport=fake)
        self.assertEqual(code, 2)
        self.assertEqual(fake.payloads, [])

    def test_bad_timeout_is_usage_exit_2(self):
        for value in ("0", "-3", "abc"):
            with self.subTest(timeout=value):
                fake = FakeTransport(reply=OK_REPLY)
                code, _, _ = run_cli(["--timeout", value, "hi"], transport=fake)
                self.assertEqual(code, 2)
                self.assertEqual(fake.payloads, [])

    def test_unknown_provider_is_usage_exit_2(self):
        fake = FakeTransport(reply=OK_REPLY)
        code, _, err = run_cli(["--model", "openai/gpt-4o", "hi"], transport=fake)
        self.assertEqual(code, 2)
        self.assertIn("openai", err)
        self.assertEqual(fake.payloads, [])


class RefusalTests(unittest.TestCase):
    """Exit 6: Anthropic or Claude ids are refused before any transport call."""

    def test_anthropic_provider_refused_exit_6_zero_calls(self):
        fake = FakeTransport(reply=OK_REPLY)
        code, out, err = run_cli(["--model", "anthropic/claude-x", "hi"], transport=fake)
        self.assertEqual(code, 6)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())
        self.assertEqual(fake.payloads, [])

    def test_claude_model_on_ollama_refused_exit_6(self):
        fake = FakeTransport(reply=OK_REPLY)
        code, _, _ = run_cli(["--model", "ollama/claude-3-haiku", "hi"], transport=fake)
        self.assertEqual(code, 6)
        self.assertEqual(fake.payloads, [])

    def test_refusal_json_object_reports_failure(self):
        fake = FakeTransport(reply=OK_REPLY)
        code, out, _ = run_cli(["--json", "--model", "anthropic/claude-x", "hi"], transport=fake)
        self.assertEqual(code, 6)
        obj = json.loads(out.strip())
        self.assertIs(obj["ok"], False)
        self.assertTrue(obj["error"])
        self.assertEqual(fake.payloads, [])


class FailureExitTests(unittest.TestCase):
    """Exit 3, 4, 5 from transport failures, plain and --json."""

    def test_unreachable_exit_3(self):
        cause = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        code, out, err = run_cli(["hi"], transport=_chain(cause))
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("ollama", err)

    def test_http_error_exit_4(self):
        cause = urllib.error.HTTPError("http://127.0.0.1:11434/v1/chat/completions", 404, "Not Found", None, None)
        code, _, _ = run_cli(["hi"], transport=_chain(cause))
        self.assertEqual(code, 4)

    def test_timeout_exit_5(self):
        cause = urllib.error.URLError(socket.timeout("timed out"))
        code, _, _ = run_cli(["hi"], transport=_chain(cause))
        self.assertEqual(code, 5)

    def test_json_failure_object_carries_error(self):
        cause = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        code, out, _ = run_cli(["--json", "hi"], transport=_chain(cause))
        self.assertEqual(code, 3)
        obj = json.loads(out.strip())
        self.assertIs(obj["ok"], False)
        self.assertEqual(obj["provider"], "ollama")
        self.assertTrue(obj["error"])


def _chain(cause):
    """A transport that raises the way make_openai_compatible_transport does (chained cause)."""
    err = RuntimeError("OpenAI-compatible API request failed: simulated")
    err.__cause__ = cause
    return FakeTransport(error=err)


if __name__ == "__main__":
    unittest.main()
