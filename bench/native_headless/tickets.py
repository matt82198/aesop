"""Frozen synthetic repository, public examples and private grading cases."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Ticket:
    key: str
    requirement: str
    starter: str
    cases: tuple


TICKETS = (
    Ticket("windows", "Return consecutive slices of the list of the requested positive size, "
           "including a final partial slice. Raise ValueError for size <= 0.",
           "return [items]",
           (([[], 2], []), ([[1, 2, 3], 2], [[1, 2], [3]]), ([[0, 0], 1], [[0], [0]]),
            ([[1], 5], [[1]]), ([[], 0], {"error": "ValueError"}), ([[1], -1], {"error": "ValueError"}))),
    Ticket("unique", "Return the first spelling of each input string in order. Compare with "
           "Unicode casefold and retain the empty string once.",
           "return items",
           (([[]], []), ([["b", "A", "a", "B"]], ["b", "A"]),
            ([["Stra\u00dfe", "STRASSE", "x"]], ["Stra\u00dfe", "x"]),
            ([["", "a", "", "A"]], ["", "a"]), ([["a ", "A"]], ["a ", "A"]))),
    Ticket("intervals", "Merge overlapping or touching closed [start,end] pairs. Return sorted "
           "pairs, preserve gaps, accept empty input. Inputs always satisfy start <= end.",
           "return pairs",
           (([[]], []), ([[[5, 7], [1, 3], [3, 5]]], [[1, 7]]),
            ([[[0, 8], [2, 3], [10, 10]]], [[0, 8], [10, 10]]),
            ([[[-2, -1], [0, 0], [-2, -1]]], [[-2, -1], [0, 0]]))),
)

PARAMS = {"windows": "items, size", "unique": "items", "intervals": "pairs"}


def starter_source(ticket):
    import textwrap
    return f"def solve({PARAMS[ticket.key]}):\n" + textwrap.indent(ticket.starter, "    ") + "\n"


def repository():
    return {t.key + ".py": starter_source(t) for t in TICKETS}


def prompt(ticket):
    return (f"Fix solve in {ticket.key}.py. {ticket.requirement} "
            f"Change only that file and use only the standard library. "
            f"Example arguments and expected result: {ticket.cases[1]!r}.")
