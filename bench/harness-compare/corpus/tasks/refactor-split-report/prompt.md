# Split report.py into formatting, totals and rendering

The working directory contains `report.py`. It mixes three jobs in one file: money formatting (`format_money`), summing (`total`) and rendering (`render_report`).

Make this layout:
- `formatting.py` defines `format_money`.
- `totals.py` defines `total`.
- `report.py` keeps `render_report` and imports `format_money` and `total` from the new modules. It must not define them itself.

Output must not change. `render_report` produces the same text as before.

## Done when

`formatting.format_money` and `totals.total` exist, `report.py` imports them rather than defining them, and `report.render_report` output is unchanged.
