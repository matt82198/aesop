"""Hidden check for nuance-leap-day-age. Usage: check.py <workdir>. Exit 0 only if every case passes."""

import sys
from datetime import date

sys.path.insert(0, sys.argv[1])

from ages import age_on  # noqa: E402

cases = [
    ("ordinary before birthday", date(1990, 6, 15), date(2024, 6, 14), 33),
    ("ordinary on birthday", date(1990, 6, 15), date(2024, 6, 15), 34),
    ("leap-born, non-leap year, Feb 28", date(2000, 2, 29), date(2025, 2, 28), 24),
    ("leap-born, non-leap year, Mar 1", date(2000, 2, 29), date(2025, 3, 1), 25),
    ("leap-born, leap year, Feb 29", date(2000, 2, 29), date(2024, 2, 29), 24),
    ("leap-born, leap year, Feb 28", date(2000, 2, 29), date(2024, 2, 28), 23),
]

failures = []
for label, born, today, want in cases:
    got = age_on(born, today)
    if got != want:
        failures.append("%s: got %r want %r" % (label, got, want))

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ok")
sys.exit(0)
