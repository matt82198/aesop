"""Simulates slow storage/database access."""

import time


# Simulated database
_USER_SETTINGS = {
    1: {"name": "Alice", "theme": "dark", "notifications": True},
    2: {"name": "Bob", "theme": "light", "notifications": False},
    3: {"name": "Charlie", "theme": "dark", "notifications": True},
}


def fetch_settings(user_id):
    """
    Fetch user settings from storage (simulated slow operation).

    Args:
        user_id: The user ID to fetch settings for

    Returns:
        dict with user settings (a new copy), or None if not found
    """
    # Simulate slow I/O (50ms)
    time.sleep(0.05)

    settings = _USER_SETTINGS.get(user_id)
    # Return a copy of the settings dict (simulating fresh data from storage)
    return dict(settings) if settings else None
