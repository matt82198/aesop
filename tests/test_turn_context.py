#!/usr/bin/env python3
"""Tests for driver/turn_context.py and the native Ollama route (TUI driver plan, phase P3).

The sizing and sticky cases come from tests/fixtures/num-ctx-cases.json, the
same file tests/chat-num-ctx-fixture.test.mjs runs against chat/harness.mjs,
so the driver and the harness cannot drift. The CLI tests cover finding
f-db2dd2fa: a 30 KB piped input keeps its first line, and input over the
window shows a warning.

Offline only (fake server on 127.0.0.1). stdlib-only (unittest), ASCII-only.
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
import turn_context  # noqa: E402
import turn_stream  # noqa: E402
from turn_fake_server import FakeServer  # noqa: E402

FIXTURE = json.loads((TESTS_DIR / "fixtures" / "num-ctx-cases.json").read_text(encoding="utf-8"))
SECRET_LINE = "The secret code is ZEBRA-" + "7421."


def messages_for(case):
    if "repeat" in case:
        return [{"role": "user", "content": case["repeat"]["unit"] * case["repeat"]["count"]}]
    return [{"role": "user", "content": "x" * n} for n in case["chars"]]


def ndjson_reply(reply, request, n):
    reply.head(200, "application/x-ndjson")
    reply.write(json.dumps({"message": {"role": "assistant", "content": "man"}, "done": False}) + "\n")
    reply.write(json.dumps({"message": {"role": "assistant", "content": "go"}, "done": False}) + "\n")
    reply.write(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 7, "eval_count": 2}) + "\n")


def entry_for(url, provider_id="ollama", **raw):
    base = {"type": "openai", "base_url": url, "models": "any", "is_local": True, "timeout_ms": 5000}
    base.update(raw)
    return {"id": provider_id, "raw": base}


def run_turn(entry, text, sizer, model="m"):
    notes = []
    result = turn_stream.run_stream_turn(
        entry, model, [{"role": "user", "content": text}], env={}, notify=notes.append, sizer=sizer,
    )
    return result, notes


class PipedStdin(io.StringIO):
    def isatty(self):
        return False


def run_cli(argv, stdin_text=""):
    out, err = io.StringIO(), io.StringIO()
    code = turn_cli.main(argv, stdin=PipedStdin(stdin_text), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


class SharedFixtureTests(unittest.TestCase):
    def test_sizing_cases_match_the_shared_fixture(self):
        self.assertGreaterEqual(len(FIXTURE["sizing"]), 10)
        for case in FIXTURE["sizing"]:
            with self.subTest(case=case["name"]):
                sized = turn_context.size_ollama_context(messages_for(case), case["raw"])
                self.assertEqual(
                    {"numCtx": sized.num_ctx, "promptTokens": sized.prompt_tokens, "truncates": sized.truncates, "cap": sized.cap},
                    case["expect"],
                )

    def test_sequences_match_num_ctx_on_the_wire_and_the_notices(self):
        self.assertGreaterEqual(len(FIXTURE["sequences"]), 3)
        for seq in FIXTURE["sequences"]:
            with self.subTest(sequence=seq["name"]):
                with FakeServer(ndjson_reply) as fake:
                    sizer = turn_context.ContextSizer()
                    entry = entry_for(fake.url, **seq["raw"])
                    for index, step in enumerate(seq["steps"]):
                        result, notes = run_turn(entry, "x" * step["chars"], sizer)
                        post = fake.posts()[index]
                        self.assertEqual(result.text, "mango", step)
                        self.assertEqual(post["url"], "/api/chat", step)
                        self.assertEqual(post["body"]["options"], {"num_ctx": step["num_ctx"]}, step)
                        self.assertEqual(notes, step["notes"], step)


class NativeRouteTests(unittest.TestCase):
    def test_ollama_goes_to_the_native_route_with_ndjson_and_usage(self):
        with FakeServer(ndjson_reply) as fake:
            result, notes = run_turn(entry_for(fake.url), "hi", turn_context.ContextSizer())
            post = fake.posts()[0]
            self.assertEqual(post["url"], "/api/chat")
            self.assertEqual(post["headers"]["accept"], "application/x-ndjson")
            self.assertEqual(post["body"], {"model": "m", "stream": True, "messages": [{"role": "user", "content": "hi"}], "options": {"num_ctx": 8192}})
            self.assertEqual((result.ok, result.text, notes), (True, "mango", []))
            self.assertEqual(result.usage, {"prompt_tokens": 7, "completion_tokens": 2})

    def test_type_ollama_is_native_and_other_providers_are_untouched(self):
        with FakeServer(ndjson_reply) as fake:
            run_turn(entry_for(fake.url, "box", type="ollama"), "hi", turn_context.ContextSizer())
            self.assertEqual(fake.posts()[0]["url"], "/api/chat")
        with FakeServer() as fake:
            result, notes = run_turn(entry_for(fake.url, "lab"), "x" * 130000, turn_context.ContextSizer())
            post = fake.posts()[0]
            self.assertEqual(post["url"], "/v1/chat/completions")
            self.assertNotIn("options", post["body"])
            self.assertEqual((result.text, notes), ("hello", []))

    def test_sticky_window_is_kept_per_provider_base_and_model(self):
        with FakeServer(ndjson_reply) as fake:
            sizer = turn_context.ContextSizer()
            entry = entry_for(fake.url)
            run_turn(entry, "x" * 60000, sizer, model="big")
            run_turn(entry, "tiny", sizer, model="big")
            run_turn(entry, "tiny", sizer, model="other")
            self.assertEqual([p["body"]["options"]["num_ctx"] for p in fake.posts()], [32768, 32768, 8192])

    def test_a_remembered_window_above_the_cap_is_not_reused(self):
        with FakeServer(ndjson_reply) as fake:
            sizer = turn_context.ContextSizer()
            run_turn(entry_for(fake.url), "x" * 60000, sizer)
            run_turn(entry_for(fake.url, num_ctx_max=16384), "tiny", sizer)
            self.assertEqual([p["body"]["options"]["num_ctx"] for p in fake.posts()], [32768, 8192])


class FindingDb2dd2faTests(unittest.TestCase):
    """f-db2dd2fa: long piped input must keep its start, and over-window input must warn."""

    def paste(self, size):
        filler = "Filler line with nothing of interest in it at all.\n"
        body = SECRET_LINE + "\n" + filler * (size // len(filler))
        return body[:size]

    def test_30kb_piped_input_keeps_line_one_and_sizes_the_window(self):
        paste = self.paste(30 * 1024)
        with FakeServer(ndjson_reply) as fake:
            code, out, err = run_cli(["--base-url", fake.url, "--model", "ollama/m", "What is the secret code?"], paste)
            post = fake.posts()[0]
            self.assertEqual((code, out), (0, "mango\n"))
            self.assertEqual(post["url"], "/api/chat")
            self.assertEqual(post["body"]["options"], {"num_ctx": 16384})
            content = post["body"]["messages"][-1]["content"]
            self.assertTrue(content.startswith(SECRET_LINE), "the first line of the paste was lost")
            self.assertTrue(content.endswith("What is the secret code?"))
            self.assertGreater(len(content), 30 * 1024)
            self.assertNotIn("warning:", err)

    def test_input_over_the_window_shows_a_warning_and_still_answers(self):
        paste = self.paste(150 * 1024)
        with FakeServer(ndjson_reply) as fake:
            code, out, err = run_cli(["--base-url", fake.url, "--model", "ollama/m", "What is the secret code?"], paste)
            self.assertEqual((code, out), (0, "mango\n"))
            self.assertEqual(fake.posts()[0]["body"]["options"], {"num_ctx": 32768})
            self.assertRegex(err, r"^warning: input ~\d+ tokens exceeds the model window 32768; the start will be cut\n$")

    def test_json_mode_uses_the_same_route_and_keeps_stdout_one_object(self):
        paste = self.paste(150 * 1024)
        with FakeServer(ndjson_reply) as fake:
            code, out, err = run_cli(["--json", "--base-url", fake.url, "--model", "ollama/m", "q"], paste)
            obj = json.loads(out)
            self.assertEqual(code, 0)
            self.assertEqual((obj["ok"], obj["provider"], obj["model"], obj["text"]), (True, "ollama", "m", "mango"))
            self.assertNotIn("error", obj)
            self.assertEqual(fake.posts()[0]["url"], "/api/chat")
            self.assertIn("warning: input ~", err)

    def test_frames_mode_carries_the_warning_as_a_notify_event_before_the_text(self):
        import turn_frames

        class BinaryOut(io.BytesIO):
            pass

        paste = self.paste(150 * 1024)
        with FakeServer(ndjson_reply) as fake:
            out = BinaryOut()
            code = turn_cli.main(["--frames", "--base-url", fake.url, "--model", "ollama/m", "q"], stdin=PipedStdin(paste), stdout=out, stderr=io.StringIO())
            items = [i for i in turn_frames.decode_stream(out.getvalue()) if i["kind"] == "frame"]
            kinds = []
            for item in items:
                if item["channel"] == "data":
                    kinds.append("data")
                else:
                    obj = json.loads(item["payload"])
                    kinds.append(obj.get("event") or obj["type"])
            self.assertEqual(code, 0)
            self.assertEqual(kinds, ["hello", "notify", "data", "data", "usage", "done"])
            self.assertIn("exceeds the model window 32768", json.loads(items[1]["payload"])["text"])


if __name__ == "__main__":
    unittest.main()
