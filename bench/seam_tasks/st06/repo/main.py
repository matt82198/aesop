"""Main entry point for the settings service."""

from settings_service import get_settings, clear_cache


def main():
    """Test the settings service caching."""
    clear_cache()

    # First call should fetch from storage (slow)
    settings1 = get_settings(1)

    # Second call should hit cache (fast)
    settings2 = get_settings(1)

    # Third call with different user should also be slow
    settings3 = get_settings(2)

    # Fourth call should be fast (cached)
    settings4 = get_settings(2)

    return {
        "first_call": settings1,
        "second_call": settings2,
        "third_call": settings3,
        "fourth_call": settings4,
    }
