"""Report rendering."""


def format_money(cents):
    """Format integer cents as dollars, e.g. -1234 -> '-$12.34'."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return "%s$%d.%02d" % (sign, cents // 100, cents % 100)


def total(rows):
    """Sum the cents field of each row."""
    return sum(row["cents"] for row in rows)


def render_report(rows):
    """Render one line per row, then a TOTAL line."""
    lines = []
    for row in rows:
        lines.append("%s %s" % (row["name"], format_money(row["cents"])))
    lines.append("TOTAL %s" % format_money(total(rows)))
    return "\n".join(lines)
