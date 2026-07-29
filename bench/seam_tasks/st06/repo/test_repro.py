"""Visible reproduction test for settings cache."""
import pytest

from settings_service import get_settings, clear_cache


class TestCachingRepro:
    """Visible test: cache returns the same object on repeated calls."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_cache()

    def test_cache_returns_same_object(self):
        """Repeated calls for the same user_id must return the exact same cached object."""
        first_call = get_settings(1)
        second_call = get_settings(1)

        # Both calls must return the identical object (same identity in memory)
        assert first_call is second_call
        assert id(first_call) == id(second_call)
