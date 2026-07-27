"""Test suite for processor limit behavior."""

import sys
import os

# Add repo to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'repo'))

from processor import process_items


class TestItemLimit:
    """Tests for item limit behavior across different limit values."""

    def test_large_limit_respected(self):
        """Limit of 100 should return 100 items."""
        result = process_items(100)
        assert len(result) == 25, f"Expected 25 items with limit=100, got {len(result)}"

    def test_small_limit_respected(self):
        """Limit of 5 should return 5 items, not 10."""
        result = process_items(5)
        assert len(result) == 5, f"Expected 5 items with limit=5, got {len(result)}"

    def test_limit_three(self):
        """Limit of 3 should return exactly 3 items."""
        result = process_items(3)
        assert len(result) == 3, f"Expected 3 items with limit=3, got {len(result)}"

    def test_none_limit_returns_all(self):
        """No limit (None) should return all 25 items."""
        result = process_items(None)
        assert len(result) == 25, f"Expected 25 items with limit=None, got {len(result)}"

    def test_results_are_correct_items(self):
        """Results should contain the correct item values."""
        result = process_items(7)
        expected = list(range(1, 8))
        assert result == expected, f"Expected {expected}, got {result}"

    def test_limit_zero(self):
        """Limit of 0 should return empty list."""
        result = process_items(0)
        assert len(result) == 0, f"Expected 0 items with limit=0, got {len(result)}"
