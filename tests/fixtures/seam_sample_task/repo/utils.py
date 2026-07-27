def validate(n):
    """Validate that n is non-negative."""
    if n < 0:
        raise ValueError("n must be >= 0")
    return True


def is_empty(items):
    """Check if a collection is empty."""
    return len(items) == 0
