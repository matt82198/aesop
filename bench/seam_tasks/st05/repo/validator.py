"""Validates and filters item lists based on constraints."""

from config import DEFAULT_MAX_ITEMS


def validate_and_filter(items, max_items):
    """
    Validate items and apply max limit.

    Args:
        items: List of items to validate
        max_items: Maximum number of items to return (or None for no limit)

    Returns:
        Filtered list of items
    """
    if not items:
        return []

    # DEFECT: Uses DEFAULT_MAX_ITEMS instead of the max_items parameter
    # when max_items is not None. This ignores the caller's explicit limit.
    if max_items is None:
        return items

    limit = DEFAULT_MAX_ITEMS
    return items[:limit]
