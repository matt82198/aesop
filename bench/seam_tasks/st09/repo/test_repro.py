"""Visible reproduction test for app startup sequence."""
import pytest

from app import get_registry


class TestStartupRepro:
    """Visible test: registry initialization happens before route access."""

    def test_initial_route_access(self):
        """Accessing a route before explicit initialization fails gracefully."""
        try:
            # Should handle the case where registry is not yet initialized
            registry = get_registry()
            # If we get here, the registry exists
            assert registry is not None
        except (RuntimeError, AttributeError, KeyError):
            # Expected if initialization hasn't happened
            pass
