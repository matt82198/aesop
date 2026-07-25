#!/usr/bin/env python3
"""OpenAI-compatible backend driver for aesop.

Implements AgentDriver for any OpenAI Chat Completions-COMPATIBLE endpoint
(e.g., OpenRouter, Together, local Ollama). Parameterized by base_url and
model, supporting optional custom API key environment variable.

REUSES Phase 2 CodexDriver execution contract (file injection, JSON schema,
full-file replacement, ownership enforcement, verification tier 2/3) but
overrides only the transport wiring and model resolution.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional

# Add driver to path for imports if needed.
DRIVER_DIR = Path(__file__).resolve().parent
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from agent_driver import (  # noqa: E402
    DriverCapabilities,
    ROLE_SETUP,
    ROLE_VERIFY,
    ROLE_WORKER,
)
from backend_config import (  # noqa: E402
    validate_base_url,
    validate_is_local_base_url,
)
from codex_driver import CodexDriver  # noqa: E402
from openai_transport import _AuthStripRedirectHandler  # noqa: E402


def make_openai_compatible_transport(
    base_url: str,
    api_key_env: str = "OPENAI_API_KEY",
    timeout_s: float = 120.0,
    is_local: bool = False,
):
    """Factory function: return a transport callable for an OpenAI-compatible endpoint.

    Args:
        base_url: e.g. "https://openrouter.ai/api/v1", "http://localhost:11434/v1"
        api_key_env: environment variable name for the API key (default "OPENAI_API_KEY")
        timeout_s: HTTP timeout in seconds
        is_local: if True, a missing API key is replaced by a dummy Bearer
            instead of raising. Requires a loopback base_url (validated here):
            the key waiver keys off this VALIDATED flag, never off the URL
            text (a substring check is spoofable, e.g. "localhost.attacker.example").

    Returns:
        A transport callable (payload dict) -> dict matching the CodexDriver contract.

    Raises:
        ValueError: if is_local=True with a non-loopback base_url.
        RuntimeError: at call time, if the API key env var is not set and
            is_local is False (no URL-based waiver).
    """
    import urllib.error
    import urllib.request

    # is_local waives the key requirement, so pin it to loopback at the
    # factory too (defense in depth for direct factory callers).
    if is_local:
        validate_is_local_base_url(base_url)

    def transport(payload: dict) -> dict:
        """POST to the OpenAI-compatible endpoint via urllib."""
        # For validated-local endpoints the API key may be unused/dummy; for
        # everything else it is required. The waiver is gated on the
        # loopback-validated is_local flag ONLY -- never on the URL text.
        retrieved_key = os.environ.get(api_key_env)
        if not retrieved_key and not is_local:
            raise RuntimeError(
                f"{api_key_env} environment variable is not set, and "
                f"base_url '{base_url}' is not a validated local endpoint "
                f"(is_local). Set {api_key_env} before running, set "
                f"is_local=True for a loopback endpoint, or use a "
                f"FakeTransport in tests."
            )

        # Use dummy key for local Ollama if not set.
        if not retrieved_key:
            retrieved_key = "local-only"

        endpoint = f"{base_url}/chat/completions"

        # Build the HTTP request.
        payload_json = json.dumps(payload)
        request = urllib.request.Request(
            endpoint,
            data=payload_json.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {retrieved_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            # POST with hard timeout.
            # Create a custom opener with auth-stripping redirect handler.
            # This prevents Authorization header leakage on cross-origin redirects.
            opener = urllib.request.build_opener(_AuthStripRedirectHandler())
            with opener.open(request, timeout=timeout_s) as response:
                status = response.status
                body = response.read().decode("utf-8")

            # Classify non-2xx as an error.
            if not (200 <= status < 300):
                raise RuntimeError(
                    f"OpenAI-compatible API returned HTTP {status}: {body[:200]}"
                )

            return json.loads(body)

        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI-compatible API request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenAI-compatible API returned invalid JSON: {exc}") from exc

    return transport


class OpenAICompatibleDriver(CodexDriver):
    """AgentDriver for OpenAI-Chat-Completions-COMPATIBLE endpoints.

    Supports OpenRouter, Together, local Ollama, and any service offering
    OpenAI-compatible endpoints. Configurable base_url, model, and API key env var.

    REUSES CodexDriver's Phase 2 execution contract entirely (file injection,
    JSON schema validation with retry, full-file replacement, ownership enforcement).
    Overrides only:
      - resolve_model: returns the configured model (not a role map)
      - transport construction: points urllib at the configured base_url
      - probe_capabilities: honest reporting of verification tier (2 for hosted, 3 for local small)
    """

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        is_local: bool = False,
        model_map: Optional[dict] = None,
        transport: Optional[callable] = None,
        now: Optional[callable] = None,
        max_owned_bytes: int = 200_000,
        max_retries: int = 2,
        timeout_s: float = 120.0,
    ):
        """Initialize the OpenAI-compatible driver.

        Args:
            base_url: OpenAI-compatible endpoint URL (e.g., https://openrouter.ai/api/v1)
            model: Model ID to use (e.g., "gpt-4-turbo" on OpenRouter, "neural-chat" on Ollama)
            api_key_env: Environment variable name for API key (default "OPENAI_API_KEY").
                For local Ollama, can be unused/dummy.
            is_local: If True, marks this as a local/small model and sets verification tier to 3.
                Default False (hosted models -> tier 2). Requires a loopback base_url
                (localhost/127.0.0.1/::1): is_local disables the key requirement, so a
                remote base_url is rejected at construction (ValueError).
            model_map: Optional role-to-model mapping for setup/verify roles (default=None, uses role-based fallback).
            transport: Optional injectable transport callable for testing (default=None, builds from base_url).
            now: callable returning time.time() for testing (default=time.time).
            max_owned_bytes: max total bytes of owned files (default 200KB).
            max_retries: max in-turn retries on malformed JSON (default 2).
            timeout_s: HTTP timeout in seconds (default 120).
        """
        # SSRF guard: validate base_url UNCONDITIONALLY at construction
        # (parity with OpenAICompatibleOrchestratorBackend.__init__), so a
        # direct construction with a private/link-local address fails even
        # when the config-layer checks were bypassed.
        validate_base_url(base_url)
        # is_local disables the API-key requirement, so it must be pinned to
        # a loopback base_url (parity with the orchestrator seat + config
        # layer): is_local + remote would ship prompts with a dummy Bearer.
        if is_local:
            validate_is_local_base_url(base_url)
        self._base_url = base_url
        self._model = model
        self._api_key_env = api_key_env
        self._is_local = is_local

        # Build the transport if not provided (for testing).
        if transport is None:
            transport = make_openai_compatible_transport(
                base_url=base_url,
                api_key_env=api_key_env,
                timeout_s=timeout_s,
                is_local=is_local,
            )

        # If no model_map provided, use a simple single-model fallback.
        # All roles use the same model.
        if model_map is None:
            model_map = {}

        # Initialize CodexDriver with the transport.
        # CodexDriver's __init__ will merge model_map with defaults.
        super().__init__(
            model_map=model_map,
            transport=transport,
            now=now,
            max_owned_bytes=max_owned_bytes,
            max_retries=max_retries,
            timeout_s=timeout_s,
        )

    def probe_capabilities(self) -> DriverCapabilities:
        """Honest capability matrix for OpenAI-compatible backend.

        Reuses CodexDriver tier 2 (hosted strong models like OpenRouter GPT-4)
        or tier 3 (local small models like Ollama neural-chat) based on is_local.

        Same contract as CodexDriver:
          - No native parallelism (orchestrator provides external loop).
          - No worker filesystem/shell (orchestrator injects + runs).
          - Structured output via JSON schema.
          - No worktree isolation (temp-dir).
          - Honest tool-use accuracy (lower for local small models).
        """
        if self._is_local:
            # Local/small model: Tier 3 (higher verification burden).
            # Tool-use accuracy lower than hosted models.
            accuracy = 0.80
            tier = 3
            notes = (
                "Local/small open model (e.g., Ollama neural-chat). Tier 3: "
                "validate all output, heavy spot-check, adversarial review required. "
                "Orchestrator-managed backend: no parallelism, no worker filesystem/shell."
            )
        else:
            # Hosted strong model (e.g., OpenRouter GPT-4): Tier 2.
            # Better accuracy than local models but lower than Claude.
            accuracy = 0.92
            tier = 2
            notes = (
                "Hosted OpenAI-compatible backend (e.g., OpenRouter, Together). "
                "Tier 2: validate all JSON, ~50% spot-check, adversarial review. "
                "Orchestrator-managed backend: no parallelism, no worker filesystem/shell."
            )

        return DriverCapabilities(
            name=f"{self.name} ({self._model})",
            parallel_dispatch=False,  # no native async; orchestrator loops
            worker_filesystem_access=False,  # orchestrator injects files
            worker_shell_access=False,  # orchestrator runs tests
            structured_output=True,  # JSON schema + response_format
            worktree_isolation=False,  # temp-dir fallback; no git
            native_cost_tracking=True,  # most endpoints report usage
            native_stall_detection=False,  # orchestrator times out
            tool_use_accuracy=accuracy,
            recommended_verification_tier=tier,
            available_models=(self._model,),
            notes=notes,
        )

    def resolve_model(self, role: str) -> str:
        """Resolve an abstract role to the configured model.

        For an OpenAI-compatible endpoint, always use the configured model.
        This overrides CodexDriver's role-based model mapping since we're a
        single-model adapter (all roles use the same model).

        Args:
            role: abstract role (worker/setup/verify)

        Returns:
            The configured model string (same for all roles).
        """
        # Always use self._model for OpenAI-compatible drivers.
        # They target a single endpoint/model, unlike CodexDriver which can
        # vary models by role (worker=gpt-3.5, setup/verify=gpt-4).
        return self._model
