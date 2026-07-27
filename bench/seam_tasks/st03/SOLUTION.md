# st03 Solution: Interval Merger Boundary Operator Fix

## Defect Class
Wrong comparison operator in boundary check (off-by-one logic error)

## Files
- `repo/interval_merger.py` (single file, line 28)

## The Fix
**File:** `repo/interval_merger.py`
**Line:** 28
**Change:** `<` to `<=`

```diff
@@ -25,7 +25,7 @@ def merge_intervals(intervals):
         last_start, last_end = merged[-1]

         # BUG: Using < instead of <= causes touching intervals not to merge
         # When one interval ends exactly where another starts (e.g., [1, 3] and [3, 5]),
-        # the condition "current_start < last_end" is False (3 < 3), so they aren't merged
-        if current_start < last_end:
+        # the condition "current_start <= last_end" is True (3 <= 3), so they are merged
+        if current_start <= last_end:
             # Merge intervals by extending the end time
             merged[-1] = (last_start, max(last_end, current_end))
```

## Rationale
The interval merger must merge intervals that either overlap or are adjacent (touch). Two intervals are adjacent when one ends exactly where another starts, e.g., [1, 3] and [3, 5]. The condition `current_start < last_end` fails to detect this case because when `current_start == last_end` (both 3), the comparison is False. Changing to `current_start <= last_end` correctly identifies both overlapping and adjacent intervals for merging. This is a boundary condition bug where an exclusive comparison (`<`) should be inclusive (`<=`).

## Verification Transcript

### Before Fix (Buggy Code): Oracle FAILS
```
cd bench/seam_tasks/st03 && python -m pytest oracle -q

FF.....F                                                                 [100%]
================================== FAILURES ===================================
______________ TestIntervalMerger.test_adjacent_intervals_merged ______________

    def test_adjacent_intervals_merged(self):
        """Intervals that touch (one ends where another starts) should be merged."""
        # [1, 3] and [3, 5] are adjacent and should become [1, 5]
        intervals = [(1, 3), (3, 5)]
        result = merge_intervals(intervals)
>       assert result == [(1, 5)]
E       assert [(1, 3), (3, 5)] == [(1, 5)]

oracle\test_interval_merger.py:15: AssertionError
_____________ TestIntervalMerger.test_multiple_adjacent_intervals _____________

    def test_multiple_adjacent_intervals(self):
        """Multiple adjacent intervals should all merge into one."""
        intervals = [(1, 2), (2, 3), (3, 4), (4, 5)]
        result = merge_intervals(intervals)
>       assert result == [(1, 5)]
E       assert [(1, 2), (2, 3), (3, 4), (4, 5)] == [(1, 5)]

oracle\test_interval_merger.py:21: AssertionError
__________ TestIntervalMerger.test_complex_mix_of_overlaps_and_gaps ___________

    def test_complex_mix_of_overlaps_and_gaps(self):
        """Complex mix of overlapping and non-overlapping intervals."""
        intervals = [(1, 3), (2, 5), (5, 8), (10, 12)]
        result = merge_intervals(intervals)
>       assert result == [(1, 8), (10, 12)]
E       assert [(1, 5), (5, 8), (10, 12)] == [(1, 8), (10, 12)]

oracle\test_interval_merger.py:56: AssertionError
=========================== short test summary info ===========================
FAILED oracle/test_interval_merger.py::TestIntervalMerger::test_adjacent_intervals_merged
FAILED oracle/test_interval_merger.py::TestIntervalMerger::test_multiple_adjacent_intervals
FAILED oracle/test_interval_merger.py::TestIntervalMerger::test_complex_mix_of_overlaps_and_gaps
3 failed, 5 passed in 0.05s
```

### After Fix: Oracle PASSES
```
cd bench/seam_tasks/st03 && python -m pytest oracle -q

........                                                                 [100%]
8 passed in 0.01s
```

## Oracle Tests
- Total count: 8 focused tests
- `test_adjacent_intervals_merged`: Catches the boundary operator bug
- `test_multiple_adjacent_intervals`: Verifies chain of adjacent intervals
- `test_overlapping_intervals_merged`: Verifies overlapping case still works
- `test_non_overlapping_intervals_separate`: Verifies gaps are preserved (happy path)
- `test_empty_list`: Edge case handling (happy path)
- `test_single_interval`: Edge case handling (happy path)
- `test_unsorted_input_sorted_in_output`: Input ordering handled correctly (happy path)
- `test_complex_mix_of_overlaps_and_gaps`: Complex scenario with both types (happy path)
