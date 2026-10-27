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
import urllib.parse

# Must import config module, never "from config import <paths>"
import config


# Module-level session token (set by init())
SESSION_TOKEN = None

# Loopback hosts accepted in an Origin/Referer, pinned to the bound port.
LOCAL_ORIGIN_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def is_local_origin(origin, expected_port):
    """True if an Origin/Referer value is http[s]://<loopback host>:<expected_port>.

    Exact match only: scheme must be ``http`` or ``https`` and netloc must be exactly
    ``127.0.0.1:<port>``, ``localhost:<port>`` or ``[::1]:<port>``. Userinfo
    (``user@host``) and host suffixes (``localhost:8770.evil.com``) therefore
    never match, and a bare host with no port is rejected.

    Args:
        origin: raw header value (may be empty).
        expected_port: the port the server is actually bound to.
    """
    if not origin or not isinstance(expected_port, int):
        return False
    try:
        parts = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if parts.netloc in {f"{host}:{expected_port}" for host in LOCAL_ORIGIN_HOSTS}:
        return True
    # Explicit opt-in only (Vite dev server proxying /api): exact (scheme, netloc) match.
    return (parts.scheme, parts.netloc) in _dev_origin_allowlist()


DEV_ORIGINS_ENV = "AESOP_DASH_DEV_ORIGINS"


def _dev_origin_allowlist():
    """Parse AESOP_DASH_DEV_ORIGINS (comma-separated exact origins), read live.

    Each entry must be exactly http[s]://<loopback host>:<explicit port>, with
    host in 127.0.0.1 / localhost / [::1]. Anything else is ignored with a
    stderr warning; the default allowlist is unaffected.

    Returns:
        set of (scheme, netloc) tuples.
    """
    allowed = set()
    for item in os.environ.get(DEV_ORIGINS_ENV, "").split(","):
        item = item.strip()
        if not item:
            continue
        valid = False
        try:
            parts = urllib.parse.urlsplit(item)
            port = parts.port
            valid = (
                parts.scheme in ("http", "https")
                and port is not None
                and parts.netloc in {f"{host}:{port}" for host in LOCAL_ORIGIN_HOSTS}
                and item == f"{parts.scheme}://{parts.netloc}"
            )
        except ValueError:
            valid = False
        if valid:
            allowed.add((parts.scheme, parts.netloc))
        else:
            print(f"[csrf] {DEV_ORIGINS_ENV} entry ignored (must be exact "
                  f"http[s]://<loopback host>:<port>): {item!r}", file=sys.stderr)
    return allowed


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
    except Exception as e:
        # Token still returned from memory; warn so the failure is not silent.
        print(f"[csrf] Failed to persist token file (in-memory token only): {e}", file=sys.stderr)

    return token


def validate_csrf_request(headers, expected_port):
    """Validate CSRF protections on /submit POST request.

    Performs two checks:
    1. Origin/Referer validation: if Origin or Referer header is present, must be
       http://127.0.0.1:<expected_port>, http://localhost:<expected_port> or
       http://[::1]:<expected_port> (see is_local_origin)
    2. X-Aesop-Token validation: must match SESSION_TOKEN

    Args:
        headers: dict-like object with HTTP headers (case-insensitive)
        expected_port: the port the server is bound to (keyword-friendly; required
            so no caller can silently skip the port pin)

    Returns:
        tuple: (is_valid: bool, reason: str or None)
        - (True, None) if CSRF checks pass
        - (False, reason) if either check fails
    """
    # Check 1: Origin/Referer header validation
    origin = headers.get("Origin", "").strip()
    referer = headers.get("Referer", "").strip()

    # If Origin or Referer is present, validate it's local and on the bound port
    if origin or referer:
        check_value = origin or referer
        if not is_local_origin(check_value, expected_port):
            return (False, "Foreign Origin/Referer rejected")

    # Check 2: X-Aesop-Token validation
    token = headers.get("X-Aesop-Token", "").strip()
    if not token:
        return (False, "Missing X-Aesop-Token header")

    if not hmac.compare_digest(token, SESSION_TOKEN):
        return (False, "Invalid X-Aesop-Token")

    return (True, None)
