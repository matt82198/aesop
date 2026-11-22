#!/usr/bin/env python3
"""The routed turn engine: candidates, fallback, breaker, context (TUI driver plan, phase P5).

PURPOSE: this artifact exists to make the aesop chat TUI, aesop ask and
codex/gemini/agy run every turn through the aesop driver (one turn engine;
the pipe is the product); status: real

`TurnEngine.run_turn` is a port of the turn loop in chat/harness.mjs
(harnessSource), which stays the parity oracle until P7. One engine lives for
a session and holds the state a session needs:

- the conversation (completed chat turns; the newest 20 messages or 24000
  characters are sent as context, never splitting a message);
- a circuit breaker per provider (3 consecutive retryable failures open it for
  30 s; the skip is announced once);
- a first-delta latency average per provider, for the `fastest` strategy;
- the sticky Ollama window (turn_context.ContextSizer).

A turn takes the registry as data: `{"providers": {id: raw}, "routes": {task:
{"target", "candidates", "fallback", "fallbackOn", "strategy"}}}` with targets
already resolved to `{"provider", "model"}`. The chat adapter sends it with
every turn, so the engine always acts on the registry as it is now.

System lines keep the harness wording: `error: <provider>: <class>: <reason>`,
`falling back: A -> B (<class>)`, `stream interrupted from <provider>`,
`cancelled: <provider> request aborted`, and the breaker skip notice.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DRIVER_DIR = Path(__file__).resolve().parent
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from turn_backend import EXIT_OK, EXIT_PROVIDER  # noqa: E402
from turn_context import ContextSizer, js_length  # noqa: E402
from turn_stream import EXIT_CANCELLED, CancelToken, exit_code_for, stream_once  # noqa: E402

CONTEXT_MAX_MESSAGES = 20
CONTEXT_MAX_CHARS = 24000
RETRYABLE = frozenset(("connect", "timeout", "rate-limit", "server"))
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_MS = 30000
NO_PROVIDER = "no provider configured (see /help)"


class EngineError(Exception):
    """A turn that cannot start at all (no provider selected, a route with no target)."""


@dataclass(frozen=True)
class EngineResult:
    """Outcome of one routed turn."""

    ok: bool
    text: str
    provider: Optional[str]
    model: Optional[str]
    exit_code: int
    error: Optional[str] = None
    cancelled: bool = False
    elapsed_ms: int = 0


def bound_context(convo, max_messages=CONTEXT_MAX_MESSAGES, max_chars=CONTEXT_MAX_CHARS) -> list:
    """The newest messages that fit both limits; never splits a message, always starts on a user message."""
    out = []
    chars = 0
    index = len(convo) - 1
    while index >= 0 and len(out) < max_messages:
        chars += js_length(convo[index].get("content"))
        if chars > max_chars and out:
            break
        out.insert(0, convo[index])
        index -= 1
    while out and out[0].get("role") != "user":
        out.pop(0)
    return out


def _number_or_zero(value) -> float:
    """JavaScript `Number(value) || 0`."""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 0 if isinstance(value, float) and math.isnan(value) else value
    if isinstance(value, str):
        try:
            return float(value.strip()) if value.strip() else 0
        except ValueError:
            return 0
    return 0


def _as_int_if_whole(value):
    return int(value) if isinstance(value, float) and value.is_integer() else value


class TurnEngine:
    """One session's turn engine. Not thread-safe: turns run one at a time."""

    def __init__(self, env=None, sizer=None, connect=None):
        self.env = env
        self.sizer = sizer or ContextSizer()
        self.connect = connect
        self.convo = []
        self._breakers = {}
        self._latency = {}
        self._now_override = None

    # -- clock: a turn may pin the clock (the chat tests inject one); otherwise wall time in ms
    def _now(self) -> float:
        if self._now_override is not None:
            return self._now_override
        return time.time() * 1000.0

    def _breaker(self, provider_id):
        return self._breakers.setdefault(provider_id, {"fails": 0, "open_until": 0, "announced": False})

    def _record_failure(self, provider_id) -> None:
        breaker = self._breaker(provider_id)
        breaker["fails"] += 1
        if breaker["fails"] >= BREAKER_THRESHOLD:
            breaker["open_until"] = self._now() + BREAKER_COOLDOWN_MS
            breaker["announced"] = False

    def _record_success(self, provider_id) -> None:
        self._breakers[provider_id] = {"fails": 0, "open_until": 0, "announced": False}

    @staticmethod
    def _cost_of(providers, provider_id) -> float:
        raw = providers.get(provider_id)
        if not isinstance(raw, dict):
            return math.inf
        if raw.get("is_local") is True:
            return 0
        cost = raw.get("cost_per_mtok")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            return cost
        if isinstance(cost, dict):
            return _number_or_zero(cost.get("input")) + _number_or_zero(cost.get("output"))
        return math.inf

    def _order(self, providers, candidates, strategy) -> list:
        indexed = list(enumerate(candidates))
        if strategy == "cheapest":
            indexed.sort(key=lambda pair: (self._cost_of(providers, pair[1]["provider"]), pair[0]))
        elif strategy == "fastest":
            indexed.sort(key=lambda pair: (self._latency.get(pair[1]["provider"], 0), pair[0]))
        seen = set()
        ordered = []
        for _, candidate in indexed:
            key = f"{candidate['provider']}/{candidate['model']}"
            if key not in seen:
                seen.add(key)
                ordered.append(candidate)
        return ordered

    @staticmethod
    def usage_of(providers, provider_id, usage) -> dict:
        """Price one usage report: local is free, cost_per_mtok prices input and output."""
        usage = usage if isinstance(usage, dict) else {}
        prompt = _number_or_zero(usage.get("prompt_tokens"))
        completion = _number_or_zero(usage.get("completion_tokens"))
        total = _number_or_zero(usage.get("total_tokens")) or prompt + completion
        raw = providers.get(provider_id)
        cost = None
        if isinstance(raw, dict) and raw.get("is_local") is True:
            cost = 0
        elif isinstance(raw, dict) and isinstance(raw.get("cost_per_mtok"), dict):
            rates = raw["cost_per_mtok"]
            cost = (_number_or_zero(rates.get("input")) * prompt + _number_or_zero(rates.get("output")) * completion) / 1e6
        return {
            "provider": provider_id,
            "total": _as_int_if_whole(total),
            "prompt": _as_int_if_whole(prompt),
            "completion": _as_int_if_whole(completion),
            "cost": _as_int_if_whole(cost) if cost is not None else None,
        }

    def run_turn(self, registry, selection, line, *, task="chat", cancel=None, on_delta=None,
                 notify=None, flash=None, usage=None, now=None, env=None, messages=None) -> EngineResult:
        """Run one turn through the route plan. Raises EngineError when the turn cannot start.

        `messages`, when given, is the whole request (a one-shot turn such as `aesop ask`): it is sent
        as it is, with no session context before it, and the turn is not remembered.
        """
        notify = notify or (lambda text: None)
        flash = flash or notify
        report_usage = usage or (lambda report: None)
        cancel = cancel if cancel is not None else CancelToken()
        env = env if env is not None else self.env
        started = time.monotonic()
        self._now_override = now
        try:
            return self._run(registry, selection, str(line), task, cancel, on_delta, notify, flash, report_usage, env, started, messages)
        finally:
            self._now_override = None

    def _run(self, registry, selection, line, task, cancel, on_delta, notify, flash, report_usage, env, started, preset=None):
        providers = registry.get("providers") if isinstance(registry.get("providers"), dict) else {}
        routes = registry.get("routes") if isinstance(registry.get("routes"), dict) else {}
        selection = selection or {}
        if not selection.get("provider"):
            raise EngineError(NO_PROVIDER)
        route = routes.get(task) if isinstance(routes.get(task), dict) else None
        if task == "chat":
            primary = {"provider": selection["provider"], "model": selection.get("model")}
        else:
            primary = route.get("target") if route else None
        if route and isinstance(route.get("candidates"), list):
            candidates = route["candidates"]
        else:
            candidates = [c for c in [primary] + list((route or {}).get("fallback") or []) if c]
        if not candidates:
            raise EngineError(f"route {task} has no target")
        fallback_on = (route or {}).get("fallbackOn") or "retryable"
        ordered = self._order(providers, candidates, (route or {}).get("strategy"))

        def result(ok, exit_code, candidate, text="", error=None, cancelled=False):
            return EngineResult(
                ok=ok, text=text, provider=candidate["provider"], model=candidate.get("model"),
                exit_code=exit_code, error=error, cancelled=cancelled,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )

        attempted = 0
        first_error = None
        first_code = EXIT_PROVIDER
        last = ordered[0]
        for index, candidate in enumerate(ordered):
            provider_id = candidate["provider"]
            last = candidate
            breaker = self._breaker(provider_id)
            if breaker["open_until"] > self._now():
                if not breaker["announced"]:
                    breaker["announced"] = True
                    flash(f"skipping {provider_id}: circuit open after {BREAKER_THRESHOLD} failures, retry after cooldown")
                continue
            attempted += 1
            raw = providers.get(provider_id)
            if not isinstance(raw, dict):
                line_text = f"error: {provider_id}: refused: unknown provider"
                notify(line_text)
                return result(False, exit_code_for("refused"), candidate, error=first_error or line_text)
            if preset is not None:
                messages = list(preset)
            else:
                prior = bound_context(self.convo) if task == "chat" else []
                messages = prior + [{"role": "user", "content": line}]
            began = self._now()

            def note_first(provider_id=provider_id, began=began):
                sample = self._now() - began
                previous = self._latency.get(provider_id)
                self._latency[provider_id] = sample if previous is None else 0.5 * previous + 0.5 * sample

            outcome = stream_once(
                {"id": provider_id, "raw": raw}, candidate.get("model"), messages, env=env, cancel=cancel,
                on_delta=on_delta, on_first=note_first, connect=self.connect, sizer=self.sizer, notify=notify,
            )
            if outcome.ok:
                self._record_success(provider_id)
                if preset is None and task == "chat" and outcome.text != "":
                    self.convo.append({"role": "user", "content": line})
                    self.convo.append({"role": "assistant", "content": outcome.text})
                if outcome.usage:
                    report_usage(self.usage_of(providers, provider_id, outcome.usage))
                return result(True, EXIT_OK, candidate, text=outcome.text, error=first_error)
            # A cancel is shown as cancelled: never a failure, never a fallback.
            if cancel.cancelled:
                line_text = f"cancelled: {provider_id} request aborted"
                notify(line_text)
                return result(False, EXIT_CANCELLED, candidate, text=outcome.text, error=line_text, cancelled=True)
            failure = outcome.failure
            if outcome.yielded > 0:
                # A failure after the first delta is never re-sent to another provider.
                self._record_failure(provider_id)
                line_text = f"stream interrupted from {provider_id}"
                notify(line_text)
                return result(False, EXIT_PROVIDER, candidate, text=outcome.text, error=first_error or line_text)
            retryable = failure.cls in RETRYABLE
            if retryable:
                self._record_failure(provider_id)
            line_text = f"error: {provider_id}: {failure.cls}: {failure.reason}"
            notify(line_text)
            if first_error is None:
                first_error = line_text
                first_code = exit_code_for(failure.cls)
            following = ordered[index + 1] if index + 1 < len(ordered) else None
            if not following or not (retryable or fallback_on == "any"):
                return result(False, first_code, candidate, error=first_error)
            notify(f"falling back: {provider_id} -> {following['provider']} ({failure.cls})")
        if attempted == 0:
            line_text = f"error: {ordered[0]['provider']}: server: no candidate available, circuit open"
            notify(line_text)
            return result(False, exit_code_for("server"), ordered[0], error=line_text)
        return result(False, first_code, last, error=first_error)
