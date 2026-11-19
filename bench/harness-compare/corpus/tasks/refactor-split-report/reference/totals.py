"""Row totals."""


def total(rows):
    """Sum the cents field of each row."""
    return sum(row["cents"] for row in rows)
