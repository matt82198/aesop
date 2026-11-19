"""Money formatting."""


def format_money(cents):
    """Format integer cents as dollars, e.g. -1234 -> '-$12.34'."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return "%s$%d.%02d" % (sign, cents // 100, cents % 100)
