"""Interval merger implementation."""


def merge_intervals(intervals):
    """
    Merge overlapping or adjacent intervals.

    Takes a list of (start, end) tuples and returns a new list where
    overlapping or adjacent intervals have been merged into single spans.
    Intervals are sorted by start time before merging.

    Args:
        intervals: List of (start, end) tuples representing time intervals.

    Returns:
        List of merged (start, end) tuples, sorted by start time.
    """
    if not intervals:
        return []

    # Sort intervals by start time
    sorted_intervals = sorted(intervals)

    merged = [sorted_intervals[0]]

    for current_start, current_end in sorted_intervals[1:]:
        last_start, last_end = merged[-1]

        # BUG: Using < instead of <= causes touching intervals not to merge
        # When one interval ends exactly where another starts (e.g., [1, 3] and [3, 5]),
        # the condition "current_start < last_end" is False (3 < 3), so they aren't merged
        if current_start < last_end:
            # Merge intervals by extending the end time
            merged[-1] = (last_start, max(last_end, current_end))
        else:
            # No overlap or adjacency, add as new interval
            merged.append((current_start, current_end))

    return merged
