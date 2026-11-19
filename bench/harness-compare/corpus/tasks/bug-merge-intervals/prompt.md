# Fix merge_intervals on unsorted input

The working directory contains `intervals.py` with `merge_intervals(intervals)`. It takes a list of `(start, end)` pairs and returns the merged, non-overlapping intervals as tuples.

It only works when the input is already sorted by start. Callers pass unsorted lists. Fix the function so it handles any order. Do not change its signature.

## Done when

`merge_intervals([(8, 10), (1, 3), (2, 6)])` returns `[(1, 6), (8, 10)]`, `merge_intervals([])` returns `[]`, and `merge_intervals([(1, 4), (4, 5)])` returns `[(1, 5)]`.
