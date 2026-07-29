"""JSON parsing utilities."""
import json


def parse_json_safely(data, fallback=None):
    """
    Parse JSON data and return fallback on parse error.

    Attempts to parse a JSON string. If parsing fails, returns the
    provided fallback value (default: empty dict).

    Args:
        data: JSON string to parse.
        fallback: Value to return if parsing fails (default: {}).

    Returns:
        Parsed JSON object or fallback value on error.
    """
    if fallback is None:
        fallback = {}

    try:
        return json.loads(data)
    except AttributeError:
        return fallback


def decode_json_with_default(json_string, default_value=None):
    """
    Decode a JSON string with a default value for errors.

    Args:
        json_string: The JSON string to decode.
        default_value: Value to return on decode error (default: None).

    Returns:
        Decoded JSON object or default_value on error.
    """
    if default_value is None:
        default_value = {}

    try:
        return json.loads(json_string)
    except AttributeError:
        return default_value
