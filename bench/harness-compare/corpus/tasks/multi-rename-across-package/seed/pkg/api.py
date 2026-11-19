"""Tiny request handler."""

from .store import fetch_record


def handle_get(key):
    """Return a response dict for a GET of key."""
    record = fetch_record(key)
    if record is None:
        return {"status": 404, "body": None}
    return {"status": 200, "body": record}
