"""Settings service with caching layer."""

from cache import Cache
from storage import fetch_settings

_cache = Cache()


def get_settings(user_id):
    """
    Get user settings, using cache if available.

    Args:
        user_id: The user ID to fetch settings for

    Returns:
        dict with user settings, or None if not found
    """
    # DEFECT: When checking cache, uses user_id directly (int),
    # but cache stores with string key f"{user_id}".
    # This means the cache key format doesn't match between store and retrieve operations.

    # Check cache with wrong key format (int instead of string)
    cached = _cache.get(user_id)
    if cached is not None:
        return cached

    # Fetch from storage
    settings = fetch_settings(user_id)

    # Store in cache with string key format
    if settings is not None:
        _cache.set(f"{user_id}", settings)

    return settings


def clear_cache():
    """Clear the cache (for testing)."""
    _cache._data.clear()
