"""Plugin B: Registers another handler at import time."""

# This import MUST happen after event_registry is set up in the initializer
# If this runs before event_registry import, the register function won't exist yet
from event_registry import register


def handle_data_event(data):
    """Handle a data event."""
    return f"plugin_b_handled: {data}"


# Register handler at module import time
register("data_event", handle_data_event)
