#!/usr/bin/env python3
"""Tests for driver/turn_gate.py, the Python refusal gate (TUI driver plan, phase P4).

The gate is a port of assertProviderAllowed in chat/harness.mjs. The shared
fixture tests/fixtures/provider-config-cases.json is run here, by
tests/test_provider_config_conformance.py and by the Node suite, so the three
readers agree. A refused turn exits 6 and opens ZERO sockets: every socket
construction and every DNS lookup in the process is counted.

Offline only. stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import io
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
TESTS_DIR = REPO / "tests"
for _p in (DRIVER_DIR, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import turn as turn_cli  # noqa: E402
import turn_frames  # noqa: E402
import turn_gate  # noqa: E402
import turn_stream  # noqa: E402
from turn_fake_server import FakeServer  # noqa: E402

FIXTURE = json.loads((TESTS_DIR / "fixtures" / "provider-config-cases.json").read_text(encoding="utf-8"))
MESSAGES = [{"role": "user", "content": "hi"}]
ANTHROPIC_URLS = [
    "http://api.anthropic.com/v1",
    "https://api.anthropic.com/v1",
    "https://API.ANTHROPIC.COM./v1",
    "http://127.0.0.1@api.anthropic.com/v1",
    "https://api.%61nthropic.com/v1",
    "https://sub.claude.ai/v1",
    "https://claude.ai",
    # userinfo plus a port; assembled at runtime so the secret scanner does not read it as a credential
    "https://" + "user" + ":" + "pw" + "@anthropic.com:8443/v1",
]


LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")


class NetworkCounter:
    """Counts every socket constructed and every DNS lookup made while active.

    It is also a guard: a lookup of, or a connect to, anything but loopback is
    counted and then blocked, so a broken gate turns the test red without a
    single packet leaving the machine.
    """

    def __init__(self):
        self.sockets = 0
        self.lookups = 0

    def __enter__(self):
        counter = self
        real_socket = socket.socket
        real_getaddrinfo = socket.getaddrinfo

        class CountingSocket(real_socket):
            def __init__(self, *args, **kwargs):
                counter.sockets += 1
                super().__init__(*args, **kwargs)

            def connect(self, address):
                if not isinstance(address, tuple) or address[0] not in LOOPBACK_NAMES:
                    raise OSError("blocked by the test network guard")
                return super().connect(address)

        def counting_getaddrinfo(host, *args, **kwargs):
            counter.lookups += 1
            if host not in LOOPBACK_NAMES:
                raise socket.gaierror("blocked by the test network guard")
            return real_getaddrinfo(host, *args, **kwargs)

        self._patches = [
            mock.patch.object(socket, "socket", CountingSocket),
            mock.patch.object(socket, "getaddrinfo", counting_getaddrinfo),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self._patches:
            patch.stop()
        return False

    @property
    def total(self):
        return self.sockets + self.lookups


class RecordingStdin(io.StringIO):
    """Piped stdin that records whether it was read."""

    reads = 0

    def isatty(self):
        return False

    def read(self, *args):
        self.reads += 1
        return super().read(*args)


def entry_for(url, provider_id="p", **raw):
    base = {"type": "openai", "base_url": url, "models": "any"}
    base.update(raw)
    return {"id": provider_id, "raw": base}


def reason_for(provider, model=None):
    try:
        turn_gate.assert_provider_allowed(provider, model)
    except turn_gate.ProviderRefused as refused:
        return refused.reason
    return None


class GateRuleTests(unittest.TestCase):
    def test_shared_fixture_cases_match_the_gate(self):
        self.assertGreaterEqual(len(FIXTURE), 20)
        for case in FIXTURE:
            with self.subTest(case=case["reason"]):
                ok, reason = turn_gate.check_provider(case["config"])
                self.assertEqual(ok, case["expect"] == "accept", reason)
                self.assertEqual(reason is None, ok)

    def test_anthropic_hosts_are_refused_through_every_spelling(self):
        for url in ANTHROPIC_URLS:
            with self.subTest(url=url):
                self.assertEqual(reason_for({"id": "evil", "type": "openai", "base_url": url}), "base_url host is an Anthropic host")

    def test_lookalike_hosts_are_not_refused(self):
        for url in ("https://anthropic.com.example.test/v1", "https://notanthropic.com/v1", "https://claude.ai.example.test/v1", "http://127.0.0.1:11434/v1"):
            with self.subTest(url=url):
                self.assertIsNone(reason_for({"id": "ok", "type": "openai", "base_url": url}))

    def test_model_ids_naming_claude_are_refused_as_whole_tokens(self):
        provider = {"id": "gw", "type": "openai", "base_url": "https://gateway.example.test/v1"}
        for model in ("claude-3-haiku", "Claude-Opus-5", "anthropic/claude-x", "openrouter/anthropic/claude-3", "us.anthropic.claude-3", " claude ", "x:claude", "a_claude_b"):
            with self.subTest(model=model):
                self.assertEqual(reason_for(provider, model), f'model "{model}" resolves to Claude')
        for model in ("claudette-7b", "qwen3-coder:30b", "gpt-4o-mini", "misanthropic-7b", "m"):
            with self.subTest(model=model):
                self.assertIsNone(reason_for(provider, model))
        self.assertIsNone(reason_for({**provider, "type": "orchestrator"}, "claude-seat"))

    def test_command_style_types_schemes_and_insecure_http(self):
        for ptype in ("cli", "exec", "claude", "anthropic"):
            with self.subTest(type=ptype):
                self.assertEqual(
                    reason_for({"id": "p", "type": ptype, "base_url": "http://127.0.0.1:1/v1"}),
                    f'provider type "{ptype}" is command-style or Anthropic',
                )
        self.assertEqual(reason_for({"id": "p", "base_url": "not a url"}), "base_url is not a valid URL")
        self.assertEqual(reason_for({"id": "p"}), "base_url is not a valid URL")
        self.assertEqual(reason_for({"id": "p", "base_url": "ftp://127.0.0.1/v1"}), "base_url must be http or https")
        self.assertEqual(reason_for({"id": "p", "base_url": "http://example.test/v1"}), "non-loopback http requires allow_insecure")
        self.assertIsNone(reason_for({"id": "p", "base_url": "http://example.test/v1", "allow_insecure": True}))
        self.assertIsNone(reason_for({"id": "p", "base_url": "http://[::1]:8080/v1"}))

    def test_a_provider_id_naming_claude_or_anthropic_is_refused(self):
        for provider_id in ("anthropic", "Claude", "claude-proxy"):
            with self.subTest(provider=provider_id):
                reason = reason_for({"id": provider_id, "type": "openai", "base_url": "http://127.0.0.1:1/v1"})
                self.assertEqual(reason, f'provider id "{provider_id}" names Claude or Anthropic')
        self.assertIsNone(reason_for({"id": "claudette", "type": "openai", "base_url": "http://127.0.0.1:1/v1"}))

    def test_refusal_carries_the_provider_and_the_harness_message_shape(self):
        with self.assertRaises(turn_gate.ProviderRefused) as caught:
            turn_gate.assert_provider_allowed({"id": "evil", "type": "cli", "base_url": "http://127.0.0.1:1/v1"})
        self.assertEqual(caught.exception.provider, "evil")
        self.assertEqual(str(caught.exception), 'evil: refused: provider type "cli" is command-style or Anthropic')


class ZeroSocketTests(unittest.TestCase):
    """Exit 6 and nothing on the network: no socket, no DNS lookup, stdin unread."""

    REFUSED = [
        (entry_for("https://api.anthropic.com/v1"), "m"),
        (entry_for("http://127.0.0.1@api.anthropic.com/v1"), "m"),
        (entry_for("https://api.%61nthropic.com/v1"), "m"),
        (entry_for("https://gateway.example.test/v1"), "openrouter/anthropic/claude-3"),
        (entry_for("http://127.0.0.1:1/v1", type="cli"), "m"),
        (entry_for("http://example.test/v1"), "m"),
        (entry_for("ftp://example.test/v1"), "m"),
        (entry_for("http://127.0.0.1:1/v1", provider_id="anthropic"), "m"),
    ]

    def test_the_counter_sees_a_real_turn(self):
        with FakeServer() as fake, NetworkCounter() as net:
            result = turn_stream.run_stream_turn(entry_for(fake.url, is_local=True), "m", MESSAGES, env={})
            self.assertTrue(result.ok)
            self.assertGreaterEqual(net.sockets, 1, "the counter must see the socket of an allowed turn")

    def test_refused_turns_open_zero_sockets_at_request_time(self):
        for entry, model in self.REFUSED:
            with self.subTest(url=entry["raw"]["base_url"], model=model, id=entry["id"]):
                lines = []
                with NetworkCounter() as net:
                    result = turn_stream.run_stream_turn(entry, model, MESSAGES, env={}, notify=lines.append)
                self.assertEqual(net.total, 0, f"sockets={net.sockets} lookups={net.lookups}")
                self.assertEqual((result.ok, result.exit_code), (False, 6))
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0].startswith(f"error: {entry['id']}: refused: "), lines)

    def test_a_config_changed_after_load_is_refused_at_request_time(self):
        with FakeServer() as fake:
            entry = entry_for(fake.url, is_local=True)
            self.assertTrue(turn_stream.run_stream_turn(entry, "m", MESSAGES, env={}).ok)
            entry["raw"]["base_url"] = "https://api.anthropic.com/v1"
            with NetworkCounter() as net:
                result = turn_stream.run_stream_turn(entry, "m", MESSAGES, env={})
            self.assertEqual((result.exit_code, net.total), (6, 0))
            self.assertEqual(len(fake.posts()), 1)

    def cli(self, argv):
        stdin = RecordingStdin("piped context that must not be read")
        out_text, out_bytes, err = io.StringIO(), io.BytesIO(), io.StringIO()
        frames = "--frames" in argv
        with NetworkCounter() as net:
            code = turn_cli.main(argv, stdin=stdin, stdout=out_bytes if frames else out_text, stderr=err)
        return code, (out_bytes.getvalue() if frames else out_text.getvalue()), err.getvalue(), net, stdin

    def test_cli_refusals_exit_6_with_zero_sockets_in_every_mode(self):
        targets = [
            ["--base-url", "https://api.anthropic.com/v1"],
            ["--base-url", "http://127.0.0.1@api.anthropic.com/v1"],
            ["--base-url", "https://api.%61nthropic.com/v1"],
            ["--base-url", "http://example.test/v1"],
            ["--base-url", "https://gateway.example.test/v1", "--model", "gw/openrouter/anthropic/claude-3"],
            ["--model", "ollama/claude-3-haiku"],
            ["--model", "anthropic/some-model"],
        ]
        for mode in ([], ["--json"], ["--stream"], ["--frames"]):
            for target in targets:
                with self.subTest(mode=mode, target=target):
                    code, out, err, net, stdin = self.cli(mode + target + ["hi"])
                    self.assertEqual(code, 6)
                    self.assertEqual(net.total, 0, f"sockets={net.sockets} lookups={net.lookups}")
                    self.assertEqual(stdin.reads, 0, "stdin must not be read for a refused turn")
                    if mode == ["--frames"]:
                        items = turn_frames.decode_stream(out)
                        self.assertEqual(items[-1], {"kind": "end", "exit": 6})
                        done = json.loads(items[-2]["payload"])
                        self.assertEqual((done["ok"], done["exit"]), (False, 6))
                        self.assertIn("refused", done["error"])
                    elif mode == ["--json"]:
                        obj = json.loads(out)
                        self.assertIs(obj["ok"], False)
                        self.assertIn("refused", obj["error"])
                    else:
                        self.assertEqual(out, "")
                        self.assertIn("refused", err)

    def test_serve_refuses_a_claude_model_per_turn_and_keeps_serving(self):
        with FakeServer() as fake:
            requests = "\n".join(json.dumps(r) for r in (
                {"op": "turn", "id": 1, "line": "hi", "model": "claude-3-haiku"},
                {"op": "shutdown"},
            )) + "\n"
            out = io.BytesIO()
            with NetworkCounter() as net:
                code = turn_cli.main(["--serve", "--base-url", fake.url], stdin=io.BytesIO(requests.encode("utf-8")), stdout=out, stderr=io.StringIO())
            control = [json.loads(i["payload"]) for i in turn_frames.decode_stream(out.getvalue()) if i["kind"] == "frame" and i["channel"] == "control"]
            done = [c for c in control if c["type"] == "done"]
            self.assertEqual(code, 0)
            self.assertEqual([(d["id"], d["exit"], d["ok"]) for d in done], [(1, 6, False)])
            self.assertIn('refused: model "claude-3-haiku" resolves to Claude', done[0]["error"])
            self.assertEqual(net.total, 0)
            self.assertEqual(fake.requests, [])


if __name__ == "__main__":
    unittest.main()
