# ST07: Unit Mismatch at Boundary — Solution

## Defect Class
**Unit conversion formula mismatch at boundary function**

## Hop Structure
- **Hop 0 (Symptom):** main.py calls process_plot(10, 10), receives area_sq_meters = 160.9M instead of 259M
- **Hop 1:** plot_processor.py calls convert_to_square_meters() with the area in square miles
- **Hop 2 (Root cause):** converter.py uses DEFECTIVE_FACTOR (1.609 * 1M) instead of SQUARE_MILES_TO_SQ_METERS (2.589 * 1M)

The defect manifests as wrong output values, but the root cause is the boundary converter using a linear conversion factor (1.609, for miles→km) instead of the correct square conversion factor (1.609² = 2.589, for square miles→square km).

## Reference Fix

In `converter.py`, change line 8-10 from:
```python
# DEFECT: Using linear conversion factor instead of square conversion
# 1.609 is for linear miles->km, but for area (square miles -> sq km), we need 1.609^2 = 2.589
DEFECTIVE_FACTOR = 1.609 * 1_000_000  # This gives 1.609M, not 2.59M
```

To:
```python
SQUARE_MILES_TO_SQ_METERS = 2_589_988
DEFECTIVE_FACTOR = SQUARE_MILES_TO_SQ_METERS
```

And change line 19 from:
```python
return area_square_miles * DEFECTIVE_FACTOR
```

To:
```python
return area_square_miles * SQUARE_MILES_TO_SQ_METERS
```

**Rationale:** The converter module uses a linear conversion factor (1.609 million) derived from the miles-to-kilometers conversion, but this is wrong for area conversion. Converting area requires squaring the linear conversion factor: (1.609)² ≈ 2.589. The defect surfaces 2 hops away in the output values—the caller gets consistently wrong area conversions—but the root cause is the incorrect conversion constant at the boundary layer between area calculation and metric conversion.

## Verification Transcript

### Before Fix
```
oracle\test_area_conversion.py:23: AssertionError: Expected 2589988 sq m, got 1609000.0
oracle\test_area_conversion.py:36: AssertionError: Expected 258998800 sq m, got 160900000.0
oracle\test_area_conversion.py:49: AssertionError: Expected 64749700 sq m, got 40225000.0
oracle\test_area_conversion.py:58: AssertionError: Conversion factor too small: 1609000.0 sq m per sq mile
oracle\test_area_conversion.py:73: AssertionError: Expected 15539928 sq m, got 9654000.0
=========================== 5 failed in 0.07s =========================
```

### After Fix
Apply the reference fix to `repo/converter.py` lines 8-10 and 19 (use SQUARE_MILES_TO_SQ_METERS for both storage and return value).

Run `python -m pytest oracle -q`:
```
.....                                                                  [100%]
========================= 5 passed in 0.07s =========================
```

All 5 tests pass after applying the fix.
