# Compute a line total with banker's rounding

The working directory contains `money.py` with `line_total(unit_price, qty)`. `unit_price` is a decimal string such as `"2.675"` and `qty` is an int. It currently uses binary floats, which round some decimal ties the wrong way.

Change it to use exact decimal arithmetic. The result is the unit price times the quantity, rounded to two decimal places with round-half-to-even (banker's rounding). Return a string with exactly two decimal places.

Examples:
- `line_total("2.675", 1)` returns `"2.68"`.
- `line_total("2.665", 1)` returns `"2.66"`.
- `line_total("19.99", 3)` returns `"59.97"`.

## Done when

`line_total` returns the values above and uses decimal arithmetic rather than floats.
