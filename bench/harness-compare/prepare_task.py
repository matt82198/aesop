"""Copy a task's seed working copy into a fresh directory for a harness to edit.

Refuses a destination that already holds files, so every run starts clean.

Usage:
    python prepare_task.py --task bug-paginate --dest <empty-dir>

Stdlib only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import harness_corpus as hc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--task", required=True)
    parser.add_argument("--dest", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        row = hc.task_by_id(args.task)
    except KeyError:
        parser.error("unknown task: %r" % args.task)
    try:
        out = hc.prepare_task(row, args.dest)
    except FileExistsError as exc:
        parser.error(str(exc))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
