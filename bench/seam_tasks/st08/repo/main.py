"""Main entry point for the plugin system."""

from initializer import initialize
import event_registry


def main():
    """Run the plugin system."""
    initialize()

    # Send a data event
    results = event_registry.dispatch("data_event", "test_data")

    # Get count of registered handlers
    handler_count = event_registry.get_handlers("data_event")

    return {
        "handler_count": handler_count,
        "results": results,
    }
