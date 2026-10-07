#!/usr/bin/env python3
"""
Aesop UI API — shared mutation-request validation (wave-10 P0 split from handler.py).

Every mutating endpoint (/submit, POST /api/tracker, POST /api/tracker/<id>)
repeats the same two gates before touching domain logic: CSRF validation and
a Content-Length bound check, followed by a JSON body parse. This module
centralizes those two gates + the parse so they are directly unit-testable
without an HTTP server (see tests/test_api_tracker.py).

SAFETY NOTE (load-bearing): callers MUST check Content-Length is within
[1, MAX_BODY_BYTES] *before* calling self.rfile.read() on the live socket --
reading an attacker-declared length without a cap first is exactly the DoS
hole this gate exists to close. validate_mutation() re-derives the same
bound from headers (cheap, no I/O) so its error response matches what the
caller already decided; callers should gate their own read using
MAX_BODY_BYTES from this module so the two stay in lockstep. This module
never touches the socket itself -- it only ever receives bytes the caller
already obtained.

Sibling modules (ui/api/tracker.py, ui/api/submit.py) that read config paths
must do so live via `import config` at call time, never `from config import
<path>` -- a frozen import goes stale after config.reload() (breaks
test-fixture isolation). See ui/CLAUDE.md.
"""
import json

from csrf import validate_csrf_request


class NotFoundError(Exception):
    """Raised when a tracker item is not found."""


# Mutating endpoints cap request bodies at 10KB. Kept as a shared constant so
# handler.py's pre-read bound check and this module's own re-check never drift.
MAX_BODY_BYTES = 10000


def validate_mutation(headers, body_bytes, content_length_error="Invalid Content-Length"):
    """Run the CSRF + Content-Length gates shared by every mutating endpoint,
    then JSON-decode the body.

    Args:
        headers: dict-like HTTP headers (case-insensitive), as accepted by
            csrf.validate_csrf_request.
        body_bytes: raw request body bytes already read by the caller. The
            caller must have bounded how many bytes it read using the same
            Content-Length header (see SAFETY NOTE above) -- this function
            performs no socket I/O itself.
        content_length_error: exact error message to use on an out-of-range
            Content-Length, so callers can preserve endpoint-specific wording
            (e.g. /submit's "Invalid Content-Length (must be 1-10000 bytes)"
            vs /api/tracker's "Invalid Content-Length").

    Returns:
        (True, parsed) where parsed is the decoded JSON value (typically a
            dict) on success.
        (False, (status_code, error_dict)) on a CSRF or Content-Length gate
            failure -- callers should write this directly as the JSON error
            response.

    Note: a malformed JSON body raises json.JSONDecodeError rather than being
    caught here, so callers can keep their historical per-endpoint handling
    (some endpoints report 400 "Invalid JSON", others let the parse failure
    fall through to a generic 500) -- see ui/api/tracker.py and
    ui/api/submit.py callers in ui/handler.py.
    """
    is_valid, reason = validate_csrf_request(headers)
    if not is_valid:
        return False, (403, {"error": "CSRF protection: " + reason})

    try:
        content_length = int(headers.get('Content-Length', 0) or 0)
    except (TypeError, ValueError):
        content_length = 0

    if content_length <= 0 or content_length > MAX_BODY_BYTES:
        return False, (400, {"error": content_length_error})

    body_str = (body_bytes or b'').decode('utf-8', errors='ignore')
    parsed = json.loads(body_str)
    return True, parsed
