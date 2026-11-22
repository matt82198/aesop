#!/usr/bin/env python3
"""Tests for driver/turn_frames.py and driver/turn_stream.py (TUI driver plan, phase P2).

Offline only: every request goes to a scripted fake server on 127.0.0.1
(tests/turn_fake_server.py). The streaming tests prove a delta reaches the
caller before the server finishes, and that a cancel closes the socket.

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import activity_feed_isolation  # noqa: E402
activity_feed_isolation.pin()
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
TESTS_DIR = REPO / "tests"
for _p in (DRIVER_DIR, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import turn_frames  # noqa: E402
import turn_stream  # noqa: E402
from turn_fake_server import FakeServer, closed_port  # noqa: E402

EURO_TEXT = "a" + chr(0x20AC) + "b"
MESSAGES = [{"role": "user", "content": "hi"}]


def entry_for(url, provider_id="fake", **raw):
    base = {"type": "openai", "base_url": url, "models": "any", "is_local": True, "timeout_ms": 5000}
    base.update(raw)
    return {"id": provider_id, "raw": base}


def run_turn(entry, **kwargs):
    deltas, lines = [], []
    result = turn_stream.run_stream_turn(
        entry, "m", MESSAGES, env=kwargs.pop("env", {}), on_delta=deltas.append, notify=lines.append, **kwargs
    )
    return result, deltas, lines


class FrameCodecTests(unittest.TestCase):
    def test_encode_frame_counts_payload_bytes_not_characters(self):
        # U+00E9 is two bytes in UTF-8, so the length field is 6, not 5.
        frame = turn_frames.encode_frame("data", "text/plain", "caf" + chr(0xE9) + "!")
        self.assertEqual(frame, b"F data text/plain 6\ncaf\xc3\xa9!\n")

    def test_encode_end_and_bad_values(self):
        self.assertEqual(turn_frames.encode_end(6), b"E 6\n")
        with self.assertRaises(ValueError):
            turn_frames.encode_end(-1)
        with self.assertRaises(ValueError):
            turn_frames.encode_frame("Bad Channel", "text/plain", "x")

    def test_writer_emits_magic_once_then_frames_then_end(self):
        out = io.BytesIO()
        writer = turn_frames.FrameWriter(out)
        writer.data("a\nb")
        writer.control({"type": "done", "exit": 0})
        writer.end(0)
        writer.data("after the end is dropped")
        raw = out.getvalue()
        self.assertTrue(raw.startswith(b"AESOP-PIPE/1\nF data "))
        self.assertEqual(raw.count(b"AESOP-PIPE/1\n"), 1)
        items = turn_frames.decode_stream(raw)
        self.assertEqual([i["kind"] for i in items], ["frame", "frame", "end"])
        self.assertEqual(items[0]["payload"], b"a\nb")
        self.assertEqual(items[0]["channel"], "data")
        self.assertEqual(json.loads(items[1]["payload"]), {"type": "done", "exit": 0})
        self.assertEqual(items[2]["exit"], 0)

    def test_decode_rejects_truncated_and_unterminated_streams(self):
        good = turn_frames.MAGIC + turn_frames.encode_frame("data", "text/plain", "hello") + turn_frames.encode_end(0)
        self.assertEqual(len(turn_frames.decode_stream(good)), 2)
        for bad in (good[:-4], good[len(turn_frames.MAGIC):], good + b"x", good.replace(b" 5\n", b" 9\n")):
            with self.assertRaises(turn_frames.FramingError):
                turn_frames.decode_stream(bad)


class StreamParsingTests(unittest.TestCase):
    def test_sse_deltas_arrive_in_order_with_usage(self):
        usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
        with FakeServer(lambda reply, request, n: reply.sse(["hel", "lo"], usage=usage)) as fake:
            result, deltas, lines = run_turn(entry_for(fake.url))
            self.assertEqual(deltas, ["hel", "lo"])
            self.assertEqual((result.ok, result.text, result.exit_code), (True, "hello", 0))
            self.assertEqual(result.usage, usage)
            self.assertEqual(lines, [])
            post = fake.posts()[0]
            self.assertEqual(post["url"], "/v1/chat/completions")
            self.assertEqual(post["body"], {"model": "m", "stream": True, "messages": MESSAGES})
            self.assertEqual(post["headers"]["accept"], "text/event-stream")
            self.assertNotIn("authorization", post["headers"])

    def test_first_delta_reaches_the_caller_before_the_server_finishes(self):
        first_seen = threading.Event()
        released = []

        def handler(reply, request, n):
            reply.head(200, "text/event-stream")
            reply.write('data: {"choices":[{"delta":{"content":"one"}}]}\n\n')
            # The server only continues once the client has handled the first delta.
            released.append(first_seen.wait(5))
            reply.write('data: {"choices":[{"delta":{"content":"two"}}]}\n\ndata: [DONE]\n\n')

        with FakeServer(handler) as fake:
            deltas = []

            def on_delta(text):
                deltas.append(text)
                first_seen.set()

            result = turn_stream.run_stream_turn(entry_for(fake.url), "m", MESSAGES, env={}, on_delta=on_delta)
            self.assertEqual(released, [True], "the first delta was not delivered while the stream was open")
            self.assertEqual(deltas, ["one", "two"])
            self.assertTrue(result.ok)

    def test_frame_and_multibyte_character_split_across_writes(self):
        frame = ('data: ' + json.dumps({"choices": [{"delta": {"content": EURO_TEXT}}]}, ensure_ascii=False) + '\r\n\r\n').encode("utf-8")
        cut = frame.index(b"\xe2\x82\xac") + 1  # inside the 3-byte euro sign

        def handler(reply, request, n):
            reply.head(200, "text/event-stream")
            reply.write(b": keepalive\n\n" + frame[:cut])
            reply.write(frame[cut:] + b"data: [DONE]\n\n")

        with FakeServer(handler) as fake:
            result, deltas, _ = run_turn(entry_for(fake.url))
            self.assertEqual(result.text, EURO_TEXT)
            self.assertEqual(deltas, [EURO_TEXT])

    def test_ndjson_and_whole_json_normalise_to_the_same_text(self):
        def ndjson(reply, request, n):
            reply.head(200, "application/x-ndjson")
            reply.write(json.dumps({"message": {"content": "hel"}}) + "\n")
            reply.write(json.dumps({"message": {"content": "lo"}}) + "\n")
            reply.write(json.dumps({"done": True, "prompt_eval_count": 7, "eval_count": 2}))

        def whole(reply, request, n):
            reply.json({"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 9}})

        with FakeServer(ndjson) as a, FakeServer(whole) as b:
            ra, da, _ = run_turn(entry_for(a.url))
            rb, db, _ = run_turn(entry_for(b.url))
            self.assertEqual((ra.text, rb.text), ("hello", "hello"))
            self.assertEqual(da, ["hel", "lo"])
            self.assertEqual(db, ["hello"])
            self.assertEqual(ra.usage, {"prompt_tokens": 7, "completion_tokens": 2})
            self.assertEqual(rb.usage, {"total_tokens": 9})

    def test_null_empty_and_reasoning_only_deltas_produce_no_text(self):
        def handler(reply, request, n):
            reply.head(200, "text/event-stream")
            for delta in ({"content": None}, {"content": ""}, {"reasoning": "thinking"}, {"content": "ok"}, {}):
                reply.write("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n")
            reply.write("event: ping\n\ndata: [DONE]\n\n")

        with FakeServer(handler) as fake:
            result, deltas, _ = run_turn(entry_for(fake.url))
            self.assertEqual(deltas, ["ok"])
            self.assertEqual(result.text, "ok")

    def test_key_is_sent_as_bearer_and_a_missing_key_is_no_key_without_a_request(self):
        secret = "sk-" + "test-" + "SENTINEL"
        with FakeServer() as fake:
            entry = entry_for(fake.url, is_local=False, api_key_env="FAKE_TURN_KEY")
            result, _, lines = run_turn(entry, env={"FAKE_TURN_KEY": secret})
            self.assertTrue(result.ok)
            self.assertEqual(fake.posts()[0]["headers"]["authorization"], "Bearer " + secret)
            self.assertNotIn(secret, "".join(lines))
            missing, _, lines = run_turn(entry, env={})
            self.assertEqual(lines, ["error: fake: no-key: env FAKE_TURN_KEY is unset"])
            self.assertEqual(missing.exit_code, 4)
            self.assertEqual(len(fake.posts()), 1, "a no-key turn must not send a request")


class FailureClassTests(unittest.TestCase):
    def one_line(self, handler, **raw):
        with FakeServer(handler) as fake:
            result, deltas, lines = run_turn(entry_for(fake.url, **raw))
            self.assertEqual(len(lines), 1, lines)
            return result, deltas, lines[0], fake

    def test_http_statuses_map_to_classes_with_the_provider_message(self):
        cases = [
            (401, {"error": {"message": "bad   key\n here"}}, "error: fake: auth: HTTP 401: bad key here", 4),
            (429, {"error": "slow down"}, "error: fake: rate-limit: HTTP 429: slow down", 4),
            (500, "not json", "error: fake: server: HTTP 500", 4),
            (404, {"error": {"message": "model 'x' not found"}}, "error: fake: bad-response: HTTP 404: model 'x' not found", 4),
        ]
        for status, body, expected, code in cases:
            with self.subTest(status=status):
                def handler(reply, request, n, status=status, body=body):
                    reply.head(status, "application/json")
                    reply.write(body if isinstance(body, str) else json.dumps(body))

                result, deltas, line, _ = self.one_line(handler)
                self.assertEqual(line, expected)
                self.assertEqual((result.ok, result.exit_code, deltas), (False, code, []))

    def test_redirect_is_not_followed(self):
        with FakeServer() as other:
            def handler(reply, request, n):
                reply.head(302, None, headers={"location": other.url + "/chat/completions"})

            result, _, line, fake = self.one_line(handler)
            self.assertEqual(line, "error: fake: bad-response: redirect 302 not followed")
            self.assertEqual(result.exit_code, 4)
            self.assertEqual(len(fake.posts()), 1)
            self.assertEqual(other.requests, [], "the redirect target must receive nothing")

    def test_malformed_frame_is_bad_response(self):
        def handler(reply, request, n):
            reply.head(200, "text/event-stream")
            reply.write("data: {not json}\n\n")

        _, _, line, _ = self.one_line(handler)
        self.assertEqual(line, "error: fake: bad-response: malformed stream frame")

    def test_connection_refused_is_connect_exit_3(self):
        url = f"http://127.0.0.1:{closed_port()}/v1"
        result, deltas, lines = run_turn(entry_for(url))
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("error: fake: connect: cannot reach fake at http://127.0.0.1:"), lines[0])
        self.assertIn("is it running? (ECONNREFUSED)", lines[0])
        self.assertEqual((result.exit_code, deltas), (3, []))

    def test_a_refused_loopback_connect_is_reported_at_once(self):
        # Windows retransmits the SYN to a closed loopback port for about 2 s before it reports the
        # refusal; the Node harness answers in milliseconds. "Is it running?" must not take seconds.
        import time
        for host in ("127.0.0.1", "localhost"):
            with self.subTest(host=host):
                url = f"http://{host}:{closed_port()}/v1"
                started = time.monotonic()
                result, _, lines = run_turn(entry_for(url))
                elapsed = time.monotonic() - started
                self.assertEqual(result.exit_code, 3, lines)
                self.assertIn("(ECONNREFUSED)", lines[0])
                self.assertLess(elapsed, 1.0, f"a refused connect to {host} took {elapsed:.2f} s")

    def test_a_hang_past_the_idle_timeout_is_timeout_exit_5(self):
        release = threading.Event()

        def handler(reply, request, n):
            release.wait(5)

        try:
            result, _, line, _ = self.one_line(handler, timeout_ms=300)
        finally:
            release.set()
        self.assertEqual(line, "error: fake: timeout: no response within 300 ms")
        self.assertEqual(result.exit_code, 5)

    def test_a_cut_after_the_first_delta_keeps_the_text_and_says_interrupted(self):
        def handler(reply, request, n):
            reply.head(200, "text/event-stream")
            reply.write('data: {"choices":[{"delta":{"content":"par"}}]}\n\n')
            reply.write('data: {"choices":[{"delta":{"content":"tial"}}]}\n\n')
            # A reset discards bytes the client has not read yet, so wait until both deltas were handled.
            both_seen.wait(5)
            reply.drop()

        both_seen = threading.Event()
        deltas, lines = [], []

        def on_delta(text):
            deltas.append(text)
            if len(deltas) == 2:
                both_seen.set()

        with FakeServer(handler) as fake:
            result = turn_stream.run_stream_turn(entry_for(fake.url), "m", MESSAGES, env={}, on_delta=on_delta, notify=lines.append)
        self.assertEqual(lines, ["stream interrupted from fake"])
        self.assertEqual((result.ok, result.text, result.exit_code), (False, "partial", 4))
        self.assertEqual(deltas, ["par", "tial"])

    def test_claude_ids_are_refused_before_any_socket(self):
        with FakeServer() as fake:
            for provider_id, model in (("fake", "claude-haiku-5-5"), ("anthropic", "m")):
                with self.subTest(provider=provider_id, model=model):
                    lines = []
                    result = turn_stream.run_stream_turn(entry_for(fake.url, provider_id), model, MESSAGES, env={}, notify=lines.append)
                    self.assertEqual(result.exit_code, 6)
                    self.assertTrue(lines[0].startswith(f"error: {provider_id}: refused: "), lines)
            self.assertEqual(fake.requests, [])


class CleanEofAfterShutdown:
    """A fake http.client connection whose read returns a clean end of stream once its socket is shut down.

    That is what Linux does: shutdown() makes a blocked recv() return 0 bytes, not an error. Windows
    raises instead. The fake makes the Linux shape reproducible on every platform.
    """

    class _Socket:
        def __init__(self):
            self.down = threading.Event()

        def shutdown(self, how):
            self.down.set()

        def close(self):
            self.down.set()

    class _Response:
        status = 200

        def __init__(self, sock, first_seen):
            self._sock = sock
            self._first_seen = first_seen
            self._sent = False

        def getheader(self, name, default=None):
            return "text/event-stream"

        def read1(self, size):
            if not self._sent:
                self._sent = True
                return b'data: {"choices":[{"delta":{"content":"one"}}]}\n\n'
            self._sock.down.wait(5)
            return b""

    def __init__(self):
        self.sock = None
        self.first_seen = threading.Event()

    def connect(self):
        self.sock = self._Socket()

    def putrequest(self, *args, **kwargs):
        pass

    def putheader(self, *args):
        pass

    def endheaders(self, body=None):
        pass

    def getresponse(self):
        return self._Response(self.sock, self.first_seen)

    def close(self):
        pass


class CancelTests(unittest.TestCase):
    def test_a_cancel_that_ends_the_stream_cleanly_is_still_cancelled_not_ok(self):
        # Linux shape: after the cancel the read just ends. The turn must not be reported as a
        # completed reply (it would also be remembered as conversation context).
        fake = CleanEofAfterShutdown()
        token = turn_stream.CancelToken()
        box = {}

        def on_delta(text):
            fake.first_seen.set()

        def work():
            box["lines"] = []
            box["result"] = turn_stream.run_stream_turn(
                entry_for("http://127.0.0.1:9/v1"), "m", MESSAGES, env={}, cancel=token,
                on_delta=on_delta, notify=box["lines"].append, connect=lambda *args: fake,
            )

        thread = threading.Thread(target=work)
        thread.start()
        self.assertTrue(fake.first_seen.wait(5))
        token.cancel()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        result = box["result"]
        self.assertEqual((result.ok, result.cancelled, result.text, result.exit_code), (False, True, "one", 130))
        self.assertEqual(box["lines"], ["cancelled: fake request aborted"])

    def test_cancel_mid_stream_closes_the_socket_and_reports_cancelled(self):
        first_seen = threading.Event()

        def handler(reply, request, n):
            reply.head(200, "text/event-stream")
            reply.write('data: {"choices":[{"delta":{"content":"one"}}]}\n\n')
            reply.wait_for_client_close(5)

        with FakeServer(handler) as fake:
            token = turn_stream.CancelToken()
            box = {}

            def work():
                box["result"] = turn_stream.run_stream_turn(
                    entry_for(fake.url), "m", MESSAGES, env={}, cancel=token,
                    on_delta=lambda text: first_seen.set(), notify=box.setdefault("lines", []).append,
                )

            thread = threading.Thread(target=work)
            thread.start()
            self.assertTrue(first_seen.wait(5))
            token.cancel()
            thread.join(2)
            self.assertFalse(thread.is_alive(), "cancel did not end the turn within 2 s")
            self.assertTrue(fake.client_closed.wait(1), "the server did not observe the close within 1 s")
            result = box["result"]
            self.assertEqual(box["lines"], ["cancelled: fake request aborted"])
            self.assertEqual((result.ok, result.cancelled, result.text, result.exit_code), (False, True, "one", 130))

    def test_a_token_cancelled_before_the_turn_sends_nothing(self):
        with FakeServer() as fake:
            token = turn_stream.CancelToken()
            token.cancel()
            result, _, lines = run_turn(entry_for(fake.url), cancel=token)
            self.assertEqual(lines, ["cancelled: fake request aborted"])
            self.assertTrue(result.cancelled)
            self.assertEqual(fake.requests, [])


if __name__ == "__main__":
    unittest.main()
