"""Money helpers."""

from decimal import ROUND_HALF_EVEN, Decimal


def line_total(unit_price, qty):
    """Return unit_price * qty as a two-decimal string, rounding half to even."""
    total = Decimal(unit_price) * qty
    return str(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
