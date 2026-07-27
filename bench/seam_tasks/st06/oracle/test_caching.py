"""Test suite for cache effectiveness."""

import sys
import os

# Add repo to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'repo'))

from settings_service import get_settings, clear_cache


class TestCacheBehavior:
    """Tests for proper cache operation across identical lookups."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_cache()

    def test_first_call_returns_settings(self):
        """First call should return the settings."""
        settings = get_settings(1)
        assert settings is not None, "First call should return settings"
        assert settings["name"] == "Alice"

    def test_cache_hit_returns_same_object(self):
        """Second call for same user_id should return the cached object (same identity)."""
        first_call = get_settings(1)
        second_call = get_settings(1)

        assert first_call is second_call, "Cache should return the same object instance"
        assert id(first_call) == id(second_call), "Object identities should match"

    def test_cache_hit_different_user(self):
        """Caching should work for multiple different users."""
        user1_first = get_settings(1)
        user2_first = get_settings(2)
        user1_second = get_settings(1)
        user2_second = get_settings(2)

        assert user1_first is user1_second, "User 1 should be cached"
        assert user2_first is user2_second, "User 2 should be cached"
        assert user1_first is not user2_first, "Different users should have different objects"

    def test_cache_hit_user_three(self):
        """Caching should work for user ID 3."""
        settings1 = get_settings(3)
        settings2 = get_settings(3)

        assert settings1 is settings2, "User 3 settings should be cached"
        assert settings1["name"] == "Charlie"

    def test_multiple_users_all_cached(self):
        """All users should be properly cached on second access."""
        # First access
        u1_a = get_settings(1)
        u2_a = get_settings(2)
        u3_a = get_settings(3)

        # Second access (should all hit cache)
        u1_b = get_settings(1)
        u2_b = get_settings(2)
        u3_b = get_settings(3)

        assert u1_a is u1_b, "User 1 cache miss"
        assert u2_a is u2_b, "User 2 cache miss"
        assert u3_a is u3_b, "User 3 cache miss"
