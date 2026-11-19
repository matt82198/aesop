"""Run a task's hidden check against a working copy and print the verdict.

Exit 0 when the check passes, 1 when it fails, 2 on bad input.

Usage:
    python check_task.py --task bug-paginate --workdir <dir>

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
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        row = hc.task_by_id(args.task)
    except KeyError:
        parser.error("unknown task: %r" % args.task)
    if not args.workdir.is_dir():
        parser.error("--workdir is not a directory: %s" % args.workdir)

    result = hc.run_check(row, args.workdir)
    print("PASS" if result.passed else "FAIL (exit %d)" % result.exit_code)
    if not result.passed and result.reason:
        print(result.reason)
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
