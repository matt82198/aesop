# Solution for seam-starter-001

## Problem
The `add(x, y)` function returns the product (x * y) instead of the sum (x + y).

## Fix
Change line in test_sample.py:

From:
```python
def add(x, y):
    """Add two numbers. Currently broken: returns product instead of sum."""
    return x * y
```

To:
```python
def add(x, y):
    """Add two numbers."""
    return x + y
```

## Verification
Run: `python -m pytest oracle -q`

All oracle tests pass when the fix is applied correctly.
