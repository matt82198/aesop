"""Age calculation."""

import calendar
from datetime import date


def _birthday_in(year, born):
    """Return the date the birthday falls on in year.

    A February 29 birthday falls on March 1 in a non-leap year.
    """
    if born.month == 2 and born.day == 29 and not calendar.isleap(year):
        return date(year, 3, 1)
    return date(year, born.month, born.day)


def age_on(born, today):
    """Return full years between born and today."""
    years = today.year - born.year
    if today < _birthday_in(today.year, born):
        years -= 1
    return years
