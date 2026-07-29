"""Oracle tests for rate limiter boundary conditions."""
import time
from unittest.mock import patch
import pytest

from rate_limiter import RateLimiter


class TestRateLimiterBoundary:
    """Tests for rate limiter boundary conditions."""

    def test_request_at_exact_boundary_allowed(self):
        """At exactly window_duration seconds, new requests should be allowed."""
        limiter = RateLimiter(max_requests=2, window_duration=10)

        # Simulate time progression
        with patch('rate_limiter.time') as mock_time:
            # First request at T=0
            mock_time.time.return_value = 0
            assert limiter.allow() is True

            # Second request at T=5 (within window)
            mock_time.time.return_value = 5
            assert limiter.allow() is True

            # Third request at T=10 (exactly at boundary)
            # First request should be considered outside window now
            mock_time.time.return_value = 10
            assert limiter.allow() is True  # Should be allowed

    def test_request_after_boundary_allowed(self):
        """After window_duration seconds, old requests should be cleared."""
        limiter = RateLimiter(max_requests=1, window_duration=10)

        with patch('rate_limiter.time') as mock_time:
            # Request at T=0
            mock_time.time.return_value = 0
            assert limiter.allow() is True

            # Request at T=10.1 (after boundary)
            mock_time.time.return_value = 10.1
            assert limiter.allow() is True  # Old request expired

    def test_normal_rate_limit_within_window(self):
        """Within the window, rate limit should be enforced."""
        limiter = RateLimiter(max_requests=2, window_duration=10)

        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 0
            assert limiter.allow() is True
            assert limiter.allow() is True
            assert limiter.allow() is False  # Over limit

    def test_reset_clears_history(self):
        """Reset should clear all request history."""
        limiter = RateLimiter(max_requests=1, window_duration=10)

        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 0
            assert limiter.allow() is True
            assert limiter.allow() is False  # Over limit

            # After reset
            limiter.reset()
            assert limiter.allow() is True  # Should be allowed again
