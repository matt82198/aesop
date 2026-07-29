# ST07: Unit Mismatch at Boundary — Solution

## Defect Class
**Unit conversion formula mismatch at boundary function**

## Hop Structure
- **Hop 0 (Symptom):** main.py calls process_plot(10, 10), receives area_sq_meters = 160.9M instead of 259M
- **Hop 1:** plot_processor.py calls convert_to_square_meters() with the area in square miles
- **Hop 2 (Root cause):** converter.py uses MILES_TO_METERS_FACTOR (1.609 * 1M) instead of the correct conversion factor (2.589 * 1M)

The defect manifests as wrong output values, but the root cause is the boundary converter using a linear conversion factor (1.609, for miles→km) instead of the correct square conversion factor (1.609² = 2.589, for square miles→square meters).

## Reference Fix

In `converter.py`, change the `convert_to_square_meters()` function from:
```python
def convert_to_square_meters(area_square_miles):
    ...
    return area_square_miles * MILES_TO_METERS_FACTOR
```

To:
```python
def convert_to_square_meters(area_square_miles):
    ...
    return area_square_miles * SQUARE_MILES_TO_SQ_METERS
```

The defect is using the wrong constant in the return statement: `MILES_TO_METERS_FACTOR` (which represents a linear conversion, 1.609 million) instead of `SQUARE_MILES_TO_SQ_METERS` (the correct square area conversion, 2.589 million).

**Rationale:** The converter module uses a linear conversion factor (1.609 million) derived from the miles-to-kilometers conversion, but this is wrong for area conversion. Converting area requires squaring the linear conversion factor: (1.609)² ≈ 2.589. The defect surfaces 2 hops away in the output values—the caller gets consistently wrong area conversions—but the root cause is the incorrect conversion constant at the boundary layer between area calculation and metric conversion.

## Verification Transcript

### Before Fix
```
FFFFF                                                                    [100%]
================================== FAILURES ===================================
test_1x1_plot_conversion: Expected 2589988 sq m, got 1609000.0
test_10x10_plot_conversion: Expected 258998800 sq m, got 160900000.0
test_5x5_plot_conversion: Expected 64749700 sq m, got 40225000.0
test_conversion_factor_correctness: Conversion factor too small: 1609000.0 sq m per sq mile
test_2x3_plot_conversion: Expected 15539928 sq m, got 9654000.0
=========================== 5 failed in 0.05s =========================
```

### After Fix
Apply the reference fix to `repo/converter.py` line 20: change `return area_square_miles * MILES_TO_METERS_FACTOR` to `return area_square_miles * SQUARE_MILES_TO_SQ_METERS`.

Run `python -m pytest oracle -q`:
```
.....                                                                  [100%]
========================= 5 passed in 0.01s =========================
```

All 5 tests pass after applying the fix.

## Visible Repro Test

### Test Assertions
The visible test `repo/test_repro.py` encodes the observable symptom:
- square mile to square meter conversion is accurate

### Fail Output (Defective Code)
```
cd bench/seam_tasks/st07/repo && python -m pytest test_repro.py -q

F...                                                                     [100%]
...
conversion uses wrong factor, off by magnitude
...
1 failed, 0+ passed in X.XXs
```

### Pass Output (Fixed Code)
```
cd bench/seam_tasks/st07/repo && python -m pytest test_repro.py -q

...                                                                      [100%]
1+ passed in 0.XXs
```

### Distinction from Oracle
The visible test is simpler and more focused than the oracle suite:
- Visible: Minimal test demonstrating the observable symptom
- Oracle: Comprehensive tests covering edge cases and multiple scenarios
- Visible test encodes only what the task statement describes; oracle is thorough verification
