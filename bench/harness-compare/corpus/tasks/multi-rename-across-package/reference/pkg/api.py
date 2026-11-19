"""Tiny request handler."""

from .store import get_record


def handle_get(key):
    """Return a response dict for a GET of key."""
    record = get_record(key)
    if record is None:
        return {"status": 404, "body": None}
    return {"status": 200, "body": record}
