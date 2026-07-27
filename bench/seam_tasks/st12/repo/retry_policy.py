"""Retry policy with exponential backoff."""
import time


class RetryPolicy:
    """Implements exponential backoff retry logic."""

    def __init__(self, max_retries=3, base_delay=0.1):
        """Initialize retry policy."""
        self.max_retries = max_retries
        self.base_delay = base_delay

    def execute_with_retry(self, request_func):
        """
        Execute a request function with retries.
        Returns (success, response).
        """
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                response = request_func()
                return True, response
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    time.sleep(delay)

        return False, last_error

    def is_retriable_error(self, error):
        """Check if an error is transient and retriable."""
        # Transient errors (timeout, connection reset, etc.)
        return isinstance(
            error, (TimeoutError, ConnectionError, IOError)
        )
