#!/usr/bin/env python3
"""One-turn backend for the TUI driver seam (TUI driver plan, phase P1).

`run_turn` sends one chat turn to an OpenAI-compatible provider and maps the
outcome to the `aesop ask` exit-code contract:

    0 ok, 3 unreachable, 4 provider/model error, 5 timeout, 6 refused.

Exit 2 (usage / bad provider config) is decided by the CLI and by the base-URL
guard here. The HTTP path is `openai_compatible_driver.make_openai_compatible_transport`
(reused, not a second client). Proxy env vars are ignored explicitly.

Refusal (P4-lite): any provider or model id containing `claude` or `anthropic`,
or a host under anthropic.com / claude.ai, is refused before any transport is
built or called.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import socket
import sys
import time
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

DRIVER_DIR = Path(__file__).resolve().parent
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from activity_feed import ActivityFeed, prompt_and_context, status_for  # noqa: E402
from backend_config import validate_base_url  # noqa: E402
from openai_compatible_driver import make_openai_compatible_transport  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNREACHABLE = 3
EXIT_PROVIDER = 4
EXIT_TIMEOUT = 5
EXIT_REFUSED = 6

DEFAULT_PROVIDER_NAME = "ollama"
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen3-coder:30b"
# Dummy key name for a loopback provider: is_local waives the key requirement,
# so this variable is normally unset. Never OPENAI_API_KEY (no leak to localhost).
LOCAL_KEY_ENV = "AESOP_TURN_API_KEY"

_REFUSED_TOKENS = ("claude", "anthropic")
_REFUSED_HOSTS = ("anthropic.com", "claude.ai")


@dataclass(frozen=True)
class TurnProvider:
    """One provider endpoint plus the model to call on it."""

    name: str
    base_url: str
    model: str
    is_local: bool = True


@dataclass(frozen=True)
class TurnResult:
    """Outcome of one turn. `exit_code` follows the aesop ask table."""

    ok: bool
    text: str
    provider: str
    model: str
    elapsed_ms: int
    error: Optional[str]
    exit_code: int


def default_provider() -> TurnProvider:
    """The built-in provider: local Ollama with the qwen3-coder model."""
    return TurnProvider(DEFAULT_PROVIDER_NAME, DEFAULT_BASE_URL, DEFAULT_MODEL, is_local=True)


def _host_is_refused(base_url: Optional[str]) -> bool:
    if not base_url:
        return False
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _REFUSED_HOSTS)


def refusal_reason(provider_name, model, base_url=None) -> Optional[str]:
    """Return a refusal message when the boundary forbids this turn, else None."""
    for label, value in (("provider", provider_name), ("model", model)):
        text = str(value or "").lower()
        if any(token in text for token in _REFUSED_TOKENS):
            return f"{label} '{value}' is a Claude/Anthropic id; the TUI driver is non-Anthropic only"
    if _host_is_refused(base_url):
        return f"host of '{base_url}' is an Anthropic/Claude host; refused"
    return None


def classify_exception(exc: BaseException) -> int:
    """Map a transport failure to an exit code by walking the cause chain.

    HTTPError (a URLError subclass) is checked first: a reply arrived, so it is a
    provider error (4). A URLError or OSError without a reply is unreachable (3),
    unless its reason is a timeout (5).
    """
    current: Optional[BaseException] = exc
    depth = 0
    while current is not None and depth < 8:
        if isinstance(current, urllib.error.HTTPError):
            return EXIT_PROVIDER
        if isinstance(current, urllib.error.URLError):
            if isinstance(current.reason, (TimeoutError, socket.timeout)):
                return EXIT_TIMEOUT
            return EXIT_UNREACHABLE
        if isinstance(current, TimeoutError):
            return EXIT_TIMEOUT
        if isinstance(current, OSError):
            return EXIT_UNREACHABLE
        current = current.__cause__
        depth += 1
    return EXIT_PROVIDER


def _extract_text(reply) -> tuple:
    """Return (text, None) for a usable chat reply, else (None, reason)."""
    if not isinstance(reply, dict):
        return None, "malformed reply: not a JSON object"
    choices = reply.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, "malformed reply: no choices"
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        return None, "malformed reply: no text content"
    if not content.strip():
        return None, "empty reply"
    return content, None


def _fail(provider: TurnProvider, exit_code: int, message: str, started: float) -> TurnResult:
    return TurnResult(
        ok=False,
        text="",
        provider=provider.name,
        model=provider.model,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        error=message,
        exit_code=exit_code,
    )


def run_turn(provider: TurnProvider, messages, *, transport=None, timeout_s=300) -> TurnResult:
    """Send one chat turn and return a TurnResult. Never raises for transport failures.

    Every call is mirrored to the shared activity feed (driver/activity_feed.py): start
    before the refusal guard, one chunk with the reply text, then end with the exit code
    (a refused turn has start and end exit 6 only). A feed failure never changes the result.
    """
    messages = list(messages)
    feed = ActivityFeed()
    prompt, context = prompt_and_context(messages)
    feed.start(model=f"{provider.name}/{provider.model}", prompt=prompt, stdin=context)
    result = _run_turn_core(provider, messages, transport=transport, timeout_s=timeout_s)
    if result.ok:
        feed.chunk(result.text)
    feed.end(
        status=status_for(result.exit_code), exit_code=result.exit_code,
        elapsed_ms=result.elapsed_ms, error=result.error,
    )
    return result


def _run_turn_core(provider: TurnProvider, messages, *, transport=None, timeout_s=300) -> TurnResult:
    """The turn itself: refusal, base-URL guard, one transport call, reply mapping."""
    started = time.monotonic()
    reason = refusal_reason(provider.name, provider.model, provider.base_url)
    if reason:
        return _fail(provider, EXIT_REFUSED, reason, started)
    try:
        validate_base_url(provider.base_url)
    except ValueError as exc:
        return _fail(provider, EXIT_USAGE, f"invalid base url: {exc}", started)
    if transport is None:
        transport = make_openai_compatible_transport(
            base_url=provider.base_url,
            api_key_env=LOCAL_KEY_ENV,
            timeout_s=timeout_s,
            is_local=provider.is_local,
            ignore_env_proxy=True,
        )
    payload = {"model": provider.model, "messages": list(messages), "stream": False}
    try:
        reply = transport(payload)
    except Exception as exc:  # classified below; never escapes as an exception
        return _fail(provider, classify_exception(exc), f"{type(exc).__name__}: {exc}", started)
    text, problem = _extract_text(reply)
    if problem is not None:
        return _fail(provider, EXIT_PROVIDER, problem, started)
    return TurnResult(
        ok=True,
        text=text,
        provider=provider.name,
        model=provider.model,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        error=None,
        exit_code=EXIT_OK,
    )
