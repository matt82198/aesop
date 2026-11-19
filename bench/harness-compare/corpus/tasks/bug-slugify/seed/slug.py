"""URL slug helper."""

import re


def slugify(text):
    """Return a lowercase, dash-separated slug for ``text``."""
    lowered = text.lower()
    dashed = re.sub(r"[^a-z]+", "-", lowered)
    return dashed.strip("-")
