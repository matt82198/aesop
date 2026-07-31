"""state_store.export — thin alias to materialize for backward compatibility.

DEPRECATED (Inc 1): use materialize.materialize_tracker directly.

This module is kept for backward compatibility during the state-consolidation
transition. All new code should import from state_store.materialize.

The canonical materializer is now state_store.materialize, which is the
single place all callers use to render views.
"""
from __future__ import annotations

from state_store.materialize import materialize_tracker


def export_tracker(api, out_path: str) -> None:
    """DEPRECATED: Write the tracker projection to ``out_path`` as pretty JSON.

    This is a thin wrapper around materialize_tracker for backward compatibility.
    New code should call materialize_tracker directly.

    Args:
        api: StateAPI instance
        out_path: Path to write the JSON file
    """
    projection = api.project("tracker")
    content = materialize_tracker(projection)
    with open(out_path, "wb") as fh:
        fh.write(content)
