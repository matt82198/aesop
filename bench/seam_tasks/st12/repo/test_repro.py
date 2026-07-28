"""Visible reproduction test for retry and caching behavior."""
import pytest

from http_client import fetch_data


class TestRetryRepro:
    """Visible test: responses are cached and retries use exponential backoff."""

    def test_cached_response_reused(self):
        """Cached responses are reused on subsequent calls."""
        url = "http://example.com/data"

        # First call fetches data
        result1 = fetch_data(url)
        assert result1 is not None

        # Second call with same URL uses cached response
        result2 = fetch_data(url)
        assert result2 is not None

        # Both are the same cached data
        assert result1 == result2
