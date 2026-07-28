"""Visible reproduction test for processor item limit."""
import pytest

from processor import process_items


class TestProcessorRepro:
    """Visible test: item_limit parameter is applied correctly."""

    def test_item_limit_applied(self):
        """Different item_limit values produce different output sizes."""
        # Get all items (no limit)
        all_items = process_items(None)
        total = len(all_items)

        # Get items with limit=5
        limited_items = process_items(5)

        # With limit=5, output must be 5 items, not the default or total
        assert len(limited_items) == 5
        assert len(limited_items) < total or total == 5

    def test_item_limit_large_value(self):
        """Large item_limit still applies (e.g., limit=100 with 25 available)."""
        result = process_items(100)
        all_items = process_items(None)

        # Result should be all available items, capped by actual pool size
        assert len(result) <= len(all_items)
