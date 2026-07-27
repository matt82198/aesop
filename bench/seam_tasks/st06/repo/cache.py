"""Simple in-memory cache for settings."""


class Cache:
    """In-memory cache for storing and retrieving values by key."""

    def __init__(self):
        self._data = {}

    def get(self, key):
        """Retrieve value from cache by key, or None if not found."""
        return self._data.get(key)

    def set(self, key, value):
        """Store value in cache with the given key."""
        self._data[key] = value

    def has(self, key):
        """Check if key exists in cache."""
        return key in self._data
