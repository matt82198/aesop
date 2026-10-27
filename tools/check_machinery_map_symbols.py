#!/usr/bin/env python3
"""Check that docs/MACHINERY-MAP.md cites symbols, never line numbers.

INDEX: Machinery-map gate; fails when docs/MACHINERY-MAP.md has a bare `:<digits>` line citation or a `file `symbol`` cite whose symbol no longer exists in the cited file; `(was :N, now stale: ...)` drift annotations are allowed and counted; `--skip-if-scaffold` exits 0 only when the map is absent and bin/cli.js is absent; stdlib-only; exit 0 pass, 1 drift, 2 usage/environment error.

Why: line numbers in the map go stale on every edit, so agents learn the map is
unreliable and read the code directly. Each citation now names the enclosing
function, class, job or heading (`hooks/pre-push-policy.sh `check_tracker_guard``).
When a named symbol no longer sits where the row says, the rewrite keeps the
symbol and records the drift as `(was :N, now stale: <clause>)`. That annotation
is the only place a line number may appear, so drift stays visible to the
orchestrator instead of being silently corrected.

Checks:
- any `:<digits>` (or `:<digits>-<digits>`) that is not inside a `(was :N, ...)`
  annotation fails the gate, with its line number in the map;
- every `<path> `<symbol>`` cite must resolve to a tracked file (exact path, or a
  unique path suffix such as `ci.yml`) and the symbol must appear in that file as
  a whole token (or as a substring for multi-word heading names);
- a missing docs/MACHINERY-MAP.md is exit 2 (fail closed, not a silent pass).

Usage: python tools/check_machinery_map_symbols.py --check [--root <repo>] [--map <path>] [--skip-if-scaffold]
"""

import argparse
import os
import re
import sys
from pathlib import Path

MAP_RELPATH = "docs/MACHINERY-MAP.md"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
EXT_RE = r"(?:py|sh|mjs|js|ps1|vbs|yml|yaml|md|json|txt|jsonl|ts)"
PATH_RE = r"(?:\.?[\w.-]+/)*\.?[\w.-]+\." + EXT_RE
# A bare line citation: colon + digits, not glued to a word, path or time (10:30).
LINE_CITE_RE = re.compile(r"(?<![0-9])(:\d+(?:-\d+)?)(?![\w])")
# A code span that is `<path> <symbol>` (the file and symbol inside one backtick pair).
SPAN_PAIR_RE = re.compile(r"^(" + PATH_RE + r")\s+([^\s`]+)$")
# `<symbol>` immediately after a path outside a code span: `hooks/x.sh `check_y``.
OUTSIDE_RE = re.compile(r"(" + PATH_RE + r")\s+\(?`([^`]+)`")
ANNOTATION_RE = re.compile(r"\(was :\d+(?:-\d+)?, now stale: [^)]*\)")
IDENT_RE = re.compile(r"^[A-Za-z_][\w.-]*$")


def _repo_files(root: Path):
    """Return {relpath_posix: abspath} for every regular file under root (no .git)."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            found[rel] = str(full)
    return found


def resolve_path(token, files):
    """Resolve a cited path to one repo file: exact, else the unique suffix match."""
    token = token.lstrip("./") if token.startswith("./") else token
    if token in files:
        return token
    matches = [rel for rel in files if rel == token or rel.endswith("/" + token)]
    if len(matches) == 1:
        return matches[0]
    return None


def is_symbol_candidate(symbol):
    """False for command flags, globs, shell words and other non-symbol code spans."""
    if symbol.startswith(("-", "$", "~", "/", "<", ".")):
        return False
    return not any(ch in symbol for ch in "=<>*|/")


def symbol_present(symbol, text):
    """True when the symbol names something in text (identifier token, dotted parts, or heading substring)."""
    if IDENT_RE.match(symbol):
        parts = symbol.split(".")
        return all(
            re.search(r"(?<![\w-])" + re.escape(p) + r"(?![\w-])", text) is not None for p in parts if p
        )
    return symbol in text


def _iter_cites(line):
    """Yield (path, symbol) for every `file `symbol`` cite on one map line."""
    # Code spans first: `path symbol` inside one backtick pair.
    for span in re.findall(r"`([^`]+)`", line):
        m = SPAN_PAIR_RE.match(span)
        if m:
            yield m.group(1), m.group(2)
    for m in OUTSIDE_RE.finditer(line):
        yield m.group(1), m.group(2)


def check_map(root: Path, map_path: Path):
    """Return (problems, resolved_count, stale_count, line_cite_count)."""
    problems = []
    resolved = 0
    stale = 0
    line_cites = 0
    files = _repo_files(root)
    bodies = {}
    text = map_path.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.split("\n"), 1):
        stripped = ANNOTATION_RE.sub("", line)
        stale += len(ANNOTATION_RE.findall(line))
        for m in LINE_CITE_RE.finditer(stripped):
            line_cites += 1
            problems.append(f"line {lineno}: line citation {m.group(1)} (cite the enclosing symbol instead)")
        for path, symbol in _iter_cites(stripped):
            if not is_symbol_candidate(symbol):
                continue
            rel = resolve_path(path, files)
            if rel is None:
                problems.append(f"line {lineno}: cited file not found or ambiguous: {path}")
                continue
            if rel not in bodies:
                bodies[rel] = Path(files[rel]).read_text(encoding="utf-8", errors="replace")
            if not symbol_present(symbol, bodies[rel]):
                problems.append(f"line {lineno}: symbol {symbol} not found in {rel}")
                continue
            resolved += 1
    return problems, resolved, stale, line_cites


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check machinery-map citations name symbols, not line numbers.")
    parser.add_argument("--check", action="store_true", help="verify the map (exit 1 on drift)")
    parser.add_argument("--root", default=None, help="repository root (default: parent of tools/)")
    parser.add_argument("--map", default=None, help="map file (default: <root>/docs/MACHINERY-MAP.md)")
    parser.add_argument("--skip-if-scaffold", action="store_true",
                        help="exit 0 (skip token) only when the map is absent and bin/cli.js is absent (scaffold tree)")
    args = parser.parse_args(argv)
    if not args.check:
        parser.error("--check is required")
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    map_path = Path(args.map).resolve() if args.map else root / MAP_RELPATH
    if args.skip_if_scaffold and not map_path.is_file() and not (root / "bin" / "cli.js").is_file():
        print("machinery_map_symbols_skipped_scaffold")
        return 0
    if not map_path.is_file():
        print(f"ERROR: machinery map not found: {map_path}", file=sys.stderr)
        return 2
    problems, resolved, stale, line_cites = check_map(root, map_path)
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        print(f"machinery-map symbols: {len(problems)} problem(s); fix the citation, then re-run --check")
        return 1
    print(f"PASS machinery-map symbols: {resolved} symbol citations resolved, "
          f"{stale} stale annotations, {line_cites} line citations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
