"""Oracle tests for interval merger."""
import pytest

from interval_merger import merge_intervals


class TestIntervalMerger:
    """Tests for interval merger edge cases."""

    def test_adjacent_intervals_merged(self):
        """Intervals that touch (one ends where another starts) should be merged."""
        # [1, 3] and [3, 5] are adjacent and should become [1, 5]
        intervals = [(1, 3), (3, 5)]
        result = merge_intervals(intervals)
        assert result == [(1, 5)]

    def test_multiple_adjacent_intervals(self):
        """Multiple adjacent intervals should all merge into one."""
        intervals = [(1, 2), (2, 3), (3, 4), (4, 5)]
        result = merge_intervals(intervals)
        assert result == [(1, 5)]

    def test_overlapping_intervals_merged(self):
        """Overlapping intervals should be merged."""
        intervals = [(1, 4), (2, 6)]
        result = merge_intervals(intervals)
        assert result == [(1, 6)]

    def test_non_overlapping_intervals_separate(self):
        """Non-overlapping intervals should remain separate."""
        intervals = [(1, 2), (4, 5), (7, 8)]
        result = merge_intervals(intervals)
        assert result == [(1, 2), (4, 5), (7, 8)]

    def test_empty_list(self):
        """Empty input should return empty output."""
        assert merge_intervals([]) == []

    def test_single_interval(self):
        """Single interval should be returned as-is."""
        assert merge_intervals([(1, 5)]) == [(1, 5)]

    def test_unsorted_input_sorted_in_output(self):
        """Unsorted input should be sorted in output."""
        intervals = [(5, 7), (1, 3)]
        result = merge_intervals(intervals)
        assert result == [(1, 3), (5, 7)]

    def test_complex_mix_of_overlaps_and_gaps(self):
        """Complex mix of overlapping and non-overlapping intervals."""
        # (1,3) and (2,5) overlap -> (1,5)
        # (5,6) and (5,8) are adjacent and overlap -> (5,8)
        # (10,12) is separate
        intervals = [(1, 3), (2, 5), (5, 8), (10, 12)]
        result = merge_intervals(intervals)
        assert result == [(1, 8), (10, 12)]
