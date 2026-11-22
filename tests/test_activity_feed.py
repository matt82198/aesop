#!/usr/bin/env python3
"""Tests for driver/activity_feed.py and its hooks (one shared activity feed).

Behavioural only: drives run_turn and the OpenAI-compatible driver against the
scriptable loopback fake server (tests/turn_fake_server.py), then reads the feed
file the call wrote. No Anthropic endpoint, no API key, no real Ollama.

Run: python -I -m unittest tests.test_activity_feed   (or python -I -m pytest -q tests/test_activity_feed.py)

stdlib (unittest) + turn_fake_server, ASCII-only, Windows + Linux safe.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
TESTS_DIR = REPO / "tests"
for p in (DRIVER_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import activity_feed as af  # noqa: E402
import turn_backend as tb  # noqa: E402
from openai_compatible_driver import OpenAICompatibleDriver  # noqa: E402
from turn_fake_server import FakeServer  # noqa: E402

MESSAGES = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "say hi"}]


def _events(path: Path):
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.split("\n") if line]


def _env(home: Path, feed: Path, **extra):
    env = {"HOME": str(home), "USERPROFILE": str(home), "AESOP_ACTIVITY_FEED": str(feed)}
    env.update(extra)
    return env


def _json_reply(text: str):
    """Fake-server handler for the non-streamed turn: one JSON chat completion."""
    return lambda reply, request, n: reply.json({"choices": [{"message": {"role": "assistant", "content": text}}]})


def _provider(url: str, model: str = "qwen2.5-coder:14b") -> tb.TurnProvider:
    return tb.TurnProvider("ollama", url, model, is_local=True)


class PathRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aesop-feed-path-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_default_path_is_shared_home_location(self):
        env = {"HOME": str(self.tmp), "USERPROFILE": str(self.tmp / "other"), "AESOP_STATE_ROOT": str(self.tmp / "checkout-state")}
        self.assertEqual(af.feed_path(env), self.tmp / ".aesop" / "state" / "chat" / "activity.jsonl")

    def test_default_path_falls_back_to_userprofile(self):
        self.assertEqual(af.feed_path({"USERPROFILE": str(self.tmp)}), self.tmp / ".aesop" / "state" / "chat" / "activity.jsonl")

    def test_override_env_wins(self):
        override = self.tmp / "custom.jsonl"
        self.assertEqual(af.feed_path({"HOME": str(self.tmp), "AESOP_ACTIVITY_FEED": str(override)}), override)


class WriterSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aesop-feed-writer-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_start_chunk_end_schema_and_caps(self):
        feed = self.tmp / "feed.jsonl"
        writer = af.ActivityFeed(env=_env(self.tmp, feed, AESOP_FROM="codex"), job="j1")
        writer.start(model="ollama/m", prompt="p" * 2000, stdin="s" * 5000)
        writer.chunk("x" * 1300)
        writer.end(status="ok", exit_code=0, elapsed_ms=7)
        ev = _events(feed)
        self.assertEqual([e["type"] for e in ev], ["start", "chunk", "chunk", "chunk", "end"])
        self.assertEqual({e["job"] for e in ev}, {"j1"})
        start = ev[0]
        self.assertEqual(start["from"], "codex")
        self.assertEqual(start["model"], "ollama/m")
        self.assertLessEqual(len(start["prompt"].encode("utf-8")), 1024)
        self.assertEqual(start["prompt_bytes"], 2000)
        self.assertLessEqual(len(start["stdin_preview"].encode("utf-8")), 2048)
        self.assertEqual(start["stdin_bytes"], 5000)
        self.assertIsInstance(start["ts"], int)
        self.assertEqual([c["seq"] for c in ev[1:4]], [1, 2, 3])
        self.assertTrue(all(len(c["text"]) <= 600 for c in ev[1:4]))
        self.assertEqual("".join(c["text"] for c in ev[1:4]), "x" * 1300)
        self.assertEqual(ev[-1], {"job": "j1", "type": "end", "status": "ok", "exit": 0, "elapsed_ms": 7})

    def test_from_defaults_to_driver(self):
        feed = self.tmp / "feed.jsonl"
        af.ActivityFeed(env=_env(self.tmp, feed), job="j2").start(model="m", prompt="q")
        self.assertEqual(_events(feed)[0]["from"], "driver")

    def test_write_failure_never_raises(self):
        blocker = self.tmp / "file"
        blocker.write_text("x", encoding="utf-8")
        writer = af.ActivityFeed(env=_env(self.tmp, blocker / "sub" / "feed.jsonl"), job="j3")
        writer.start(model="m", prompt="q")
        writer.chunk("hi")
        writer.end(status="ok", exit_code=0, elapsed_ms=1)  # must not raise


class RunTurnHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aesop-feed-turn-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_run_turn_emits_start_chunk_end(self):
        feed = self.tmp / "feed.jsonl"
        with mock.patch.dict(os.environ, _env(self.tmp, feed, AESOP_FROM="codex")):
            with FakeServer(_json_reply("hello")) as fake:
                result = tb.run_turn(_provider(fake.url), MESSAGES, timeout_s=10)
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "hello")
        ev = _events(feed)
        self.assertEqual([e["type"] for e in ev], ["start", "chunk", "end"])
        self.assertEqual(ev[0]["from"], "codex")
        self.assertEqual(ev[0]["model"], "ollama/qwen2.5-coder:14b")
        self.assertEqual(ev[0]["prompt"], "say hi")
        self.assertEqual(ev[0]["stdin_preview"], "be brief")
        self.assertEqual(ev[1]["text"], "hello")
        self.assertEqual(ev[2]["status"], "ok")
        self.assertEqual(ev[2]["exit"], 0)
        self.assertIsInstance(ev[2]["elapsed_ms"], int)
        self.assertEqual(len({e["job"] for e in ev}), 1)

    def test_refused_turn_emits_start_and_end_exit_6_only(self):
        feed = self.tmp / "feed.jsonl"
        calls = []
        provider = tb.TurnProvider("ollama", "http://127.0.0.1:11434/v1", "claude-opus", is_local=True)
        with mock.patch.dict(os.environ, _env(self.tmp, feed)):
            result = tb.run_turn(provider, MESSAGES, transport=lambda payload: calls.append(payload) or {}, timeout_s=5)
        self.assertEqual(result.exit_code, 6)
        self.assertEqual(calls, [])
        ev = _events(feed)
        self.assertEqual([e["type"] for e in ev], ["start", "end"])
        self.assertEqual(ev[1]["exit"], 6)
        self.assertEqual(ev[1]["status"], "refused")

    def test_unwritable_feed_does_not_fail_the_turn(self):
        blocker = self.tmp / "file"
        blocker.write_text("x", encoding="utf-8")
        with mock.patch.dict(os.environ, _env(self.tmp, blocker / "sub" / "feed.jsonl")):
            with FakeServer(_json_reply("ok!")) as fake:
                result = tb.run_turn(_provider(fake.url), MESSAGES, timeout_s=10)
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.text, "ok!")

    def test_no_secrets_in_feed_events(self):
        feed = self.tmp / "feed.jsonl"
        secret = "sk-" + "test-SECRET-value-123"  # assembled at runtime (tests/CLAUDE.md dummy-secret rule)
        with mock.patch.dict(os.environ, _env(self.tmp, feed, AESOP_TURN_API_KEY=secret)):
            with FakeServer(_json_reply("a")) as fake:
                tb.run_turn(_provider(fake.url), MESSAGES, timeout_s=10)
        raw = feed.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        self.assertNotIn("Authorization", raw)
        self.assertNotIn("Bearer", raw)


class OpenAICompatibleHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aesop-feed-oai-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_openai_compatible_driver_transport_emits_feed(self):
        feed = self.tmp / "feed.jsonl"
        replies = [{"choices": [{"message": {"role": "assistant", "content": "{\"ok\": true}"}}]}]
        with mock.patch.dict(os.environ, _env(self.tmp, feed, AESOP_FROM="driver-test")):
            driver = OpenAICompatibleDriver(
                base_url="http://127.0.0.1:11434/v1", model="qwen2.5-coder:14b", is_local=True,
                transport=lambda payload: replies.pop(0),
            )
            out = driver._transport({"model": "qwen2.5-coder:14b", "messages": MESSAGES})
        self.assertEqual(out["choices"][0]["message"]["content"], "{\"ok\": true}")
        ev = _events(feed)
        self.assertEqual([e["type"] for e in ev], ["start", "chunk", "end"])
        self.assertEqual(ev[0]["from"], "driver-test")
        self.assertEqual(ev[0]["model"], "qwen2.5-coder:14b")
        self.assertEqual(ev[1]["text"], "{\"ok\": true}")
        self.assertEqual(ev[2]["status"], "ok")
        self.assertEqual(ev[2]["exit"], 0)

    def test_openai_compatible_driver_transport_error_ends_with_error(self):
        feed = self.tmp / "feed.jsonl"

        def boom(payload):
            raise RuntimeError("OpenAI API request failed: connection refused")

        with mock.patch.dict(os.environ, _env(self.tmp, feed)):
            driver = OpenAICompatibleDriver(
                base_url="http://127.0.0.1:11434/v1", model="m", is_local=True, transport=boom,
            )
            with self.assertRaises(RuntimeError):
                driver._transport({"model": "m", "messages": MESSAGES})
        ev = _events(feed)
        self.assertEqual([e["type"] for e in ev], ["start", "end"])
        self.assertEqual(ev[-1]["status"], "error")
        self.assertIn("connection refused", ev[-1]["error"])


if __name__ == "__main__":
    unittest.main()
