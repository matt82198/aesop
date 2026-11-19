"""In-memory record store."""

_DATA = {}


def save_record(key, value):
    """Store a value under key."""
    _DATA[key] = value


def get_record(key):
    """Return the value for key, or None."""
    return _DATA.get(key)
