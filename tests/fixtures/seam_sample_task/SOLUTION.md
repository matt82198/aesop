# Solution: Off-by-one Error in count()

## Problem
The `count()` function in `src/main.py` has an off-by-one error. It adds 1 to the length of the input list, resulting in incorrect counts.

## Fix
Change line 2 in `src/main.py` from:
```python
    return len(items) + 1  # BUG: off-by-one error
```

To:
```python
    return len(items)
```

## Verification
After applying the fix, the oracle tests should pass.
