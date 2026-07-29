"""Processes items with limit constraints."""

from validator import validate_and_filter
from config import AVAILABLE_ITEMS


def process_items(item_limit):
    """
    Process items from the available pool with the given limit.

    Args:
        item_limit: Maximum number of items to process (or None for all)

    Returns:
        List of processed items, capped at item_limit
    """
    items = list(range(1, AVAILABLE_ITEMS + 1))

    # Calls validator with the limit, expecting it to be respected
    result = validate_and_filter(items, item_limit)

    return result
