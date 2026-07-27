"""Cache decorator that memoizes function results."""
from functools import wraps


def cached(func):
    """Decorator that caches the result of a function call."""
    cache = {}

    @wraps(func)
    def wrapper(*args, **kwargs):
        key = (args, tuple(sorted(kwargs.items())))
        if key not in cache:
            cache[key] = func(*args, **kwargs)
        return cache[key]

    wrapper.clear_cache = lambda: cache.clear()
    return wrapper
