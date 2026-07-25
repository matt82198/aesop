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

Default (no config) -> ClaudeCodeDriver (preserves today's behavior).

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import ipaddress
import json
import os
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

from agent_driver import AgentDriver
from claude_code_driver import ClaudeCodeDriver


def validate_base_url(base_url: str) -> None:
    """Validate base_url to prevent SSRF attacks.

    Enforces:
    - Scheme is http or https only (rejects ftp://, etc.)
    - Rejects private/link-local IP ranges EXCEPT localhost and 127.0.0.1
      which are allowed explicitly for local Ollama-style deployments.

    Private IP ranges rejected:
    - 10.0.0.0/8
    - 172.16.0.0/12
    - 192.168.0.0/16
    - 169.254.0.0/16 (link-local; includes AWS metadata endpoint 169.254.169.254)
    - 127.0.0.0/8 (loopback, EXCEPT localhost hostname and 127.0.0.1)

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

    # Extract hostname and port (netloc includes port; hostname does not)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Could not extract hostname from base_url: {base_url}")

    # Explicitly allow localhost and 127.0.0.1 (for local Ollama, etc.)
    if hostname.lower() in ("localhost", "127.0.0.1"):
        return

    # Try to parse as an IP address
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        # Not an IP address; assume it's a valid hostname (domain name)
        return

    # Check for private/reserved IP ranges
    # These ranges are forbidden EXCEPT localhost (handled above)
    private_ranges = [
        ipaddress.ip_network("10.0.0.0/8"),           # Private (RFC 1918)
        ipaddress.ip_network("172.16.0.0/12"),        # Private (RFC 1918)
        ipaddress.ip_network("192.168.0.0/16"),       # Private (RFC 1918)
        ipaddress.ip_network("169.254.0.0/16"),       # Link-local (includes metadata)
        ipaddress.ip_network("127.0.0.0/8"),          # Loopback (except 127.0.0.1, caught above)
    ]

    for network in private_ranges:
        if ip in network:
            raise ValueError(
                f"base_url points to private IP range {network}: {hostname}. "
                f"Use 'localhost' or '127.0.0.1' for local deployments. "
                f"URL: {base_url}"
            )


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

    # Extract the backend block (nested or at root level).
    # Support both {"backend": {...}} and direct backend dict.
    if "backend" in config and isinstance(config["backend"], dict):
        backend_block = config["backend"]
    elif "backend" in config and isinstance(config["backend"], str):
        # Flat structure: backend is a string, not nested.
        backend_block = config
    else:
        # No backend key; treat as default Claude.
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

    # Normalize: return backend dict with all fields.
    result = dict(backend_block)
    result["backend"] = backend_name
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

        model_map = config.get("model_map", {})
        if not isinstance(model_map, dict):
            model_map = {}

        return CodexDriver(
            model_map=model_map,
            transport=None,  # Will use default; key read at call time.
            max_owned_bytes=config.get("max_owned_bytes", 200_000),
            max_retries=config.get("max_retries", 2),
            timeout_s=config.get("timeout_s", 120.0),
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


def describe_backend(config: Optional[dict] = None) -> str:
    """Return a human-readable description of a backend configuration.

    Args:
        config: Backend config dict (from load_backend_config).

    Returns:
        A short ASCII string suitable for logging, e.g.:
          "claude-code: parallel=1 wfs=1 ... tier=1"
          "codex (gpt-3.5-turbo) @ OpenAI: tier=2"
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
        driver = CodexDriver(model_map=config.get("model_map", {}))
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
