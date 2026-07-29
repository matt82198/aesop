"""Response caching layer."""
import time


class ResponseCache:
    """Cache for HTTP responses."""

    def __init__(self, ttl_seconds=60):
        """Initialize cache with time-to-live."""
        self.ttl = ttl_seconds
        self.cache = {}

    def get(self, key):
        """Get a cached response if it exists and hasn't expired."""
        if key not in self.cache:
            return None

        value, timestamp = self.cache[key]
        if time.time() - timestamp > self.ttl:
            del self.cache[key]
            return None

        return value

    def set(self, key, value):
        """Cache a response."""
        self.cache[key] = (value, time.time())

    def clear(self):
        """Clear all cached responses."""
        self.cache.clear()
