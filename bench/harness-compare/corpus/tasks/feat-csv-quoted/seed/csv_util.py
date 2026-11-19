"""Tiny CSV helpers."""


def csv_row(line):
    """Return the fields of one CSV line. Quoted fields are not handled yet."""
    return line.split(",")
