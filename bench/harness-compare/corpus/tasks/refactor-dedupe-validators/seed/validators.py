"""Input validators."""


def is_zip(value):
    """Five ASCII digits."""
    if len(value) != 5:
        return False
    for ch in value:
        if not (ch.isascii() and ch.isdigit()):
            return False
    return True


def is_pin(value):
    """Four ASCII digits."""
    if len(value) != 4:
        return False
    for ch in value:
        if not (ch.isascii() and ch.isdigit()):
            return False
    return True


def is_year(value):
    """Four ASCII digits."""
    if len(value) != 4:
        return False
    for ch in value:
        if not (ch.isascii() and ch.isdigit()):
            return False
    return True
