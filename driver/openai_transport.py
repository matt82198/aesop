#!/usr/bin/env python3
"""OpenAI HTTP transport for the AgentDriver codex_driver.

Provides a callable transport that POSTs to the OpenAI Chat Completions API
via stdlib urllib.request. This is the injectable seam that lets tests pass
canned responses without network/secrets, and lets the driver stay dependency-light.

Transport contract:
  Transport = callable(payload: dict) -> dict
  where payload is the OpenAI Chat Completions request body (with schema + messages)
  and returns the parsed response JSON (with choices/usage).

The real transport reads OPENAI_API_KEY from os.environ at call time (NEVER
hardcoded). Use the pattern:
  key = os.environ.get("OPENAI_API_KEY")
so the source code string does NOT trigger secret_scan rules.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request


# Type alias documenting the transport callable contract.
Transport = callable  # (payload: dict) -> dict

# Maximum response size (100 KB) to prevent OOM from hostile/broken endpoints.
# Enforced at the read() level BEFORE parsing to fail-safe on excessive responses.
MAX_RESPONSE_SIZE = 100 * 1024  # 100KB in bytes


class _AuthStripRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Custom redirect handler that strips Authorization on cross-origin redirects.

    This prevents credentials from leaking to a different host if the base_url
    or a network MITM redirects the request (e.g., 301/302 to evil.com).
    Same-origin redirects preserve the Authorization header.
    """

    # Sensitive headers that must not leak on cross-origin redirects.
    _SENSITIVE_HEADERS = {"authorization", "api-key", "x-api-key"}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Handle redirect: strip sensitive headers if the origin changed.

        Args:
            req: original Request object
            fp: file pointer (unused here)
            code: HTTP redirect status code (301, 302, 307, etc.)
            msg: HTTP reason message (unused here)
            headers: response headers (unused here)
            newurl: the redirect target URL

        Returns:
            A new urllib.request.Request with Authorization stripped if
            newurl is a different origin; otherwise a Request preserving headers.
        """
        # Parse both URLs to extract origin (scheme + host + port).
        orig_parsed = urllib.parse.urlparse(req.full_url)
        new_parsed = urllib.parse.urlparse(newurl)

        # Build comparable origins (normalize port if not specified).
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

        # Let the parent class build the redirected request normally.
        redirected = super().redirect_request(
            req, fp, code, msg, headers, newurl
        )

        # If origins differ, strip sensitive headers from the new request.
        if orig_origin != new_origin and redirected:
            for header_name in list(redirected.headers.keys()):
                if header_name.lower() in self._SENSITIVE_HEADERS:
                    del redirected.headers[header_name]

        return redirected


def default_openai_transport(
    payload: dict,
    timeout_s: float = 120.0,
    base_url: str = "https://api.openai.com/v1",
    api_key: str = None,
) -> dict:
    """Default transport: POST to OpenAI Chat Completions via urllib.

    Args:
        payload: OpenAI Chat Completions request body (messages, model, etc.).
        timeout_s: HTTP timeout in seconds (default 120).
        base_url: OpenAI API base URL (default production).
        api_key: Optional pre-resolved API key (HS-1 seats: callers that honor
            a configured api_key_env or an is_local dummy key pass it here).
            When None, falls back to reading OPENAI_API_KEY from the
            environment (the legacy behavior, unchanged).

    Returns:
        Parsed JSON response (choices, usage, etc.).

    Raises:
        RuntimeError: if no api_key was given and OPENAI_API_KEY env var is
            not set, or if the HTTP status is not 200-299.
        urllib.error.URLError: if the HTTP request fails.
    """
    # Read API key at call time from environment (unless pre-resolved by the
    # caller); NEVER hardcoded. The pattern os.environ.get("OPENAI_API_KEY")
    # does not trigger secret_scan because the RHS contains dots/parens.
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is not set. "
            "Set it before running the codex driver, or pass a FakeTransport in tests."
        )

    endpoint = f"{base_url}/chat/completions"

    # Build the HTTP request.
    payload_json = json.dumps(payload)
    request = urllib.request.Request(
        endpoint,
        data=payload_json.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        # Create a custom opener with auth-stripping redirect handler.
        # This prevents Authorization header leakage on cross-origin redirects.
        opener = urllib.request.build_opener(_AuthStripRedirectHandler())
        with opener.open(request, timeout=timeout_s) as response:
            status = response.status
            # Cap response read at MAX_RESPONSE_SIZE bytes before parsing.
            # Read MAX_RESPONSE_SIZE+1 bytes to detect if response exceeds limit.
            body_bytes = response.read(MAX_RESPONSE_SIZE + 1)
            if len(body_bytes) > MAX_RESPONSE_SIZE:
                raise RuntimeError(
                    f"Response size limit exceeded: {len(body_bytes)} bytes > "
                    f"{MAX_RESPONSE_SIZE} bytes ({MAX_RESPONSE_SIZE // 1024}KB). "
                    f"The response is too large."
                )
            body = body_bytes.decode("utf-8")

        # Classify non-2xx as an error.
        if not (200 <= status < 300):
            raise RuntimeError(
                f"OpenAI API returned HTTP {status}: {body[:200]}"
            )

        return json.loads(body)

    except urllib.error.HTTPError as exc:
        # Extract error details from response body if available.
        # HTTPError.fp contains the response body with potentially actionable
        # error codes (e.g., insufficient_quota vs rate_limit_exceeded).
        error_code = None
        error_message = None
        if exc.fp:
            try:
                # Read bounded response body (cap at MAX_RESPONSE_SIZE).
                error_body = exc.fp.read(MAX_RESPONSE_SIZE + 1).decode("utf-8", errors="replace")
                error_data = json.loads(error_body)
                if isinstance(error_data, dict) and "error" in error_data:
                    error_obj = error_data["error"]
                    if isinstance(error_obj, dict):
                        error_code = error_obj.get("code")
                        error_message = error_obj.get("message")
            except (json.JSONDecodeError, ValueError, AttributeError):
                # If we can't parse the body, fall back to plain message.
                pass

        # Build exception message with extracted details if available.
        # Never include request headers or API key material.
        if error_code or error_message:
            details = []
            if error_code:
                details.append(error_code)
            if error_message:
                details.append(error_message)
            exc_msg = f"OpenAI API request failed: HTTP Error {exc.code}: {exc.msg} [{', '.join(details)}]"
        else:
            exc_msg = f"OpenAI API request failed: HTTP Error {exc.code}: {exc.msg}"
        raise RuntimeError(exc_msg) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenAI API returned invalid JSON: {exc}") from exc
