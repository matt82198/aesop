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

# Max response size (same as OpenAI transport).
MAX_RESPONSE_SIZE = 10 * 1024 * 1024


class _AuthStripRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that strips Authorization header on cross-domain redirects.

    Defense-in-depth: a Location header pointing to an attacker-controlled domain
    should not leak the API key. This is the same guard as openai_transport.py.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Override: strip Authorization from cross-domain redirects."""
        m = urllib.request.AbstractBasicAuthHandler.redirect_request
        newreq = m(self, req, fp, code, msg, headers, newurl)
        if newreq:
            orig_host = urllib.parse.urlparse(req.get_full_url()).netloc
            new_host = urllib.parse.urlparse(newurl).netloc
            if orig_host != new_host:
                if "Authorization" in newreq.headers:
                    del newreq.headers["Authorization"]
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
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"{api_key_env} environment variable is not set. "
                f"Set {api_key_env} before running, or use a FakeTransport in tests."
            )

        endpoint = "https://api.anthropic.com/v1/messages"

        # Build the HTTP request.
        payload_json = json.dumps(payload)
        request = urllib.request.Request(
            endpoint,
            data=payload_json.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )

        # Use the auth-stripping handler for redirects.
        opener = urllib.request.build_opener(_AuthStripRedirectHandler)
        try:
            response = opener.open(request, timeout=timeout_s)
            body = response.read(MAX_RESPONSE_SIZE)
            return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Re-raise with the status and response body for debugging.
            try:
                error_body = exc.read(MAX_RESPONSE_SIZE).decode("utf-8")
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
