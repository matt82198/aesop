#!/usr/bin/env python3
"""Repair CLAUDE.md line-cap errors at sentence boundaries.

INDEX: Split excess over-cap CLAUDE.md lines at sentence boundaries, honoring the lint baseline. Use --check to preview or --write to apply.
"""

import argparse
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

from claudemd_lint import (LINE_CAP_BASELINE, MAX_LINE_BYTES,
                           _load_line_cap_baseline, find_over_cap_lines)


# A list item (including the hooks document's "2a." numbering) keeps its
# continuations in the same paragraph. Ordinary prose starts a new paragraph.
LIST_ITEM = re.compile(rb" {0,3}(?:[-*+]|\d+[a-z]?[.)])[ \t]+")
STRUCTURAL = re.compile(rb" {4}|\t| {0,3}(?:#{1,6}\s|>|\||~~~|```|<!--)")
BOUNDARY_OR_TICKS = re.compile(rb"""(?P<ticks>`+)|[.!?]["')\]]*(?P<gap>[ \t]+)(?=\S)""")
EOL = re.compile(rb"(\r\n|\n|\r)")


def split_sentences(line):
    """Return capped byte slices, or None if no complete repair is possible."""
    if STRUCTURAL.match(line):
        return None
    marker = LIST_ITEM.match(line)
    cuts = []
    ticks = 0
    for match in BOUNDARY_OR_TICKS.finditer(line):
        token = match.group("ticks")
        if token:
            if ticks == len(token):
                ticks = 0
            elif not ticks:
                ticks = len(token)
        elif not ticks and (not marker or match.start() >= marker.end()):
            # A block marker at the start of a new line changes its meaning.
            if not (STRUCTURAL.match(line, match.end()) or LIST_ITEM.match(line, match.end())):
                cuts.append(match.span("gap"))

    pieces = []
    start = index = 0
    while len(line) - start > MAX_LINE_BYTES:
        first = index
        while index < len(cuts) and cuts[index][0] - start <= MAX_LINE_BYTES:
            index += 1
        if index == first:
            return None
        end, following = cuts[index - 1]
        pieces.append(line[start:end])
        start = following
    return pieces + [line[start:]]


def plan(original, allowance):
    # Match the linter's text-mode newline handling, but retain every original
    # terminator (including mixed endings and a missing final newline) for output.
    fields = EOL.split(original)
    content = b"\n".join(fields[::2]).decode("utf-8")
    flagged = find_over_cap_lines(content)
    needed = len(flagged) - allowance
    if needed <= 0:
        return original, []

    candidates = []
    refused = []
    for number, _ in flagged:
        line = fields[2 * (number - 1)]
        pieces = split_sentences(line)
        if pieces is None:
            refused.append(number)
        else:
            candidates.append((len(pieces), number, pieces))
    if len(candidates) < needed:
        lines = ", ".join(map(str, refused))
        raise ValueError(f"cannot repair within allowance: no safe sentence split on lines {lines}")

    selected = []
    for _, number, pieces in sorted(candidates)[:needed]:
        i = 2 * (number - 1)
        eol = fields[i + 1] if i + 1 < len(fields) else (fields[i - 1] if i else b"\n")
        separator = eol if LIST_ITEM.match(fields[i]) else eol * 2
        fields[i] = separator.join(pieces)
        selected.append(number)
    return b"".join(fields), sorted(selected)


def replace_file(path, data):
    mode = stat.S_IMODE(path.stat().st_mode)
    tmp = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
    name = Path(tmp.name)
    try:
        with tmp:
            tmp.write(data)
        name.chmod(mode)
        os.replace(name, path)
    finally:
        name.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true", help="report changes and exit 1 if needed")
    modes.add_argument("--write", action="store_true", help="apply the complete repair")
    args = parser.parse_args()
    path = args.path.resolve()
    if path.name != "CLAUDE.md":
        parser.error("expected a CLAUDE.md path")
    try:
        root = next((p for p in path.parents if (p / ".git").exists()
                     or (p / LINE_CAP_BASELINE).is_file()), path.parent)
        rel = path.relative_to(root).as_posix()
        allowance = _load_line_cap_baseline(root).get(rel, 0)
        result, lines = plan(path.read_bytes(), allowance)
        if lines:
            if args.write:
                replace_file(path, result)
            action = "split" if args.write else "would split"
            print(f"{rel}: {action} lines {', '.join(map(str, lines))}")
    except (ValueError, OSError) as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return 2
    return int(args.check and bool(lines))


if __name__ == "__main__":
    sys.exit(main())
