"""Interval helpers."""


def merge_intervals(intervals):
    """Merge overlapping or touching ``(start, end)`` pairs into sorted tuples."""
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(pair) for pair in merged]
