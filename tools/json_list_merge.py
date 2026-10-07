#!/usr/bin/env python3
"""Typed JSON list-union merge driver for ratchet/baseline files.
INDEX: Typed JSON list-union git merge driver for `*-baseline.json` ratchets (registered in `.gitattributes` as `merge=aesop-json-union`); signature `ANCESTOR OURS THEIRS` = git's `%O %A %B`, result written to `%A`; merges a bare string array OR an object holding exactly one string array (`{"violations": [...]}`), result = `sorted(set(ours) | set(theirs))` — which IS the ancestor-deletion rule (dropped by BOTH sides stays deleted, by ONE side stays kept); count-map baselines and every parse/shape failure exit 1 so git falls back to a normal conflict; `--stdout`/`--quiet`; exit 0=merged/1=refused/2=usage. ONE-TIME PER CLONE (see docs/INSTALL.md): `git config merge.aesop-json-union.name "union-and-sort JSON string lists"` and `git config merge.aesop-json-union.driver "python tools/json_list_merge.py %O %A %B"`

Kills the "two lanes both appended to the same ratchet file" conflict class.
Callable two ways with the SAME positional signature:

  CLI:           python tools/json_list_merge.py ANCESTOR OURS THEIRS [--stdout]
  git driver:    [merge "aesop-json-union"]
                     name = union-and-sort JSON string lists
                     driver = python tools/json_list_merge.py %O %A %B

Git hands the driver three temp files (%O ancestor, %A ours, %B theirs) and reads
the merge result back out of %A. Extra positional args (%L marker size, %P path)
are accepted and ignored so the standard five-placeholder form also works.

Supported shapes (all three sides must agree, else exit 1):
  * a bare top-level JSON array of strings, or
  * a top-level JSON object holding EXACTLY ONE string-array value plus any
    number of scalar keys (the ``{"violations": [...]}`` + ``_comment`` shape
    used by .stateapi-baseline.json).

Merge semantics: result = sorted(set(ours) | set(theirs)).
That single expression already IS the ancestor-aware deletion rule -- an entry
present in the ancestor but dropped by BOTH sides is absent from the union and
stays deleted, while an entry dropped by only ONE side survives in the union and
stays kept. The ancestor is therefore parsed and shape-checked (a corrupt
ancestor is a real signal that this file is not what the driver thinks it is)
but contributes no members. Count-map ratchets (.portability-baseline.json,
.subprocess-guard-baseline.json) are deliberately NOT a supported shape: union
is not a sound merge for counts, so they fail closed to a normal conflict.

Fail-closed contract: ANY parse failure, unsupported shape, shape mismatch
between sides, or non-string member => exit 1 and %A is left untouched, so git
falls back to a conventional conflict. The driver never writes invalid JSON and
never silently drops an entry.

Exit codes: 0 = merged and written; 1 = refused (git conflicts); 2 = usage error.
"""

import json
import sys
from typing import Any, Dict, List, Optional, Tuple

USAGE = (
    "usage: json_list_merge.py ANCESTOR OURS THEIRS [%L] [%P] [--stdout] [--quiet]\n"
    "       json_list_merge.py --help\n"
)

# (kind, list_key, items, scalars) describes one parsed side.
Shape = Tuple[str, Optional[str], List[str], Dict[str, Any]]

_SCALAR_TYPES = (str, int, float, bool, type(None))

BOM = "\ufeff"  # some baselines are BOM-prefixed on Windows


class ShapeError(Exception):
    """Raised when a side is not a supported typed-JSON-list document."""


def _as_string_list(value: Any) -> Optional[List[str]]:
    """Return value as a list of str, or None when it is not a string array."""
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, str):
            return None
    return list(value)


def parse_shape(text: str, label: str) -> Shape:
    """Parse one side into (kind, list_key, items, scalars). Raises ShapeError."""
    try:
        data = json.loads(text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ShapeError("%s: not valid JSON (%s)" % (label, exc))

    items = _as_string_list(data)
    if items is not None:
        return ("array", None, items, {})

    if not isinstance(data, dict):
        raise ShapeError(
            "%s: top level is %s; expected a string array or an object holding one"
            % (label, type(data).__name__)
        )

    list_keys = []
    scalars = {}
    for key, value in data.items():
        candidate = _as_string_list(value)
        if candidate is not None:
            list_keys.append(key)
        elif isinstance(value, _SCALAR_TYPES):
            scalars[key] = value
        else:
            raise ShapeError(
                "%s: key %r holds an unsupported value (%s); only string arrays and "
                "scalars are mergeable" % (label, key, type(value).__name__)
            )

    if len(list_keys) != 1:
        raise ShapeError(
            "%s: expected exactly one string-array key, found %d %r"
            % (label, len(list_keys), sorted(list_keys))
        )

    key = list_keys[0]
    return ("object", key, _as_string_list(data[key]) or [], scalars)


def merge_shapes(ancestor: Shape, ours: Shape, theirs: Shape) -> Any:
    """Union ours+theirs into the ours-shaped document. Raises ShapeError."""
    kinds = {ancestor[0], ours[0], theirs[0]}
    if len(kinds) != 1:
        raise ShapeError(
            "shape mismatch between sides: ancestor=%s ours=%s theirs=%s"
            % (ancestor[0], ours[0], theirs[0])
        )
    if ours[0] == "object":
        keys = {ancestor[1], ours[1], theirs[1]}
        if len(keys) != 1:
            raise ShapeError(
                "list key mismatch between sides: ancestor=%r ours=%r theirs=%r"
                % (ancestor[1], ours[1], theirs[1])
            )

    merged = sorted(set(ours[2]) | set(theirs[2]))

    if ours[0] == "array":
        return merged

    # Preserve the ours-side key order; scalar keys (e.g. _comment) come from
    # ours, then any scalar key only theirs has, so a newly added comment is not
    # silently dropped.
    out: Dict[str, Any] = {}
    for key in list(ours[3].keys()) + [ours[1]]:
        if key == ours[1]:
            out[key] = merged
        else:
            out[key] = ours[3][key]
    for key, value in theirs[3].items():
        if key not in out:
            out[key] = value
    return out


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def run_merge(ancestor_path: str, ours_path: str, theirs_path: str,
              to_stdout: bool = False, quiet: bool = False) -> int:
    """Merge the three sides; write to ours_path. Returns the process exit code."""
    try:
        ancestor_text = _read(ancestor_path)
        ours_text = _read(ours_path)
        theirs_text = _read(theirs_path)
    except (IOError, OSError, UnicodeDecodeError) as exc:
        if not quiet:
            sys.stderr.write("json_list_merge: unreadable side: %s\n" % exc)
        return 1

    try:
        merged = merge_shapes(
            parse_shape(ancestor_text.lstrip(BOM), "ancestor"),
            parse_shape(ours_text.lstrip(BOM), "ours"),
            parse_shape(theirs_text.lstrip(BOM), "theirs"),
        )
    except ShapeError as exc:
        if not quiet:
            sys.stderr.write(
                "json_list_merge: refusing to merge (%s); falling back to conflict\n" % exc
            )
        return 1

    # Match the generator's byte convention: json.dumps(indent=2) and the same
    # trailing-newline habit the ours side already had.
    rendered = json.dumps(merged, indent=2)
    if ours_text.endswith("\n"):
        rendered += "\n"

    if to_stdout:
        sys.stdout.write(rendered)
        return 0

    try:
        with open(ours_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
    except (IOError, OSError) as exc:
        if not quiet:
            sys.stderr.write("json_list_merge: cannot write %s: %s\n" % (ours_path, exc))
        return 1
    return 0


def main(argv: List[str]) -> int:
    if "--help" in argv or "-h" in argv:
        sys.stdout.write(__doc__ + "\n" + USAGE)
        return 0

    to_stdout = False
    quiet = False
    positional: List[str] = []
    for arg in argv:
        if arg == "--stdout":
            to_stdout = True
        elif arg == "--quiet":
            quiet = True
        elif arg.startswith("-"):
            sys.stderr.write("json_list_merge: unknown flag %r\n%s" % (arg, USAGE))
            return 2
        else:
            positional.append(arg)

    if len(positional) < 3:
        sys.stderr.write(USAGE)
        return 2

    return run_merge(positional[0], positional[1], positional[2], to_stdout, quiet)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
