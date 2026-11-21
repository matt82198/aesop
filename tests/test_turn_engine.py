#!/usr/bin/env python3
"""Tests for driver/turn_engine.py and the routed serve protocol (TUI driver plan, phase P5).

The engine is a port of the turn loop in chat/harness.mjs. The cross-backend
proof is tests/chat-driver-parity.test.mjs (same scripted turns through both,
equal transcripts); these tests pin the engine's own behaviour from Python so
a regression is caught in the Python shards too.

Offline only (fake servers on 127.0.0.1). stdlib-only (unittest), ASCII-only.
"""

import io
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
TESTS_DIR = REPO / "tests"
for _p in (DRIVER_DIR, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import turn as turn_cli  # noqa: E402
import turn_engine  # noqa: E402
import turn_frames  # noqa: E402
from turn_fake_server import FakeServer, closed_port  # noqa: E402


def raw_for(url, **extra):
    raw = {"type": "openai-compatible", "base_url": url, "models": ["m"], "is_local": True, "timeout_ms": 5000}
    raw.update(extra)
    return raw


def pick(provider, model="m"):
    return {"provider": provider, "model": model}


def route(target=None, fallback=(), candidates=None, fallback_on="retryable", strategy=None):
    return {"target": target, "candidates": candidates, "fallback": list(fallback), "fallbackOn": fallback_on, "strategy": strategy}


def echo_count(reply, request, n):
    reply.sse([f"reply to {len(request['body']['messages'])} message(s)"])


def status(code, body="x"):
    def handler(reply, request, n):
        reply.head(code, "application/json")
        reply.write(body)
    return handler


class Run:
    """Collects everything one engine turn reports."""

    def __init__(self, engine, registry, selection, line, **kwargs):
        self.deltas, self.lines, self.flashes, self.usage = [], [], [], []
        self.result = engine.run_turn(
            registry, selection, line, on_delta=self.deltas.append, notify=self.lines.append,
            flash=self.flashes.append, usage=self.usage.append, env=kwargs.pop("env", {}), **kwargs
        )


class ContextTests(unittest.TestCase):
    def test_completed_turns_are_sent_as_context_and_failed_turns_are_not(self):
        with FakeServer(echo_count) as a, FakeServer(status(500)) as bad:
            engine = turn_engine.TurnEngine()
            registry = {"providers": {"A": raw_for(a.url), "BAD": raw_for(bad.url)}, "routes": {}}
            texts = [Run(engine, registry, pick("A"), line).result.text for line in ("one", "two")]
            failed = Run(engine, registry, pick("BAD"), "lost")
            third = Run(engine, registry, pick("A"), "three")
            self.assertEqual(texts, ["reply to 1 message(s)", "reply to 3 message(s)"])
            self.assertEqual(failed.lines, ["error: BAD: server: HTTP 500"])
            self.assertEqual(third.result.text, "reply to 5 message(s)")
            sent = a.posts()[2]["body"]["messages"]
            self.assertEqual([m["role"] for m in sent], ["user", "assistant", "user", "assistant", "user"])
            self.assertEqual([m["content"] for m in sent][0::2], ["one", "two", "three"])

    def test_bound_context_keeps_the_newest_whole_messages_and_starts_on_a_user(self):
        convo = []
        for i in range(15):
            convo += [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": f"a{i}"}]
        bounded = turn_engine.bound_context(convo)
        self.assertEqual(len(bounded), 20)
        self.assertEqual((bounded[0], bounded[-1]), ({"role": "user", "content": "u5"}, {"role": "assistant", "content": "a14"}))
        big = [{"role": "user", "content": "x" * 20000}, {"role": "assistant", "content": "y" * 3000},
               {"role": "user", "content": "z" * 3000}, {"role": "assistant", "content": "w" * 100}]
        # 100 + 3000 + 3000 fit 24000; adding the 20000 does not, and no message is split.
        self.assertEqual([len(m["content"]) for m in turn_engine.bound_context(big)], [3000, 100])
        self.assertEqual(turn_engine.bound_context([{"role": "user", "content": "q" * 30000}]), [{"role": "user", "content": "q" * 30000}])
        self.assertEqual(turn_engine.bound_context([]), [])

    def test_a_task_route_turn_carries_no_context_and_is_not_remembered(self):
        with FakeServer(echo_count) as a:
            engine = turn_engine.TurnEngine()
            registry = {"providers": {"A": raw_for(a.url)}, "routes": {"code": route(pick("A"))}}
            Run(engine, registry, pick("A"), "chat one")
            routed = Run(engine, registry, pick("A"), "write x", task="code")
            after = Run(engine, registry, pick("A"), "chat two")
            self.assertEqual(routed.result.text, "reply to 1 message(s)")
            self.assertEqual(after.result.text, "reply to 3 message(s)")


class FallbackTests(unittest.TestCase):
    def test_retryable_failures_fall_back_in_order_each_tried_once(self):
        with FakeServer(status(429)) as a, FakeServer(status(503)) as b, FakeServer() as c:
            registry = {"providers": {"A": raw_for(a.url), "B": raw_for(b.url), "C": raw_for(c.url)},
                        "routes": {"chat": route(pick("A"), [pick("B"), pick("C")])}}
            run = Run(turn_engine.TurnEngine(), registry, pick("A"), "hi")
            self.assertEqual(run.lines, [
                "error: A: rate-limit: HTTP 429", "falling back: A -> B (rate-limit)",
                "error: B: server: HTTP 503", "falling back: B -> C (server)",
            ])
            self.assertEqual((run.result.ok, run.result.text, run.result.provider, run.deltas), (True, "hello", "C", ["hel", "lo"]))
            self.assertEqual([len(s.posts()) for s in (a, b, c)], [1, 1, 1])

    def test_auth_failure_stops_the_chain_unless_fallback_on_any(self):
        with FakeServer(status(401, json.dumps({"error": {"message": "bad key"}}))) as a, FakeServer() as b:
            providers = {"A": raw_for(a.url), "B": raw_for(b.url)}
            stopped = Run(turn_engine.TurnEngine(), {"providers": providers, "routes": {"chat": route(pick("A"), [pick("B")])}}, pick("A"), "hi")
            self.assertEqual(stopped.lines, ["error: A: auth: HTTP 401: bad key"])
            self.assertEqual((stopped.result.ok, stopped.result.exit_code, len(b.posts())), (False, 4, 0))
            anyway = Run(turn_engine.TurnEngine(), {"providers": providers, "routes": {"chat": route(pick("A"), [pick("B")], fallback_on="any")}}, pick("A"), "hi")
            self.assertEqual(anyway.lines, ["error: A: auth: HTTP 401: bad key", "falling back: A -> B (auth)"])
            self.assertEqual((anyway.result.ok, len(b.posts())), (True, 1))

    def test_connect_failure_exit_code_and_fallback(self):
        dead = f"http://127.0.0.1:{closed_port()}/v1"
        alone = Run(turn_engine.TurnEngine(), {"providers": {"A": raw_for(dead)}, "routes": {}}, pick("A"), "hi")
        self.assertEqual((alone.result.ok, alone.result.exit_code), (False, 3))
        self.assertIn("error: A: connect: cannot reach A at http://127.0.0.1:", alone.lines[0])

    def test_a_cut_after_the_first_delta_is_never_sent_to_the_next_candidate(self):
        import threading
        seen = threading.Event()

        def cut(reply, request, n):
            reply.head(200, "text/event-stream")
            reply.write('data: {"choices":[{"delta":{"content":"par"}}]}\n\n')
            seen.wait(5)
            reply.drop()

        with FakeServer(cut) as a, FakeServer() as b:
            registry = {"providers": {"A": raw_for(a.url), "B": raw_for(b.url)}, "routes": {"chat": route(pick("A"), [pick("B")])}}
            lines = []
            result = turn_engine.TurnEngine().run_turn(registry, pick("A"), "hi", on_delta=lambda text: seen.set(), notify=lines.append, env={})
            self.assertEqual(lines, ["stream interrupted from A"])
            self.assertEqual((result.ok, result.text, result.exit_code, len(b.posts())), (False, "par", 4, 0))

    def test_unknown_provider_no_selection_and_an_empty_route(self):
        engine = turn_engine.TurnEngine()
        ghost = Run(engine, {"providers": {}, "routes": {}}, pick("gone"), "hi")
        self.assertEqual(ghost.lines, ["error: gone: refused: unknown provider"])
        with self.assertRaises(turn_engine.EngineError) as none:
            engine.run_turn({"providers": {}, "routes": {}}, {"provider": None, "model": None}, "hi")
        self.assertEqual(str(none.exception), "no provider configured (see /help)")
        with self.assertRaises(turn_engine.EngineError) as empty:
            engine.run_turn({"providers": {}, "routes": {"x": route(candidates=[])}}, pick("A"), "hi", task="x")
        self.assertEqual(str(empty.exception), "route x has no target")


class BreakerAndStrategyTests(unittest.TestCase):
    def test_breaker_opens_after_three_failures_is_flashed_once_and_closes_after_the_cooldown(self):
        with FakeServer(status(503)) as a, FakeServer() as b:
            engine = turn_engine.TurnEngine()
            registry = {"providers": {"A": raw_for(a.url), "B": raw_for(b.url)}, "routes": {"chat": route(pick("A"), [pick("B")])}}
            for _ in range(3):
                Run(engine, registry, pick("A"), "t", now=1000000)
            self.assertEqual(len(a.posts()), 3)
            skipped = Run(engine, registry, pick("A"), "skip", now=1000000)
            again = Run(engine, registry, pick("A"), "skip2", now=1029999)
            self.assertEqual(len(a.posts()), 3, "A must be skipped while the breaker is open")
            self.assertEqual(skipped.flashes, ["skipping A: circuit open after 3 failures, retry after cooldown"])
            self.assertEqual((again.flashes, skipped.result.ok, skipped.result.text), ([], True, "hello"))
            Run(engine, registry, pick("A"), "later", now=1030001)
            self.assertEqual(len(a.posts()), 4, "A is tried again after the cooldown")

    def test_every_candidate_behind_an_open_breaker_is_one_server_line(self):
        with FakeServer(status(503)) as a:
            engine = turn_engine.TurnEngine()
            registry = {"providers": {"A": raw_for(a.url)}, "routes": {}}
            for _ in range(3):
                Run(engine, registry, pick("A"), "t", now=5)
            blocked = Run(engine, registry, pick("A"), "blocked", now=5)
            self.assertEqual(blocked.lines, ["error: A: server: no candidate available, circuit open"])
            self.assertEqual((blocked.result.ok, len(a.posts())), (False, 3))

    def test_cheapest_orders_by_cost_with_local_free_and_ties_in_config_order(self):
        with FakeServer(lambda r, q, n: r.sse(["from-a"])) as a, FakeServer(lambda r, q, n: r.sse(["from-b"])) as b, FakeServer(lambda r, q, n: r.sse(["from-c"])) as c:
            paid = {"is_local": False, "allow_insecure": True}
            providers = {"A": raw_for(a.url, cost_per_mtok={"input": 2, "output": 3}, **paid), "B": raw_for(b.url, cost_per_mtok=1, **paid), "C": raw_for(c.url)}
            engine = turn_engine.TurnEngine()
            two = Run(engine, {"providers": providers, "routes": {"t": route(candidates=[pick("A"), pick("B")], strategy="cheapest")}}, pick("A"), "hi", task="t")
            three = Run(engine, {"providers": providers, "routes": {"t": route(candidates=[pick("A"), pick("B"), pick("C")], strategy="cheapest")}}, pick("A"), "hi", task="t")
            self.assertEqual((two.result.text, three.result.text), ("from-b", "from-c"))

    def test_usage_is_priced_per_provider(self):
        usage = {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500}
        with FakeServer(lambda r, q, n: r.sse(["ok"], usage=usage)) as a:
            providers = {"L": raw_for(a.url), "P": raw_for(a.url, is_local=False, allow_insecure=True, cost_per_mtok={"input": 2, "output": 3})}
            engine = turn_engine.TurnEngine()
            local = Run(engine, {"providers": providers, "routes": {}}, pick("L"), "hi")
            paid = Run(engine, {"providers": providers, "routes": {}}, pick("P"), "hi")
            self.assertEqual(local.usage, [{"provider": "L", "total": 1500, "prompt": 1200, "completion": 300, "cost": 0}])
            self.assertEqual(paid.usage, [{"provider": "P", "total": 1500, "prompt": 1200, "completion": 300, "cost": (2 * 1200 + 3 * 300) / 1e6}])


class ServeProtocolTests(unittest.TestCase):
    """The routed turn request: registry, selection, task and keys travel on stdin."""

    def serve(self, requests):
        out = io.BytesIO()
        payload = "".join(json.dumps(r) + "\n" for r in requests).encode("utf-8")
        code = turn_cli.main(["--serve"], stdin=io.BytesIO(payload), stdout=out, stderr=io.StringIO())
        items = turn_frames.decode_stream(out.getvalue())
        control = [json.loads(i["payload"]) for i in items if i["kind"] == "frame" and i["channel"] == "control"]
        data = [i["payload"].decode("utf-8") for i in items if i["kind"] == "frame" and i["channel"] == "data"]
        return code, items[-1], control, data

    def test_routed_turns_use_the_request_registry_and_keep_session_context(self):
        key_value = "-".join(("sk", "engine", "SENTINEL"))
        with FakeServer(echo_count) as a, FakeServer(status(503)) as b:
            registry = {
                "providers": {"A": raw_for(a.url, is_local=False, allow_insecure=True, api_key_env="ENGINE_KEY"), "B": raw_for(b.url)},
                "routes": {"code": route(pick("B"), [pick("A")])},
            }
            base = {"op": "turn", "registry": registry, "selection": pick("A"), "keys": {"ENGINE_KEY": key_value}}
            code, end, control, data = self.serve([
                {**base, "id": 1, "line": "one"},
                {**base, "id": 2, "line": "two"},
                {**base, "id": 3, "line": "write x", "task": "code"},
                {**base, "id": 4, "line": "nobody", "selection": {"provider": None, "model": None}},
                {"op": "shutdown"},
            ])
            done = {c["id"]: c for c in control if c["type"] == "done"}
            self.assertEqual((code, end), (0, {"kind": "end", "exit": 0}))
            self.assertEqual(data, ["reply to 1 message(s)", "reply to 3 message(s)", "reply to 1 message(s)"])
            self.assertEqual([done[i]["ok"] for i in (1, 2, 3)], [True, True, True])
            self.assertEqual(done[3]["provider"], "A", "the route fell back from B to A")
            self.assertEqual([c["text"] for c in control if c.get("event") == "notify" and c["id"] == 3], ["error: B: server: HTTP 503", "falling back: B -> A (server)"])
            self.assertEqual((done[4]["ok"], done[4]["thrown"]), (False, "no provider configured (see /help)"))
            self.assertEqual(a.posts()[0]["headers"]["authorization"], "Bearer " + key_value)
            self.assertNotIn(key_value, json.dumps(control))

    def test_shutdown_lets_a_queued_turn_finish(self):
        with FakeServer(echo_count) as a:
            registry = {"providers": {"A": raw_for(a.url)}, "routes": {}}
            turns = [{"op": "turn", "id": i, "line": f"t{i}", "registry": registry, "selection": pick("A")} for i in (1, 2, 3)]
            code, _, control, data = self.serve(turns + [{"op": "shutdown"}])
            self.assertEqual(code, 0)
            self.assertEqual(len(data), 3, "every queued turn must finish before a graceful shutdown")
            self.assertEqual([c["ok"] for c in control if c["type"] == "done"], [True, True, True])


if __name__ == "__main__":
    unittest.main()
