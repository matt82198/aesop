#!/usr/bin/env python3
"""Tests for driver/activity_feed.py and its hooks (one shared activity feed).

Behavioural only: drives run_turn and the OpenAI-compatible driver against the
scriptable loopback fake server (tests/turn_fake_server.py), then reads the feed
file the call wrote. No Anthropic endpoint, no API key, no real Ollama.

Run: python -I -m pytest -q tests/test_activity_feed.py

stdlib + pytest, ASCII-only, Windows + Linux safe.
"""

import json
import sys
from pathlib import Path

import pytest

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


def _events(path: Path):
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.split("\n") if line]


def _env(tmp_path: Path, feed: Path, **extra):
    env = {"HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home"), "AESOP_ACTIVITY_FEED": str(feed)}
    env.update(extra)
    return env


def _json_reply(text: str):
    """Fake-server handler for the non-streamed turn: one JSON chat completion."""
    return lambda reply, request, n: reply.json({"choices": [{"message": {"role": "assistant", "content": text}}]})


def _provider(url: str, model: str = "qwen2.5-coder:14b") -> tb.TurnProvider:
    return tb.TurnProvider("ollama", url, model, is_local=True)


MESSAGES = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "say hi"}]


# ---- path rule (shared with chat/activity_feed.mjs feedPath)

def test_default_path_is_shared_home_location(tmp_path):
    env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path / "other"), "AESOP_STATE_ROOT": str(tmp_path / "checkout-state")}
    assert af.feed_path(env) == tmp_path / ".aesop" / "state" / "chat" / "activity.jsonl"


def test_default_path_falls_back_to_userprofile(tmp_path):
    env = {"USERPROFILE": str(tmp_path)}
    assert af.feed_path(env) == tmp_path / ".aesop" / "state" / "chat" / "activity.jsonl"


def test_override_env_wins(tmp_path):
    override = tmp_path / "custom.jsonl"
    assert af.feed_path({"HOME": str(tmp_path), "AESOP_ACTIVITY_FEED": str(override)}) == override


# ---- writer schema

def test_start_chunk_end_schema_and_caps(tmp_path):
    feed = tmp_path / "feed.jsonl"
    writer = af.ActivityFeed(env=_env(tmp_path, feed, AESOP_FROM="codex"), job="j1")
    writer.start(model="ollama/m", prompt="p" * 2000, stdin="s" * 5000)
    writer.chunk("x" * 1300)
    writer.end(status="ok", exit_code=0, elapsed_ms=7)
    ev = _events(feed)
    assert [e["type"] for e in ev] == ["start", "chunk", "chunk", "chunk", "end"]
    assert {e["job"] for e in ev} == {"j1"}
    start = ev[0]
    assert start["from"] == "codex" and start["model"] == "ollama/m"
    assert len(start["prompt"].encode("utf-8")) <= 1024
    assert start["prompt_bytes"] == 2000
    assert len(start["stdin_preview"].encode("utf-8")) <= 2048
    assert start["stdin_bytes"] == 5000
    assert isinstance(start["ts"], int)
    assert [c["seq"] for c in ev[1:4]] == [1, 2, 3]
    assert all(len(c["text"]) <= 600 for c in ev[1:4])
    assert "".join(c["text"] for c in ev[1:4]) == "x" * 1300
    assert ev[-1] == {"job": "j1", "type": "end", "status": "ok", "exit": 0, "elapsed_ms": 7}


def test_from_defaults_to_driver(tmp_path):
    feed = tmp_path / "feed.jsonl"
    af.ActivityFeed(env=_env(tmp_path, feed), job="j2").start(model="m", prompt="q")
    assert _events(feed)[0]["from"] == "driver"


def test_write_failure_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    writer = af.ActivityFeed(env=_env(tmp_path, blocker / "sub" / "feed.jsonl"), job="j3")
    writer.start(model="m", prompt="q")
    writer.chunk("hi")
    writer.end(status="ok", exit_code=0, elapsed_ms=1)  # must not raise


# ---- run_turn hook against the fake Ollama-style server

def test_run_turn_emits_start_chunk_end(tmp_path, monkeypatch):
    feed = tmp_path / "feed.jsonl"
    for k, v in _env(tmp_path, feed, AESOP_FROM="codex").items():
        monkeypatch.setenv(k, v)
    with FakeServer(_json_reply("hello")) as fake:
        result = tb.run_turn(_provider(fake.url), MESSAGES, timeout_s=10)
    assert result.ok and result.text == "hello"
    ev = _events(feed)
    assert [e["type"] for e in ev] == ["start", "chunk", "end"]
    assert ev[0]["from"] == "codex"
    assert ev[0]["model"] == "ollama/qwen2.5-coder:14b"
    assert ev[0]["prompt"] == "say hi"
    assert ev[0]["stdin_preview"] == "be brief"
    assert ev[1]["text"] == "hello"
    assert ev[2]["status"] == "ok" and ev[2]["exit"] == 0
    assert isinstance(ev[2]["elapsed_ms"], int)
    assert len({e["job"] for e in ev}) == 1


def test_refused_turn_emits_start_and_end_exit_6_only(tmp_path, monkeypatch):
    feed = tmp_path / "feed.jsonl"
    for k, v in _env(tmp_path, feed).items():
        monkeypatch.setenv(k, v)
    calls = []
    provider = tb.TurnProvider("ollama", "http://127.0.0.1:11434/v1", "claude-opus", is_local=True)
    result = tb.run_turn(provider, MESSAGES, transport=lambda payload: calls.append(payload) or {}, timeout_s=5)
    assert result.exit_code == 6
    assert calls == []
    ev = _events(feed)
    assert [e["type"] for e in ev] == ["start", "end"]
    assert ev[1]["exit"] == 6 and ev[1]["status"] == "refused"


def test_unwritable_feed_does_not_fail_the_turn(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    for k, v in _env(tmp_path, blocker / "sub" / "feed.jsonl").items():
        monkeypatch.setenv(k, v)
    with FakeServer(_json_reply("ok!")) as fake:
        result = tb.run_turn(_provider(fake.url), MESSAGES, timeout_s=10)
    assert result.ok and result.exit_code == 0 and result.text == "ok!"


def test_no_secrets_in_feed_events(tmp_path, monkeypatch):
    feed = tmp_path / "feed.jsonl"
    secret = "sk-" + "test-SECRET-value-123"  # assembled at runtime (tests/CLAUDE.md dummy-secret rule)
    for k, v in _env(tmp_path, feed, AESOP_TURN_API_KEY=secret).items():
        monkeypatch.setenv(k, v)
    with FakeServer(_json_reply("a")) as fake:
        tb.run_turn(_provider(fake.url), MESSAGES, timeout_s=10)
    raw = feed.read_text(encoding="utf-8")
    assert secret not in raw
    assert "Authorization" not in raw and "Bearer" not in raw


# ---- OpenAI-compatible driver hook

def test_openai_compatible_driver_transport_emits_feed(tmp_path, monkeypatch):
    feed = tmp_path / "feed.jsonl"
    for k, v in _env(tmp_path, feed, AESOP_FROM="driver-test").items():
        monkeypatch.setenv(k, v)
    replies = [{"choices": [{"message": {"role": "assistant", "content": "{\"ok\": true}"}}]}]
    driver = OpenAICompatibleDriver(
        base_url="http://127.0.0.1:11434/v1", model="qwen2.5-coder:14b", is_local=True,
        transport=lambda payload: replies.pop(0),
    )
    out = driver._transport({"model": "qwen2.5-coder:14b", "messages": MESSAGES})
    assert out["choices"][0]["message"]["content"] == "{\"ok\": true}"
    ev = _events(feed)
    assert [e["type"] for e in ev] == ["start", "chunk", "end"]
    assert ev[0]["from"] == "driver-test"
    assert ev[0]["model"] == "qwen2.5-coder:14b"
    assert ev[1]["text"] == "{\"ok\": true}"
    assert ev[2]["status"] == "ok" and ev[2]["exit"] == 0


def test_openai_compatible_driver_transport_error_ends_with_error(tmp_path, monkeypatch):
    feed = tmp_path / "feed.jsonl"
    for k, v in _env(tmp_path, feed).items():
        monkeypatch.setenv(k, v)

    def boom(payload):
        raise RuntimeError("OpenAI API request failed: connection refused")

    driver = OpenAICompatibleDriver(
        base_url="http://127.0.0.1:11434/v1", model="m", is_local=True, transport=boom,
    )
    with pytest.raises(RuntimeError):
        driver._transport({"model": "m", "messages": MESSAGES})
    ev = _events(feed)
    assert [e["type"] for e in ev] == ["start", "end"]
    assert ev[-1]["status"] == "error"
    assert "connection refused" in ev[-1]["error"]
