"""Input validators."""


def _ascii_digits(text):
    """Return True when text is non-empty and made only of ASCII digits."""
    return bool(text) and text.isascii() and text.isdigit()


def is_zip(value):
    """Five ASCII digits."""
    return len(value) == 5 and _ascii_digits(value)


def is_pin(value):
    """Four ASCII digits."""
    return len(value) == 4 and _ascii_digits(value)


def is_year(value):
    """Four ASCII digits."""
    return len(value) == 4 and _ascii_digits(value)
