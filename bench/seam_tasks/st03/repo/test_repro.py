"""Visible reproduction test for JSON parser."""
import pytest

from json_parser import parse_json_safely


class TestJsonParserRepro:
    """Visible test: invalid JSON is handled gracefully."""

    def test_invalid_json_returns_fallback(self):
        """Parsing invalid JSON returns the fallback dict instead of raising."""
        result = parse_json_safely("not valid json")
        assert result == {}

    def test_valid_json_is_parsed(self):
        """Valid JSON is parsed correctly."""
        result = parse_json_safely('{"key": "value"}')
        assert result == {"key": "value"}
