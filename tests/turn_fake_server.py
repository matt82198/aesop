#!/usr/bin/env python3
"""Scriptable fake OpenAI-compatible server on 127.0.0.1 for the turn-engine tests.

Not a test module (no test_ prefix). It listens on an ephemeral loopback port,
records every request, and answers through a handler the test supplies:

    handler(reply, request, n)

where `reply` writes the response (see Reply), `request` is the recorded
request dict and `n` is the 1-based count of POSTs seen so far. GET
`/v1/models` is answered with the model list and never reaches the handler.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import json
import os
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Reply:
    """Response writer handed to a handler. Every write is flushed at once."""

    def __init__(self, handler, server):
        self._h = handler
        self._server = server

    def head(self, status=200, content_type="text/event-stream", headers=None):
        self._h.send_response(status)
        if content_type:
            self._h.send_header("content-type", content_type)
        for name, value in (headers or {}).items():
            self._h.send_header(name, value)
        self._h.end_headers()
        self._h.wfile.flush()

    def write(self, data):
        self._h.wfile.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        self._h.wfile.flush()

    def sse(self, parts, usage=None, done=True):
        """A complete SSE reply: one delta frame per part, optional usage, then [DONE]."""
        self.head(200, "text/event-stream")
        for part in parts:
            self.write("data: " + json.dumps({"choices": [{"delta": {"content": part}}]}) + "\n\n")
        if usage is not None:
            self.write("data: " + json.dumps({"choices": [], "usage": usage}) + "\n\n")
        if done:
            self.write("data: [DONE]\n\n")

    def json(self, obj, status=200):
        self.head(status, "application/json")
        self.write(json.dumps(obj))

    def wait_for_client_close(self, timeout_s=5.0):
        """Block until the client closes its side; records the close on the server."""
        sock = self._h.connection
        sock.settimeout(timeout_s)
        try:
            while True:
                if sock.recv(4096) == b"":
                    break
        except socket.timeout:
            return False
        except OSError:
            pass
        self._server.client_closed.set()
        return True

    def drop(self):
        """Reset the connection without finishing the reply."""
        sock = self._h.connection
        try:
            # linger on, 0 s: close() sends RST. The struct is two shorts on Windows, two ints elsewhere.
            linger = struct.pack("HH", 1, 0) if os.name == "nt" else struct.pack("ii", 1, 0)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


class FakeServer:
    """`with FakeServer(handler) as fake:` then use fake.url, fake.requests, fake.posts()."""

    def __init__(self, handler=None, models=("m",)):
        self.requests = []
        self.client_closed = threading.Event()
        self._handler = handler or (lambda reply, request, n: reply.sse(["hel", "lo"]))
        self._models = list(models)
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _record(self):
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except ValueError:
                    body = raw.decode("utf-8", errors="replace")
                request = {
                    "method": self.command,
                    "url": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                    "raw": raw,
                }
                fake.requests.append(request)
                return request

            def do_GET(self):
                self._record()
                if self.path.startswith("/v1/models"):
                    payload = json.dumps({"data": [{"id": m} for m in fake._models]}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                request = self._record()
                try:
                    fake._handler(Reply(self, fake), request, len(fake.posts()))
                except OSError:
                    pass  # the client went away mid-reply; the test asserts on what it saw

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.url = f"{self.origin}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def posts(self):
        return [r for r in self.requests if r["method"] == "POST"]

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False


def closed_port() -> int:
    """A loopback port with nothing listening on it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port
