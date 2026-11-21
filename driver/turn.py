#!/usr/bin/env python3
"""One-shot turn CLI for the TUI driver seam (TUI driver plan, phase P1).

    python -m driver.turn [--json] [--model provider/model] [--system text]
                          [--timeout s] "prompt"

Piped (non-TTY) stdin is appended to the prompt as context. `--json` prints one
object {ok, provider, model, text, elapsed_ms, error?}. Exit codes match
`aesop ask`: 0 ok, 2 usage, 3 unreachable, 4 provider/model error, 5 timeout,
6 refused. Refusal happens before stdin is read and before any socket opens.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import argparse
import json
import sys
from pathlib import Path

DRIVER_DIR = Path(__file__).resolve().parent
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from turn_backend import (  # noqa: E402
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_USAGE,
    default_provider,
    refusal_reason,
    run_turn,
    TurnProvider,
    TurnResult,
)

PROG = "python -m driver.turn"
DEFAULT_TIMEOUT_S = 300.0


class _UsageError(Exception):
    """Raised instead of argparse's exit so main() can return the usage code."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=PROG, description="One-shot turn through the aesop driver (non-Anthropic only).")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    parser.add_argument("--model", default=None, help="provider/model, or a bare model id on the default provider")
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
    stderr.write(f"usage: {PROG} [--json] [--model provider/model] [--system text] [--timeout s] \"prompt\"\n")
    stderr.write(f"error: {message}\n")
    return EXIT_USAGE


def main(argv=None, *, stdin=None, stdout=None, stderr=None, transport=None) -> int:
    """Run one turn and return the exit code. Streams and transport are injectable."""
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

    prompt = (args.prompt or "").strip()
    if not prompt:
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

    # Refusal is decided on the raw ids, before stdin is read and before any socket.
    reason = refusal_reason(provider_name or default_provider().name, model_id or default_provider().model)
    if reason:
        result = TurnResult(False, "", provider_name or default_provider().name, model_id or default_provider().model, 0, reason, EXIT_REFUSED)
        _emit(result, args.json, stdout, stderr)
        return EXIT_REFUSED

    base = default_provider()
    if provider_name is not None and provider_name != base.name:
        return _usage_fail(f"unknown provider '{provider_name}'; configured: {base.name}", stderr)
    provider = TurnProvider(base.name, base.base_url, model_id or base.model, is_local=base.is_local)

    context = _read_context(stdin)
    messages = _build_messages(prompt, context, args.system)
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
