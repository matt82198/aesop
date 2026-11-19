"""Tiny CSV helpers."""

import csv
import io


def csv_row(line):
    """Return the fields of one CSV line, honouring double-quote quoting."""
    return next(csv.reader(io.StringIO(line)))
