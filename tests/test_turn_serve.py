#!/usr/bin/env python3
"""Tests for the streaming modes of driver/turn.py (TUI driver plan, phase P2).

`--frames` (one turn as an AESOP-PIPE/1 stream), `--stream` (plain text
deltas) and `--serve` (one child for many turns, with cancel). Offline only:
providers are scripted fake servers on 127.0.0.1. The serve tests spawn the
real `python -m driver.turn` child and prove that a cancel reports
`cancelled` and that a tree-kill leaves no process holding the connection.

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import io
import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
TESTS_DIR = REPO / "tests"
for _p in (DRIVER_DIR, TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import proc_util  # noqa: E402
import turn as turn_cli  # noqa: E402
import turn_frames  # noqa: E402
import turn_stream  # noqa: E402
from turn_fake_server import FakeServer, closed_port  # noqa: E402


class TtyStdin:
    def isatty(self):
        return True

    def read(self, *args):
        raise AssertionError("TTY stdin must not be read")


def run_cli(argv, *, stdout=None, **kwargs):
    out = io.BytesIO() if stdout is None else stdout
    err = io.StringIO()
    code = turn_cli.main(argv, stdin=TtyStdin(), stdout=out, stderr=err, **kwargs)
    return code, out, err.getvalue()


def decode(raw):
    """Split a frames stream into (data texts, control objects, end exit)."""
    items = turn_frames.decode_stream(raw)
    data = [i["payload"].decode("utf-8") for i in items if i["kind"] == "frame" and i["channel"] == "data"]
    control = [json.loads(i["payload"]) for i in items if i["kind"] == "frame" and i["channel"] == "control"]
    return data, control, items[-1]["exit"]


def events(control, name="notify"):
    return [c for c in control if c.get("type") == "event" and c.get("event") == name]


def hold_after_first_delta(reply, request, n):
    reply.head(200, "text/event-stream")
    reply.write('data: {"choices":[{"delta":{"content":"one"}}]}\n\n')
    reply.wait_for_client_close(10)


class FramesModeTests(unittest.TestCase):
    def test_frames_mode_streams_deltas_then_done_then_end(self):
        usage = {"prompt_tokens": 1, "completion_tokens": 2}
        with FakeServer(lambda reply, request, n: reply.sse(["hel", "lo"], usage=usage)) as fake:
            code, out, err = run_cli(["--frames", "--base-url", fake.url, "--model", "lab/m1", "hi"])
            data, control, end = decode(out.getvalue())
            self.assertEqual((code, end, err), (0, 0, ""))
            self.assertEqual(data, ["hel", "lo"])
            self.assertEqual(control[0]["type"], "hello")
            done = control[-1]
            self.assertEqual((done["type"], done["ok"], done["exit"]), ("done", True, 0))
            self.assertEqual((done["provider"], done["model"], done["text"]), ("lab", "m1", "hello"))
            self.assertEqual([e["usage"] for e in events(control, "usage")], [usage])
            self.assertEqual(fake.posts()[0]["body"]["messages"], [{"role": "user", "content": "hi"}])
            self.assertEqual(fake.posts()[0]["body"]["model"], "m1")

    def test_frames_mode_reports_a_failure_as_one_line_and_the_exit_code(self):
        url = f"http://127.0.0.1:{closed_port()}/v1"
        code, out, _ = run_cli(["--frames", "--base-url", url, "hi"])
        data, control, end = decode(out.getvalue())
        self.assertEqual((code, end, data), (3, 3, []))
        lines = [e["text"] for e in events(control)]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("error: custom: connect: cannot reach custom at http://127.0.0.1:"), lines)
        done = control[-1]
        self.assertEqual((done["ok"], done["exit"], done["error"]), (False, 3, lines[0]))

    def test_stream_mode_writes_plain_text_and_errors_to_stderr(self):
        with FakeServer(lambda reply, request, n: reply.sse(["hel", "lo"])) as fake:
            out = io.StringIO()
            code, _, err = run_cli(["--stream", "--base-url", fake.url, "hi"], stdout=out)
            self.assertEqual((code, out.getvalue(), err), (0, "hello\n", ""))
        out = io.StringIO()
        code, _, err = run_cli(["--stream", "--base-url", f"http://127.0.0.1:{closed_port()}/v1", "hi"], stdout=out)
        self.assertEqual((code, out.getvalue()), (3, ""))
        self.assertIn("error: custom: connect: cannot reach custom", err)

    def test_cancel_shows_cancelled_and_exits_130(self):
        token = turn_stream.CancelToken()

        class CancelOnFirstDelta(io.BytesIO):
            def write(self, data):
                written = super().write(data)
                if b"F data " in data:
                    token.cancel()
                return written

        with FakeServer(hold_after_first_delta) as fake:
            code, out, _ = run_cli(["--frames", "--base-url", fake.url, "hi"], stdout=CancelOnFirstDelta(), cancel=token)
            data, control, end = decode(out.getvalue())
            self.assertEqual((code, end, data), (130, 130, ["one"]))
            self.assertEqual([e["text"] for e in events(control)], ["cancelled: custom request aborted"])
            self.assertEqual((control[-1]["ok"], control[-1]["cancelled"]), (False, True))
            self.assertTrue(fake.client_closed.wait(1), "the server did not observe the close within 1 s")

    def test_total_timeout_is_exit_5(self):
        release = threading.Event()
        try:
            with FakeServer(lambda reply, request, n: release.wait(5)) as fake:
                code, out, _ = run_cli(["--frames", "--timeout", "0.3", "--base-url", fake.url, "hi"])
                _, control, end = decode(out.getvalue())
                self.assertEqual((code, end), (5, 5))
                self.assertEqual([e["text"] for e in events(control)], ["error: custom: timeout: timed out after 0.3 s"])
        finally:
            release.set()

    def test_base_url_boundary_and_usage(self):
        with FakeServer() as fake:
            code, out, _ = run_cli(["--frames", "--base-url", "https://api.anthropic.com/v1", "hi"])
            _, control, end = decode(out.getvalue())
            self.assertEqual((code, end), (6, 6))
            self.assertIn("refused", events(control)[0]["text"])
            code, out, err = run_cli(["--frames", "--base-url", "not a url", "hi"])
            self.assertEqual(code, 2)
            self.assertIn("--base-url", err)
            self.assertEqual(fake.requests, [])


class ServeChild:
    """The real `python -m driver.turn --serve` child, with an incremental frame reader."""

    def __init__(self, url):
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "driver.turn", "--serve", "--base-url", url],
            cwd=str(REPO), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=flags, start_new_session=(os.name != "nt"),
        )
        self.items = []
        self._cond = threading.Condition()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        out = self.proc.stdout
        if out.readline() != turn_frames.MAGIC:
            return
        while True:
            header = out.readline()
            if not header:
                break
            parts = header.rstrip(b"\n").split(b" ")
            if parts[0] == b"E":
                item = {"end": int(parts[1])}
            else:
                payload = out.read(int(parts[3]) + 1)[:-1]
                item = {"data": payload.decode("utf-8")} if parts[1] == b"data" else {"control": json.loads(payload)}
            with self._cond:
                self.items.append(item)
                self._cond.notify_all()

    def send(self, obj):
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def wait_for(self, predicate, timeout_s=15):
        with self._cond:
            ok = self._cond.wait_for(lambda: any(predicate(i) for i in self.items), timeout_s)
            return ok

    def done(self, turn_id):
        self.wait_for(lambda i: i.get("control", {}).get("type") == "done" and i["control"].get("id") == turn_id)
        found = [i["control"] for i in self.items if i.get("control", {}).get("type") == "done" and i["control"].get("id") == turn_id]
        return found[0] if found else None

    def notes(self, turn_id):
        return [i["control"]["text"] for i in self.items
                if i.get("control", {}).get("event") == "notify" and i["control"].get("id") == turn_id]

    def close(self):
        if self.proc.poll() is None:
            proc_util.kill_process_tree(self.proc)
        self.proc.wait(10)
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except OSError:
                pass


class ServeModeTests(unittest.TestCase):
    def test_one_child_serves_turns_cancels_one_and_ends_on_eof(self):
        def handler(reply, request, n):
            if request["body"]["messages"][-1]["content"] == "hold":
                hold_after_first_delta(reply, request, n)
            else:
                reply.sse(["turn", str(n)])

        with FakeServer(handler) as fake:
            child = ServeChild(fake.url)
            try:
                child.send({"op": "turn", "id": 1, "line": "first"})
                self.assertEqual(child.done(1)["text"], "turn1")
                child.send({"op": "turn", "id": 2, "messages": [{"role": "user", "content": "second"}]})
                done = child.done(2)
                self.assertEqual((done["ok"], done["exit"], done["text"]), (True, 0, "turn2"))

                child.send({"op": "turn", "id": 3, "line": "hold"})
                self.assertTrue(child.wait_for(lambda i: i.get("data") == "one"), "no streamed delta before the cancel")
                child.send({"op": "cancel", "id": 3})
                done = child.done(3)
                self.assertIsNotNone(done, "the cancelled turn never reported done")
                self.assertEqual((done["ok"], done["cancelled"]), (False, True))
                self.assertEqual(child.notes(3), ["cancelled: custom request aborted"])
                self.assertTrue(fake.client_closed.wait(1), "the server did not observe the close within 1 s")

                child.send({"this is": "not an op"})
                child.send({"op": "turn", "id": 4, "line": "after"})
                self.assertEqual(child.done(4)["text"], "turn4", "the session must survive a cancel and a bad request")

                child.proc.stdin.close()
                self.assertEqual(child.proc.wait(10), 0)
                child._reader.join(5)
                self.assertEqual(child.items[-1], {"end": 0})
                self.assertEqual(len(fake.posts()), 4)
            finally:
                child.close()

    def test_tree_kill_during_a_turn_leaves_no_stray_process(self):
        with FakeServer(hold_after_first_delta) as fake:
            child = ServeChild(fake.url)
            try:
                child.send({"op": "turn", "id": 1, "line": "hold"})
                self.assertTrue(child.wait_for(lambda i: i.get("data") == "one"))
                proc_util.kill_process_tree(child.proc)
                child.proc.wait(10)
                self.assertIsNotNone(child.proc.poll(), "the driver child is still running after the tree kill")
                # Nothing is left holding the connection: the server sees it close.
                self.assertTrue(fake.client_closed.wait(3), "a stray process still holds the provider connection")
            finally:
                child.close()


if __name__ == "__main__":
    unittest.main()
