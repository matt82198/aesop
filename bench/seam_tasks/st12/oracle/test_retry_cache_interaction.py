"""Oracle tests for retry-backoff and cache interaction bug."""
import pytest
import sys
import os

# Add repo to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "repo"))

from http_client import HTTPClient
from cache import ResponseCache


class MockHTTPClient(HTTPClient):
    """Mock client for testing."""

    def __init__(self, base_url="http://test.local", cache_ttl=60):
        super().__init__(base_url, cache_ttl)
        self.request_count = 0
        self.fail_until = 0  # Fail the next fail_until requests

    def _make_request(self, path):
        """Mock implementation that can simulate failures."""
        self.request_count += 1
        if self.request_count <= self.fail_until:
            raise TimeoutError(f"Request {self.request_count} timed out")
        return f"Success: {path}"


def test_successful_request():
    """Test that successful requests are cached."""
    client = MockHTTPClient()
    client.fail_until = 0  # No failures

    response = client.get("/api/data")
    assert response == "Success: /api/data"
    assert client.request_count == 1

    # Second request should hit cache
    response = client.get("/api/data")
    assert response == "Success: /api/data"
    assert client.request_count == 1  # No new request


def test_retry_on_transient_failure():
    """
    Test that transient failures are retried.
    The retry should not hit the error cache.
    """
    client = MockHTTPClient()
    client.fail_until = 1  # First request fails, second succeeds

    response = client.get("/api/data")
    assert response == "Success: /api/data"
    # Should have retried after first failure
    assert client.request_count == 2  # One failed, one succeeded


def test_cache_poisoning_bug():
    """
    Test that demonstrates the cache-retry interaction bug:
    When a request fails, the error gets cached.
    Subsequent calls hit the cache and get the cached error without retrying.

    On fixed code, errors are NOT cached, so recovery is possible.
    On defective code, errors ARE cached, so recovery is blocked.
    """
    client = MockHTTPClient()
    client.retry_policy.max_retries = 0  # No retries - fail immediately
    client.fail_until = 1  # First request fails

    # First call fails
    try:
        response = client.get("/api/data")
        assert False, "First call should have raised TimeoutError"
    except TimeoutError:
        pass  # Expected: request failed and error was cached

    # Now fix the server so requests succeed
    client.fail_until = 0

    # Second call: the defective code has cached the error, so it will fail
    # even though the server is now healthy.
    # The fixed code does NOT cache errors, so the second call should succeed.
    try:
        response = client.get("/api/data")
        # Success: the error was not cached, allowing recovery
        assert response == "Success: /api/data"
        # If we get here without exception, the fix is working
    except TimeoutError:
        # The defective code cached the error and raised it without retrying
        # This means the bug is present and the test fails
        pytest.fail(
            "Defect detected: error response was cached, preventing recovery after server becomes healthy"
        )


def test_multiple_failures_then_recovery():
    """
    Test the full scenario: fail multiple times, then server recovers.
    With the bug, clients continue to fail even after recovery.
    """
    client = MockHTTPClient(cache_ttl=10)
    client.fail_until = 2  # First two requests fail

    # Try to get data while server is down
    response = client.get("/api/status")
    assert response == "Success: /api/status"
    assert client.request_count == 3  # 2 failed + 1 succeeded

    # Server was temporarily down, now it's back up
    client.fail_until = 0

    # With the bug: the last error is cached, so next call fails without retry
    try:
        response = client.get("/api/status")
        assert response == "Success: /api/status"
    except TimeoutError:
        pytest.fail(
            "Cache poisoning bug: error cached even after server recovers"
        )


def test_error_not_cacheable():
    """Test that errors should not be cached at all."""
    client = MockHTTPClient()
    client.fail_until = 1  # Fail once

    # First call should succeed after retry
    response = client.get("/api/data")
    assert response == "Success: /api/data"

    # Check that error is NOT in cache
    cache_key = "GET:/api/data"
    cached = client.cache.get(cache_key)

    # Should only have the successful response, not the error
    assert cached == "Success: /api/data"
    assert not isinstance(cached, Exception), "Error should not be cached"


def test_different_paths_independent():
    """Test that different API paths have independent caches."""
    client = MockHTTPClient()
    client.fail_until = 0

    response1 = client.get("/api/users")
    response2 = client.get("/api/posts")

    assert response1 == "Success: /api/users"
    assert response2 == "Success: /api/posts"
    assert client.request_count == 2

    # Each path should have its own cache entry
    response1_again = client.get("/api/users")
    response2_again = client.get("/api/posts")

    assert client.request_count == 2  # No new requests (cache hit)
