# Fix age_on for leap-day birthdays

The working directory contains `ages.py` with `age_on(born, today)`, where both arguments are `datetime.date` values. It returns the number of full years between `born` and `today`. It is currently wrong for people born on February 29.

Rule to implement: a person's age increases on their birthday. In a year that has no February 29, a leap-day birthday is treated as reached on March 1. In a leap year it is reached on February 29 itself.

Return an int. Do not change the function signature.

## Done when

`age_on` returns the number of completed years under the rule above for ordinary birthdays and for February 29 birthdays, including the day before and the day of March 1 in a non-leap year.
