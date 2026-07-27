"""Data processing module."""
from validator import validate_record
from logger import get_logger

log = get_logger("processor")


def process_records(records):
    """
    Process a list of records, validating and storing valid ones.
    Returns count of valid records.
    """
    valid_count = 0
    invalid_records = []

    for record in records:
        if validate_record(record):
            valid_count += 1
        else:
            # Record was invalid - validator has already logged it
            invalid_records.append(record)

    return valid_count, invalid_records


def process_with_reporting(records):
    """
    Process records and report on the results.
    In production, if logger suppresses WARNING, validation failures won't appear.
    """
    valid_count, invalid_records = process_records(records)

    if invalid_records:
        # This should alert operators of problems
        log.warning(f"Failed to process {len(invalid_records)} records")

    return valid_count
