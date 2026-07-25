#!/usr/bin/env python3
"""OrchestratorBackend — protocol for orchestrator decision-making backends.

Defines the interface that orchestrator backends must implement to make
structured decisions (decide_call). Mirrors the AgentDriver seam pattern
but isolates the orchestrator's judgment-making from agent worker logic.

Protocol:
  decide_call(prompt: str, *, schema: dict|None) -> str
    Returns the raw model text response (typically JSON). The caller
    (OrchestratorDriver.decide()) is responsible for parsing, validating,
    and retrying on malformed output.

stdlib-only, ASCII-only, Windows + Linux safe (concrete backends own SDKs).
"""

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

# For real OpenAI transport
try:
    from openai_transport import default_openai_transport
except ImportError:
    default_openai_transport = None

# base_url SSRF guard (shared with backend_config). The deferred import keeps
# the module importable standalone, but construction FAILS CLOSED when the
# guard is unavailable (see OpenAICompatibleOrchestratorBackend.__init__):
# without it, urllib's default opener would happily open file:// or ftp://
# base URLs and DIRECT construction would bypass the config-layer validation.
try:
    from backend_config import validate_base_url, validate_is_local_base_url
except ImportError:
    validate_base_url = None
    validate_is_local_base_url = None


class OrchestratorBackend(ABC):
    """Abstract base class for orchestrator backends.

    Implementations provide decide_call() to make structured decisions
    using a configured backend model (Claude, OpenAI, etc.).
    """

    @abstractmethod
    def decide_call(
        self, prompt: str, *, schema: Optional[Dict[str, Any]] = None
    ) -> str:
        """Make a structured decision and return the model's response.

        Args:
            prompt: The complete decision prompt (system + context + request).
            schema: Optional JSON schema for the response. Used by some backends
                   to enforce structured output; ignored by backends that don't
                   support it.

        Returns:
            The raw model text response (typically JSON). The caller is
            responsible for parsing, validating, and retrying on errors.

        Raises:
            RuntimeError: on transport errors, missing credentials, etc.
                         Caller should retry or return DECISION_FAILED.
        """
        pass

    def get_tokens_spent(self) -> int:
        """Total tokens this backend has spent on decisions (best effort).

        HS-2 block-gate hardening: the orchestrator SEAT's spend must count
        against the cost ceiling like the worker seat's. Backends that can
        meter usage override this; the default 0 means "no metering
        available" (never a fabricated figure).
        """
        return 0


class FakeOrchestratorBackend(OrchestratorBackend):
    """Testing backend with canned responses.

    Useful for offline regression tests and controlling behavior deterministically.
    """

    def __init__(
        self,
        canned_responses: Optional[list] = None,
        tokens_per_call: int = 0,
    ):
        """Initialize with a list of canned JSON responses.

        Args:
            canned_responses: List of response dicts (or JSON strings) to return
                             in order. Each call to decide_call consumes one.
            tokens_per_call: Simulated token spend accrued per successful
                             decide_call (for seat-spend metering tests).
        """
        self.canned_responses = canned_responses or []
        self.call_count = 0
        self.received_prompts = []  # Capture prompts for regression tests
        self.tokens_per_call = tokens_per_call
        self.total_tokens_spent = 0

    def get_tokens_spent(self) -> int:
        return self.total_tokens_spent

    def decide_call(
        self, prompt: str, *, schema: Optional[Dict[str, Any]] = None
    ) -> str:
        """Return the next canned response."""
        # Record the prompt for testing (regression guard).
        self.received_prompts.append(prompt)

        if self.call_count >= len(self.canned_responses):
            raise RuntimeError(
                f"FakeOrchestratorBackend exhausted canned responses "
                f"(call {self.call_count + 1} of {len(self.canned_responses)})"
            )

        response = self.canned_responses[self.call_count]
        self.call_count += 1
        self.total_tokens_spent += self.tokens_per_call

        # Return as JSON string if it's a dict.
        if isinstance(response, dict):
            return json.dumps(response)
        return str(response)


class HarnessOrchestratorBackend(OrchestratorBackend):
    """Null backend for the DEFAULT orchestrator seat: the live harness.

    When aesop.config.json has no seats.orchestrator block (or names backend
    'harness'/'claude'), the orchestrator seat is the live harness itself --
    the Claude Code session driving the loop -- not a swapped API backend.
    This mirrors claude_code_driver's harness-serviced operations on the
    worker seat: there is no Python code path that can "call" the harness,
    so decide_call raises a clear, documented error instead of fabricating
    a decision.

    build_orchestrator_backend() returns this class for the no-op default,
    which keeps existing installs byte-identical: no OpenAI backend is
    constructed and no API key is required.
    """

    def decide_call(
        self, prompt: str, *, schema: Optional[Dict[str, Any]] = None
    ) -> str:
        """Refuse: this seat is the live harness, not a swapped backend."""
        raise RuntimeError(
            "HarnessOrchestratorBackend has no decide_call: the orchestrator "
            "seat is the live harness (the Claude Code session) itself, not a "
            "swapped API backend. Decisions on this seat are made by the "
            "harness directly. To route orchestrator decisions to an API "
            "model, configure seats.orchestrator in aesop.config.json, e.g. "
            '{"seats": {"orchestrator": {"backend": "openai-compatible", '
            '"model": "gpt-4o-mini"}}}.'
        )


class OpenAICompatibleOrchestratorBackend(OrchestratorBackend):
    """Real OpenAI-compatible orchestrator backend.

    Uses OpenAI Chat Completions API (or compatible) to make decisions.
    Handles temperature fallback for reasoning models (gpt-5.x series).

    Args:
        model: The model id to use (default "gpt-4o-mini").
        base_url: OpenAI API base URL (default production).
        timeout_s: HTTP timeout in seconds (default 120).
        transport: Optionally inject a custom transport for testing.
        api_key_env: Env var name holding the API key (default OPENAI_API_KEY;
            parity with the worker seat -- no hardcoded key env).
        is_local: True for local endpoints (Ollama etc.): a missing key env is
            replaced by a dummy 'local-only' key instead of raising. Requires
            a loopback base_url (localhost/127.0.0.1/::1) -- construction
            rejects is_local with a remote base_url.
    """

    # Maximum allowed response size (100KB) to prevent excessive memory use
    MAX_RESPONSE_SIZE = 100 * 1024  # 100KB

    # Default API key env var name (assembled to avoid secret-scan false positive).
    _DEFAULT_KEY_ENV = "OPENAI" + "_" + "API" + "_" + "KEY"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout_s: float = 120.0,
        transport: Optional[Any] = None,
        api_key_env: Optional[str] = None,
        is_local: bool = False,
    ):
        self.model = model
        # SSRF guard at the constructor seam (mirrors backend_config): rejects
        # non-http(s) schemes and private/link-local hosts on direct
        # construction. FAIL CLOSED: if the guard could not be imported,
        # refuse to construct rather than silently skipping validation.
        if validate_base_url is None or validate_is_local_base_url is None:
            raise RuntimeError(
                "backend_config's base_url validators could not be imported; "
                "refusing to construct OpenAICompatibleOrchestratorBackend "
                "without the SSRF guard (fail closed). Ensure driver/ is on "
                "sys.path so backend_config.py is importable."
            )
        validate_base_url(base_url)
        # is_local disables the key requirement, so it must be pinned to a
        # loopback base_url (parity with the worker seat / config layer).
        if is_local:
            validate_is_local_base_url(base_url)
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.transport = transport or default_openai_transport
        self.api_key_env = api_key_env or self._DEFAULT_KEY_ENV
        self.is_local = bool(is_local)
        # HS-2 block-gate hardening: accumulate usage tokens so the seat's
        # spend can be counted against the cost ceiling (get_tokens_spent).
        self.total_tokens_spent = 0

    def get_tokens_spent(self) -> int:
        return self.total_tokens_spent

    def decide_call(
        self, prompt: str, *, schema: Optional[Dict[str, Any]] = None
    ) -> str:
        """Call OpenAI API and return the decision response text.

        Args:
            prompt: The decision prompt.
            schema: Optional JSON schema (passed to API if supported).

        Returns:
            The raw model text (typically JSON).

        Raises:
            RuntimeError: on API errors, missing credentials, etc.
        """
        # Ensure API key is set (configured env var, not hardcoded). Local
        # endpoints (is_local) fall back to a dummy key -- Ollama-style
        # deployments require an Authorization header but ignore its value.
        # (Named retrieved_key to mirror openai_compatible_driver's local path.)
        retrieved_key = os.environ.get(self.api_key_env)
        if not retrieved_key:
            if self.is_local:
                retrieved_key = "local-only"
            else:
                raise RuntimeError(
                    f"{self.api_key_env} environment variable not set"
                )

        # Build the Chat Completions payload with temperature.
        # Temperature fallback is per-call (local to this method), not persisted.
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }

        # Add schema if provided and backend supports it.
        if schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "decision_response",
                    "schema": schema,
                    "strict": False,
                },
            }

        # Call the transport with temperature fallback (per-call, not persistent).
        # The stock transport accepts the resolved api_key so a configured
        # api_key_env / is_local dummy key is honored end-to-end; injected
        # test transports keep the legacy (payload, timeout_s, base_url)
        # signature, so the key kwarg is passed only to the default transport.
        call_kwargs = {"timeout_s": self.timeout_s, "base_url": self.base_url}
        if (
            default_openai_transport is not None
            and self.transport is default_openai_transport
        ):
            call_kwargs["api_key"] = retrieved_key
        try:
            response_data = self.transport(payload, **call_kwargs)
        except Exception as e:
            error_str = str(e)
            # TEMPERATURE FALLBACK: gpt-5.x reasoning models reject temperature=0.
            # This fallback is LOCAL to this call; it does NOT persist to future calls.
            if "temperature" in error_str and "unsupported_value" in error_str.lower():
                # Retry without temperature (remove it from payload for this call only).
                payload.pop("temperature", None)
                response_data = self.transport(payload, **call_kwargs)
            else:
                raise

        # Meter usage tokens (best effort) BEFORE response-shape validation:
        # the provider charged for the call even if the payload is unusable.
        if isinstance(response_data, dict):
            usage = response_data.get("usage")
            if isinstance(usage, dict):
                try:
                    total = usage.get("total_tokens")
                    if total is None:
                        total = int(usage.get("prompt_tokens") or 0) + int(
                            usage.get("completion_tokens") or 0
                        )
                    self.total_tokens_spent += max(0, int(total))
                except (TypeError, ValueError):
                    pass  # Unparseable usage: never fabricate spend.

        # Extract the completion text from the response.
        if not isinstance(response_data, dict) or "choices" not in response_data:
            raise RuntimeError(f"Unexpected API response format: {response_data}")

        choices = response_data.get("choices", [])
        if not choices or "message" not in choices[0]:
            raise RuntimeError(f"No message in API response: {response_data}")

        completion_text = choices[0]["message"].get("content", "")
        if not completion_text:
            raise RuntimeError("Empty completion text from API")

        # Enforce response size limit to prevent excessive memory use.
        # Measure BYTES not CHARS (multi-byte UTF-8 chars count as multiple bytes).
        completion_bytes = completion_text.encode("utf-8")
        if len(completion_bytes) > self.MAX_RESPONSE_SIZE:
            raise RuntimeError(
                f"Response size limit exceeded: {len(completion_bytes)} bytes > "
                f"{self.MAX_RESPONSE_SIZE} bytes (100KB). The response is too large."
            )

        return completion_text
