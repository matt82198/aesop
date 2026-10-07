#!/usr/bin/env python3
"""
Aesop UI CSRF Protection — Token generation and validation.

Session token is generated once at startup and persisted to state/.ui-session-token (0600).
Token is used to validate mutations via /submit POST endpoint.

CRITICAL: This module reads config paths LIVE at call time via 'import config',
never 'from config import <path>'. This ensures paths are recomputed when
config.reload() is called (e.g., between test fixtures).
"""
import hmac
import os
import secrets
import sys

# Must import config module, never "from config import <paths>"
import config


# Module-level session token (set by init())
SESSION_TOKEN = None


def init():
    """Initialize CSRF protection: generate or load the per-session token.

    This should be called once at startup (after config.reload()).
    Token is persisted to state/.ui-session-token (mode 0600) and reused
    across server restarts.

    SECURITY: File is created atomically with restricted permissions using
    os.open(O_CREAT|O_EXCL) to avoid TOCTOU window where file exists with
    world-readable permissions.
    """
    global SESSION_TOKEN
    SESSION_TOKEN = _generate_session_token()


def _generate_session_token():
    """Generate or load the per-session CSRF token.

    Token is generated once at startup and persisted to state/.ui-session-token (mode 0600).
    Subsequent calls return the same token (in-memory).

    SECURITY: File is created atomically with restricted permissions using os.open(O_CREAT|O_EXCL)
    to avoid TOCTOU window where file exists with world-readable permissions.

    Returns:
        str: 43-character base64-like random token (256 bits / 3 bytes per char = ~43 chars)
    """
    # LIVE CONFIG READ: access config.UI_SESSION_TOKEN_FILE at call time
    token_file = config.UI_SESSION_TOKEN_FILE

    # Check if token file exists and is readable
    if token_file.exists():
        try:
            token = token_file.read_text().strip()
            if token and len(token) >= 32:
                return token
        except Exception as e:
            print(f"[csrf] Failed to read token file: {e}", file=sys.stderr)

    # Generate new token: 32 random bytes → 43-char base64-like string
    token = secrets.token_urlsafe(32)

    # Persist to file with restricted permissions (0600) using atomic creation
    try:
        # LIVE CONFIG READ: access config.STATE_DIR at call time
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)

        # Atomically create file with 0600 permissions using os.open with O_CREAT|O_EXCL.
        # This ensures the file is never world-readable (no TOCTOU window).
        # On Windows, mode bits are largely ignored, which is fine.
        try:
            fd = os.open(
                str(token_file),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600
            )
            # Write token via the file descriptor (no separate chmod needed)
            with os.fdopen(fd, 'w') as f:
                f.write(token)
        except FileExistsError:
            # File already exists (race condition or previous run).
            # Try to read it and use that token instead.
            try:
                token = token_file.read_text().strip()
                if token and len(token) >= 32:
                    return token
            except Exception as e:
                print(f"[csrf] Failed to read existing token file: {e}", file=sys.stderr)
            # If we can't read the existing file, fall back to in-memory token
    except Exception:
        pass  # Fail-open: token exists in memory even if file write fails

    return token


def validate_csrf_request(headers):
    """Validate CSRF protections on /submit POST request.

    Performs two checks:
    1. Origin/Referer validation: if Origin or Referer header is present, must be local
       (http[s]://127.0.0.1:<port>, http[s]://localhost:<port>, http[s]://[::1]:<port>)
    2. X-Aesop-Token validation: must match SESSION_TOKEN

    Args:
        headers: dict-like object with HTTP headers (case-insensitive)

    Returns:
        tuple: (is_valid: bool, reason: str or None)
        - (True, None) if CSRF checks pass
        - (False, reason) if either check fails
    """
    # Check 1: Origin/Referer header validation
    origin = headers.get("Origin", "").strip()
    referer = headers.get("Referer", "").strip()

    # If Origin or Referer is present, validate it's local
    if origin or referer:
        check_value = origin or referer
        # Check if it's a local origin: http[s]://127.0.0.1:<PORT>, http[s]://localhost:<PORT>,
        # or http[s]://[::1]:<PORT> (both http:// and https:// schemes accepted for same hosts)
        is_local = (
            check_value.startswith("http://127.0.0.1:") or
            check_value.startswith("https://127.0.0.1:") or
            check_value.startswith("http://localhost:") or
            check_value.startswith("https://localhost:") or
            check_value.startswith("http://[::1]:") or  # IPv6 localhost
            check_value.startswith("https://[::1]:")  # IPv6 localhost HTTPS
        )
        if not is_local:
            return (False, "Foreign Origin/Referer rejected")

    # Check 2: X-Aesop-Token validation
    token = headers.get("X-Aesop-Token", "").strip()
    if not token:
        return (False, "Missing X-Aesop-Token header")

    if not hmac.compare_digest(token, SESSION_TOKEN):
        return (False, "Invalid X-Aesop-Token")

    return (True, None)
