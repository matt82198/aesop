"""Pagination helper. Pages are numbered from 1."""


def paginate(items, page, per_page):
    """Return the items on ``page`` (1-based), or an empty list if none."""
    if page < 1:
        return []
    start = (page - 1) * per_page
    end = start + per_page - 1
    return items[start:end]
