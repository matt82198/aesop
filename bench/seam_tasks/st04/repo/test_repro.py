"""Visible reproduction test for pagination helper."""
import pytest

from paginator import get_pagination_params


class TestPaginatorRepro:
    """Visible test: user parameters take precedence over defaults."""

    def test_user_page_size_overrides_default(self):
        """User-provided page_size takes precedence over default."""
        user_params = {"page_size": 50}
        result = get_pagination_params(user_params)

        assert result["page_size"] == 50
        assert result["page"] == 1

    def test_user_page_overrides_default(self):
        """User-provided page takes precedence over default."""
        user_params = {"page": 5}
        result = get_pagination_params(user_params)

        assert result["page"] == 5
        assert result["page_size"] == 10
