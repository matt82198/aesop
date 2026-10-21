#!/usr/bin/env python3
"""Lossless reconcile of two diverged copies of an append-only log.

INDEX: lossless union reconcile of two diverged append-only logs (base optional; never drops a line; refuses to overwrite; verifies kept-all before writing; exit 0/2).

Usage:
    python tools/reconcile_log.py --base <file> --ours <file> --theirs <file> --out <newfile>

Output is a union of both sides. Runs shared by both sides are emitted once,
and every line that only one side has is kept, including a line that one side
deleted: an append-only log must never lose a line to a one-sided deletion.
Line endings are normalized to LF before comparing, so CRLF and LF copies of
the same content do not duplicate.

--base is optional. It is validated when given but does not change the output,
because a 3-way merge would apply a one-sided deletion and drop the line.

The output is verified before anything is written: every line of ours and of
theirs must appear in the output with a multiset count of at least the larger
of the two inputs' counts. On violation nothing is written and the tool exits 2.
Input files are never written. --out must not exist and must not be an input.

Exit codes: 0 success; 2 refusal, invariant violation, or unreadable input.
"""

import argparse
import collections
import difflib
import os
import sys

EXIT_OK = 0
EXIT_REFUSED = 2


class ReconcileError(Exception):
    """A refusal or failed invariant. main() maps it to exit 2."""


def read_lines(path):
    """Return the lines of a UTF-8 file, with CRLF and LF both normalized to LF."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ReconcileError("cannot read %s: %s" % (path, exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReconcileError("%s is not valid UTF-8: %s" % (path, exc))
    text = text.replace("\r\n", "\n")
    if not text:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


def merge_lines(ours, theirs):
    """Union two line lists: shared runs once, one-sided runs from both sides."""
    matcher = difflib.SequenceMatcher(None, ours, theirs, autojunk=False)
    merged = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            merged.extend(ours[i1:i2])
        else:
            merged.extend(ours[i1:i2])
            merged.extend(theirs[j1:j2])
    return merged


def find_missing(ours, theirs, merged):
    """Return {line: shortfall} for every line whose output count is too low."""
    required = collections.Counter()
    for side in (collections.Counter(ours), collections.Counter(theirs)):
        for line, count in side.items():
            if count > required[line]:
                required[line] = count
    have = collections.Counter(merged)
    return {
        line: required[line] - have[line]
        for line in required
        if have[line] < required[line]
    }


def _same_path(first, second):
    """True when two paths name the same file location."""
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def reconcile(base_path, ours_path, theirs_path, out_path):
    """Merge the two sides into out_path. Returns (ours, theirs, merged) line lists."""
    inputs = [path for path in (base_path, ours_path, theirs_path) if path]
    for path in inputs:
        if _same_path(path, out_path):
            raise ReconcileError("--out must not be an input: %s" % out_path)
    if os.path.exists(out_path):
        raise ReconcileError("refusing to overwrite existing %s" % out_path)
    if base_path:
        read_lines(base_path)
    ours = read_lines(ours_path)
    theirs = read_lines(theirs_path)
    merged = merge_lines(ours, theirs)
    missing = find_missing(ours, theirs, merged)
    if missing:
        raise ReconcileError(
            "invariant violated: %d line(s) missing from output; nothing written"
            % sum(missing.values())
        )
    body = "".join(line + "\n" for line in merged)
    try:
        with open(out_path, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
    except FileExistsError:
        raise ReconcileError("refusing to overwrite existing %s" % out_path)
    except OSError as exc:
        raise ReconcileError("cannot write %s: %s" % (out_path, exc))
    return ours, theirs, merged


def main(argv=None):
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        description="Lossless union reconcile of two diverged append-only logs."
    )
    parser.add_argument("--base", default=None, help="optional common ancestor")
    parser.add_argument("--ours", required=True, help="this side's copy")
    parser.add_argument("--theirs", required=True, help="the other side's copy")
    parser.add_argument("--out", required=True, help="new file to create")
    args = parser.parse_args(argv)
    try:
        ours, theirs, merged = reconcile(args.base, args.ours, args.theirs, args.out)
    except ReconcileError as exc:
        sys.stderr.write("RECONCILE REFUSED: %s\n" % exc)
        return EXIT_REFUSED
    print(
        "RECONCILE: ours=%d theirs=%d out=%d kept-all=yes"
        % (len(ours), len(theirs), len(merged))
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
