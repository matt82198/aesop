# Add quoted-field support to csv_row

The working directory contains `csv_util.py` with `csv_row(line)`. It takes one line of comma-separated text and returns a list of fields. It currently splits on every comma, which breaks quoted fields.

Add support for RFC 4180 style quoting:
- A field wrapped in double quotes may contain commas.
- A doubled double quote inside a quoted field means one literal `"`.
- Unquoted fields behave as before.

Write a test in `tests/test_csv_util.py` that covers these cases, then make it pass.

## Done when

`tests/test_csv_util.py` exists and passes, `csv_row('a,"b,c",d')` returns `['a', 'b,c', 'd']`, and `csv_row('"x""y",z')` returns `['x"y', 'z']`.
