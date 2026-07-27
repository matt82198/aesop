"""Oracle tests for JSON parser."""
import pytest

from json_parser import parse_json_safely, decode_json_with_default


class TestJsonParser:
    """Tests for JSON parser exception handling."""

    def test_valid_json_returns_parsed_object(self):
        """Valid JSON should be parsed and returned."""
        result = parse_json_safely('{"key": "value"}')
        assert result == {"key": "value"}

    def test_invalid_json_returns_fallback(self):
        """Invalid JSON should return fallback value, not crash."""
        result = parse_json_safely("not valid json")
        # Should return the fallback (empty dict by default), not crash
        assert result == {}

    def test_invalid_json_with_custom_fallback(self):
        """Invalid JSON should return custom fallback value."""
        fallback = {"error": "parse failed"}
        result = parse_json_safely("invalid json", fallback=fallback)
        assert result == fallback

    def test_empty_string_returns_fallback(self):
        """Empty string is invalid JSON, should return fallback."""
        result = parse_json_safely("")
        assert result == {}

    def test_malformed_json_returns_fallback(self):
        """Malformed JSON (missing quotes, syntax errors) returns fallback."""
        result = parse_json_safely('{"key": value}')  # value not quoted
        assert result == {}

    def test_decode_valid_json(self):
        """decode_json_with_default should parse valid JSON."""
        result = decode_json_with_default('["a", "b", "c"]')
        assert result == ["a", "b", "c"]

    def test_decode_invalid_json_returns_default(self):
        """decode_json_with_default should return default on parse error."""
        result = decode_json_with_default("not json")
        assert result == {}

    def test_decode_custom_default(self):
        """decode_json_with_default should use provided default on error."""
        default = {"status": "error"}
        result = decode_json_with_default("invalid", default_value=default)
        assert result == default

    def test_json_with_numbers(self):
        """Valid JSON with numbers should be parsed correctly."""
        result = parse_json_safely('{"count": 42}')
        assert result["count"] == 42

    def test_json_with_nested_objects(self):
        """Valid JSON with nested objects should be parsed correctly."""
        result = parse_json_safely('{"outer": {"inner": true}}')
        assert result["outer"]["inner"] is True
