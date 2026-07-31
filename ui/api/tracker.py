#!/usr/bin/env python3
"""
Aesop UI API — tracker CRUD logic (wave-10 P0 split from handler.py).

Free functions: take already-parsed request input (headers/body bytes/path
params extracted by ui/handler.py) and call collectors' tracker CRUD,
returning (status_code, dict). No self/HTTP coupling -- ui/handler.py owns
parsing the raw request and writing the HTTP response bytes.

Every mutation here is CSRF-gated via api.validate_mutation() (create) or a
direct csrf.validate_csrf_request() check performed by the caller before
delete (see ui/handler.py's handle_tracker_mutate -- CSRF is checked once,
before the path is even parsed, matching the pre-split handler's ordering).

Reads collectors' tracker CRUD, which itself reads config.X live via
`import config` -- see ui/CLAUDE.md load-bearing rule.
"""
import json

from . import validate_mutation
from collectors import (create_tracker_item, delete_tracker_item,
                         get_tracker_items, update_tracker_item)


def list_items(status=None, priority=None):
    """GET /api/tracker -- list items with optional status/priority filters.

    Returns:
        (200, items_list) on success.
        (500, error_dict) on failure.
    """
    try:
        items = get_tracker_items(status=status, priority=priority)
        return 200, items
    except Exception as e:
        import sys
        import traceback
        print(f"[tracker.list_items] Uncaught exception: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 500, {"error": "Internal server error"}


def create(headers, body_bytes):
    """POST /api/tracker -- create a tracker item. CSRF-gated.

    Args:
        headers: request headers (case-insensitive dict-like).
        body_bytes: raw request body already read + bounded by the caller.

    Returns:
        (201, item_dict) on success.
        (403, error_dict) on CSRF failure.
        (400, error_dict) on invalid Content-Length or invalid JSON.
        (500, error_dict) on any other failure.
    """
    try:
        ok, result = validate_mutation(headers, body_bytes)
        if not ok:
            status, error = result
            return status, error
        item = create_tracker_item(result)
        return 201, item
    except json.JSONDecodeError:
        return 400, {"error": "Invalid JSON"}
    except Exception as e:
        import sys
        import traceback
        print(f"[tracker.create] Uncaught exception: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 500, {"error": "Internal server error"}


def update(item_id, body_bytes):
    """POST /api/tracker/<id> (default action) -- update a tracker item.

    CSRF must already have been validated by the caller before invoking this
    (see ui/handler.py's handle_tracker_mutate: CSRF is checked once up front,
    shared with the delete branch). This function only handles JSON parse +
    the update itself, matching the pre-split handler's behavior where a
    malformed JSON body on this path falls through to a generic error rather
    than a dedicated "Invalid JSON" 400 (unlike create()).

    Args:
        item_id: tracker item id extracted from the URL path by the caller.
        body_bytes: raw request body already read + bounded by the caller.

    Returns:
        (200, item_dict) on success.
        (404, error_dict) if item_id is unknown.
        (500, error_dict) on any other failure (including malformed JSON --
            preserves the pre-split handler's behavior).
    """
    try:
        body_str = (body_bytes or b'').decode('utf-8', errors='replace')
        update_data = json.loads(body_str)
        item = update_tracker_item(item_id, update_data)
        return 200, item
    except Exception as e:
        import sys
        import traceback
        if "404" in str(e) or "not found" in str(e).lower():
            return 404, {"error": "404 Item not found"}
        print(f"[tracker.update] Uncaught exception: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 500, {"error": "Internal server error"}


def delete(item_id):
    """POST /api/tracker/<id>?action=delete -- soft-delete (archive) an item.

    CSRF must already have been validated by the caller before invoking this
    (see update()'s docstring -- same shared up-front check).

    Idempotent: deleting an already-archived item succeeds again (re-sets
    status to "archived"), it does not error.

    Returns:
        (200, item_dict) on success.
        (404, error_dict) if item_id is unknown.
        (500, error_dict) on any other failure.
    """
    try:
        item = delete_tracker_item(item_id)
        return 200, item
    except Exception as e:
        import sys
        import traceback
        if "404" in str(e) or "not found" in str(e).lower():
            return 404, {"error": "404 Item not found"}
        print(f"[tracker.delete] Uncaught exception: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 500, {"error": "Internal server error"}
