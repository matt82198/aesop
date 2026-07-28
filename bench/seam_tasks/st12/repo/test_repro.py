"""Visible reproduction test for retry-cache interaction bug."""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from http_client import HTTPClient


class MockHTTPClient(HTTPClient):
    """Mock client for testing."""

    def __init__(self, base_url="http://test.local", cache_ttl=60):
        super().__init__(base_url, cache_ttl)
        self.request_count = 0
        self.fail_until = 0

    def _make_request(self, path):
        """Mock implementation that can simulate failures."""
        self.request_count += 1
        if self.request_count <= self.fail_until:
            raise TimeoutError(f"Request {self.request_count} timed out")
        return f"Success: {path}"


def test_error_not_cached_allows_recovery():
    """Test that errors are not cached, allowing recovery after server becomes healthy."""
    client = MockHTTPClient()
    client.retry_policy.max_retries = 0

    client.fail_until = 1

    try:
        response = client.get("/api/data")
        pytest.fail("First call should have raised TimeoutError")
    except TimeoutError:
        pass

    client.fail_until = 0

    try:
        response = client.get("/api/data")
        assert response == "Success: /api/data", \
            f"After server recovery, should get success, not cached error"
    except TimeoutError:
        pytest.fail(
            "Defect: error response was cached, preventing recovery after server becomes healthy"
        )
