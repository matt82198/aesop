#!/usr/bin/env python3
"""Configuration loading and driver instantiation for aesop backends.

This module provides offline-safe config loading: reading an aesop.config.json,
validating the backend block schema, and instantiating the correct AgentDriver
subclass. Critically, building a driver requires NO API key at construction time;
keys are read from os.environ at call time when live dispatch happens.

The config schema for the backend block:
  {
    "backend": "claude" | "codex" | "openai-compatible",
    "model": "...",                           # required for codex, openai-compatible
    "base_url": "..."(optional),              # required for openai-compatible
    "api_key_env": "..."(optional),           # optional; env var for API key
    "tier": N(optional),                      # deprecated; ignored if present
    "is_local": bool(optional)                # optional for openai-compatible
  }

HS-1 unified two-seat schema (0.4.0): ONE namespaced block selects BOTH seats:
  {
    "seats": {
      "worker": {        # same fields as the legacy backend block above
        "backend": "claude" | "codex" | "openai-compatible", ...
      },
      "orchestrator": {  # the decision seat (OrchestratorBackend)
        "backend": "harness" | "claude" | "openai-compatible",
        "model": "...",              # required for openai-compatible
        "base_url": "..."(optional), # default https://api.openai.com/v1
        "api_key_env": "..."(optional),
        "is_local": bool(optional),
        "timeout_s": N(optional)
      }
    }
  }
seats.worker takes precedence over the legacy flat/nested backend block; the
legacy block still parses unchanged (backward compatible). NO seats block ->
byte-identical behavior to today: Claude Code worker + harness orchestrator
(build_orchestrator_backend returns the null HarnessOrchestratorBackend; no
OpenAI backend is constructed and no API key is required). HONESTY NOTE: a
bare legacy flat block ({"backend": "codex", ...} with no seats) was dead
config on pre-0.4.0 installs (documented but consumed by nothing), so the
scheduler's config-first default keeps it INERT -- wave_scheduler activates a
configured worker seat ONLY when a seats block names one. Migrate the flat
block to seats.worker to opt in. Direct build_driver() callers still honor
the flat block as before.

Default (no config) -> ClaudeCodeDriver (preserves today's behavior).

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import ipaddress
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

from agent_driver import AgentDriver, ROLE_WORKER
from claude_code_driver import ClaudeCodeDriver


def _codex_model_map(config: dict) -> dict:
    """Build the CodexDriver model_map from a codex config block.

    Honors the schema-required 'model' field by mapping it to the worker role
    unless model_map explicitly overrides it. Previously 'model' was validated
    by load_backend_config but silently IGNORED here, so a config declaring
    e.g. model=gpt-4o still dispatched the default worker model.
    """
    model_map = config.get("model_map", {})
    if not isinstance(model_map, dict):
        model_map = {}
    model_map = dict(model_map)
    model = config.get("model")
    if isinstance(model, str) and model:
        model_map.setdefault(ROLE_WORKER, model)
    return model_map


def validate_base_url(base_url: str) -> None:
    """Validate base_url to prevent SSRF attacks.

    Enforces:
    - Scheme is http or https only (rejects ftp://, etc.)
    - Rejects embedded credentials (user:pass@host) which can leak into
      logs/error messages and get sent to whatever host the URL names.
    - Rejects private/link-local IP ranges EXCEPT localhost, 127.0.0.1 and ::1
      which are allowed explicitly for local Ollama-style deployments.

    IP literals rejected (both IPv4 AND IPv6, including IPv4-mapped IPv6
    forms like ::ffff:169.254.169.254 which would otherwise bypass IPv4
    checks): private (10/8, 172.16/12, 192.168/16, fc00::/7), loopback
    (127/8, ::1 -- except the explicit local allowlist), link-local
    (169.254/16 incl. the cloud metadata endpoint, fe80::/10), reserved,
    multicast, and unspecified (0.0.0.0, ::) addresses.

    Hostnames are ALSO resolved (socket.getaddrinfo) at validation time and
    every resolved address is held to the same private/loopback/link-local
    rules, so a DNS name pointing at an internal address is rejected too.

    RESIDUAL (documented, not closed here): this is a load/construct-time
    check. A TTL=0 DNS-rebinding attacker can pass validation and re-point
    the name at a private address before the HTTP call; fully closing that
    requires connection-time address pinning in the transport. An
    unresolvable hostname is allowed through (offline config loading must
    not fail), and the eventual connection attempt fails on its own.

    Args:
        base_url: The URL to validate (e.g., "https://api.openai.com/v1")

    Raises:
        ValueError: if validation fails
    """
    try:
        parsed = urlparse(base_url)
    except Exception as exc:
        raise ValueError(f"Invalid base_url format: {exc}") from exc

    # Check scheme (http or https only)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"base_url scheme must be 'http' or 'https', got '{parsed.scheme}'. "
            f"URL: {base_url}"
        )

    # Empty or missing netloc
    if not parsed.netloc:
        raise ValueError(f"base_url must include a host (netloc), got: {base_url}")

    # Reject embedded credentials (user:pass@host) -- these get sent to whatever
    # host the URL names and can leak into logs/error messages via str(base_url).
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(
            f"base_url must not contain embedded credentials (user:pass@host). "
            f"URL: {base_url}"
        )

    # Extract hostname and port (netloc includes port; hostname does not)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Could not extract hostname from base_url: {base_url}")

    # Explicitly allow localhost, 127.0.0.1, and the IPv6 loopback (for local
    # Ollama-style deployments that may bind to either address family).
    # urlparse strips the brackets from an IPv6 literal, but accept the
    # bracketed form too in case a caller passes a raw hostname.
    if hostname.lower() in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return

    # Try to parse as an IP address (IPv4 or IPv6)
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        # Not an IP literal: resolve the hostname and apply the same
        # private/loopback/link-local checks to every resolved address
        # (a DNS name is otherwise a trivial bypass of the IP rules).
        _validate_resolved_hostname(hostname, base_url)
        return

    # IPv4-mapped IPv6 addresses (e.g. ::ffff:169.254.169.254) embed a real
    # IPv4 address; unwrap it so the IPv4 checks below still apply. Without
    # this, an attacker can bypass every IPv4 rule below by writing the
    # target as its IPv4-mapped IPv6 form.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped

    # Reject private/loopback/link-local/reserved/multicast/unspecified
    # literals for BOTH IPv4 and IPv6. Covers RFC 1918 (10/8, 172.16/12,
    # 192.168/16), link-local 169.254/16 (cloud metadata) and fe80::/10,
    # loopback 127/8 and ::1, IPv6 ULA fc00::/7, and 0.0.0.0 / ::.
    # The explicit local allowlist (localhost, 127.0.0.1, ::1) is handled above.
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise ValueError(
            f"base_url points to a private/reserved IP range: {hostname}. "
            f"Use 'localhost', '127.0.0.1', or '::1' for local deployments. "
            f"URL: {base_url}"
        )


def _is_disallowed_ip(ip) -> bool:
    """True when an ip_address falls in a private/reserved/local range."""
    # IPv4-mapped IPv6 forms embed a real IPv4 address; unwrap it so the
    # IPv4 rules still apply (e.g. ::ffff:169.254.169.254).
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_resolved_hostname(hostname: str, base_url: str) -> None:
    """Resolve a hostname and reject private/reserved resolved addresses.

    Unresolvable hostnames pass (offline config loading must not fail);
    see validate_base_url's RESIDUAL note for the DNS-rebinding caveat.

    Raises:
        ValueError: if any resolved address is private/reserved/local.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, OSError):
        return
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]  # strip IPv6 scope id
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_disallowed_ip(ip):
            raise ValueError(
                f"base_url hostname '{hostname}' resolves to a "
                f"private/reserved address ({addr}). Use 'localhost', "
                f"'127.0.0.1', or '::1' for local deployments. "
                f"URL: {base_url}"
            )


# Hostnames allowed for is_local endpoints (loopback only).
_LOCAL_HOSTNAMES = ("localhost", "127.0.0.1", "::1", "[::1]")


def validate_is_local_base_url(base_url: str) -> None:
    """Require a loopback base_url when is_local=True.

    is_local disables the API-key requirement (a dummy Bearer is sent), so
    it MUST be pinned to loopback: is_local + a remote base_url would ship
    prompt content to an arbitrary host with no key needed.

    Raises:
        ValueError: if the base_url hostname is not localhost/127.0.0.1/::1.
    """
    try:
        hostname = urlparse(base_url).hostname or ""
    except Exception as exc:
        raise ValueError(f"Invalid base_url format: {exc}") from exc
    if hostname.lower() not in _LOCAL_HOSTNAMES:
        raise ValueError(
            f"'is_local': true requires a loopback base_url "
            f"(localhost, 127.0.0.1, or ::1), got host '{hostname}'. "
            f"For a remote endpoint drop is_local and set api_key_env. "
            f"URL: {base_url}"
        )


# Default API key env var name (assembled to avoid secret-scan false positive).
_DEFAULT_KEY_ENV = "OPENAI" + "_" + "API" + "_" + "KEY"

# api_key_env must LOOK like an LLM API key env var: uppercase, ending in
# _KEY or _API_KEY. Combined with the deny fragments below this blocks
# configs that name arbitrary env secrets (GITHUB_TOKEN, AWS creds, ...)
# which would otherwise be sent as a Bearer to the configured base_url.
_API_KEY_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*_(API_)?KEY$")
_API_KEY_ENV_DENY = (
    "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL",
    "PRIVATE", "ACCESS", "SESSION", "COOKIE", "SIGNING",
)


def validate_api_key_env(name, where: str = "backend") -> None:
    """Validate an api_key_env value against a safe key-name shape.

    Accepts the default key env and provider-key-shaped names
    (e.g. OPENROUTER_API_KEY, MY_PROVIDER_KEY); rejects names that do not
    end in _KEY/_API_KEY or that contain obvious non-LLM secret fragments
    (SECRET/TOKEN/PASSWORD/ACCESS/...). Logs a NOTICE to stderr when a
    non-default name is configured so key routing is visible at load time.

    Raises:
        ValueError: if the name fails the pattern or hits the denylist.
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"'{where}.api_key_env' must be a non-empty string")
    if name == _DEFAULT_KEY_ENV:
        return
    if not _API_KEY_ENV_RE.match(name):
        raise ValueError(
            f"'{where}.api_key_env' value '{name}' does not look like an "
            f"LLM API key env var (expected an UPPERCASE name ending in "
            f"_KEY or _API_KEY, e.g. OPENROUTER_API_KEY)"
        )
    for fragment in _API_KEY_ENV_DENY:
        if fragment in name:
            raise ValueError(
                f"'{where}.api_key_env' value '{name}' names what looks "
                f"like a non-LLM secret ('{fragment}'); refusing to send "
                f"it as a Bearer token to a configured endpoint"
            )
    print(
        f"NOTICE: {where} api_key_env is '{name}' (non-default); its value "
        f"will be sent as a Bearer token to the configured base_url",
        file=sys.stderr,
    )


# Hosted OpenAI default for the orchestrator seat (worker openai-compatible
# blocks require an explicit base_url; the orchestrator seat may omit it).
DEFAULT_ORCHESTRATOR_BASE_URL = "https://api.openai.com/v1"

_VALID_WORKER_BACKENDS = ("claude", "codex", "openai-compatible")
_VALID_ORCHESTRATOR_BACKENDS = ("harness", "claude", "openai-compatible")


def _validate_worker_seat(block: dict) -> None:
    """Validate a seats.worker block (same rules as the legacy backend block).

    Raises:
        TypeError/ValueError mirroring the legacy block's error texts.
    """
    backend_name = block.get("backend")
    if not isinstance(backend_name, str):
        raise TypeError("'seats.worker.backend' field must be a string")
    if backend_name not in _VALID_WORKER_BACKENDS:
        raise ValueError(
            f"Unknown backend '{backend_name}'. "
            f"Valid choices: {', '.join(_VALID_WORKER_BACKENDS)}"
        )
    if backend_name == "codex":
        if "model" not in block:
            raise ValueError("backend 'codex' requires 'model' field")
        if not isinstance(block["model"], str):
            raise ValueError("'model' must be a string")
    if backend_name == "openai-compatible":
        if "base_url" not in block:
            raise ValueError("backend 'openai-compatible' requires 'base_url' field")
        if "model" not in block:
            raise ValueError("backend 'openai-compatible' requires 'model' field")
        if not isinstance(block["base_url"], str):
            raise ValueError("'base_url' must be a string")
        if not isinstance(block["model"], str):
            raise ValueError("'model' must be a string")
        # Validate base_url to prevent SSRF attacks
        validate_base_url(block["base_url"])
        # is_local must be pinned to loopback (it disables the key check).
        if block.get("is_local"):
            validate_is_local_base_url(block["base_url"])
        # api_key_env must look like an LLM key env var, not any secret.
        if "api_key_env" in block:
            validate_api_key_env(block["api_key_env"], where="seats.worker")


def _validate_orchestrator_seat(block: dict) -> None:
    """Validate a seats.orchestrator block.

    Raises:
        TypeError: if 'backend' is not a string.
        ValueError: on unknown backend, missing model, or SSRF-unsafe base_url.
    """
    backend_name = block.get("backend")
    if not isinstance(backend_name, str):
        raise TypeError("'seats.orchestrator.backend' field must be a string")
    if backend_name not in _VALID_ORCHESTRATOR_BACKENDS:
        raise ValueError(
            f"Unknown orchestrator backend '{backend_name}'. "
            f"Valid choices: {', '.join(_VALID_ORCHESTRATOR_BACKENDS)}"
        )
    if backend_name in ("harness", "claude") and "model" in block:
        # The live-harness seat decides with the harness's own model; a
        # configured model here is silently unused -- say so loudly.
        print(
            f"NOTICE: seats.orchestrator backend '{backend_name}' ignores "
            f"the 'model' field (the live harness decides with its own "
            f"model); remove it, or use backend 'openai-compatible' to "
            f"route decisions to that model",
            file=sys.stderr,
        )
    if backend_name == "openai-compatible":
        if "model" not in block or not isinstance(block["model"], str):
            raise ValueError(
                "orchestrator backend 'openai-compatible' requires 'model' field (string)"
            )
        base_url = block.get("base_url", DEFAULT_ORCHESTRATOR_BASE_URL)
        if not isinstance(base_url, str):
            raise ValueError("'base_url' must be a string")
        # Validate base_url to prevent SSRF attacks (load-time, earliest catch).
        validate_base_url(base_url)
        # is_local must be pinned to loopback (it disables the key check);
        # note the DEFAULT base_url is the hosted endpoint, so is_local
        # without an explicit loopback base_url is rejected too.
        if block.get("is_local"):
            validate_is_local_base_url(base_url)
        # api_key_env must look like an LLM key env var, not any secret.
        if "api_key_env" in block:
            validate_api_key_env(
                block["api_key_env"], where="seats.orchestrator"
            )


def _normalize_seats(config: dict) -> Optional[dict]:
    """Extract and validate the optional 'seats' block from a raw config dict.

    Returns:
        A normalized {"worker": {...}?, "orchestrator": {...}?} dict, or None
        when no seats block is present (the legacy/no-op path).

    Raises:
        TypeError/ValueError: on malformed seat blocks (fail loud at load time).
    """
    seats_block = config.get("seats")
    if seats_block is None:
        return None
    if not isinstance(seats_block, dict):
        raise TypeError(
            "'seats' must be a JSON object with optional 'worker'/'orchestrator' keys"
        )
    worker_seat = seats_block.get("worker")
    orch_seat = seats_block.get("orchestrator")
    if worker_seat is not None and not isinstance(worker_seat, dict):
        raise TypeError("'seats.worker' must be a JSON object")
    if orch_seat is not None and not isinstance(orch_seat, dict):
        raise TypeError("'seats.orchestrator' must be a JSON object")
    normalized: Dict[str, dict] = {}
    if worker_seat is not None:
        _validate_worker_seat(worker_seat)
        normalized["worker"] = dict(worker_seat)
    if orch_seat is not None:
        _validate_orchestrator_seat(orch_seat)
        normalized["orchestrator"] = dict(orch_seat)
    return normalized


def load_backend_config(path: Optional[str] = None) -> dict:
    """Load backend configuration from an aesop.config.json file.

    Args:
        path: Path to the config file. If None, looks for aesop.config.json
              in the current working directory. If the file does not exist, returns
              a Claude default config dict (backend='claude').

    Returns:
        A dict with structure:
          {
            "backend": "claude" | "codex" | "openai-compatible",
            "model": "...",
            "base_url": "...",
            "api_key_env": "...",
            "is_local": bool,
            ... other fields preserved
          }

    Raises:
        ValueError: if the config file exists but is malformed JSON, or if the
                    backend block has invalid/conflicting fields.
        TypeError: if the parsed config is not a dict or does not have a 'backend' key.
    """
    if path is None:
        path = "aesop.config.json"

    config_path = Path(path)
    if not config_path.exists():
        # Default: Claude backend.
        return {"backend": "claude"}

    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"aesop.config.json is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot read aesop.config.json: {exc}") from exc

    if not isinstance(config, dict):
        raise TypeError("aesop.config.json must be a JSON object (dict)")

    # HS-1: unified two-seat block. seats.worker (validated) takes precedence
    # over the legacy flat/nested backend block; seats.orchestrator is
    # validated here and preserved under result["seats"] for
    # build_orchestrator_backend(). No seats block -> legacy path unchanged.
    seats = _normalize_seats(config)
    if seats is not None and "worker" in seats:
        result = dict(seats["worker"])
        result["seats"] = seats
        return result

    # Extract the backend block (nested or at root level).
    # Support both {"backend": {...}} and direct backend dict.
    if "backend" in config and isinstance(config["backend"], dict):
        backend_block = config["backend"]
    elif "backend" in config and isinstance(config["backend"], str):
        # Flat structure: backend is a string, not nested.
        backend_block = config
    else:
        # No backend key; treat as default Claude (attach seats when present
        # so an orchestrator-only seats block still reaches the builder).
        if seats is not None:
            return {"backend": "claude", "seats": seats}
        return {"backend": "claude"}

    # Validate backend field.
    backend_name = backend_block.get("backend")
    if not backend_name:
        backend_name = config.get("backend")
    if not isinstance(backend_name, str):
        raise TypeError("'backend' field must be a string")

    valid_backends = ("claude", "codex", "openai-compatible")
    if backend_name not in valid_backends:
        raise ValueError(
            f"Unknown backend '{backend_name}'. "
            f"Valid choices: {', '.join(valid_backends)}"
        )

    # Validate required fields per backend.
    if backend_name == "codex":
        if "model" not in backend_block:
            raise ValueError("backend 'codex' requires 'model' field")
        if not isinstance(backend_block["model"], str):
            raise ValueError("'model' must be a string")

    if backend_name == "openai-compatible":
        if "base_url" not in backend_block:
            raise ValueError("backend 'openai-compatible' requires 'base_url' field")
        if "model" not in backend_block:
            raise ValueError("backend 'openai-compatible' requires 'model' field")
        if not isinstance(backend_block["base_url"], str):
            raise ValueError("'base_url' must be a string")
        if not isinstance(backend_block["model"], str):
            raise ValueError("'model' must be a string")
        # Validate base_url to prevent SSRF attacks
        validate_base_url(backend_block["base_url"])
        # Same is_local / api_key_env hardening as the seats.worker path.
        if backend_block.get("is_local"):
            validate_is_local_base_url(backend_block["base_url"])
        if "api_key_env" in backend_block:
            validate_api_key_env(backend_block["api_key_env"], where="backend")

    # Normalize: return backend dict with all fields.
    result = dict(backend_block)
    result["backend"] = backend_name
    if seats is not None:
        result["seats"] = seats
    return result


def build_driver(config: Optional[dict] = None) -> AgentDriver:
    """Instantiate the correct AgentDriver from a config dict.

    Args:
        config: Backend config dict (from load_backend_config). If None,
                uses Claude Code driver (default).

    Returns:
        An AgentDriver subclass (ClaudeCodeDriver, CodexDriver, OpenAICompatibleDriver).

    Raises:
        ValueError: if the config specifies an unknown backend or is missing required fields.
        RuntimeError: if a live dispatch later tries to use an API key that is not set.
                      This runtime error happens at call time, not at build time, so
                      building a driver is always offline-safe.
    """
    if config is None:
        config = {"backend": "claude"}

    # HS-1: honor a seats.worker block on raw (unloaded) dicts too, via the
    # SAME replace+validate promotion as load_backend_config: the worker
    # seat REPLACES the top-level view (top-level keys never merge into the
    # seat -- a stray top-level base_url must not leak in) and is validated,
    # so raw dicts and loader output yield identical drivers. Configs from
    # load_backend_config() are already flattened; re-promoting the copy it
    # keeps under 'seats' is idempotent.
    seats = config.get("seats")
    if isinstance(seats, dict) and isinstance(seats.get("worker"), dict):
        worker_seat = seats["worker"]
        _validate_worker_seat(worker_seat)
        config = dict(worker_seat)

    backend_name = config.get("backend", "claude")

    if backend_name == "claude":
        # Optional model_map override for Claude.
        model_map = None
        if "model_map" in config and isinstance(config.get("model_map"), dict):
            model_map = config["model_map"]
        return ClaudeCodeDriver(model_map=model_map)

    if backend_name == "codex":
        # Import here to avoid circular dependency.
        try:
            from codex_driver import CodexDriver
        except ImportError as exc:
            raise RuntimeError(
                "Cannot import CodexDriver. Make sure codex_driver.py is in the driver/ directory."
            ) from exc

        return CodexDriver(
            model_map=_codex_model_map(config),
            transport=None,  # Will use default; key read at call time.
            max_owned_bytes=config.get("max_owned_bytes", 200_000),
            max_retries=config.get("max_retries", 2),
            timeout_s=config.get("timeout_s", 120.0),
            allow_unverified_models=bool(
                config.get("allow_unverified_models", False)
            ),
        )

    if backend_name == "openai-compatible":
        # Import here to avoid circular dependency.
        try:
            from openai_compatible_driver import OpenAICompatibleDriver
        except ImportError as exc:
            raise RuntimeError(
                "Cannot import OpenAICompatibleDriver. "
                "Make sure openai_compatible_driver.py is in the driver/ directory."
            ) from exc

        base_url = config["base_url"]
        model = config["model"]
        # Validate base_url to prevent SSRF attacks
        validate_base_url(base_url)
        # Default API key env var name (assembled to avoid secret-scan false positive).
        default_key_env = "OPENAI" + "_" + "API" + "_" + "KEY"
        api_key_env = config.get("api_key_env", default_key_env)
        is_local = config.get("is_local", False)
        if not isinstance(is_local, bool):
            is_local = False

        model_map = config.get("model_map", {})
        if not isinstance(model_map, dict):
            model_map = {}

        return OpenAICompatibleDriver(
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            is_local=is_local,
            model_map=model_map,
            transport=None,  # Will use default; key read at call time.
            max_owned_bytes=config.get("max_owned_bytes", 200_000),
            max_retries=config.get("max_retries", 2),
            timeout_s=config.get("timeout_s", 120.0),
        )

    raise ValueError(f"Unknown backend '{backend_name}'")


def build_orchestrator_backend(config: Optional[dict] = None):
    """Instantiate the orchestrator-seat backend from a config dict (HS-1).

    Mirrors build_driver() for the decision seat: reads seats.orchestrator
    from a config dict (as returned by load_backend_config, or hand-built).

    Seat resolution:
      - No config, no seats block, no orchestrator seat, or backend
        'harness'/'claude' -> HarnessOrchestratorBackend (the null backend:
        the live harness IS the orchestrator; decide_call raises). This is
        the no-op default -- no OpenAI backend constructed, no key required.
      - backend 'openai-compatible' -> OpenAICompatibleOrchestratorBackend
        configured with model/base_url/api_key_env/is_local/timeout_s.
        base_url defaults to the hosted OpenAI endpoint and is SSRF-validated
        via validate_base_url (also re-checked in the backend constructor).

    Building is offline-safe: no API key is read until decide_call time.

    Args:
        config: Config dict (from load_backend_config), or None.

    Returns:
        An OrchestratorBackend instance.

    Raises:
        ValueError/TypeError: on an invalid orchestrator seat block.
    """
    # Import lazily: orchestrator_backend imports validate_base_url from this
    # module, so a top-level import here would create a cycle.
    from orchestrator_backend import (
        HarnessOrchestratorBackend,
        OpenAICompatibleOrchestratorBackend,
    )

    if not config:
        return HarnessOrchestratorBackend()

    seats = config.get("seats")
    orch = seats.get("orchestrator") if isinstance(seats, dict) else None
    if not isinstance(orch, dict) or not orch:
        return HarnessOrchestratorBackend()

    backend_name = orch.get("backend", "harness")
    if backend_name in ("harness", "claude"):
        return HarnessOrchestratorBackend()

    if backend_name == "openai-compatible":
        # Re-validate for direct-dict callers (load_backend_config output is
        # already validated; validation is idempotent).
        _validate_orchestrator_seat(orch)
        base_url = orch.get("base_url", DEFAULT_ORCHESTRATOR_BASE_URL)
        # Default API key env var name (assembled to avoid secret-scan false positive).
        default_key_env = "OPENAI" + "_" + "API" + "_" + "KEY"
        return OpenAICompatibleOrchestratorBackend(
            model=orch["model"],
            base_url=base_url,
            timeout_s=float(orch.get("timeout_s", 120.0)),
            api_key_env=orch.get("api_key_env", default_key_env),
            is_local=bool(orch.get("is_local", False)),
        )

    raise ValueError(
        f"Unknown orchestrator backend '{backend_name}'. "
        f"Valid choices: {', '.join(_VALID_ORCHESTRATOR_BACKENDS)}"
    )


def describe_backend(config: Optional[dict] = None) -> str:
    """Return a human-readable description of a backend configuration.

    Args:
        config: Backend config dict (from load_backend_config).

    Returns:
        A short ASCII string suitable for logging, e.g.:
          "claude-code: parallel=1 wfs=1 ... tier=1"
          "codex (gpt-4o-mini) @ OpenAI: tier=2"
          "openai-compatible (neural-chat) @ localhost:11434 (local): tier=3"
    """
    if config is None:
        config = {"backend": "claude"}

    backend_name = config.get("backend", "claude")

    if backend_name == "claude":
        driver = ClaudeCodeDriver()
        return driver.describe()

    if backend_name == "codex":
        try:
            from codex_driver import CodexDriver
        except ImportError:
            return "codex (import failed)"
        try:
            driver = CodexDriver(
                model_map=_codex_model_map(config),
                allow_unverified_models=bool(
                    config.get("allow_unverified_models", False)
                ),
            )
        except ValueError as exc:
            # Describe must not crash on a config the driver would reject;
            # surface the rejection instead.
            return f"codex (invalid model config: {exc})"
        return driver.describe()

    if backend_name == "openai-compatible":
        try:
            from openai_compatible_driver import OpenAICompatibleDriver
        except ImportError:
            return "openai-compatible (import failed)"
        driver = OpenAICompatibleDriver(
            base_url=config["base_url"],
            model=config["model"],
            api_key_env=config.get("api_key_env", "OPENAI_API_KEY"),
            is_local=config.get("is_local", False),
        )
        return driver.describe()

    return f"unknown backend '{backend_name}'"
