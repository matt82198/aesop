"""Visible reproduction test for rate limiter boundary condition."""
from unittest.mock import patch
import pytest

from rate_limiter import RateLimiter


class TestRateLimiterBoundaryRepro:
    """Visible test reproducing the observable behavior issue."""

    def test_request_at_window_boundary(self):
        """At exactly window_duration seconds, a new request is allowed."""
        limiter = RateLimiter(max_requests=2, window_duration=10)

        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 0
            assert limiter.allow() is True

            mock_time.time.return_value = 5
            assert limiter.allow() is True

            # At exactly window_duration (10 seconds after first request),
            # the first request expires and room opens for a new one
            mock_time.time.return_value = 10
            assert limiter.allow() is True
