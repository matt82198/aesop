"""state_store.ingest — backfill events from an existing tracker.json.

Migration path for the DB-source-of-truth cutover: read the current
tracker.json and emit one ``item_created`` event per item (full payload) so the
event store reproduces the current state. History is not reconstructed (only
final state exists on disk), but the projection round-trips to the same items,
so an export re-renders the identical tracker.json.
"""
from __future__ import annotations

import json


def ingest_tracker_json(api, tracker_json_path: str, actor: str = "migration") -> int:
    """Append one ``item_created`` per item in ``tracker_json_path``.

    Returns the number of items ingested.
    Validates that each item is a dict; rejects malformed events with a clear error.
    """
    with open(tracker_json_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    # Two on-disk shapes are legitimate (wave_scheduler.load_tracker_items accepts both):
    # the canonical {"items": [...]} and the pre-migration bare list [...].
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("items", [])
    else:
        raise TypeError(
            f"tracker.json root must be a dict or list, got {type(data).__name__}"
        )

    if not isinstance(items, list):
        raise TypeError(
            f"tracker.json 'items' field must be a list, got {type(items).__name__}"
        )

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise TypeError(
                f"tracker.json item at index {i} must be a dict, got {type(item).__name__}"
            )
        api.append("tracker", "item_created", item, actor)

    return len(items)
