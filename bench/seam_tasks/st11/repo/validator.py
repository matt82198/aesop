"""Data validation module."""
from logger import get_logger

log = get_logger("validator")


def validate_record(record):
    """
    Validate a data record.
    Returns True if valid, False if invalid.
    """
    if not isinstance(record, dict):
        log.error(f"Invalid record type: {type(record)}")
        return False

    if "id" not in record:
        log.error("Record missing required field: id")
        return False

    if "value" not in record:
        log.error("Record missing required field: value")
        return False

    # Additional checks
    if not isinstance(record.get("id"), (str, int)):
        log.error(f"Invalid id type: {type(record.get('id'))}")
        return False

    if not isinstance(record.get("value"), (str, int, float)):
        log.error(f"Invalid value type: {type(record.get('value'))}")
        return False

    return True
