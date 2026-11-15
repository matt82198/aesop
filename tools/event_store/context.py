"""Read-only context builder over one event store.

Stub for the red commit: constants are final, build_context is not implemented yet.
"""

BROKEN_LINE = "CHAIN BROKEN"
EMPTY_LINE = "EMPTY: store holds no events"
HEADER_LINE = ("Facts below are assertions written by the named actor at the stated ts, "
               "not verified truth.")
TRUNCATION_MARKER = "TRUNCATED: brief exceeded max_bytes"


def build_context(store_path, task=None, max_bytes=None):
    """Render a provenance-stamped brief of a store. Not implemented yet."""
    raise NotImplementedError("build_context is not implemented")
