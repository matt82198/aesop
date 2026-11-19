"""Hidden check for refactor-split-report. Usage: check.py <workdir>. Exit 0 only if every case passes."""

import os
import sys

WORKDIR = sys.argv[1]

failures = []

with open(os.path.join(WORKDIR, "report.py"), encoding="utf-8") as fh:
    report_src = fh.read()
if "def format_money" in report_src:
    failures.append("report.py still defines format_money")
if "def total(" in report_src:
    failures.append("report.py still defines total")

try:
    import formatting
    import totals
    import report
except ImportError as exc:
    print("import failed: %s" % exc)
    sys.exit(1)

if report.format_money is not formatting.format_money:
    failures.append("report.format_money is not imported from formatting")
if report.total is not totals.total:
    failures.append("report.total is not imported from totals")

if formatting.format_money(-1234) != "-$12.34":
    failures.append("format_money(-1234) wrong")
if formatting.format_money(5) != "$0.05":
    failures.append("format_money(5) wrong")
if totals.total([{"cents": 1}, {"cents": 2}]) != 3:
    failures.append("total wrong")

rows = [{"name": "a", "cents": 150}, {"name": "b", "cents": -25}]
want = "a $1.50\nb -$0.25\nTOTAL $1.25"
got = report.render_report(rows)
if got != want:
    failures.append("render_report: got %r want %r" % (got, want))

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ok")
sys.exit(0)
