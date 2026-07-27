"""Rate limiter implementation."""
import time


class RateLimiter:
    """Allows a maximum of N requests per window_duration seconds."""

    def __init__(self, max_requests, window_duration):
        """
        Args:
            max_requests: Maximum number of requests allowed per window.
            window_duration: Time window in seconds.
        """
        self.max_requests = max_requests
        self.window_duration = window_duration
        self.request_times = []

    def allow(self):
        """
        Check if a request should be allowed.
        Removes requests older than the window and checks if we're under the limit.
        Returns True if the request is allowed, False otherwise.
        """
        now = time.time()

        # Remove requests outside the window
        self.request_times = [
            req_time
            for req_time in self.request_times
            if now - req_time <= self.window_duration
        ]

        # Check if we can allow this request
        if len(self.request_times) < self.max_requests:
            self.request_times.append(now)
            return True

        return False

    def reset(self):
        """Clear all request history."""
        self.request_times = []
