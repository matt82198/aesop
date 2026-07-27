"""Main entry point for the processor service."""

from processor import process_items


def main():
    """Run the processor with various limits."""
    result_100 = process_items(100)
    result_5 = process_items(5)
    result_none = process_items(None)

    return {
        "limit_100": result_100,
        "limit_5": result_5,
        "limit_none": result_none,
    }
