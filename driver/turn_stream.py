#!/usr/bin/env python3
"""Streaming turn transport for the turn engine (TUI driver plan, phase P2).

PURPOSE: this artifact exists to make the aesop chat TUI, aesop ask and
codex/gemini/agy run every turn through the aesop driver (one turn engine;
the pipe is the product); status: real

One request to one provider, streamed: OpenAI SSE (`data: {json}`,
`data: [DONE]`), Ollama-style NDJSON and a whole-JSON reply all normalise to
the same text deltas, each handed to `on_delta` as soon as it arrives. Failures
are classified with the chat harness's classes (`connect`, `timeout`, `auth`,
`rate-limit`, `server`, `bad-response`, `refused`, `no-key`); a cancel closes
the socket and is reported as `cancelled`, never as a failure.

The HTTP client is `http.client` (stdlib): it reads no proxy environment
variable and never follows a redirect, so a 3xx is a `bad-response` and no
second connection is made. The behaviour mirrors chat/harness.mjs streamOnce,
which is the parity oracle until P7.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import codecs
import http.client
import json
import os
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

DRIVER_DIR = Path(__file__).resolve().parent
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from turn_backend import (  # noqa: E402
    EXIT_OK,
    EXIT_PROVIDER,
    EXIT_REFUSED,
    EXIT_TIMEOUT,
    EXIT_UNREACHABLE,
    refusal_reason,
)
from turn_context import DEFAULT_SIZER, is_ollama  # noqa: E402

# Exit code for a cancelled one-shot turn (128 + SIGINT), outside the aesop ask table.
EXIT_CANCELLED = 130
DEFAULT_TIMEOUT_MS = 60000
_ERROR_BODY_CAP = 4096
_READ_SIZE = 65536
_KEY_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
# U+2014, built with chr() so this file stays ASCII; the harness line uses the same character.
_EM_DASH = chr(0x2014)

DONE = object()


class TurnFailure(Exception):
    """A classified request failure. `cls` is one of the harness error classes."""

    def __init__(self, cls: str, reason: str):
        super().__init__(f"{cls}: {reason}")
        self.cls = cls
        self.reason = reason


class CancelToken:
    """Cancels a turn from another thread by closing the socket it is reading."""

    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._sock = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def bind(self, sock) -> None:
        """Register the live socket; a token cancelled earlier closes it at once."""
        with self._lock:
            self._sock = sock
            if self._event.is_set():
                _close_socket(sock)

    def unbind(self) -> None:
        with self._lock:
            self._sock = None

    def cancel(self) -> None:
        with self._lock:
            self._event.set()
            if self._sock is not None:
                _close_socket(self._sock)


def _close_socket(sock) -> None:
    # shutdown() wakes a recv() blocked in another thread; close() alone does not on every platform.
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


@dataclass
class Outcome:
    """Result of one request to one candidate (mirrors the harness's outcome object)."""

    ok: bool = False
    yielded: int = 0
    failure: Optional[TurnFailure] = None
    usage: Optional[dict] = None
    text: str = ""
    cancelled: bool = False


@dataclass(frozen=True)
class StreamResult:
    """Result of one streamed turn, with the aesop ask exit code."""

    ok: bool
    text: str
    provider: str
    model: str
    elapsed_ms: int
    error: Optional[str]
    exit_code: int
    cancelled: bool = False
    usage: Optional[dict] = None


def _first_present(*values):
    """JavaScript `a ?? b ?? c`: the first value that is not None."""
    for value in values:
        if value is not None:
            return value
    return None


def _dig(obj, *path):
    """Optional chaining over dicts and lists; None when any step is missing."""
    current = obj
    for step in path:
        if isinstance(step, int):
            if not isinstance(current, list) or len(current) <= step:
                return None
            current = current[step]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(step)
    return current


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_stream_line(line: str, on_usage=None):
    """Parse one stream line into a text delta ('' for none) or DONE.

    SSE (`data: {json}`, `data: [DONE]`) and NDJSON (`{json}` per line) are
    both accepted; comments, keepalives and other lines yield ''. A frame that
    is not JSON raises TurnFailure('bad-response').
    """
    text = line.strip()
    if text == "" or text.startswith(":"):
        return ""
    if text.startswith("data:"):
        payload = text[5:].strip()
        if payload == "[DONE]":
            return DONE
    elif text.startswith("{"):
        payload = text
    else:
        return ""
    try:
        obj = json.loads(payload)
    except ValueError:
        raise TurnFailure("bad-response", "malformed stream frame") from None
    if isinstance(obj, dict):
        usage = obj.get("usage")
        if isinstance(usage, (dict, list)) and on_usage:
            on_usage(usage)
        if obj.get("done") is True and _is_number(obj.get("prompt_eval_count")) and on_usage:
            completion = obj.get("eval_count")
            on_usage({
                "prompt_tokens": obj["prompt_eval_count"],
                "completion_tokens": completion if _is_number(completion) else 0,
            })
    content = _first_present(
        _dig(obj, "choices", 0, "delta", "content"),
        _dig(obj, "message", "content"),
        _dig(obj, "response"),
    )
    return content if isinstance(content, str) else ""


def status_failure(status: int, detail: str = "") -> TurnFailure:
    """Map a non-2xx status to a classified failure."""
    tail = f": {detail}" if detail else ""
    if status in (401, 403):
        return TurnFailure("auth", f"HTTP {status}{tail}")
    if status == 429:
        return TurnFailure("rate-limit", f"HTTP 429{tail}")
    if status >= 500:
        return TurnFailure("server", f"HTTP {status}{tail}")
    if 300 <= status < 400:
        return TurnFailure("bad-response", f"redirect {status} not followed")
    return TurnFailure("bad-response", f"HTTP {status}{tail}")


def provider_message(body: str) -> str:
    """The provider's own error text from `{error: {message}}` or `{error: "..."}`, one line."""
    try:
        obj = json.loads(body)
    except ValueError:
        return ""
    err = obj.get("error") if isinstance(obj, dict) else None
    msg = err if isinstance(err, str) else (err.get("message") if isinstance(err, dict) else None)
    if isinstance(msg, str):
        return re.sub(r"\s+", " ", msg).strip()[:300]
    return ""


def origin_of(base: str) -> str:
    """scheme://host[:port] of a base URL, or the text itself when it does not parse."""
    try:
        parts = urlsplit(base)
    except ValueError:
        return base
    if not parts.scheme or not parts.netloc:
        return base
    return f"{parts.scheme}://{parts.netloc}"


def _error_code(exc: BaseException) -> str:
    """A Node-style error code for a connection failure (the harness prints err.code)."""
    if isinstance(exc, ConnectionRefusedError):
        return "ECONNREFUSED"
    if isinstance(exc, ConnectionResetError):
        return "ECONNRESET"
    if isinstance(exc, ConnectionAbortedError):
        return "ECONNABORTED"
    if isinstance(exc, socket.gaierror):
        return "ENOTFOUND"
    if isinstance(exc, BrokenPipeError):
        return "EPIPE"
    return "error"


def _connect_failure(provider_id: str, base: str, exc: BaseException) -> TurnFailure:
    return TurnFailure(
        "connect",
        f"cannot reach {provider_id} at {origin_of(base)} {_EM_DASH} is it running? ({_error_code(exc)})",
    )


def _timeout_failure(timeout_ms) -> TurnFailure:
    return TurnFailure("timeout", f"no response within {_format_number(timeout_ms)} ms")


def _format_number(value) -> str:
    """Print a number the way JavaScript does for the values used here (60000, not 60000.0)."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, (socket.timeout, TimeoutError))


def _default_connect(scheme: str, host: str, port: Optional[int], timeout_s: float):
    if scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=timeout_s)
    return http.client.HTTPConnection(host, port, timeout=timeout_s)


def stream_request(endpoint, headers, body, *, provider_id, base, timeout_ms=DEFAULT_TIMEOUT_MS,
                   cancel=None, on_delta=None, on_first=None, connect=None) -> Outcome:
    """POST `body` to `endpoint` and stream the reply. Never raises for a request failure.

    `timeout_ms` is an idle timeout: it bounds the connect, the wait for the
    response head and each wait for the next body bytes. `cancel` is a
    CancelToken; `connect(scheme, host, port, timeout_s)` builds the
    http.client connection (injectable for tests).
    """
    outcome = Outcome()
    cancel = cancel if cancel is not None else CancelToken()
    if cancel.cancelled:
        outcome.cancelled = True
        return outcome

    def report_usage(usage):
        outcome.usage = usage

    def emit(delta: str) -> None:
        if outcome.yielded == 0 and on_first:
            on_first()
        outcome.yielded += 1
        outcome.text += delta
        if on_delta:
            on_delta(delta)

    parts = urlsplit(endpoint)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    timeout_s = float(timeout_ms) / 1000.0
    conn = (connect or _default_connect)(parts.scheme, parts.hostname, parts.port, timeout_s)
    try:
        try:
            conn.connect()
            cancel.bind(conn.sock)
            conn.putrequest("POST", path, skip_accept_encoding=True)
            for name, value in headers.items():
                conn.putheader(name, value)
            conn.endheaders(body)
            response = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            if not cancel.cancelled:
                outcome.failure = _timeout_failure(timeout_ms) if _is_timeout(exc) else _connect_failure(provider_id, base, exc)
            return outcome

        status = response.status
        if status < 200 or status >= 300:
            err_body = b""
            try:
                while len(err_body) < _ERROR_BODY_CAP:
                    chunk = response.read1(_READ_SIZE)
                    if not chunk:
                        break
                    err_body += chunk
            except (OSError, http.client.HTTPException):
                pass  # a cut error body only loses the detail
            detail = "" if 300 <= status < 400 else provider_message(err_body.decode("utf-8", errors="replace"))
            outcome.failure = status_failure(status, detail)
            return outcome

        ctype = (response.getheader("content-type") or "").lower()
        whole_json = "application/json" in ctype and "ndjson" not in ctype
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            if whole_json:
                whole = ""
                while True:
                    chunk = response.read1(_READ_SIZE)
                    if not chunk:
                        break
                    whole += decoder.decode(chunk)
                whole += decoder.decode(b"", final=True)
                try:
                    obj = json.loads(whole)
                except ValueError:
                    raise TurnFailure("bad-response", "malformed JSON reply") from None
                usage = obj.get("usage") if isinstance(obj, dict) else None
                if isinstance(usage, (dict, list)):
                    report_usage(usage)
                text = _first_present(_dig(obj, "choices", 0, "message", "content"), _dig(obj, "message", "content"))
                if isinstance(text, str) and text != "":
                    emit(text)
            else:
                pending = ""
                done = False
                while not done:
                    chunk = response.read1(_READ_SIZE)
                    if not chunk:
                        break
                    pending += decoder.decode(chunk)
                    nl = pending.find("\n")
                    while nl >= 0:
                        line = pending[:nl]
                        if line.endswith("\r"):
                            line = line[:-1]
                        pending = pending[nl + 1:]
                        delta = parse_stream_line(line, report_usage)
                        if delta is DONE:
                            done = True
                            break
                        if delta != "":
                            emit(delta)
                        nl = pending.find("\n")
                if not done:
                    pending += decoder.decode(b"", final=True)
                    if pending.strip() != "":
                        tail = pending[:-1] if pending.endswith("\r") else pending
                        delta = parse_stream_line(tail, report_usage)
                        if delta is not DONE and delta != "":
                            emit(delta)
            outcome.ok = True
        except TurnFailure as failure:
            outcome.failure = failure
        except (OSError, http.client.HTTPException, ValueError) as exc:
            outcome.failure = _timeout_failure(timeout_ms) if _is_timeout(exc) else TurnFailure("connect", "stream cut")
        return outcome
    finally:
        cancel.unbind()
        try:
            conn.close()
        except OSError:
            pass
        if cancel.cancelled and not outcome.ok:
            outcome.cancelled = True


def _positive_number(value):
    """JavaScript `Number(v) > 0 ? Number(v) : None` for config values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if value > 0 else None
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return number if number > 0 else None
    return None


def build_request(entry: dict, model: str, messages: list, env, *, sizer=None, notify=None) -> dict:
    """Build the endpoint, headers, body and idle timeout for one request.

    A provider with id or type `ollama` goes to the native `/api/chat` route
    with `options.num_ctx` from `sizer` (turn_context.ContextSizer); its
    growth notice and over-window warning go to `notify` before anything is
    sent. Every other provider gets the OpenAI `/chat/completions` request.

    Raises TurnFailure('no-key') when the provider names a key variable that is
    unset and the provider is not local.
    """
    raw = entry["raw"]
    key_name = raw.get("api_key_env")
    keyed = isinstance(key_name, str) and bool(_KEY_ENV_RE.match(key_name))
    if keyed and not env.get(key_name) and raw.get("is_local") is not True:
        raise TurnFailure("no-key", f"env {key_name} is unset")
    base = str(raw.get("base_url") or "").rstrip("/")
    native = is_ollama(entry)
    payload = {"model": model, "stream": True, "messages": messages}
    if native:
        plan = (sizer or DEFAULT_SIZER).plan(entry["id"], base, model, messages, raw)
        payload["options"] = {"num_ctx": plan.num_ctx}
        for note in plan.notes:
            if notify:
                notify(note)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "content-length": str(len(body)),
        "accept": "application/x-ndjson" if native else "text/event-stream",
    }
    if keyed and env.get(key_name):
        headers["authorization"] = f"Bearer {env[key_name]}"
    return {
        "endpoint": (re.sub(r"/v1$", "", base) + "/api/chat") if native else (base + "/chat/completions"),
        "headers": headers,
        "body": body,
        "base": base,
        "timeout_ms": _positive_number(raw.get("timeout_ms")) or DEFAULT_TIMEOUT_MS,
    }


def stream_once(entry: dict, model: str, messages: list, *, env=None, cancel=None,
                on_delta=None, on_first=None, connect=None, sizer=None, notify=None) -> Outcome:
    """One request to one provider entry `{"id", "raw"}`. The boundary gate runs first."""
    env = os.environ if env is None else env
    raw = entry["raw"]
    reason = refusal_reason(entry["id"], model, raw.get("base_url"))
    if reason:
        return Outcome(failure=TurnFailure("refused", reason))
    try:
        request = build_request(entry, model, messages, env, sizer=sizer, notify=notify)
    except TurnFailure as failure:
        return Outcome(failure=failure)
    return stream_request(
        request["endpoint"], request["headers"], request["body"],
        provider_id=entry["id"], base=request["base"], timeout_ms=request["timeout_ms"],
        cancel=cancel, on_delta=on_delta, on_first=on_first, connect=connect,
    )


def exit_code_for(cls: str) -> int:
    """The aesop ask exit code for a failure class."""
    if cls == "connect":
        return EXIT_UNREACHABLE
    if cls == "timeout":
        return EXIT_TIMEOUT
    if cls == "refused":
        return EXIT_REFUSED
    return EXIT_PROVIDER


def run_stream_turn(entry: dict, model: str, messages: list, *, env=None, cancel=None,
                    on_delta=None, notify=None, connect=None, sizer=None) -> StreamResult:
    """Run one streamed turn against one provider and report it like the harness does.

    System lines go to `notify`: `error: <provider>: <class>: <reason>`,
    `stream interrupted from <provider>` (a failure after the first delta),
    `cancelled: <provider> request aborted`, and for Ollama the window growth
    notice and the over-window warning (turn_context).
    """
    started = time.monotonic()
    notify = notify or (lambda text: None)
    provider_id = entry["id"]
    outcome = stream_once(
        entry, model, messages, env=env, cancel=cancel, on_delta=on_delta, connect=connect,
        sizer=sizer, notify=notify,
    )

    def result(ok, exit_code, error=None, cancelled=False):
        return StreamResult(
            ok=ok, text=outcome.text, provider=provider_id, model=model,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=error, exit_code=exit_code, cancelled=cancelled, usage=outcome.usage,
        )

    if outcome.ok:
        return result(True, EXIT_OK)
    if outcome.cancelled:
        line = f"cancelled: {provider_id} request aborted"
        notify(line)
        return result(False, EXIT_CANCELLED, line, cancelled=True)
    failure = outcome.failure or TurnFailure("connect", "error")
    if outcome.yielded > 0:
        line = f"stream interrupted from {provider_id}"
        notify(line)
        return result(False, EXIT_PROVIDER, line)
    line = f"error: {provider_id}: {failure.cls}: {failure.reason}"
    notify(line)
    return result(False, exit_code_for(failure.cls), line)
