"""Report rendering."""

from formatting import format_money
from totals import total


def render_report(rows):
    """Render one line per row, then a TOTAL line."""
    lines = []
    for row in rows:
        lines.append("%s %s" % (row["name"], format_money(row["cents"])))
    lines.append("TOTAL %s" % format_money(total(rows)))
    return "\n".join(lines)
