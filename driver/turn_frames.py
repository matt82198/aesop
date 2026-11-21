#!/usr/bin/env python3
"""AESOP-PIPE/1 frame writer and reader for the turn engine (TUI driver plan, phase P2).

PURPOSE: this artifact exists to make the aesop chat TUI, aesop ask and
codex/gemini/agy run every turn through the aesop driver (one turn engine;
the pipe is the product); status: real

The grammar is the one stdio/frames.mjs decodes:

    AESOP-PIPE/1\\n
    F <channel> <content-type> <length>\\n<payload>\\n
    E <exit>\\n

`length` counts payload BYTES. Text deltas go on the `data` channel; system
lines, usage and the per-turn result go on the `control` channel as one JSON
object each (types `hello`, `event`, `done`).

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import json
import re
import threading

MAGIC = b"AESOP-PIPE/1\n"
TEXT_TYPE = "text/plain;charset=utf-8"
JSON_TYPE = "application/json"

_CHANNEL_RE = re.compile(r"^[a-z0-9._-]{1,32}$")
_CONTENT_TYPE_RE = re.compile(r"^[!-~]+$")
_FRAME_RE = re.compile(rb"^F ([a-z0-9._-]{1,32}) ([!-~]+) ([0-9]+)$")
_END_RE = re.compile(rb"^E ([0-9]+)$")


class FramingError(ValueError):
    """Raised for bytes that violate the frame grammar."""


def encode_frame(channel: str, content_type: str, payload) -> bytes:
    """Return `F <channel> <content-type> <length>\\n<payload>\\n` as bytes."""
    if not isinstance(channel, str) or not _CHANNEL_RE.match(channel):
        raise ValueError(f"invalid frame channel {channel!r}")
    if not isinstance(content_type, str) or not _CONTENT_TYPE_RE.match(content_type):
        raise ValueError(f"invalid frame content type {content_type!r}")
    body = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    header = f"F {channel} {content_type} {len(body)}\n".encode("ascii")
    return header + body + b"\n"


def encode_end(exit_code: int) -> bytes:
    """Return the end frame `E <exit>\\n`."""
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code < 0:
        raise ValueError(f"end frame exit must be a non-negative integer, got {exit_code!r}")
    return f"E {exit_code}\n".encode("ascii")


class FrameWriter:
    """Writes one frames stream to a binary stream. Every frame is flushed at once.

    Thread-safe: the serve loop writes turn frames from a worker thread while
    the reader thread may write a result for a rejected request.
    """

    def __init__(self, stream):
        self._stream = stream
        self._lock = threading.Lock()
        self._started = False
        self._ended = False

    def _write(self, data: bytes) -> None:
        with self._lock:
            if self._ended:
                return
            if not self._started:
                self._started = True
                data = MAGIC + data
            self._stream.write(data)
            self._stream.flush()

    def data(self, text: str) -> None:
        """One text delta on the data channel."""
        self._write(encode_frame("data", TEXT_TYPE, text))

    def control(self, obj: dict) -> None:
        """One JSON control message."""
        self._write(encode_frame("control", JSON_TYPE, json.dumps(obj, ensure_ascii=False)))

    def end(self, exit_code: int) -> None:
        """The end frame. Nothing is written after it."""
        self._write(encode_end(exit_code))
        with self._lock:
            self._ended = True


def decode_stream(data: bytes) -> list:
    """Decode a complete frames stream into items.

    Items are `{"kind": "frame", "channel", "content_type", "payload": bytes}`
    and a final `{"kind": "end", "exit": int}`. Raises FramingError on any
    grammar violation, truncation or a missing end frame.
    """
    if not data.startswith(MAGIC):
        raise FramingError("missing AESOP-PIPE/1 magic line")
    pos = len(MAGIC)
    items = []
    while True:
        nl = data.find(b"\n", pos)
        if nl == -1:
            raise FramingError("missing end frame")
        header = data[pos:nl]
        pos = nl + 1
        end = _END_RE.match(header)
        if end:
            if pos != len(data):
                raise FramingError("bytes after the end frame")
            items.append({"kind": "end", "exit": int(end.group(1))})
            return items
        frame = _FRAME_RE.match(header)
        if not frame:
            raise FramingError(f"malformed frame header: {header[:80]!r}")
        length = int(frame.group(3))
        if len(data) - pos < length + 1:
            raise FramingError("truncated frame payload")
        if data[pos + length:pos + length + 1] != b"\n":
            raise FramingError("frame payload is not followed by a newline")
        items.append({
            "kind": "frame",
            "channel": frame.group(1).decode("ascii"),
            "content_type": frame.group(2).decode("ascii"),
            "payload": data[pos:pos + length],
        })
        pos += length + 1
