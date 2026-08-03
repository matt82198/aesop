#!/usr/bin/env python3
"""Registry of repo paths whose content is MACHINE-GENERATED.

A generated file has exactly one legitimate writer: its generator. When a human
or an agent hand-edits one, two things go wrong at once -- the edit is silently
reverted on the next regeneration, and every concurrent lane that regenerates it
collides on the same lines. That is the contended-file conflict class. This
module is the single declared list of those paths, plus the gate that keeps them
out of ordinary pushes.

API:
    is_generated(path) -> dict | None   the matching registry entry, or None

CLI:
    generated_paths.py --list [--json]        show the registry
    generated_paths.py --check PATH [PATH..]  exit 1 if any path is registered
    generated_paths.py --check                same, reading paths from stdin

Exit codes: 0 = no registered path touched (or escape hatch set); 1 = at least
one registered path touched; 2 = usage error.

Escape hatch: AESOP_ALLOW_GENERATED=1 turns --check into a no-op that reports
what it would have blocked. This is NOT a gate weakening -- it is the DESIGNED
writer path. The generators themselves, the regeneration step of the merge
train, and the daemon/orchestrator regeneration pushes set it precisely because
they are the legitimate writer; everyone else is not.

Matching is purely lexical (segment-wise fnmatch on a POSIX-normalized relative
path); a registered path does not have to exist on disk, so entries can be
declared before the generator that will own them lands.
"""

import fnmatch
import os
import sys
from typing import Dict, List, Optional

USAGE = (
    "usage: generated_paths.py --list [--json]\n"
    "       generated_paths.py --check [PATH ...]   (paths from stdin when omitted)\n"
)

ALLOW_ENV = "AESOP_ALLOW_GENERATED"

# Each entry: pattern (repo-relative, POSIX separators, segment-wise globs),
# generator (the ONLY writer), why (what the file is).
REGISTRY: List[Dict[str, str]] = [
    {
        "pattern": "state/ledger/*.jsonl",
        "generator": "tools/transcript_digest.py / tools/fleet_ledger.py (append-only writers)",
        "why": "append-only machine ledgers; hand edits break the journal invariant",
    },
    {
        "pattern": "tools/INDEX.md",
        "generator": "tools/gen_tool_index.py --regenerate",
        "why": "generated tool index extracted from per-module INDEX: docstrings",
    },
    {
        "pattern": "tests/SUITE-COUNTS.md",
        "generator": "tools/verify_test_suite_count.py --fix",
        "why": "generated SUITE-COUNTS marker block (test suite counts by family)",
    },
]


def normalize(path: str) -> str:
    """Normalize a path to a repo-relative POSIX string for lexical matching."""
    text = str(path).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def _match(path: str, pattern: str) -> bool:
    """Segment-wise fnmatch so ``*`` never crosses a directory separator."""
    parts = normalize(path).split("/")
    globs = pattern.split("/")
    if len(parts) != len(globs):
        return False
    return all(fnmatch.fnmatchcase(p, g) for p, g in zip(parts, globs))


def is_generated(path: str) -> Optional[Dict[str, str]]:
    """Return the registry entry owning ``path``, or None when it is authored."""
    for entry in REGISTRY:
        if _match(path, entry["pattern"]):
            return entry
    return None


def check_paths(paths: List[str]) -> List[Dict[str, str]]:
    """Return one {path, pattern, generator, why} record per registered path."""
    hits = []
    for raw in paths:
        path = normalize(raw)
        if not path:
            continue
        entry = is_generated(path)
        if entry is not None:
            record = dict(entry)
            record["path"] = path
            hits.append(record)
    return hits


def _emit_list(as_json: bool) -> int:
    if as_json:
        import json

        sys.stdout.write(json.dumps({"registry": REGISTRY}, indent=2) + "\n")
        return 0
    sys.stdout.write("Generated-path registry (%d entries)\n" % len(REGISTRY))
    for entry in REGISTRY:
        sys.stdout.write("  %-28s generator: %s\n" % (entry["pattern"], entry["generator"]))
        sys.stdout.write("  %-28s %s\n" % ("", entry["why"]))
    return 0


def _emit_check(paths: List[str], as_json: bool) -> int:
    hits = check_paths(paths)
    allowed = os.environ.get(ALLOW_ENV, "") == "1"

    if as_json:
        import json

        sys.stdout.write(json.dumps(
            {"hits": hits, "count": len(hits), "allowed": allowed}, indent=2) + "\n")
    elif hits:
        stream = sys.stdout if allowed else sys.stderr
        verb = "ALLOWED (%s=1)" % ALLOW_ENV if allowed else "BLOCKED"
        stream.write("generated_paths: %s -- %d machine-generated path(s) touched\n"
                     % (verb, len(hits)))
        for hit in hits:
            stream.write("  %s\n" % hit["path"])
            stream.write("      generator: %s\n" % hit["generator"])
            stream.write("      %s\n" % hit["why"])
        if not allowed:
            stream.write("  Do not hand-edit these. Re-run the generator above; the\n")
            stream.write("  generator/daemon path sets %s=1 to push the result.\n" % ALLOW_ENV)

    if not hits or allowed:
        return 0
    return 1


def main(argv: List[str]) -> int:
    if "--help" in argv or "-h" in argv:
        sys.stdout.write(__doc__ + "\n" + USAGE)
        return 0

    as_json = "--json" in argv
    do_list = "--list" in argv
    do_check = "--check" in argv
    paths: List[str] = []
    for arg in argv:
        if arg in ("--json", "--list", "--check"):
            continue
        if arg.startswith("-"):
            sys.stderr.write("generated_paths: unknown flag %r\n%s" % (arg, USAGE))
            return 2
        paths.append(arg)

    if do_list and do_check:
        sys.stderr.write("generated_paths: --list and --check are mutually exclusive\n" + USAGE)
        return 2
    if not do_list and not do_check:
        return _emit_list(as_json)
    if do_list:
        if paths:
            sys.stderr.write("generated_paths: --list takes no paths\n" + USAGE)
            return 2
        return _emit_list(as_json)

    if not paths and not sys.stdin.isatty():
        paths = [line for line in sys.stdin.read().splitlines() if line.strip()]
    return _emit_check(paths, as_json)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
