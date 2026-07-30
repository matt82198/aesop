#!/usr/bin/env python3
"""Anthropic API transport for the AgentDriver seam (minimal backend for bench).

Provides urllib-based HTTP transport for Anthropic's messages API endpoint.
Follows the same injectable pattern as openai_transport.py for test isolation.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict

# Maximum response size (100 KB) to prevent OOM from hostile/broken endpoints.
MAX_RESPONSE_SIZE = 100 * 1024


class _AuthStripRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that strips sensitive headers on cross-domain redirects.

    Defense-in-depth: a Location header pointing to an attacker-controlled domain
    should not leak the API key. Mirrors openai_transport.py's guard, covering
    x-api-key (Anthropic's auth header) alongside Authorization and api-key.
    """

    _SENSITIVE_HEADERS = {"authorization", "api-key", "x-api-key"}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Override: strip sensitive headers from cross-domain redirects."""
        newreq = super().redirect_request(req, fp, code, msg, headers, newurl)
        if newreq:
            orig_parsed = urllib.parse.urlparse(req.get_full_url())
            new_parsed = urllib.parse.urlparse(newurl)
            orig_origin = (
                orig_parsed.scheme,
                orig_parsed.hostname or "",
                orig_parsed.port or (443 if orig_parsed.scheme == "https" else 80),
            )
            new_origin = (
                new_parsed.scheme,
                new_parsed.hostname or "",
                new_parsed.port or (443 if new_parsed.scheme == "https" else 80),
            )
            if orig_origin != new_origin:
                for header_name in list(newreq.headers.keys()):
                    if header_name.lower() in self._SENSITIVE_HEADERS:
                        del newreq.headers[header_name]
        return newreq


def make_anthropic_transport(
    api_key_env: str = "ANTHROPIC_API_KEY",
    timeout_s: float = 120.0,
) -> Callable[[dict], dict]:
    """Factory: return a transport callable for Anthropic messages API.

    Args:
        api_key_env: environment variable name for the API key (default "ANTHROPIC_API_KEY")
        timeout_s: HTTP timeout in seconds

    Returns:
        A transport callable (request_payload dict) -> response dict

    Raises:
        RuntimeError: at call time if the API key env var is not set.
    """

    def transport(payload: dict) -> dict:
        """POST to Anthropic messages API via urllib."""
        # Look up the key from the configured env var, then the bench key, then
        # the conventional ANTHROPIC_API_KEY — the seam study supplies the key
        # as BENCH_API_KEY (pay-per-use), not ANTHROPIC_API_KEY.
        api_key = (
            os.environ.get(api_key_env)
            or os.environ.get("BENCH_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        )
        if not api_key:
            raise RuntimeError(
                f"No API key found ({api_key_env} / BENCH_API_KEY / ANTHROPIC_API_KEY "
                f"all unset). Set one before running, or use a FakeTransport in tests."
            )

        endpoint = "https://api.anthropic.com/v1/messages"

        # Build the HTTP request. Anthropic authenticates with the x-api-key
        # header (NOT Authorization: Bearer, which is the OpenAI convention).
        payload_json = json.dumps(payload)
        request = urllib.request.Request(
            endpoint,
            data=payload_json.encode("utf-8"),
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )

        # Use the auth-stripping handler for redirects.
        opener = urllib.request.build_opener(_AuthStripRedirectHandler)
        try:
            response = opener.open(request, timeout=timeout_s)
            body = response.read(MAX_RESPONSE_SIZE + 1)
            if len(body) > MAX_RESPONSE_SIZE:
                raise RuntimeError(
                    f"Anthropic API response exceeded {MAX_RESPONSE_SIZE} bytes"
                )
            return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Re-raise with the status and response body for debugging.
            try:
                error_body = exc.read(MAX_RESPONSE_SIZE + 1).decode("utf-8")
            except Exception:
                error_body = "(could not read error body)"
            raise RuntimeError(
                f"Anthropic API error {exc.code}: {error_body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error contacting Anthropic API: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON in Anthropic API response: {exc}") from exc

    return transport


def default_anthropic_transport() -> Callable[[dict], dict]:
    """Return the default Anthropic transport (reads ANTHROPIC_API_KEY from env)."""
    return make_anthropic_transport()
