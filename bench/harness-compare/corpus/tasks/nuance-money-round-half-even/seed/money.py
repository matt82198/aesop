"""Money helpers."""


def line_total(unit_price, qty):
    """Return unit_price * qty as a two-decimal string. Uses floats, which is wrong for ties."""
    return "%.2f" % (float(unit_price) * qty)
