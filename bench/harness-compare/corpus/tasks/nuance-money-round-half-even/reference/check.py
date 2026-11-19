"""Hidden check for nuance-money-round-half-even. Usage: check.py <workdir>. Exit 0 only if every case passes."""

import sys


from money import line_total  # noqa: E402

cases = [
    ("tie rounds to even up", "2.675", 1, "2.68"),
    ("tie rounds to even down", "2.665", 1, "2.66"),
    ("plain multiply", "19.99", 3, "59.97"),
]

failures = []
for label, price, qty, want in cases:
    got = line_total(price, qty)
    if got != want:
        failures.append("%s: got %r want %r" % (label, got, want))

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ok")
sys.exit(0)
