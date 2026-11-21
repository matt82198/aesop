#!/usr/bin/env python3
"""Turn CLI for the TUI driver seam (TUI driver plan, phases P1 and P2).

    python -m driver.turn [--json | --stream | --frames] [--model provider/model]
                          [--base-url url] [--system text] [--timeout s] "prompt"
    python -m driver.turn --serve [--model provider/model] [--base-url url]

One-shot modes. Piped (non-TTY) stdin is appended to the prompt as context.
`--json` prints one object {ok, provider, model, text, elapsed_ms, error?}.
`--stream` writes text deltas to stdout as they arrive. `--frames` writes an
AESOP-PIPE/1 stream (driver/turn_frames.py): a `hello` control frame, one
`data` frame per delta, `event` control frames for system lines and usage, a
`done` control frame with the result, then `E <exit>`. Ctrl-C cancels the
request, shows `cancelled` and exits 130.

Serve mode. One child serves many turns: it reads one JSON request per stdin
line ({"op": "turn", "id", "line" | "messages", "model"?}, {"op": "cancel",
"id"}, {"op": "shutdown"}) and writes one frames stream for the whole session;
every control frame of a turn carries its `id`. End of stdin cancels the turn
in flight and ends the stream with `E 0`.

Exit codes match `aesop ask`: 0 ok, 2 usage, 3 unreachable, 4 provider/model
error, 5 timeout, 6 refused. Refusal happens before stdin is read and before
any socket opens.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import argparse
import json
import queue
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

DRIVER_DIR = Path(__file__).resolve().parent
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from backend_config import validate_base_url  # noqa: E402
from turn_backend import (  # noqa: E402
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_TIMEOUT,
    EXIT_USAGE,
    default_provider,
    run_turn,
    TurnProvider,
    TurnResult,
)
from turn_engine import EngineError, TurnEngine  # noqa: E402
from turn_frames import FrameWriter  # noqa: E402
from turn_gate import parse_url, refusal_for  # noqa: E402
from turn_stream import CancelToken, StreamResult, run_stream_turn  # noqa: E402

PROG = "python -m driver.turn"
DEFAULT_TIMEOUT_S = 300.0
# Idle timeout of the built-in local provider, matching the chat harness default (300 s).
DEFAULT_LOCAL_IDLE_MS = 300000
CUSTOM_PROVIDER_NAME = "custom"
PROTOCOL_VERSION = 1
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_USAGE = (
    f"usage: {PROG} [--json | --stream | --frames] [--model provider/model] [--base-url url] "
    f"[--system text] [--timeout s] \"prompt\"\n"
    f"       {PROG} --serve [--model provider/model] [--base-url url]\n"
)


class _UsageError(Exception):
    """Raised instead of argparse's exit so main() can return the usage code."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=PROG, description="One turn through the aesop driver (non-Anthropic only).")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    parser.add_argument("--stream", action="store_true", help="write text deltas to stdout as they arrive")
    parser.add_argument("--frames", action="store_true", help="write an AESOP-PIPE/1 frames stream")
    parser.add_argument("--serve", action="store_true", help="serve many turns: JSON requests on stdin, frames on stdout")
    parser.add_argument("--model", default=None, help="provider/model, or a bare model id on the default provider")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL to use instead of the built-in local provider")
    parser.add_argument("--system", default=None, help="system prompt text")
    parser.add_argument("--timeout", default=str(int(DEFAULT_TIMEOUT_S)), help="seconds (positive number)")
    parser.add_argument("prompt", nargs="?", default=None, help="the prompt text")
    return parser


def _split_model_spec(spec):
    """Return (provider_name_or_None, model_id) from `provider/model` or `model`."""
    if spec is None:
        return None, None
    if "/" in spec:
        provider_name, model_id = spec.split("/", 1)
        return provider_name, model_id
    return None, spec


def _read_context(stdin) -> str:
    """Piped stdin is context; a TTY or unreadable stdin contributes nothing."""
    if stdin is None:
        return ""
    try:
        if stdin.isatty():
            return ""
        return stdin.read() or ""
    except (OSError, ValueError):
        return ""


def _build_messages(prompt: str, context: str, system):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    content = f"{context.rstrip()}\n\n{prompt}" if context.strip() else prompt
    messages.append({"role": "user", "content": content})
    return messages


def _emit(result: TurnResult, json_mode: bool, stdout, stderr) -> None:
    if json_mode:
        obj = {
            "ok": result.ok,
            "provider": result.provider,
            "model": result.model,
            "text": result.text,
            "elapsed_ms": result.elapsed_ms,
        }
        if not result.ok:
            obj["error"] = result.error or "unknown error"
        stdout.write(json.dumps(obj) + "\n")
        return
    if result.ok:
        stdout.write(result.text + "\n")
    else:
        stderr.write(f"error: {result.provider}: {result.error}\n")


def _usage_fail(message: str, stderr) -> int:
    stderr.write(_USAGE)
    stderr.write(f"error: {message}\n")
    return EXIT_USAGE


def _binary(stream):
    """The byte stream under a text stream (sys.stdout.buffer), or the stream itself."""
    return getattr(stream, "buffer", stream)


def _is_loopback_url(base_url: str) -> bool:
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOOPBACK_HOSTS


def _provider_entry(provider: TurnProvider) -> dict:
    """The provider as a chat-registry entry `{"id", "raw"}`, the shape the stream path takes."""
    raw = {"type": "openai", "base_url": provider.base_url, "models": "any", "is_local": provider.is_local}
    if provider.is_local:
        raw["timeout_ms"] = DEFAULT_LOCAL_IDLE_MS
    return {"id": provider.name, "raw": raw}


def _format_seconds(value: float) -> str:
    return f"{value:g}"


def _run_cancellable(work, token: CancelToken):
    """Run `work()` on a worker thread; Ctrl-C in the main thread cancels the token.

    A blocking socket read is not interruptible by Ctrl-C on Windows, so the
    main thread only waits here and the worker does the I/O.
    """
    box = {}

    def target():
        try:
            box["result"] = work()
        except BaseException as exc:  # re-raised in the caller's thread
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    while thread.is_alive():
        try:
            thread.join(0.1)
        except KeyboardInterrupt:
            token.cancel()
    if "error" in box:
        raise box["error"]
    return box["result"]


def _stream_one(entry, model, messages, *, timeout_s, token, on_delta, notify) -> StreamResult:
    """One streamed turn bounded by a total wall-clock timeout (exit 5 when it fires)."""
    timed_out = threading.Event()

    def expire():
        timed_out.set()
        token.cancel()

    def live_notify(text):
        # The cancel that enforces the total timeout is reported as a timeout below, not as "cancelled".
        if not timed_out.is_set():
            notify(text)

    timer = threading.Timer(timeout_s, expire)
    timer.daemon = True
    timer.start()
    try:
        result = _run_cancellable(
            lambda: run_stream_turn(entry, model, messages, cancel=token, on_delta=on_delta, notify=live_notify),
            token,
        )
    finally:
        timer.cancel()
    if timed_out.is_set() and not result.ok:
        line = f"error: {entry['id']}: timeout: timed out after {_format_seconds(timeout_s)} s"
        notify(line)
        return StreamResult(
            ok=False, text=result.text, provider=result.provider, model=result.model,
            elapsed_ms=result.elapsed_ms, error=line, exit_code=EXIT_TIMEOUT, usage=result.usage,
        )
    return result


def _done_message(result: StreamResult, turn_id=None) -> dict:
    obj = {
        "type": "done",
        "ok": result.ok,
        "exit": result.exit_code,
        "provider": result.provider,
        "model": result.model,
        "text": result.text,
        "elapsed_ms": result.elapsed_ms,
    }
    if turn_id is not None:
        obj["id"] = turn_id
    if not result.ok:
        obj["error"] = result.error or "unknown error"
    if result.cancelled:
        obj["cancelled"] = True
    return obj


def _event(name: str, turn_id=None, **fields) -> dict:
    obj = {"type": "event", "event": name}
    if turn_id is not None:
        obj["id"] = turn_id
    obj.update(fields)
    return obj


def _hello() -> dict:
    return {"type": "hello", "protocol": PROTOCOL_VERSION, "engine": "driver.turn"}


def _run_collected(entry, model, messages, timeout_s, token, json_mode, stdout, stderr) -> int:
    """Default and --json modes: the same streamed route, printed once at the end.

    Warnings (the over-window notice) are diagnostics on stderr; the exit code
    is unchanged by them. A failure line is the `error` of the JSON object and
    is also written to stderr.
    """
    def note(text):
        if text.startswith(("warning: ", "growing ")):
            stderr.write(text + "\n")

    result = _stream_one(entry, model, messages, timeout_s=timeout_s, token=token, on_delta=None, notify=note)
    if json_mode:
        obj = {
            "ok": result.ok,
            "provider": result.provider,
            "model": result.model,
            "text": result.text,
            "elapsed_ms": result.elapsed_ms,
        }
        if not result.ok:
            obj["error"] = result.error or "unknown error"
        stdout.write(json.dumps(obj) + "\n")
    elif result.ok:
        stdout.write(result.text + "\n")
    if not result.ok:
        stderr.write((result.error or "unknown error") + "\n")
    return result.exit_code


def _run_frames(entry, model, messages, timeout_s, token, stdout) -> int:
    writer = FrameWriter(_binary(stdout))
    writer.control(_hello())
    result = _stream_one(
        entry, model, messages, timeout_s=timeout_s, token=token,
        on_delta=writer.data, notify=lambda text: writer.control(_event("notify", text=text)),
    )
    if result.usage is not None:
        writer.control(_event("usage", usage=result.usage))
    writer.control(_done_message(result))
    writer.end(result.exit_code)
    return result.exit_code


def _run_plain_stream(entry, model, messages, timeout_s, token, stdout, stderr) -> int:
    def on_delta(text):
        stdout.write(text)
        stdout.flush()

    result = _stream_one(
        entry, model, messages, timeout_s=timeout_s, token=token,
        on_delta=on_delta, notify=lambda text: stderr.write(text + "\n"),
    )
    if result.ok and result.text != "" and not result.text.endswith("\n"):
        stdout.write("\n")
    return result.exit_code


def _read_requests(stream):
    """Yield one decoded JSON value (or a ValueError) per non-empty stdin line."""
    source = _binary(stream)
    while True:
        try:
            raw = source.readline()
        except (OSError, ValueError):
            return
        if not raw:
            return
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        if text.strip() == "":
            continue
        try:
            yield json.loads(text)
        except ValueError as exc:
            yield exc


def _serve(entry, default_model, stdin, stdout) -> int:
    """Serve turns until stdin ends. Turns run one at a time on a worker thread."""
    writer = FrameWriter(_binary(stdout))
    writer.control(_hello())
    pending = queue.Queue()
    tokens = {}
    tokens_lock = threading.Lock()

    def bad_request(reason, turn_id=None):
        writer.control(_event("notify", turn_id, text=f"error: driver: bad-request: {reason}"))

    engine = TurnEngine()
    default_registry = {"providers": {entry["id"]: entry["raw"]}, "routes": {}}

    def run_messages(request):
        """A turn given as explicit messages: one stateless request to the CLI's provider."""
        turn_id = request["id"]
        result = run_stream_turn(
            entry, request["model"], request["messages"], cancel=request["token"], on_delta=writer.data,
            notify=lambda text: writer.control(_event("notify", turn_id, text=text)),
        )
        if result.usage is not None:
            writer.control(_event("usage", turn_id, usage=result.usage))
        writer.control(_done_message(result, turn_id))

    def run_line(request):
        """A turn given as a line: the routed engine, with this session's context and breaker state."""
        turn_id = request["id"]
        raw = request["raw"]
        registry = raw.get("registry") if isinstance(raw.get("registry"), dict) else default_registry
        selection = raw.get("selection") if isinstance(raw.get("selection"), dict) else {"provider": entry["id"], "model": request["model"]}
        env = None
        if isinstance(raw.get("keys"), dict):
            # Keys travel on stdin with the turn, never on the command line or in the child's environment.
            env = {str(name): str(value) for name, value in raw["keys"].items()}
        now = raw.get("now") if isinstance(raw.get("now"), (int, float)) and not isinstance(raw.get("now"), bool) else None
        try:
            result = engine.run_turn(
                registry, selection, request["line"], task=raw.get("task") if isinstance(raw.get("task"), str) else "chat",
                cancel=request["token"], on_delta=writer.data, now=now, env=env,
                notify=lambda text: writer.control(_event("notify", turn_id, text=text)),
                flash=lambda text: writer.control(_event("flash", turn_id, text=text)),
                usage=lambda report: writer.control(_event("usage", turn_id, usage=report)),
            )
        except EngineError as exc:
            # The turn could not start; the adapter raises this text the way the harness throws it.
            writer.control({"type": "done", "id": turn_id, "ok": False, "exit": EXIT_USAGE, "thrown": str(exc), "error": str(exc)})
            return
        done = {
            "type": "done", "id": turn_id, "ok": result.ok, "exit": result.exit_code,
            "provider": result.provider, "model": result.model, "text": result.text, "elapsed_ms": result.elapsed_ms,
        }
        if not result.ok:
            done["error"] = result.error or "unknown error"
        if result.cancelled:
            done["cancelled"] = True
        writer.control(done)

    def run_one(request):
        try:
            if "messages" in request:
                run_messages(request)
            else:
                run_line(request)
        finally:
            with tokens_lock:
                tokens.pop(request["id"], None)

    def worker():
        while True:
            request = pending.get()
            if request is None:
                return
            run_one(request)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    graceful = False
    for request in _read_requests(stdin):
        if not isinstance(request, dict):
            bad_request("a request is one JSON object per line")
            continue
        op = request.get("op")
        turn_id = request.get("id")
        if op == "shutdown":
            graceful = True
            break
        if op == "cancel":
            with tokens_lock:
                token = tokens.get(turn_id)
            if token is not None:
                token.cancel()
            continue
        if op != "turn":
            bad_request("unknown op", turn_id)
            continue
        messages = request.get("messages")
        line = request.get("line")
        if messages is not None and (not isinstance(messages, list) or not messages):
            bad_request("messages must be a non-empty list", turn_id)
            continue
        if messages is None and not isinstance(line, str):
            bad_request("a turn needs a line or a messages list", turn_id)
            continue
        _, model = _split_model_spec(request.get("model") if isinstance(request.get("model"), str) else None)
        token = CancelToken()
        with tokens_lock:
            tokens[turn_id] = token
        job = {"id": turn_id, "token": token, "model": model or default_model, "raw": request}
        if messages is not None:
            job["messages"] = messages
        else:
            job["line"] = line
        pending.put(job)
    # `shutdown` lets queued turns finish. End of input (the parent went away) cancels whatever is
    # running or queued. Either way the stream then closes with `E 0`.
    if not graceful:
        with tokens_lock:
            live = list(tokens.values())
        for token in live:
            token.cancel()
    pending.put(None)
    thread.join()
    writer.end(EXIT_OK)
    return EXIT_OK


def main(argv=None, *, stdin=None, stdout=None, stderr=None, transport=None, cancel=None) -> int:
    """Run one turn (or serve turns) and return the exit code.

    Streams, the P1 transport and the cancel token are injectable.
    """
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    argv = sys.argv[1:] if argv is None else list(argv)

    try:
        args = _build_parser().parse_args(argv)
    except _UsageError as exc:
        return _usage_fail(str(exc), stderr)
    except SystemExit as exc:
        return int(exc.code or 0)

    modes = [name for name in ("json", "stream", "frames", "serve") if getattr(args, name)]
    if len(modes) > 1:
        return _usage_fail(f"--{modes[0]} and --{modes[1]} cannot be combined", stderr)

    prompt = (args.prompt or "").strip()
    if args.serve:
        if prompt:
            return _usage_fail("--serve takes no prompt; send turns on stdin", stderr)
    elif not prompt:
        return _usage_fail("a prompt is required", stderr)
    try:
        timeout_s = float(args.timeout)
    except ValueError:
        return _usage_fail(f"--timeout must be a number, got '{args.timeout}'", stderr)
    if not timeout_s > 0:
        return _usage_fail("--timeout must be positive", stderr)

    provider_name, model_id = _split_model_spec(args.model)
    if model_id is not None and not model_id.strip():
        return _usage_fail("--model needs a model id", stderr)

    base = default_provider()
    if args.base_url is not None:
        if parse_url(args.base_url) is None:
            return _usage_fail("--base-url is not a URL", stderr)
        name = provider_name or CUSTOM_PROVIDER_NAME
        base = TurnProvider(name, args.base_url.rstrip("/"), base.model, is_local=_is_loopback_url(args.base_url))
    streaming = args.stream or args.frames or args.serve

    # The boundary gate (turn_gate) runs on the ids and the URL text before stdin is read and before
    # any socket or DNS lookup. Every request runs the same gate again at send time (turn_stream).
    shown_name = provider_name or base.name
    shown_model = model_id or base.model
    reason = refusal_for({"id": shown_name, "raw": {"type": "openai", "base_url": base.base_url}}, shown_model)
    if reason:
        line = f"error: {shown_name}: refused: {reason}"
        if args.frames or args.serve:
            writer = FrameWriter(_binary(stdout))
            writer.control(_hello())
            writer.control(_event("notify", text=line))
            writer.control(_done_message(StreamResult(False, "", shown_name, shown_model, 0, line, EXIT_REFUSED)))
            writer.end(EXIT_REFUSED)
        elif args.json:
            _emit(TurnResult(False, "", shown_name, shown_model, 0, line, EXIT_REFUSED), True, stdout, stderr)
        else:
            stderr.write(line + "\n")
        return EXIT_REFUSED

    if args.base_url is not None:
        try:
            validate_base_url(base.base_url)
        except ValueError as exc:
            return _usage_fail(f"--base-url is not usable: {exc}", stderr)
    elif provider_name is not None and provider_name != base.name:
        return _usage_fail(f"unknown provider '{provider_name}'; configured: {base.name}", stderr)
    provider = TurnProvider(base.name, base.base_url, model_id or base.model, is_local=base.is_local)

    if args.serve:
        return _serve(_provider_entry(provider), provider.model, stdin, stdout)

    context = _read_context(stdin)
    messages = _build_messages(prompt, context, args.system)
    if streaming or transport is None:
        # Every real turn takes the streamed route, so Ollama gets its native route and a sized window
        # in every mode (finding f-db2dd2fa). Only an injected transport keeps the P1 one-shot path.
        token = cancel if cancel is not None else CancelToken()
        entry = _provider_entry(provider)
        if args.frames:
            return _run_frames(entry, provider.model, messages, timeout_s, token, stdout)
        if args.stream:
            return _run_plain_stream(entry, provider.model, messages, timeout_s, token, stdout, stderr)
        return _run_collected(entry, provider.model, messages, timeout_s, token, args.json, stdout, stderr)

    result = run_turn(provider, messages, transport=transport, timeout_s=timeout_s)
    _emit(result, args.json, stdout, stderr)
    return result.exit_code if result.exit_code is not None else EXIT_OK


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main())
