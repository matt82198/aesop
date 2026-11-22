#!/usr/bin/env python3
"""Context-window sizing for turns sent to Ollama (TUI driver plan, phase P3).

PURPOSE: this artifact exists to make the aesop chat TUI, aesop ask and
codex/gemini/agy run every turn through the aesop driver (one turn engine;
the pipe is the product); status: real

Ollama's OpenAI-compatible `/v1` route ignores `num_ctx` and runs at 4096
tokens, silently cutting the START of long input (finding f-db2dd2fa: wrong
data shown to the user). A provider with id or type `ollama` is therefore sent
to the native `/api/chat` route with an explicit `options.num_ctx`.

- The window is a bucket (default 8192 / 16384 / 32768; override with
  `num_ctx_buckets`), the smallest one that holds the prompt estimate plus a
  reply reserve, after filtering to `num_ctx_max` and `model_max_ctx`.
- It is sticky: Ollama reloads the model whenever `num_ctx` changes, so per
  process and per (provider, base_url, model) a request never sends a smaller
  window than the last one. A growth is announced once.
- When the prompt alone exceeds the window a warning line is emitted before
  the reply.

This is a port of sizeOllamaContext and the sticky logic in chat/harness.mjs.
Both sides run tests/fixtures/num-ctx-cases.json, so they cannot drift.
Lengths are UTF-16 code units, as JavaScript counts them.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import math
import threading
from dataclasses import dataclass

CHARS_PER_TOKEN = 3.5
OLLAMA_REPLY_RESERVE = 1024
OLLAMA_NUM_CTX_MAX = 32768
OLLAMA_NUM_CTX_BUCKETS = (8192, 16384, 32768)


@dataclass(frozen=True)
class SizedContext:
    """The window chosen for one request."""

    num_ctx: int
    prompt_tokens: int
    truncates: bool
    cap: int


@dataclass(frozen=True)
class ContextPlan:
    """What to send (`num_ctx`) and what to tell the user (`notes`, in order)."""

    num_ctx: int
    notes: tuple


def is_ollama(entry: dict) -> bool:
    """True for the Ollama provider (id or type `ollama`), which uses the native /api/chat route."""
    return entry.get("id") == "ollama" or (entry.get("raw") or {}).get("type") == "ollama"


def js_length(value) -> int:
    """`String(value).length` in JavaScript: UTF-16 code units."""
    if isinstance(value, str):
        text = value
    elif value is None:
        text = "null"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _js_number(value) -> float:
    """JavaScript `Number(value)` for config values; NaN when it is not a number."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return 0.0
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return 0.0
        try:
            return float(text)
        except ValueError:
            return math.nan
    return math.nan


def _positive(value):
    """`Number(value) > 0 ? Math.floor(Number(value)) : None`."""
    number = _js_number(value)
    if math.isfinite(number) and number > 0:
        return math.floor(number)
    return None


def size_ollama_context(messages, raw=None) -> SizedContext:
    """Choose the context window for one request to Ollama."""
    raw = raw or {}
    chars = sum(js_length(message.get("content") if isinstance(message, dict) else None) for message in messages)
    prompt_tokens = math.ceil(chars / CHARS_PER_TOKEN)
    cap = _positive(raw.get("num_ctx_max")) or OLLAMA_NUM_CTX_MAX
    model_max = _positive(raw.get("model_max_ctx"))
    if model_max is not None:
        cap = min(cap, model_max)
    configured = raw.get("num_ctx_buckets")
    custom = [b for b in (_positive(v) for v in configured) if b is not None] if isinstance(configured, list) else []
    source = custom if custom else OLLAMA_NUM_CTX_BUCKETS
    buckets = sorted({b for b in source if b <= cap})
    if not buckets:
        buckets = [cap]
    need = prompt_tokens + OLLAMA_REPLY_RESERVE
    num_ctx = next((b for b in buckets if b >= need), buckets[-1])
    return SizedContext(num_ctx=num_ctx, prompt_tokens=prompt_tokens, truncates=prompt_tokens > num_ctx, cap=cap)


class ContextSizer:
    """Sticky window memory: per (provider, base_url, model) the window only grows.

    One instance lives for the process (DEFAULT_SIZER), so a one-shot turn gets
    bucketing only and a serve session gets the sticky behaviour.
    """

    def __init__(self):
        self._last = {}
        self._lock = threading.Lock()

    def plan(self, provider_id: str, base: str, model: str, messages, raw=None) -> ContextPlan:
        """Return the window to send and the lines to show, and remember the window."""
        sized = size_ollama_context(messages, raw)
        num_ctx = sized.num_ctx
        notes = []
        key = f"{provider_id}|{base}|{model}"
        with self._lock:
            last = self._last.get(key)
            if last is not None and num_ctx < last and last <= sized.cap:
                num_ctx = last
            elif last is not None and num_ctx > last:
                notes.append(f"growing model window to {num_ctx} (Ollama reloads the model; this can take ~20 s)")
            self._last[key] = num_ctx
        if sized.truncates:
            notes.append(f"warning: input ~{sized.prompt_tokens} tokens exceeds the model window {num_ctx}; the start will be cut")
        return ContextPlan(num_ctx=num_ctx, notes=tuple(notes))


DEFAULT_SIZER = ContextSizer()
