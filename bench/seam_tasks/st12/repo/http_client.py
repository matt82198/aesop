"""HTTP client with caching and retry."""
from cache import ResponseCache
from retry_policy import RetryPolicy


class HTTPClient:
    """HTTP client with built-in caching and retry logic."""

    def __init__(self, base_url, cache_ttl=60):
        """Initialize client with cache and retry policy."""
        self.base_url = base_url
        self.cache = ResponseCache(ttl_seconds=cache_ttl)
        self.retry_policy = RetryPolicy(max_retries=2, base_delay=0.01)

    def _make_request(self, path):
        """
        Internal method to actually make the HTTP request.
        Raises an exception on failure (timeout, connection error, etc.).
        """
        # Simulate making a request
        # In the real system this would call requests.get() or similar
        url = f"{self.base_url}{path}"

        # This is where we'd actually make the HTTP call
        # For testing, this will be mocked/overridden
        raise NotImplementedError("Subclass must implement _make_request")

    def get(self, path):
        """Get a resource with caching and retry."""
        cache_key = f"GET:{path}"

        cached_response = self.cache.get(cache_key)
        if cached_response is not None:
            if isinstance(cached_response, Exception):
                raise cached_response
            return cached_response

        def request_func():
            response = self._make_request(path)
            self.cache.set(cache_key, response)
            return response

        success, result = self.retry_policy.execute_with_retry(request_func)

        if not success:
            raise result

        return result

    def clear_cache(self):
        """Clear the response cache."""
        self.cache.clear()
