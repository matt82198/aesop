"""Oracle tests for paginator."""
import pytest

from paginator import get_pagination_params, apply_pagination_defaults


class TestPaginator:
    """Tests for pagination parameter merging."""

    def test_user_page_size_respected(self):
        """User-provided page_size should override the default."""
        user_params = {"page": 2, "page_size": 50}
        result = get_pagination_params(user_params)
        # User provided page_size=50, should be 50 not default 10
        assert result["page_size"] == 50
        assert result["page"] == 2

    def test_user_sort_by_respected(self):
        """User-provided sort_by should override the default."""
        user_params = {"sort_by": "updated"}
        result = get_pagination_params(user_params)
        # User provided sort_by, should be "updated" not default "created"
        assert result["sort_by"] == "updated"

    def test_partial_user_params_merged_with_defaults(self):
        """Partial user params should be merged with defaults."""
        user_params = {"page": 3}
        result = get_pagination_params(user_params)
        # User provided page, should be 3
        assert result["page"] == 3
        # Missing page_size should use default
        assert result["page_size"] == 10
        # Missing sort_by should use default
        assert result["sort_by"] == "created"

    def test_request_limit_respected(self):
        """User-provided limit should override default."""
        request_params = {"limit": 50, "offset": 10}
        result = apply_pagination_defaults(request_params)
        # User provided limit=50, should be 50 not default 20
        assert result["limit"] == 50
        assert result["offset"] == 10

    def test_defaults_applied_to_empty_params(self):
        """Defaults should be applied when no user params provided."""
        user_params = {}
        result = get_pagination_params(user_params)
        assert result["page"] == 1
        assert result["page_size"] == 10
        assert result["sort_by"] == "created"

    def test_all_user_params_respected(self):
        """All user-provided params should be respected."""
        user_params = {"page": 5, "page_size": 25, "sort_by": "name"}
        result = get_pagination_params(user_params)
        assert result["page"] == 5
        assert result["page_size"] == 25
        assert result["sort_by"] == "name"

    def test_request_offset_respected(self):
        """User-provided offset should override default."""
        request_params = {"offset": 100}
        result = apply_pagination_defaults(request_params)
        assert result["offset"] == 100
        # Missing limit should use default
        assert result["limit"] == 20
