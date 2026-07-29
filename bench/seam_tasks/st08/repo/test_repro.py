"""Visible reproduction test for plugin registration."""
import pytest

from initializer import initialize
import event_registry


class TestPluginRepro:
    """Visible test: both plugins are registered during initialization."""

    def test_handlers_registered_on_init(self):
        """After initialization, event handlers from both plugins are registered."""
        initialize()

        # Both plugin handlers must be registered
        handler_count = event_registry.get_handlers("data_event")
        assert handler_count >= 2, f"Expected at least 2 handlers, got {handler_count}"
